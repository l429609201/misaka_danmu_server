"""
EpisodeRepository - 分集数据访问层
"""

import logging
from typing import Optional, List, Dict, Any
from sqlalchemy import select, func, distinct, and_, or_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..orm_models import Episode, AnimeSource, Anime
from .base import BaseRepository
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


class EpisodeRepository(BaseRepository[Episode]):
    """分集 Repository"""

    async def renumber(self, source_id: int, mapping: Dict[int, int]) -> Dict[int, int]:
        """同事务两阶段更新主键与集数，保留所有字段且不关闭外键检查。"""
        source = (await self._session.execute(
            select(AnimeSource).where(AnimeSource.id == source_id).with_for_update()
        )).scalar_one_or_none()
        if source is None or source.sourceOrder is None:
            raise ValueError("数据源不存在或缺少持久化顺序")
        episodes = list((await self._session.execute(
            select(Episode).where(Episode.sourceId == source_id).with_for_update()
        )).scalars().all())
        by_id = {episode.id: episode for episode in episodes}
        if not set(mapping).issubset(by_id):
            raise ValueError("部分分集已不存在")
        indices = list(mapping.values())
        if len(indices) != len(set(indices)) or any(i < 1 or i > 9999 for i in indices):
            raise ValueError("目标集数必须唯一且介于 1 到 9999")
        if set(indices) & {ep.episodeIndex for ep in episodes if ep.id not in mapping}:
            raise ValueError("目标集数与未选中的分集冲突")
        targets = {old_id: int(f"25{source.animeId:06d}{source.sourceOrder:02d}{index:04d}")
                   for old_id, index in mapping.items()}
        occupied = set((await self._session.execute(
            select(Episode.id).where(Episode.id.in_(list(targets.values())))
        )).scalars().all())
        if occupied - set(mapping):
            raise ValueError("目标分集 ID 已被其他记录占用")
        # 负数仅作事务内暂存，避免互换编号触发主键及源内集数唯一约束。
        minimum = (await self._session.execute(select(func.min(Episode.id)))).scalar() or 0
        temp_id = min(0, minimum) - 1
        temp_index = min([0] + [ep.episodeIndex for ep in episodes]) - 1
        for number, old_id in enumerate(mapping):
            episode = by_id[old_id]
            episode.id = temp_id - number
            episode.episodeIndex = temp_index - number
        await self._session.flush()
        for old_id, index in mapping.items():
            episode = by_id[old_id]
            episode.id = targets[old_id]
            episode.episodeIndex = index
        await self._session.flush()
        return targets


    async def get_by_id(self, episode_id: int) -> Optional[Episode]:
        """实现基础查询接口，避免抽象仓储无法实例化。"""
        result = await self._session.execute(select(Episode).where(Episode.id == episode_id))
        return result.scalar_one_or_none()

    async def exists(self, episode_id: int) -> bool:
        """检查指定ID的分集是否存在"""
        stmt = select(Episode.id).where(Episode.id == episode_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def get_for_import(
        self, source_id: int, provider_episode_id: str, episode_index: int,
    ) -> Optional[Episode]:
        """按源站分集 ID 优先、集数兜底查询导入目标，不创建或提交记录。"""
        stmt = select(Episode).where(
            Episode.sourceId == source_id,
            Episode.providerEpisodeId == provider_episode_id,
        ).limit(1)
        episode = (await self._session.execute(stmt)).scalar_one_or_none()
        if episode is not None:
            return episode
        stmt = select(Episode).where(
            Episode.sourceId == source_id,
            Episode.episodeIndex == episode_index,
        ).limit(1)
        return (await self._session.execute(stmt)).scalar_one_or_none()


    async def check_episode_exists_with_danmaku(
        self,
        provider: str,
        media_id: str,
        episode_index: int
    ) -> bool:
        """
        检查特定数据源的特定分集是否已存在弹幕

        Args:
            provider: 数据源提供方名称
            media_id: 数据源媒体ID
            episode_index: 分集序号

        Returns:
            bool: 如果该分集已存在且有弹幕则返回 True
        """
        stmt = select(Episode.id).join(
            AnimeSource, Episode.sourceId == AnimeSource.id
        ).where(
            AnimeSource.providerName == provider,
            AnimeSource.mediaId == media_id,
            Episode.episodeIndex == episode_index,
            Episode.danmakuFilePath.isnot(None),
            Episode.commentCount > 0
        ).limit(1)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def get_by_id_with_relations(self, episode_id: int) -> Optional[Episode]:
        """根据 ID 获取分集（包含源和动漫信息）"""
        stmt = select(Episode).where(Episode.id == episode_id).options(
            # 路径模板会读取 TMDB 元数据，必须一起预加载以避免异步隐式 IO。
            selectinload(Episode.source).selectinload(AnimeSource.anime).selectinload(Anime.metadataRecord)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_ids(self, episode_ids: List[int]) -> List[Episode]:
        """批量根据 ID 获取分集（包含源和动漫信息）"""
        if not episode_ids:
            return []
        stmt = select(Episode).where(Episode.id.in_(episode_ids)).options(
            selectinload(Episode.source).selectinload(AnimeSource.anime)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_all(self, **filters) -> List[Episode]:
        """获取所有分集"""
        stmt = select(Episode)

        if 'source_id' in filters:
            stmt = stmt.where(Episode.sourceId == filters['source_id'])
        if 'has_danmaku' in filters:
            if filters['has_danmaku']:
                stmt = stmt.where(Episode.danmakuFilePath.isnot(None))
            else:
                stmt = stmt.where(Episode.danmakuFilePath.is_(None))

        stmt = stmt.order_by(Episode.episodeIndex)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(
        self,
        source_id: int,
        episode_index: int,
        title: Optional[str] = None,
        source_url: Optional[str] = None
    ) -> Episode:
        """创建分集"""
        episode = Episode(
            sourceId=source_id,
            episodeIndex=episode_index,
            title=title or f"第 {episode_index} 集",
            sourceUrl=source_url
        )
        self._session.add(episode)
        await self._session.flush()
        return episode

    async def update(self, episode_id: int, **data) -> Optional[Episode]:
        """更新分集"""
        episode = await self.get_by_id(episode_id)
        if not episode:
            return None

        for key, value in data.items():
            if hasattr(episode, key):
                setattr(episode, key, value)

        await self._session.flush()
        return episode

    async def delete(self, episode_id: int) -> bool:
        """删除分集"""
        episode = await self.get_by_id(episode_id)
        if not episode:
            return False

        await self._session.delete(episode)
        await self._session.flush()
        return True

    async def get_by_source_and_index(
        self,
        source_id: int,
        episode_index: int
    ) -> Optional[Episode]:
        """根据源 ID 和集数获取分集"""
        stmt = select(Episode).where(
            Episode.sourceId == source_id,
            Episode.episodeIndex == episode_index
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_episodes_by_source(
        self,
        source_id: int,
        has_danmaku: Optional[bool] = None
    ) -> List[Episode]:
        """获取指定源的所有分集"""
        stmt = select(Episode).where(Episode.sourceId == source_id)

        if has_danmaku is True:
            stmt = stmt.where(Episode.danmakuFilePath.isnot(None))
        elif has_danmaku is False:
            stmt = stmt.where(Episode.danmakuFilePath.is_(None))

        stmt = stmt.order_by(Episode.episodeIndex)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def check_episode_exists(
        self,
        source_id: int,
        episode_index: int
    ) -> bool:
        """检查分集是否存在"""
        stmt = select(Episode.id).where(
            Episode.sourceId == source_id,
            Episode.episodeIndex == episode_index
        ).limit(1)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def find_by_anime_id_and_index(
        self,
        anime_id: int,
        episode_index: int
    ) -> Optional[Episode]:
        """
        根据作品ID和集数索引查找分集

        替代 crud.find_episode_by_index

        Args:
            anime_id: 作品ID
            episode_index: 集数索引

        Returns:
            Episode 对象或 None
        """
        stmt = (
            select(Episode)
            .join(AnimeSource, Episode.sourceId == AnimeSource.id)
            .where(
                AnimeSource.animeId == anime_id,
                Episode.episodeIndex == episode_index
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_episode_indices_by_anime_title(
        self,
        title: str,
        season: Optional[int] = None
    ) -> List[int]:
        """根据作品标题获取已存在的所有分集序号列表"""
        stmt = (
            select(distinct(Episode.episodeIndex))
            .join(AnimeSource, Episode.sourceId == AnimeSource.id)
            .join(Anime, AnimeSource.animeId == Anime.id)
            .where(Anime.title == title)
        )

        if season is not None:
            stmt = stmt.where(Anime.season == season)

        stmt = stmt.order_by(Episode.episodeIndex)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # 弹幕元信息统一使用下方的路径、数量接口，避免同名定义覆盖造成参数歧义。

    # ==================== 确定性 ID 建集 ====================
    # why: 以下方法自 crud/episode.py 迁入，逻辑保持一致，
    #      仅去掉显式 session 参数（改用仓储实例持有的会话）。

    async def _assign_source_order_if_missing(self, anime_id: int, source_id: int) -> int:
        """为缺少 sourceOrder 的旧记录分配一个新的持久序号。

        使用嵌套事务保证"取最大值 + 写入"的原子性，避免并发下序号重复。

        Args:
            anime_id: 番剧 ID
            source_id: 数据源 ID

        Returns:
            新分配的 sourceOrder
        """
        async with self._session.begin_nested():
            max_order_stmt = select(func.max(AnimeSource.sourceOrder)).where(
                AnimeSource.animeId == anime_id
            )
            max_order_res = await self._session.execute(max_order_stmt)
            current_max_order = max_order_res.scalar_one_or_none() or 0
            new_order = current_max_order + 1

            await self._session.execute(
                update(AnimeSource)
                .where(AnimeSource.id == source_id)
                .values(sourceOrder=new_order)
            )
            return new_order

    async def create_episode_if_not_exists(
        self,
        anime_id: int,
        source_id: int,
        episode_index: int,
        title: str,
        url: Optional[str],
        provider_episode_id: str,
        update_existing_title: bool = False,
    ) -> int:
        """分集不存在则创建，返回其确定性 ID。

        ID 由 `25{animeId:06d}{sourceOrder:02d}{episodeIndex:04d}` 拼成，
        因此同一坐标重复调用必然命中同一条记录。

        Args:
            anime_id: 番剧 ID
            source_id: 数据源 ID
            episode_index: 分集序号
            title: 分集标题
            url: 分集原始 URL
            provider_episode_id: 源站分集 ID
            update_existing_title: 是否覆盖已存在分集的标题；
                默认 False 以保护用户自定义标题

        Returns:
            分集 ID
        """
        # 1. 取该源的持久化 sourceOrder
        source_order_stmt = select(AnimeSource.sourceOrder).where(AnimeSource.id == source_id)
        source_order_res = await self._session.execute(source_order_stmt)
        source_order = source_order_res.scalar_one_or_none()

        if source_order is None:
            # why: 旧版本升级后可能缺失该字段，需就地补分配以保证 ID 可计算
            logger.warning(
                f"源 ID {source_id} 缺少 sourceOrder，将为其分配一个新的。"
                f"这通常发生在从旧版本升级后。"
            )
            source_order = await self._assign_source_order_if_missing(anime_id, source_id)

        new_episode_id = int(f"25{anime_id:06d}{source_order:02d}{episode_index:04d}")

        # 2. 直接按 ID 判存
        existing_stmt = select(Episode).where(Episode.id == new_episode_id)
        existing_result = await self._session.execute(existing_stmt)
        existing_episode = existing_result.scalar_one_or_none()

        if existing_episode:
            needs_update = False
            update_details = []

            # 标题仅在显式允许时覆盖
            if update_existing_title and title and existing_episode.title != title:
                old_title = existing_episode.title
                existing_episode.title = title
                needs_update = True
                update_details.append(f"标题: '{old_title}' → '{title}'")

            # URL 始终跟随源站更新（不影响用户体验）
            if url and existing_episode.sourceUrl != url:
                existing_episode.sourceUrl = url
                needs_update = True
                update_details.append("URL已更新")

            if needs_update:
                await self._session.flush()
                logger.info(
                    f"更新已存在的episode: id={new_episode_id}, "
                    f"更新内容: {', '.join(update_details)}"
                )
            return existing_episode.id

        # 3. ID 不存在则新建
        new_episode = Episode(
            id=new_episode_id,
            sourceId=source_id,
            episodeIndex=episode_index,
            providerEpisodeId=provider_episode_id,
            title=title,
            sourceUrl=url,
            fetchedAt=get_now(),
        )
        self._session.add(new_episode)
        await self._session.flush()
        return new_episode_id

    async def update_fetch_time(self, episode_id: int) -> None:
        """更新分集的抓取时间"""
        await self._session.execute(
            update(Episode)
            .where(Episode.id == episode_id)
            .values(fetchedAt=get_now())
        )
        await self._session.flush()

    async def update_media_server_id(self, episode_id: int, media_server_episode_id: str) -> None:
        """更新分集的媒体服务器 ID"""
        await self._session.execute(
            update(Episode)
            .where(Episode.id == episode_id)
            .values(mediaServerEpisodeId=media_server_episode_id)
        )
        await self._session.flush()

    async def update_danmaku_info(self, episode_id: int, file_path: str, count: int) -> bool:
        """
        更新分集的弹幕文件路径和弹幕数量

        替代 crud.update_episode_danmaku_info

        Args:
            episode_id: 分集ID
            file_path: 弹幕文件路径
            count: 弹幕数量

        Returns:
            是否更新成功
        """
        result = await self._session.execute(
            update(Episode)
            .where(Episode.id == episode_id)
            .values(
                danmakuFilePath=file_path,
                commentCount=count,
                fetchedAt=get_now()
            )
        )
        await self._session.flush()
        return result.rowcount > 0

    async def update_episode_info(self, episode_id: int, values: Dict[str, Any]) -> bool:
        """只更新分集字段，文件迁移与事务补偿由编排层负责。"""
        episode = await self.get_by_id(episode_id)
        if episode is None:
            return False
        # 限制可写字段，防止编辑接口意外修改主键及数据源归属。
        for key in ("title", "episodeIndex", "sourceUrl", "danmakuFilePath"):
            if key in values:
                setattr(episode, key, values[key])
        await self._session.flush()
        return True

    async def get_episode_for_refresh(self, episode_id: int) -> Optional[Dict[str, Any]]:
        """获取用于刷新的分集信息"""
        stmt = (
            select(
                Episode.id,
                Episode.title,
                AnimeSource.providerName,
                AnimeSource.mediaId,
                Episode.providerEpisodeId
            )
            .join(AnimeSource, Episode.sourceId == AnimeSource.id)
            .where(Episode.id == episode_id)
        )
        result = await self._session.execute(stmt)
        row = result.mappings().first()
        return dict(row) if row else None
