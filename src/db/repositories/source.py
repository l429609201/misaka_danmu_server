"""
SourceRepository - 数据源数据访问层
"""

import logging
from typing import Optional, List, Dict, Any
from sqlalchemy import select, func, and_, or_, update, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..orm_models import AnimeSource, Anime, Episode, Scraper, AnimeMetadata
from .base import BaseRepository
from .source_order import reserve_source_order
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


class SourceRepository(BaseRepository[AnimeSource]):
    """数据源 Repository"""

    async def set_calendar_tracking_by_metadata(
        self, *, bangumi_id: Optional[str] = None,
        trakt_id: Optional[str] = None, tmdb_id: Optional[str] = None,
        finished: bool = False,
    ) -> int:
        """按任一日历外部标识更新追更，返回匹配作品数或取消时的源数。"""
        conditions = []
        for column, value in (
            (AnimeMetadata.bangumiId, bangumi_id),
            (AnimeMetadata.traktId, trakt_id),
            (AnimeMetadata.tmdbId, tmdb_id),
        ):
            if value:
                conditions.append(column == str(value))
        if not conditions:
            return 0
        result = await self._session.execute(
            select(AnimeMetadata.animeId).where(or_(*conditions))
        )
        anime_ids = [value for value in result.scalars().all() if value is not None]
        if not anime_ids:
            return 0
        if finished:
            sources = (await self._session.execute(
                select(AnimeSource).where(AnimeSource.animeId.in_(anime_ids))
            )).scalars().all()
            for source in sources:
                source.isFinished = True
            count = len(sources)
        else:
            # 保持日历订阅兜底语义：开启匹配作品的所有源，不切换精确标记。
            await self._session.execute(
                update(AnimeSource).where(AnimeSource.animeId.in_(anime_ids))
                .values(incrementalRefreshEnabled=True)
            )
            count = len(anime_ids)
        await self._session.flush()
        return count


    async def increment_refresh_failures(self, source_id: int) -> Optional[int]:
        """原子增加追更失败次数并返回新值，由调用方决定禁用阈值及事务。"""
        # 使用数据库自增表达式，避免并发任务读改写丢失计数。
        await self._session.execute(
            update(AnimeSource).where(AnimeSource.id == source_id).values(
                incrementalRefreshFailures=AnimeSource.incrementalRefreshFailures + 1,
            )
        )
        await self._session.flush()
        result = await self._session.execute(
            select(AnimeSource.incrementalRefreshFailures).where(AnimeSource.id == source_id)
        )
        return result.scalar_one_or_none()


    async def get_by_id(self, source_id: int) -> Optional[AnimeSource]:
        """根据 ID 获取数据源（包含动漫和分集信息）"""
        stmt = select(AnimeSource).where(AnimeSource.id == source_id).options(
            selectinload(AnimeSource.anime),
            selectinload(AnimeSource.episodes)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all(self, **filters) -> List[AnimeSource]:
        """获取所有数据源"""
        stmt = select(AnimeSource)

        if 'anime_id' in filters:
            stmt = stmt.where(AnimeSource.animeId == filters['anime_id'])
        if 'provider_name' in filters:
            stmt = stmt.where(AnimeSource.providerName == filters['provider_name'])

        stmt = stmt.order_by(AnimeSource.sourceOrder)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(
        self,
        anime_id: int,
        provider_name: str,
        media_id: str,
        source_order: Optional[int] = None
    ) -> AnimeSource:
        """创建数据源"""
        # 如果未指定 sourceOrder，自动计算
        if source_order is None:
            max_order_stmt = (
                select(func.max(AnimeSource.sourceOrder))
                .where(AnimeSource.animeId == anime_id)
            )
            max_order_result = await self._session.execute(max_order_stmt)
            max_order = max_order_result.scalar_one_or_none() or 0
            source_order = max_order + 1

        source = AnimeSource(
            animeId=anime_id,
            providerName=provider_name,
            mediaId=media_id,
            sourceOrder=source_order,
            createdAt=get_now()
        )
        self._session.add(source)
        await self._session.flush()
        return source

    async def update(self, source_id: int, **data) -> Optional[AnimeSource]:
        """更新数据源"""
        source = await self.get_by_id(source_id)
        if not source:
            return None

        for key, value in data.items():
            if hasattr(source, key):
                setattr(source, key, value)

        await self._session.flush()
        return source

    async def delete(self, source_id: int) -> bool:
        """删除数据源"""
        source = await self.get_by_id(source_id)
        if not source:
            return False

        await self._session.delete(source)
        await self._session.flush()
        return True

    async def link_source_to_anime(
        self,
        anime_id: int,
        provider_name: str,
        media_id: str
    ) -> int:
        """将外部数据源关联到作品条目，已存在则直接返回其 ID。

        why: 关联前先补建 scrapers 表条目——'custom' 等非文件型源不会出现在
        已加载的搜索源列表中，缺失该条目会导致后续依赖 scrapers 的查询失效。

        Args:
            anime_id: 作品条目 ID
            provider_name: 数据源提供方名称
            media_id: 该数据源上的媒体 ID

        Returns:
            关联记录（AnimeSource）的 ID
        """
        scraper_entry = await self._session.get(Scraper, provider_name)
        if not scraper_entry:
            logger.info(f"提供商 '{provider_name}' 在 scrapers 表中不存在，将为其创建新条目。")
            max_order_stmt = select(func.max(Scraper.displayOrder))
            max_order = (await self._session.execute(max_order_stmt)).scalar_one_or_none() or 0
            self._session.add(Scraper(
                providerName=provider_name,
                displayOrder=max_order + 1,
                isEnabled=True,  # 自定义源默认启用
                useProxy=False,
            ))
            await self._session.flush()

        stmt = select(AnimeSource.id).where(
            AnimeSource.animeId == anime_id,
            AnimeSource.providerName == provider_name,
            AnimeSource.mediaId == media_id
        )
        existing_id = (await self._session.execute(stmt)).scalar_one_or_none()
        if existing_id:
            return existing_id

        # 与后备详情共用租约；锁内重查，避免并发入库重复建源或占用其他源预留序号。
        source_order = await reserve_source_order(self._session, anime_id, provider_name, media_id)
        existing_id = (await self._session.execute(stmt.with_for_update())).scalar_one_or_none()
        if existing_id:
            return existing_id

        new_source = AnimeSource(
            animeId=anime_id,
            providerName=provider_name,
            mediaId=media_id,
            sourceOrder=source_order,
            createdAt=get_now()
        )
        self._session.add(new_source)
        await self._session.flush()  # flush 取新 ID，不提交事务
        return new_source.id

    async def check_source_exists_by_media_id(
        self,
        provider_name: str,
        media_id: str,
        season: Optional[int] = None
    ) -> bool:
        """检查数据源是否存在（通过 provider + mediaId）"""
        stmt = (
            select(AnimeSource.id)
            .where(
                AnimeSource.providerName == provider_name,
                AnimeSource.mediaId == media_id
            )
        )

        # 如果指定了季度，需要 JOIN anime 表验证
        if season is not None:
            stmt = stmt.join(Anime, AnimeSource.animeId == Anime.id).where(Anime.season == season)

        stmt = stmt.limit(1)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def get_anime_id_by_media_id(
        self,
        provider_name: str,
        media_id: str
    ) -> Optional[int]:
        """根据 provider + mediaId 获取 animeId"""
        stmt = select(AnimeSource.animeId).where(
            AnimeSource.providerName == provider_name,
            AnimeSource.mediaId == media_id
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_sources_by_anime(
        self,
        anime_id: int,
        order_by_priority: bool = True
    ) -> List[AnimeSource]:
        """获取指定动漫的所有数据源"""
        stmt = select(AnimeSource).where(AnimeSource.animeId == anime_id)

        if order_by_priority:
            stmt = stmt.order_by(AnimeSource.sourceOrder)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_source_order(self, source_id: int, new_order: int) -> Optional[AnimeSource]:
        """更新数据源优先级"""
        return await self.update(source_id, sourceOrder=new_order)

    async def toggle_favorited(self, source_id: int) -> Optional[AnimeSource]:
        """切换收藏状态"""
        source = await self.get_by_id(source_id)
        if not source:
            return None

        source.isFavorited = not (source.isFavorited or False)
        await self._session.flush()
        return source

    async def toggle_incremental_refresh(self, source_id: int) -> Optional[AnimeSource]:
        """切换增量刷新状态"""
        source = await self.get_by_id(source_id)
        if not source:
            return None

        source.incrementalRefreshEnabled = not (source.incrementalRefreshEnabled or False)
        await self._session.flush()
        return source

    # ==================== 完结状态 ====================
    # why: 以下方法自 crud/source.py 迁入。原实现内部调用 session.commit()，
    #      迁入后统一改为 flush()——事务边界由 DatabaseService.transaction()
    #      掌管，仓储不得自行提交，否则会破坏调用方的原子性。

    async def toggle_finished(self, source_id: int) -> Optional[AnimeSource]:
        """切换源的完结状态，源不存在返回 None。"""
        source = await self.get_by_id(source_id)
        if not source:
            return None

        source.isFinished = not (source.isFinished or False)
        await self._session.flush()
        return source

    async def batch_set_finished(self, source_ids: List[int]) -> int:
        """批量标记源为完结，返回受影响行数。"""
        if not source_ids:
            return 0

        result = await self._session.execute(
            update(AnimeSource)
            .where(AnimeSource.id.in_(source_ids))
            .values(isFinished=True)
        )
        await self._session.flush()
        return int(result.rowcount or 0)

    async def batch_unset_finished(self, source_ids: List[int]) -> int:
        """批量取消源的完结标记，返回受影响行数。"""
        if not source_ids:
            return 0

        result = await self._session.execute(
            update(AnimeSource)
            .where(AnimeSource.id.in_(source_ids))
            .values(isFinished=False)
        )
        await self._session.flush()
        return int(result.rowcount or 0)

    async def bulk_set_finished_by_anime_ids(self, anime_ids: List[int], is_finished: bool) -> int:
        """批量设置指定番剧下所有源的完结状态，返回实际更新的记录数。"""
        if not anime_ids:
            return 0

        # PostgreSQL 的 IN 子句有参数限制，分批处理
        PG_BATCH = 30000
        total_updated = 0
        for i in range(0, len(anime_ids), PG_BATCH):
            batch = anime_ids[i:i + PG_BATCH]
            result = await self._session.execute(
                update(AnimeSource)
                .where(AnimeSource.animeId.in_(batch))
                .values(isFinished=is_finished)
            )
            total_updated += int(result.rowcount or 0)

        await self._session.flush()
        return total_updated

    # ==================== 批量标记 / 追更 ====================
    # why: 开启标记或追更时，同一番剧下只允许一个源生效，故需逐个处理并
    #      清除同番剧其他源的对应标志；关闭方向无互斥约束，可直接批量更新。

    async def batch_toggle_incremental_refresh(
        self, source_ids: List[int], enabled: bool
    ) -> int:
        """批量设置追更状态，返回成功设置的数量。

        开启时遵循「同一番剧仅一个源追更」的互斥规则。
        """
        if not source_ids:
            return 0

        if not enabled:
            result = await self._session.execute(
                update(AnimeSource)
                .where(AnimeSource.id.in_(source_ids))
                .values(incrementalRefreshEnabled=False)
            )
            await self._session.flush()
            return int(result.rowcount or 0)

        count = 0
        for source_id in source_ids:
            source = await self._session.get(AnimeSource, source_id)
            if not source:
                continue

            source.incrementalRefreshEnabled = True
            await self._session.execute(
                update(AnimeSource)
                .where(
                    AnimeSource.animeId == source.animeId,
                    AnimeSource.id != source_id,
                )
                .values(incrementalRefreshEnabled=False)
            )
            count += 1

        await self._session.flush()
        return count

    async def batch_set_favorite(self, source_ids: List[int]) -> int:
        """批量设置精确标记，返回成功设置的数量。

        每设置一个源，同时取消同一番剧下其他源的标记。
        """
        if not source_ids:
            return 0

        count = 0
        for source_id in source_ids:
            source = await self._session.get(AnimeSource, source_id)
            if not source:
                continue

            source.isFavorited = True
            await self._session.execute(
                update(AnimeSource)
                .where(
                    AnimeSource.animeId == source.animeId,
                    AnimeSource.id != source_id,
                )
                .values(isFavorited=False)
            )
            count += 1

        await self._session.flush()
        return count

    async def batch_unset_favorite(self, source_ids: List[int]) -> int:
        """批量取消精确标记，返回受影响行数。"""
        if not source_ids:
            return 0

        result = await self._session.execute(
            update(AnimeSource)
            .where(AnimeSource.id.in_(source_ids))
            .values(isFavorited=False)
        )
        await self._session.flush()
        return int(result.rowcount or 0)

    # ==================== 分集拆分 ====================

    async def split_source_episodes(
        self,
        source_id: int,
        episode_ids: List[int],
        target_anime_id: int,
        reindex_episodes: bool = True,
    ) -> int:
        """将指定分集从源数据源移动到目标媒体条目。

        目标媒体若无同 provider + mediaId 的源，会自动补建一个。
        分集主键含 animeId/sourceOrder/index 编码，故移动时必须删旧建新
        而不能直接改 sourceId。

        Args:
            source_id: 源数据源 ID
            episode_ids: 待移动的分集 ID 列表
            target_anime_id: 目标媒体 ID
            reindex_episodes: 是否在目标源内重新连续编号

        Returns:
            实际移动的分集数量

        Raises:
            ValueError: 源数据源或目标媒体不存在
        """
        if not episode_ids:
            return 0

        source = await self._session.get(AnimeSource, source_id)
        if not source:
            raise ValueError(f"源数据源 ID {source_id} 不存在")

        target_anime = await self._session.get(Anime, target_anime_id)
        if not target_anime:
            raise ValueError(f"目标媒体 ID {target_anime_id} 不存在")

        # 目标媒体下复用同 provider + mediaId 的源，缺失则补建
        existing_result = await self._session.execute(
            select(AnimeSource).where(
                AnimeSource.animeId == target_anime_id,
                AnimeSource.providerName == source.providerName,
                AnimeSource.mediaId == source.mediaId,
            )
        )
        target_source = existing_result.scalar_one_or_none()

        if not target_source:
            max_order_result = await self._session.execute(
                select(func.max(AnimeSource.sourceOrder)).where(
                    AnimeSource.animeId == target_anime_id
                )
            )
            current_max_order = max_order_result.scalar_one_or_none() or 0

            target_source = AnimeSource(
                animeId=target_anime_id,
                providerName=source.providerName,
                mediaId=source.mediaId,
                sourceOrder=current_max_order + 1,
                createdAt=get_now(),
            )
            self._session.add(target_source)
            await self._session.flush()
            logger.info(f"为目标媒体 {target_anime_id} 创建了新的数据源 {target_source.id}")

        episodes_result = await self._session.execute(
            select(Episode)
            .where(
                Episode.id.in_(episode_ids),
                Episode.sourceId == source_id,
            )
            .order_by(Episode.episodeIndex)
        )
        episodes = episodes_result.scalars().all()

        if not episodes:
            return 0

        new_index = 0
        if reindex_episodes:
            max_index_result = await self._session.execute(
                select(func.max(Episode.episodeIndex)).where(
                    Episode.sourceId == target_source.id
                )
            )
            new_index = (max_index_result.scalar_one_or_none() or 0) + 1

        moved_count = 0
        for episode in episodes:
            target_index = new_index if reindex_episodes else episode.episodeIndex
            new_episode_id = int(
                f"25{target_anime_id:06d}{target_source.sourceOrder:02d}{target_index:04d}"
            )

            new_episode = Episode(
                id=new_episode_id,
                sourceId=target_source.id,
                title=episode.title,
                episodeIndex=target_index,
                providerEpisodeId=episode.providerEpisodeId,
                sourceUrl=episode.sourceUrl,
                danmakuFilePath=episode.danmakuFilePath,
                fetchedAt=episode.fetchedAt,
                commentCount=episode.commentCount,
            )

            # 先删后加：新旧 ID 可能冲突，须让删除先落到数据库
            await self._session.delete(episode)
            await self._session.flush()
            self._session.add(new_episode)

            if reindex_episodes:
                new_index += 1
            moved_count += 1

        await self._session.flush()
        logger.info(f"成功将 {moved_count} 个分集从源 {source_id} 移动到目标媒体 {target_anime_id}")

        return moved_count

    async def get_sources_with_incremental_refresh_enabled(self) -> List[int]:
        """获取所有启用了增量刷新的源 ID 列表"""
        stmt = select(AnimeSource.id).where(AnimeSource.incrementalRefreshEnabled == True)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_air_schedule(
        self,
        anime_id: int,
        air_weekday: Optional[int],
        air_time: Optional[str],
    ) -> None:
        """更新番剧的播出日程信息"""
        stmt = select(AnimeMetadata).where(AnimeMetadata.animeId == anime_id)
        meta = (await self._session.execute(stmt)).scalar_one_or_none()
        if meta:
            meta.airWeekday = air_weekday
            meta.airTime = air_time
            await self._session.flush()

    #: update_metadata_ids 允许写入的字段白名单
    _METADATA_ID_FIELDS = frozenset({
        "bangumiId", "traktId", "tmdbId", "imdbId",
        "tvdbId", "doubanId", "airWeekday", "airTime",
    })

    async def update_metadata_ids(self, anime_id: int, **kwargs) -> None:
        """更新番剧的元数据 ID（bangumiId/traktId/tmdbId 等）与日程信息。

        why：与 update_air_schedule 的差异在于，本方法在 metadata 记录缺失时
        会主动创建（日历自动绑定场景下作品可能尚无 metadata 行），
        而 update_air_schedule 仅在记录已存在时更新。

        用法::

            await repo.update_metadata_ids(anime_id, bangumiId="12345", airWeekday=6)

        Args:
            anime_id: 作品 ID
            **kwargs: 待更新字段，仅白名单内且非 None 的值会被写入
        """
        stmt = select(AnimeMetadata).where(AnimeMetadata.animeId == anime_id)
        meta = (await self._session.execute(stmt)).scalar_one_or_none()
        if not meta:
            # 没有 metadata 记录时创建一条，避免自动绑定阶段丢失写入
            meta = AnimeMetadata(animeId=anime_id)
            self._session.add(meta)

        for key, value in kwargs.items():
            if key in self._METADATA_ID_FIELDS and value is not None:
                setattr(meta, key, value)
        await self._session.flush()

    async def get_by_provider_and_media(
        self,
        anime_id: int,
        provider_name: str,
        media_id: str
    ) -> Optional[AnimeSource]:
        """
        只读查询：根据 anime_id + provider + media_id 查找已存在的源对象。

        与 get_by_provider_and_media_id 的区别：
        - 本方法是**纯只读**，查不到返回 None，绝不创建新源；
        - 返回完整 AnimeSource 对象（调用方需要 .sourceOrder 等字段）。

        why：后备搜索复用已存在番剧时，需要读取真实 sourceOrder 拼 episodeId，
             此时源可能尚未入库（入库在后续导入任务完成），不能触发创建。
        """
        stmt = select(AnimeSource).where(
            AnimeSource.animeId == anime_id,
            AnimeSource.providerName == provider_name,
            AnimeSource.mediaId == media_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_max_source_order(self, anime_id: int) -> int:
        """
        查询指定作品当前最大的 sourceOrder，无源时返回 0。

        why：预测下一个可用 sourceOrder（max+1），用于源尚未入库时
             提前拼出与入库后一致的 episodeId。
        """
        stmt = select(func.max(AnimeSource.sourceOrder)).where(
            AnimeSource.animeId == anime_id
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() or 0

    async def get_favorited_by_provider_media_pairs(
        self,
        provider_media_pairs: List[tuple]
    ) -> List[AnimeSource]:
        """
        批量查询：给定 (provider, mediaId) 列表，返回其中被精确标记
        （isFavorited=True）的源对象列表。

        why：/match 自动选源时需批量判断哪些候选源已被用户精确标记，
             逐个查询会产生 N 次往返，故用 tuple_ IN 一次查出。

        Args:
            provider_media_pairs: [(provider_name, media_id), ...]

        Returns:
            被精确标记的 AnimeSource 对象列表（调用方读取 .providerName / .mediaId）
        """
        if not provider_media_pairs:
            return []

        stmt = select(AnimeSource).where(
            AnimeSource.isFavorited == True,  # noqa: E712
            tuple_(AnimeSource.providerName, AnimeSource.mediaId).in_(
                provider_media_pairs
            ),
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_provider_and_media_id(
        self,
        anime_id: int,
        provider_name: str,
        media_id: str
    ) -> Optional[int]:
        """
        根据 provider_name 和 media_id 获取源 ID。
        如果不存在则创建新源并返回其 ID。
        """
        # 复用统一入库入口，避免旧兼容方法绕过源序号租约。
        return await self.link_source_to_anime(anime_id, provider_name, media_id)

    async def toggle_source_favorite_status(self, source_id: int) -> Optional[bool]:
        """
        切换数据源的收藏状态，确保同一作品只有一个精确标记的源
        返回切换后的状态；数据源不存在时返回 None
        """
        # 获取当前源
        source = await self._session.get(AnimeSource, source_id)
        if not source:
            return None

        anime_id = source.animeId
        new_favorite_status = not source.isFavorited

        if new_favorite_status:
            # 如果要设置为收藏，先取消同一作品下所有其他源的收藏状态
            stmt = (
                update(AnimeSource)
                .where(AnimeSource.animeId == anime_id, AnimeSource.id != source_id)
                .values(isFavorited=False)
            )
            await self._session.execute(stmt)

        # 切换当前源的状态
        source.isFavorited = new_favorite_status
        await self._session.flush()
        return new_favorite_status

    async def get_episodes_for_source(self, source_id: int) -> List[Episode]:
        """获取指定数据源的所有分集信息（按集数排序）"""
        stmt = (
            select(Episode)
            .where(Episode.sourceId == source_id)
            .order_by(Episode.episodeIndex)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
