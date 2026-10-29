"""
TmdbRepository - TMDB 剧集组映射数据访问层
"""

import logging
from typing import Optional, Dict, Any, List
from sqlalchemy import select, func, distinct, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..orm_models import AnimeMetadata, TmdbEpisodeMapping
from src.schemas import TMDBEpisodeGroupDetails
from .base import BaseRepository

logger = logging.getLogger(__name__)


class TmdbRepository(BaseRepository[TmdbEpisodeMapping]):
    """TMDB 剧集组映射 Repository"""

    async def get_by_id(self, mapping_id: int) -> Optional[TmdbEpisodeMapping]:
        """根据 ID 获取映射"""
        return await self._session.get(TmdbEpisodeMapping, mapping_id)

    async def get_all(self, **filters) -> List[TmdbEpisodeMapping]:
        """获取所有映射"""
        stmt = select(TmdbEpisodeMapping)
        if 'group_id' in filters:
            stmt = stmt.where(TmdbEpisodeMapping.tmdbEpisodeGroupId == filters['group_id'])
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, **data) -> TmdbEpisodeMapping:
        """创建映射"""
        mapping = TmdbEpisodeMapping(**data)
        self._session.add(mapping)
        await self._session.flush()
        return mapping

    async def update(self, mapping_id: int, **data) -> Optional[TmdbEpisodeMapping]:
        """更新映射"""
        mapping = await self.get_by_id(mapping_id)
        if not mapping:
            return None

        for key, value in data.items():
            if hasattr(mapping, key):
                setattr(mapping, key, value)

        await self._session.flush()
        return mapping

    async def delete(self, mapping_id: int) -> bool:
        """删除映射"""
        mapping = await self.get_by_id(mapping_id)
        if not mapping:
            return False

        await self._session.delete(mapping)
        await self._session.flush()
        return True

    async def save_episode_group_mappings(
        self,
        tmdb_tv_id: int,
        group_id: str,
        group_details: TMDBEpisodeGroupDetails
    ) -> None:
        """保存剧集组映射"""
        # 删除旧映射
        await self._session.execute(
            delete(TmdbEpisodeMapping).where(TmdbEpisodeMapping.tmdbEpisodeGroupId == group_id)
        )

        # 创建新映射
        mappings_to_insert = []
        sorted_groups = sorted(group_details.groups, key=lambda g: g.order)

        for custom_season_group in sorted_groups:
            if not custom_season_group.episodes:
                continue
            for custom_episode_index, episode in enumerate(custom_season_group.episodes):
                mappings_to_insert.append(
                    TmdbEpisodeMapping(
                        tmdbTvId=tmdb_tv_id,
                        tmdbEpisodeGroupId=group_id,
                        tmdbEpisodeId=episode.id,
                        tmdbSeasonNumber=episode.seasonNumber,
                        tmdbEpisodeNumber=episode.episodeNumber,
                        customSeasonNumber=custom_season_group.order,
                        customEpisodeNumber=custom_episode_index + 1,
                        absoluteEpisodeNumber=episode.episodeNumber,
                        episodeName=episode.name
                    )
                )

        if mappings_to_insert:
            self._session.add_all(mappings_to_insert)

        await self._session.flush()
        logger.info(f"成功为剧集组 {group_id} 保存了 {len(mappings_to_insert)} 条分集映射。")

    async def get_episode_group_mappings(self, group_id: str) -> Optional[Dict[str, Any]]:
        """获取剧集组映射（重建为分组结构）"""
        stmt = (
            select(TmdbEpisodeMapping)
            .where(TmdbEpisodeMapping.tmdbEpisodeGroupId == group_id)
            .order_by(TmdbEpisodeMapping.customSeasonNumber, TmdbEpisodeMapping.customEpisodeNumber)
        )
        result = await self._session.execute(stmt)
        mappings = result.scalars().all()

        if not mappings:
            return None

        tmdb_tv_id = mappings[0].tmdbTvId

        # 按 customSeasonNumber 分组重建结构
        groups_dict: Dict[int, Dict[str, Any]] = {}
        for m in mappings:
            season = m.customSeasonNumber
            if season not in groups_dict:
                groups_dict[season] = {
                    "name": f"第 {season} 组" if season > 0 else "特别篇",
                    "order": season,
                    "episodes": [],
                }
            groups_dict[season]["episodes"].append({
                "seasonNumber": m.tmdbSeasonNumber,
                "episodeNumber": m.tmdbEpisodeNumber,
                "order": m.customEpisodeNumber - 1,
                "name": m.episodeName or "",
            })

        groups = sorted(groups_dict.values(), key=lambda g: g["order"])

        return {
            "id": group_id,
            "tmdbTvId": tmdb_tv_id,
            "name": "本地剧集组" if group_id.startswith("local-") else group_id,
            "description": "",
            "groups": groups,
        }

    async def list_episode_groups(self, tmdb_tv_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """列出所有剧集组摘要"""
        stmt = (
            select(
                TmdbEpisodeMapping.tmdbEpisodeGroupId,
                TmdbEpisodeMapping.tmdbTvId,
                func.count(TmdbEpisodeMapping.id).label("episodeCount"),
                func.count(distinct(TmdbEpisodeMapping.customSeasonNumber)).label("groupCount"),
            )
            .group_by(TmdbEpisodeMapping.tmdbEpisodeGroupId, TmdbEpisodeMapping.tmdbTvId)
            .order_by(TmdbEpisodeMapping.tmdbTvId)
        )
        if tmdb_tv_id is not None:
            stmt = stmt.where(TmdbEpisodeMapping.tmdbTvId == tmdb_tv_id)

        result = await self._session.execute(stmt)
        rows = result.all()

        # 批量查询所有剧集组的关联条目
        group_ids = [row.tmdbEpisodeGroupId for row in rows]
        assoc_stmt = (
            select(AnimeMetadata.tmdbEpisodeGroupId, AnimeMetadata.animeId)
            .where(AnimeMetadata.tmdbEpisodeGroupId.in_(group_ids))
        )
        assoc_result = await self._session.execute(assoc_stmt)
        assoc_map: Dict[str, List[int]] = {}
        for ar in assoc_result.all():
            assoc_map.setdefault(ar.tmdbEpisodeGroupId, []).append(ar.animeId)

        return [
            {
                "groupId": row.tmdbEpisodeGroupId,
                "tmdbTvId": row.tmdbTvId,
                "episodeCount": row.episodeCount,
                "groupCount": row.groupCount,
                "isLocal": row.tmdbEpisodeGroupId.startswith("local-"),
                "associatedAnimeIds": assoc_map.get(row.tmdbEpisodeGroupId, []),
            }
            for row in rows
        ]

    async def get_episode_equivalence(
        self,
        group_id: str,
        season: Optional[int],
        episode: Optional[int]
    ) -> Optional[Dict[str, Any]]:
        """双向查询剧集组映射的等价信息"""
        if season is None and episode is None:
            return None

        # 方向1: 作为 custom (剧集组) 季集号查询
        if season is not None and episode is not None:
            stmt1 = (
                select(TmdbEpisodeMapping)
                .where(
                    TmdbEpisodeMapping.tmdbEpisodeGroupId == group_id,
                    TmdbEpisodeMapping.customSeasonNumber == season,
                    TmdbEpisodeMapping.customEpisodeNumber == episode,
                )
                .limit(1)
            )
            result1 = await self._session.execute(stmt1)
            hit1 = result1.scalar_one_or_none()
            if hit1:
                cnt_stmt = (
                    select(func.count(TmdbEpisodeMapping.id))
                    .where(
                        TmdbEpisodeMapping.tmdbEpisodeGroupId == group_id,
                        TmdbEpisodeMapping.customSeasonNumber == season,
                    )
                )
                cnt_result = await self._session.execute(cnt_stmt)
                season_total = cnt_result.scalar() or 0

                return {
                    "custom_season": hit1.customSeasonNumber,
                    "custom_episode": hit1.customEpisodeNumber,
                    "tmdb_season": hit1.tmdbSeasonNumber,
                    "tmdb_episode": hit1.tmdbEpisodeNumber,
                    "season_total_episodes": season_total,
                    "episode_name": hit1.episodeName or "",
                    "match_direction": "custom_to_tmdb",
                }

        # 方向2: 作为 tmdb 标准季集号查询
        if season is not None and episode is not None:
            stmt2 = (
                select(TmdbEpisodeMapping)
                .where(
                    TmdbEpisodeMapping.tmdbEpisodeGroupId == group_id,
                    TmdbEpisodeMapping.tmdbSeasonNumber == season,
                    TmdbEpisodeMapping.tmdbEpisodeNumber == episode,
                )
                .limit(1)
            )
            result2 = await self._session.execute(stmt2)
            hit2 = result2.scalar_one_or_none()
            if hit2:
                cnt_stmt = (
                    select(func.count(TmdbEpisodeMapping.id))
                    .where(
                        TmdbEpisodeMapping.tmdbEpisodeGroupId == group_id,
                        TmdbEpisodeMapping.customSeasonNumber == hit2.customSeasonNumber,
                    )
                )
                cnt_result = await self._session.execute(cnt_stmt)
                season_total = cnt_result.scalar() or 0

                return {
                    "custom_season": hit2.customSeasonNumber,
                    "custom_episode": hit2.customEpisodeNumber,
                    "tmdb_season": hit2.tmdbSeasonNumber,
                    "tmdb_episode": hit2.tmdbEpisodeNumber,
                    "season_total_episodes": season_total,
                    "episode_name": hit2.episodeName or "",
                    "match_direction": "tmdb_to_custom",
                }

        return None

    async def get_episode_equivalence_batch(
        self,
        group_id: str,
        season: int,
        episode_numbers: List[int]
    ) -> Dict[int, int]:
        """批量将 custom 季集号翻译为 absoluteEpisodeNumber"""
        if not episode_numbers:
            return {}

        stmt = (
            select(
                TmdbEpisodeMapping.customEpisodeNumber,
                TmdbEpisodeMapping.absoluteEpisodeNumber,
            )
            .where(
                TmdbEpisodeMapping.tmdbEpisodeGroupId == group_id,
                TmdbEpisodeMapping.customSeasonNumber == season,
                TmdbEpisodeMapping.customEpisodeNumber.in_(episode_numbers),
            )
        )
        result = await self._session.execute(stmt)
        rows = result.all()
        return {
            row.customEpisodeNumber: row.absoluteEpisodeNumber
            for row in rows
            if row.absoluteEpisodeNumber is not None
        }

    async def get_episode_group_id_by_tmdb_id(self, tmdb_id: str) -> Optional[str]:
        """查询同一 TMDB 作品已关联的剧集组，保留原导入流程的首条匹配语义。"""
        stmt = select(AnimeMetadata.tmdbEpisodeGroupId).where(
            AnimeMetadata.tmdbId == str(tmdb_id),
            AnimeMetadata.tmdbEpisodeGroupId.isnot(None),
        ).limit(1)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


    async def get_episode_group_id_by_anime_id(self, anime_id: int) -> Optional[str]:
        """通过 anime_id 查询关联的 tmdbEpisodeGroupId"""
        stmt = select(AnimeMetadata.tmdbEpisodeGroupId).where(AnimeMetadata.animeId == anime_id)
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def get_associated_anime_ids(self, group_id: str) -> List[int]:
        """查询关联了指定剧集组的所有条目ID"""
        stmt = select(AnimeMetadata.animeId).where(AnimeMetadata.tmdbEpisodeGroupId == group_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def delete_episode_group_mappings(self, group_id: str) -> int:
        """删除指定 groupId 的所有映射记录"""
        result = await self._session.execute(
            delete(TmdbEpisodeMapping).where(TmdbEpisodeMapping.tmdbEpisodeGroupId == group_id)
        )
        await self._session.flush()
        deleted = result.rowcount
        logger.info(f"已删除剧集组 {group_id} 的 {deleted} 条映射记录。")
        return deleted
