"""
AnimeRepository - 动漫数据访问层
"""

import logging
from typing import Optional, List, Dict, Any
from sqlalchemy import select, func, or_, and_, delete, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..orm_models import Anime, AnimeSource, AnimeAlias, AnimeMetadata
from .base import BaseRepository
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


class AnimeRepository(BaseRepository[Anime]):
    """动漫 Repository"""

    async def sync_postgres_sequence(self) -> None:
        """启动时同步 PostgreSQL 作品序列，由服务事务管理提交。"""
        if self._session.get_bind().dialect.name == "postgresql":
            # 空表从 1 开始，避免 setval(0) 低于序列最小值。
            max_id = select(func.coalesce(func.max(Anime.id), 0)).scalar_subquery()
            await self._session.execute(select(func.setval(
                "anime_id_seq", func.greatest(max_id, 1), max_id > 0,
            )))

    async def create_preassigned(
        self, anime_id: int, title: str, media_type: str, season: Optional[int],
        image_url: Optional[str], local_image_path: Optional[str], year: Optional[int],
    ) -> Anime:
        """创建预分配主键作品并同步 PostgreSQL 序列，仅 flush 不提交。"""
        anime = await self.get_by_id(anime_id)
        if anime is not None:
            return anime
        anime = await self.create(
            title=title, type=media_type, season=season,
            id=anime_id, year=year, imageUrl=image_url,
        )
        anime.localImagePath = local_image_path
        anime.updatedAt = get_now()
        await self._session.flush()
        # 显式主键不会推进 PostgreSQL 序列，沿用原导入流程的同步规则。
        if self._session.get_bind().dialect.name == "postgresql":
            max_id = select(func.max(Anime.id)).scalar_subquery()
            await self._session.execute(select(func.setval("anime_id_seq", max_id)))
        return anime


    async def get_by_id(self, anime_id: int) -> Optional[Anime]:
        """根据 ID 获取动漫（包含源、别名、元数据）"""
        stmt = select(Anime).where(Anime.id == anime_id).options(
            selectinload(Anime.sources),
            selectinload(Anime.aliases),
            selectinload(Anime.metadataRecord)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all(
        self,
        type: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> List[Anime]:
        """获取所有动漫"""
        stmt = select(Anime)

        if type:
            stmt = stmt.where(Anime.type == type)

        if offset:
            stmt = stmt.offset(offset)
        if limit:
            stmt = stmt.limit(limit)

        stmt = stmt.order_by(Anime.createdAt.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(
        self,
        title: str,
        type: str,
        season: Optional[int] = None,
        episode_count: Optional[int] = None,
        *,
        id: Optional[int] = None,
        year: Optional[int] = None,
        imageUrl: Optional[str] = None,
    ) -> Anime:
        """
        创建动漫

        Args:
            title: 标题
            type: 类型 (tv_series / movie)
            season: 季度
            episode_count: 集数
            id: 指定主键 ID（后备搜索等场景需要预分配 ID）
            year: 年份
            imageUrl: 海报 URL
        """
        anime = Anime(
            title=title,
            type=type,
            season=season,
            episodeCount=episode_count,
            createdAt=get_now()
        )
        if id is not None:
            anime.id = id
        if year is not None:
            anime.year = year
        if imageUrl is not None:
            anime.imageUrl = imageUrl
        self._session.add(anime)
        await self._session.flush()
        return anime

    async def create_with_metadata(
        self,
        title: str,
        media_type: str,
        season: Optional[int],
        year: Optional[int],
        image_url: Optional[str],
    ) -> Anime:
        """创建作品并补齐元数据与别名空记录，事务由服务层统一管理。"""
        anime = await self.create(
            title=title,
            type=media_type,
            season=season,
            year=year,
            imageUrl=image_url,
        )
        self._session.add(AnimeMetadata(animeId=anime.id))
        self._session.add(AnimeAlias(animeId=anime.id))
        await self._session.flush()
        return anime


    async def update(self, anime_id: int, **data) -> Optional[Anime]:
        """更新动漫"""
        anime = await self.get_by_id(anime_id)
        if not anime:
            return None

        for key, value in data.items():
            if hasattr(anime, key):
                setattr(anime, key, value)

        await self._session.flush()
        return anime

    async def delete(self, anime_id: int) -> bool:
        """删除动漫"""
        anime = await self.get_by_id(anime_id)
        if not anime:
            return False

        await self._session.delete(anime)
        await self._session.flush()
        return True

    async def search_by_keyword(
        self,
        keyword: str,
        limit: int = 100,
        offset: int = 0
    ) -> List[Anime]:
        """根据关键词搜索动漫（标题和别名）"""
        # 标准化关键词（去空格和全角冒号）
        normalized_keyword = keyword.replace(' ', '').replace('：', ':')
        normalized_like_keyword = f"%{normalized_keyword}%"

        # 在别名表中搜索
        alias_exists = (
            select(1)
            .where(AnimeAlias.animeId == Anime.id)
            .where(
                or_(*[
                    func.replace(func.replace(col, '：', ':'), ' ', '').like(normalized_like_keyword)
                    for col in [
                        AnimeAlias.nameEn,
                        AnimeAlias.nameJp,
                        AnimeAlias.nameRomaji,
                        AnimeAlias.aliasCn1,
                        AnimeAlias.aliasCn2,
                        AnimeAlias.aliasCn3
                    ]
                ])
            )
        ).exists()

        keyword_condition = or_(
            func.replace(func.replace(Anime.title, '：', ':'), ' ', '').like(normalized_like_keyword),
            alias_exists
        )

        stmt = (
            select(Anime)
            .where(keyword_condition)
            .order_by(Anime.createdAt.desc())
            .offset(offset)
            .limit(limit)
        )

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_title_and_season(
        self,
        title: str,
        season: Optional[int] = None
    ) -> Optional[Anime]:
        """根据标题和季度查找动漫"""
        stmt = select(Anime).where(Anime.title == title)

        if season is not None:
            stmt = stmt.where(Anime.season == season)

        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def count_all(self, type: Optional[str] = None) -> int:
        """统计动漫总数"""
        stmt = select(func.count()).select_from(Anime)

        if type:
            stmt = stmt.where(Anime.type == type)

        result = await self._session.execute(stmt)
        return result.scalar_one()

    # ==================== 元数据 ID 查询 ====================

    async def get_id_by_bangumi_id(self, bangumi_id: str) -> Optional[int]:
        """通过 bangumi_id 查找 anime_id"""
        stmt = select(AnimeMetadata.animeId).where(AnimeMetadata.bangumiId == bangumi_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_id_by_tmdb_id(self, tmdb_id: str) -> Optional[int]:
        """通过 tmdb_id 查找 anime_id"""
        stmt = select(AnimeMetadata.animeId).where(AnimeMetadata.tmdbId == tmdb_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_id_by_tvdb_id(self, tvdb_id: str) -> Optional[int]:
        """通过 tvdb_id 查找 anime_id"""
        stmt = select(AnimeMetadata.animeId).where(AnimeMetadata.tvdbId == tvdb_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_id_by_imdb_id(self, imdb_id: str) -> Optional[int]:
        """通过 imdb_id 查找 anime_id"""
        stmt = select(AnimeMetadata.animeId).where(AnimeMetadata.imdbId == imdb_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_id_by_douban_id(self, douban_id: str) -> Optional[int]:
        """通过 douban_id 查找 anime_id"""
        stmt = select(AnimeMetadata.animeId).where(AnimeMetadata.doubanId == douban_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_ids_with_custom_source(self, anime_ids: List[int]) -> List[int]:
        """查询给定的 anime IDs 中哪些有 custom 源关联"""
        if not anime_ids:
            return []
        stmt = (
            select(AnimeSource.animeId)
            .where(AnimeSource.animeId.in_(anime_ids))
            .where(AnimeSource.providerName == 'custom')
            .distinct()
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ==================== 元数据更新 ====================

    async def update_tmdb_group_id(self, anime_id: int, group_id: str) -> None:
        """更新 TMDB 剧集组 ID"""
        await self._session.execute(
            update(AnimeMetadata)
            .where(AnimeMetadata.animeId == anime_id)
            .values(tmdbEpisodeGroupId=group_id)
        )
        await self._session.flush()

    async def update_aliases_if_empty(
        self,
        anime_id: int,
        aliases: Dict[str, Any],
        force_update: bool = False
    ) -> List[str]:
        """
        更新作品别名，如果字段为空则填充。

        Args:
            anime_id: 作品 ID
            aliases: 别名数据字典，支持的键：
                    - name_en, name_jp, name_romaji
                    - aliases_cn (列表，最多取前3个)
            force_update: 是否强制更新（用于 AI 修正），默认 False

        Returns:
            更新的字段列表（调试用）
        """
        # 查询或创建别名记录
        stmt = select(AnimeAlias).where(AnimeAlias.animeId == anime_id)
        result = await self._session.execute(stmt)
        alias_record = result.scalar_one_or_none()

        if not alias_record:
            alias_record = AnimeAlias(animeId=anime_id, aliasLocked=False)
            self._session.add(alias_record)
            logger.info(f"为作品 ID {anime_id} 创建新的别名记录")

        # 检查锁定状态
        if alias_record.aliasLocked and not force_update:
            logger.info(f"作品 ID {anime_id} 的别名已锁定，跳过更新")
            return []

        # 更新字段
        updated_fields = []

        if force_update:
            # 强制更新所有提供的字段
            if aliases.get('name_en'):
                alias_record.nameEn = aliases['name_en']
                updated_fields.append(f"nameEn='{aliases['name_en']}'")
            if aliases.get('name_jp'):
                alias_record.nameJp = aliases['name_jp']
                updated_fields.append(f"nameJp='{aliases['name_jp']}'")
            if aliases.get('name_romaji'):
                alias_record.nameRomaji = aliases['name_romaji']
                updated_fields.append(f"nameRomaji='{aliases['name_romaji']}'")

            cn_aliases = aliases.get('aliases_cn', [])
            if len(cn_aliases) > 0:
                alias_record.aliasCn1 = cn_aliases[0]
                updated_fields.append(f"aliasCn1='{cn_aliases[0]}'")
            if len(cn_aliases) > 1:
                alias_record.aliasCn2 = cn_aliases[1]
                updated_fields.append(f"aliasCn2='{cn_aliases[1]}'")
            if len(cn_aliases) > 2:
                alias_record.aliasCn3 = cn_aliases[2]
                updated_fields.append(f"aliasCn3='{cn_aliases[2]}'")

            if updated_fields:
                logger.info(f"为作品 ID {anime_id} 强制更新了别名字段 (AI修正): {', '.join(updated_fields)}")
        else:
            # 只在字段为空时更新
            if not alias_record.nameEn and aliases.get('name_en'):
                alias_record.nameEn = aliases['name_en']
                updated_fields.append(f"nameEn='{aliases['name_en']}'")
            if not alias_record.nameJp and aliases.get('name_jp'):
                alias_record.nameJp = aliases['name_jp']
                updated_fields.append(f"nameJp='{aliases['name_jp']}'")
            if not alias_record.nameRomaji and aliases.get('name_romaji'):
                alias_record.nameRomaji = aliases['name_romaji']
                updated_fields.append(f"nameRomaji='{aliases['name_romaji']}'")

            cn_aliases = aliases.get('aliases_cn', [])
            if not alias_record.aliasCn1 and len(cn_aliases) > 0:
                alias_record.aliasCn1 = cn_aliases[0]
                updated_fields.append(f"aliasCn1='{cn_aliases[0]}'")
            if not alias_record.aliasCn2 and len(cn_aliases) > 1:
                alias_record.aliasCn2 = cn_aliases[1]
                updated_fields.append(f"aliasCn2='{cn_aliases[1]}'")
            if not alias_record.aliasCn3 and len(cn_aliases) > 2:
                alias_record.aliasCn3 = cn_aliases[2]
                updated_fields.append(f"aliasCn3='{cn_aliases[2]}'")

            if updated_fields:
                logger.info(f"为作品 ID {anime_id} 更新了别名字段: {', '.join(updated_fields)}")

        await self._session.flush()
        return updated_fields

    async def update_metadata_if_empty(
        self,
        anime_id: int,
        *,
        tmdb_id: Optional[str] = None,
        imdb_id: Optional[str] = None,
        tvdb_id: Optional[str] = None,
        douban_id: Optional[str] = None,
        bangumi_id: Optional[str] = None,
        tmdb_episode_group_id: Optional[str] = None,
        media_server_type: Optional[str] = None,
        media_server_series_id: Optional[str] = None,
        media_server_season_id: Optional[str] = None,
    ) -> None:
        """
        如果 anime_metadata 记录中的字段为空，则使用提供的值进行更新。
        如果记录不存在，则创建一个新记录。
        使用关键字参数以提高可读性和安全性。
        """
        stmt = select(AnimeMetadata).where(AnimeMetadata.animeId == anime_id)
        result = await self._session.execute(stmt)
        metadata_record = result.scalar_one_or_none()

        if not metadata_record:
            # 创建前先确认 anime 记录存在，避免外键约束失败
            anime_exists = await self._session.get(Anime, anime_id)
            if not anime_exists:
                logger.warning(f"update_metadata_if_empty: anime_id={anime_id} 不存在，跳过创建 metadata")
                return
            metadata_record = AnimeMetadata(animeId=anime_id)
            self._session.add(metadata_record)
            await self._session.flush()

        if tmdb_id and not metadata_record.tmdbId:
            metadata_record.tmdbId = tmdb_id
        if imdb_id and not metadata_record.imdbId:
            metadata_record.imdbId = imdb_id
        if tvdb_id and not metadata_record.tvdbId:
            metadata_record.tvdbId = tvdb_id
        if douban_id and not metadata_record.doubanId:
            metadata_record.doubanId = douban_id
        if bangumi_id and not metadata_record.bangumiId:
            metadata_record.bangumiId = bangumi_id
        if tmdb_episode_group_id and not metadata_record.tmdbEpisodeGroupId:
            metadata_record.tmdbEpisodeGroupId = tmdb_episode_group_id
        if media_server_type:
            metadata_record.mediaServerType = media_server_type
        if media_server_series_id:
            metadata_record.mediaServerSeriesId = media_server_series_id
        if media_server_season_id:
            metadata_record.mediaServerSeasonId = media_server_season_id

        await self._session.flush()

    async def create_anime(self, anime_data: Any) -> Anime:
        """创建作品、元数据和别名；不创建数据源，提交由调用方事务管理。"""
        # QueryRepository 的方法不在本仓储自身上，直接进行精确查重。
        stmt = select(Anime.id).where(
            Anime.title == anime_data.title,
            Anime.season == anime_data.season,
        )
        if anime_data.year is not None:
            stmt = stmt.where(Anime.year == anime_data.year)
        existing_id = await self._session.scalar(stmt.limit(1))
        if existing_id is not None:
            raise ValueError(f"作品 '{anime_data.title}' (第 {anime_data.season} 季) 已存在。")

        created_time = get_now().replace(tzinfo=None)
        logger.info("创建作品，设置创建时间：%s", created_time)
        new_anime = Anime(
            title=anime_data.title,
            type=anime_data.type,
            season=anime_data.season,
            year=anime_data.year,
            imageUrl=getattr(anime_data, "imageUrl", None),
            createdAt=created_time
        )
        self._session.add(new_anime)
        await self._session.flush()

        # 创建元数据记录
        metadata = AnimeMetadata(
            animeId=new_anime.id,
            tmdbId=anime_data.tmdbId,
            imdbId=anime_data.imdbId,
            tvdbId=anime_data.tvdbId,
            doubanId=anime_data.doubanId,
            bangumiId=anime_data.bangumiId
        )
        self._session.add(metadata)

        # 创建别名记录
        aliases = AnimeAlias(
            animeId=new_anime.id,
            nameEn=anime_data.nameEn,
            nameJp=anime_data.nameJp,
            nameRomaji=anime_data.nameRomaji,
            aliasCn1=anime_data.aliasCn1,
            aliasCn2=anime_data.aliasCn2,
            aliasCn3=anime_data.aliasCn3
        )
        self._session.add(aliases)
        await self._session.flush()

        return new_anime

    async def update_anime_aliases(self, anime_id: int, payload: Any) -> None:
        """更新动漫的别名信息"""
        stmt = select(AnimeAlias).where(AnimeAlias.animeId == anime_id)
        result = await self._session.execute(stmt)
        alias_record = result.scalar_one_or_none()

        if not alias_record:
            alias_record = AnimeAlias(animeId=anime_id)
            self._session.add(alias_record)

        alias_record.nameEn = getattr(payload, 'nameEn', alias_record.nameEn)
        alias_record.nameJp = getattr(payload, 'nameJp', alias_record.nameJp)
        alias_record.nameRomaji = getattr(payload, 'nameRomaji', alias_record.nameRomaji)
        alias_record.aliasCn1 = getattr(payload, 'aliasCn1', alias_record.aliasCn1)
        alias_record.aliasCn2 = getattr(payload, 'aliasCn2', alias_record.aliasCn2)
        alias_record.aliasCn3 = getattr(payload, 'aliasCn3', alias_record.aliasCn3)

        await self._session.flush()

    async def bind_media_server(
        self, anime_id: int, server_type: str, series_id: str,
        season_id: Optional[str],
    ) -> bool:
        """精确更新作品的媒体服务器绑定，保留其他字段。"""
        anime = await self._session.get(
            Anime, anime_id, options=[selectinload(Anime.metadataRecord)]
        )
        if anime is None:
            return False
        if anime.metadataRecord is None:
            anime.metadataRecord = AnimeMetadata(animeId=anime_id)
        anime.metadataRecord.mediaServerType = server_type
        anime.metadataRecord.mediaServerSeriesId = series_id
        anime.metadataRecord.mediaServerSeasonId = season_id
        await self._session.flush()
        return True

    async def update_anime_details(self, anime_id: int, update_data) -> bool:
        """在事务中更新动漫的核心信息、元数据和别名"""
        anime = await self._session.get(
            Anime, anime_id,
            options=[selectinload(Anime.metadataRecord), selectinload(Anime.aliases)]
        )
        if not anime:
            return False

        # 更新 Anime 表
        anime.title = update_data.title
        anime.type = update_data.type
        anime.season = update_data.season
        anime.episodeCount = update_data.episodeCount
        anime.year = update_data.year
        anime.imageUrl = update_data.imageUrl

        # 更新或创建 AnimeMetadata
        if not anime.metadataRecord:
            anime.metadataRecord = AnimeMetadata(animeId=anime_id)
        anime.metadataRecord.tmdbId = update_data.tmdbId
        anime.metadataRecord.tmdbEpisodeGroupId = update_data.tmdbEpisodeGroupId
        anime.metadataRecord.bangumiId = update_data.bangumiId
        anime.metadataRecord.tvdbId = update_data.tvdbId
        anime.metadataRecord.doubanId = update_data.doubanId
        anime.metadataRecord.imdbId = update_data.imdbId
        anime.metadataRecord.mediaServerType = update_data.mediaServerType
        anime.metadataRecord.mediaServerSeriesId = update_data.mediaServerSeriesId
        anime.metadataRecord.mediaServerSeasonId = update_data.mediaServerSeasonId

        # 更新或创建 AnimeAlias
        if not anime.aliases:
            anime.aliases = AnimeAlias(animeId=anime_id)
        anime.aliases.nameEn = update_data.nameEn
        anime.aliases.nameJp = update_data.nameJp
        anime.aliases.nameRomaji = update_data.nameRomaji
        anime.aliases.aliasCn1 = update_data.aliasCn1
        anime.aliases.aliasCn2 = update_data.aliasCn2
        anime.aliases.aliasCn3 = update_data.aliasCn3
        anime.aliases.aliasLocked = update_data.aliasLocked

        await self._session.flush()
        return True
