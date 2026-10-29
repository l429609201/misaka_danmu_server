"""
ReassociationRepository - 重关联数据访问层

注意：此 Repository 仅负责数据库操作。
文件操作（弹幕迁移/删除）已移至 src/workflows/reassociation_flow.py
"""

import logging
from typing import Optional, List, Dict, Any
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..orm_models import Anime, AnimeSource, Episode
from src.schemas import ReassociationConflictResponse, ConflictEpisode, ProviderConflict
from .base import BaseRepository

logger = logging.getLogger(__name__)


class ReassociationRepository(BaseRepository[Anime]):
    """重关联 Repository（主要操作 Anime 表）"""

    async def get_by_id(self, anime_id: int) -> Optional[Anime]:
        """根据 ID 获取动漫（包含源和分集）"""
        stmt = select(Anime).where(Anime.id == anime_id).options(
            selectinload(Anime.sources).selectinload(AnimeSource.episodes)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all(self, **filters) -> List[Anime]:
        """获取所有动漫"""
        stmt = select(Anime)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, **data) -> Anime:
        """创建动漫（基础方法，通常不直接使用）"""
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

    async def check_reassociation_conflicts(
        self,
        source_anime_id: int,
        target_anime_id: int
    ) -> ReassociationConflictResponse:
        """检测关联操作是否存在冲突"""
        source_anime = await self.get_by_id(source_anime_id)
        target_anime = await self.get_by_id(target_anime_id)

        if not source_anime or not target_anime:
            return ReassociationConflictResponse(hasConflict=False, conflicts=[])

        # 检测冲突
        target_sources_map = {s.providerName: s for s in target_anime.sources}
        conflicts = []

        for source_to_check in source_anime.sources:
            provider = source_to_check.providerName
            if provider in target_sources_map:
                target_source = target_sources_map[provider]

                # 找出冲突的分集
                target_episode_map = {ep.episodeIndex: ep for ep in target_source.episodes}
                conflict_episodes = []

                for source_ep in source_to_check.episodes:
                    if source_ep.episodeIndex in target_episode_map:
                        target_ep = target_episode_map[source_ep.episodeIndex]
                        conflict_episodes.append(ConflictEpisode(
                            episodeIndex=source_ep.episodeIndex,
                            sourceEpisodeId=source_ep.id,
                            targetEpisodeId=target_ep.id,
                            sourceDanmakuCount=source_ep.commentCount or 0,
                            targetDanmakuCount=target_ep.commentCount or 0,
                            sourceLastFetchTime=source_ep.fetchedAt,
                            targetLastFetchTime=target_ep.fetchedAt
                        ))

                if conflict_episodes:
                    conflicts.append(ProviderConflict(
                        providerName=provider,
                        sourceSourceId=source_to_check.id,
                        targetSourceId=target_source.id,
                        conflictEpisodes=conflict_episodes
                    ))

        return ReassociationConflictResponse(
            hasConflict=len(conflicts) > 0,
            conflicts=conflicts
        )

    async def move_episode(
        self, episode: Episode, target: AnimeSource, episode_index: int,
    ) -> None:
        """迁移分集并同步双向关系，防止旧源删除时级联删除已迁移分集。"""
        with self._session.no_autoflush:
            episode.source = target
            episode.episodeIndex = episode_index
        await self._session.flush()

    async def move_source(self, source: AnimeSource, target: Anime) -> None:
        """使用空闲顺序号迁移整源，避免移动时先触发源顺序唯一键冲突。"""
        with self._session.no_autoflush:
            source.sourceOrder = max((s.sourceOrder for s in target.sources), default=0) + 1
            # 目标已有精确标记时保留目标，避免迁移产生多个精确标记源。
            if any(s.isFavorited for s in target.sources):
                source.isFavorited = False
            source.anime = target
        await self._session.flush()

    async def remove_episode(self, episode: Episode, source: AnimeSource) -> None:
        """从集合移除分集并立即落盘，释放被替换分集的唯一键。"""
        # delete-orphan 同步集合与数据库；Session.delete 不会清理内存集合。
        source.episodes.remove(episode)
        await self._session.flush()

    async def remove_empty_source(self, source: AnimeSource, anime: Anime) -> None:
        """仅删除已处理完所有分集的源，防止遗漏数据被级联删除。"""
        if source.episodes:
            raise ValueError("数据源仍有未处理分集，拒绝删除")
        anime.sources.remove(source)
        await self._session.flush()

    async def finish_reassociation(self, source: Anime, target: Anime) -> None:
        """确认源作品已空后重排目标源，并删除空作品。"""
        if source.sources:
            raise ValueError("源作品仍有未处理的数据源，拒绝删除")
        await self._renumber_sources(target)
        await self._session.delete(source)
        await self._session.flush()


    async def _renumber_sources(self, target_anime: Anime) -> None:
        """
        重排目标作品下所有源的 sourceOrder。

        why: (animeId, sourceOrder) 存在唯一约束，直接赋新值会在中途撞约束，
             故先统一置为负数占位、flush 后再写入正序。
        """
        sorted_sources = sorted(target_anime.sources, key=lambda s: s.sourceOrder)
        logger.info(f"正在为目标番剧 (ID: {target_anime.id}) 的 {len(sorted_sources)} 个源重新编号...")

        for i, source in enumerate(sorted_sources):
            source.sourceOrder = -(i + 1)
        await self._session.flush()

        for i, source in enumerate(sorted_sources):
            source.sourceOrder = i + 1
        await self._session.flush()
