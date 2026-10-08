"""
FallbackRepository - 后备流程数据访问层

从 src.db.crud.fallback 迁移而来的所有后备流程相关函数
"""

import logging
import json
from typing import Optional, List, Dict, Any
from datetime import timedelta
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..orm_models import Anime, AnimeSource, Episode, AnimeMetadata, TaskStateCache, TaskHistory
from .base import BaseRepository
from .config import ConfigRepository
from .source_order import reserve_source_order
from src.core.timezone import get_now

logger = logging.getLogger(__name__)

# ═══════════ 缓存键常量（与 api.dandan.constants 保持一致） ═══════════
EPISODE_MAPPING_CACHE_PREFIX = "episode_mapping_"
FALLBACK_SEARCH_CACHE_PREFIX = "fallback_search_"
USER_LAST_BANGUMI_CHOICE_PREFIX = "user_last_bangumi_"

# 缓存TTL
FALLBACK_SEARCH_CACHE_TTL = 3600
USER_LAST_BANGUMI_CHOICE_TTL = 86400
EPISODE_MAPPING_TTL = 10800


class FallbackRepository(BaseRepository[Anime]):
    """后备流程 Repository - 包含所有后备流程相关的数据库操作"""

    # ═══════════ 基础 CRUD ═══════════

    async def get_by_id(self, anime_id: int) -> Optional[Anime]:
        """根据 ID 获取动漫"""
        return await self._session.get(Anime, anime_id)

    async def get_all(self, **filters) -> List[Anime]:
        """获取所有动漫"""
        stmt = select(Anime)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, **data) -> Anime:
        """创建动漫"""
        anime = Anime(**data)
        self._session.add(anime)
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

    # ═══════════ ID 分配 ═══════════

    async def get_next_real_anime_id(self) -> int:
        """分配下一个真实 animeId（从 crud.fallback 迁移）

        保证不重用已删除的 id（防 episodeId 串台）
        取 max(持久化计数器, 数据库 MAX(Anime.id)) + 1
        """
        # 配置仓储在模块顶部导入，避免隐藏依赖。

        # 查询数据库最大 ID
        stmt = select(func.max(Anime.id))
        result = await self._session.execute(stmt)
        db_max = result.scalar() or 0

        # 通过 ConfigRepository 分配计数器
        config_repo = ConfigRepository(self._session)
        return await config_repo.allocate_next_counter_value(
            key="lastAllocatedRealAnimeId",
            floor=db_max,
            description="后备搜索已分配的最大真实 animeId（只增不减，防止删除后 id 重用导致 episodeId 串台）"
        )

    # ═══════════ 查询函数 ═══════════

    async def find_anime_by_title_season(
        self,
        original_title: str,
        pure_title: str,
        season: int
    ) -> Optional[int]:
        """按(标题,季度)查库内已存在作品（从 crud.fallback 迁移）

        先用 original_title 查，找不到再用 pure_title 查（兼容旧数据）
        返回 anime_id 或 None
        """
        # 先用 original_title 精确查
        stmt = select(Anime.id).where(
            Anime.title == original_title,
            Anime.season == season,
        )
        result = await self._session.execute(stmt)
        row = result.mappings().first()

        # 兼容旧数据：原始标题找不到，再用纯标题查
        if not row and original_title != pure_title:
            stmt_fallback = select(Anime.id).where(
                Anime.title == pure_title,
                Anime.season == season,
            )
            result_fallback = await self._session.execute(stmt_fallback)
            row = result_fallback.mappings().first()

        return row["id"] if row else None

    async def check_related_match_fallback_task(
        self,
        search_term: str
    ) -> Optional[Dict]:
        """查询是否有相关的匹配后备任务正在进行（从 crud.fallback 迁移）"""
        stmt = select(TaskStateCache).where(
            TaskStateCache.taskType == "match_fallback"
        ).order_by(TaskStateCache.createdAt.desc()).limit(10)
        result = await self._session.execute(stmt)
        task_caches = result.scalars().all()

        for task_cache in task_caches:
            history_stmt = select(TaskHistory).where(
                TaskHistory.taskId == task_cache.taskId,
                TaskHistory.status.in_(['排队中', '运行中'])
            )
            history_result = await self._session.execute(history_stmt)
            task_history = history_result.scalar_one_or_none()
            if task_history:
                if search_term.lower() in task_history.title.lower() or \
                   (task_history.description and search_term.lower() in task_history.description.lower()):
                    return {
                        "task_id": task_history.taskId,
                        "title": task_history.title,
                        "progress": task_history.progress or 0,
                        "status": task_history.status,
                        "description": task_history.description or "匹配后备正在进行",
                    }
        return None

    async def get_or_predict_source_order(
        self,
        anime_id: int,
        provider: str,
        media_id: str
    ) -> int:
        """获取稳定源序号；未入库源持久化预留，避免切源覆盖整季映射。"""
        return await reserve_source_order(self._session, anime_id, provider, media_id)

    # ═══════════ 冷启动建库 ═══════════

    async def ensure_fallback_anime(
        self,
        real_anime_id: int,
        display_title: str,
        media_type: str,
        final_season: int,
        imageUrl: Optional[str] = None,
        year: Optional[int] = None
    ) -> int:
        """冷启动建库第1步：创建/获取 Anime 条目（从 crud.fallback 迁移）

        以指定 id 创建 Anime，创建后 flush + sync_postgres_sequence 避免 PG 主键序列冲突
        """
        from src.db.database import sync_postgres_sequence

        existing = (await self._session.execute(
            select(Anime).where(Anime.id == real_anime_id)
        )).scalar_one_or_none()
        if existing:
            logger.info(f"anime条目已存在: id={real_anime_id}, title='{existing.title}'")
            return real_anime_id

        logger.info(f"创建anime条目: id={real_anime_id}, title='{display_title}'")
        new_anime = Anime(
            id=real_anime_id,
            title=display_title,
            type=media_type,
            season=final_season,
            imageUrl=imageUrl,
            year=year,
            createdAt=get_now(),
        )
        self._session.add(new_anime)
        await self._session.flush()
        # 同步 PostgreSQL 序列（避免后续自增主键冲突）
        await sync_postgres_sequence(self._session)
        return real_anime_id

    async def ensure_fallback_source(
        self,
        anime_id: int,
        provider: str,
        media_id: str
    ) -> int:
        """冷启动建库第2步：创建/获取 AnimeSource（从 crud.fallback 迁移）"""
        from .source import SourceRepository

        source_repo = SourceRepository(self._session)
        source_id = await source_repo.link_source_to_anime(anime_id, provider, media_id)
        logger.info(f"source_id={source_id}")
        return source_id

    async def ensure_fallback_episode(
        self,
        anime_id: int,
        source_id: int,
        episode_number: int,
        episode_title: str,
        episode_url: str,
        provider_episode_id: str
    ) -> int:
        """冷启动建库第3步：创建/获取 Episode（从 crud.fallback 迁移）"""
        from .episode import EpisodeRepository

        episode_repo = EpisodeRepository(self._session)
        episode_db_id = await episode_repo.create_episode_if_not_exists(
            anime_id, source_id, episode_number,
            episode_title, episode_url, provider_episode_id
        )
        await self._session.flush()
        logger.info(f"Episode条目已创建/存在: id={episode_db_id}")
        return episode_db_id
