"""
Tmdb相关的CRUD操作
"""

import logging
from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, distinct, case, or_, and_, update, delete
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta

from ..orm_models import Anime, AnimeMetadata, TmdbEpisodeMapping
from .. import models
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


async def save_tmdb_episode_group_mappings(session: AsyncSession, tmdb_tv_id: int, group_id: str, group_details: models.TMDBEpisodeGroupDetails):
    await session.execute(delete(TmdbEpisodeMapping).where(TmdbEpisodeMapping.tmdbEpisodeGroupId == group_id))

    mappings_to_insert = []
    sorted_groups = sorted(group_details.groups, key=lambda g: g.order)

    for custom_season_group in sorted_groups:
        if not custom_season_group.episodes: continue
        for custom_episode_index, episode in enumerate(custom_season_group.episodes):
            # 使用TMDB的episode_number作为绝对集数
            mappings_to_insert.append(
                TmdbEpisodeMapping(
                    tmdbTvId=tmdb_tv_id, tmdbEpisodeGroupId=group_id, tmdbEpisodeId=episode.id,
                    tmdbSeasonNumber=episode.seasonNumber, tmdbEpisodeNumber=episode.episodeNumber,
                    customSeasonNumber=custom_season_group.order, customEpisodeNumber=custom_episode_index + 1,
                    absoluteEpisodeNumber=episode.episodeNumber,
                    episodeName=episode.name
                )
            )
    if mappings_to_insert:
        session.add_all(mappings_to_insert)
    await session.commit()
    logging.info(f"成功为剧集组 {group_id} 保存了 {len(mappings_to_insert)} 条分集映射。")


async def get_episode_group_mappings(session: AsyncSession, group_id: str) -> Optional[Dict[str, Any]]:
    """
    从数据库读取已保存的剧集组映射，重建为分组结构返回。
    返回格式: { id, tmdbTvId, groups: [{ name, order, episodes: [{ seasonNumber, episodeNumber, order, name }] }] }
    """
    stmt = (
        select(TmdbEpisodeMapping)
        .where(TmdbEpisodeMapping.tmdbEpisodeGroupId == group_id)
        .order_by(TmdbEpisodeMapping.customSeasonNumber, TmdbEpisodeMapping.customEpisodeNumber)
    )
    result = await session.execute(stmt)
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
            "order": m.customEpisodeNumber - 1,  # customEpisodeNumber 从1开始，order从0开始
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


async def list_episode_groups(session: AsyncSession, tmdb_tv_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    列出所有剧集组摘要，按 tmdbEpisodeGroupId 聚合。
    可选按 tmdbTvId 过滤。
    """
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

    result = await session.execute(stmt)
    rows = result.all()

    # 批量查询所有剧集组的关联条目
    group_ids = [row.tmdbEpisodeGroupId for row in rows]
    assoc_stmt = (
        select(AnimeMetadata.tmdbEpisodeGroupId, AnimeMetadata.animeId)
        .where(AnimeMetadata.tmdbEpisodeGroupId.in_(group_ids))
    )
    assoc_result = await session.execute(assoc_stmt)
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
    session: AsyncSession,
    group_id: str,
    season: Optional[int],
    episode: Optional[int]
) -> Optional[Dict[str, Any]]:
    """
    双向查询剧集组映射的等价信息。
    传入的season/episode可以是custom(剧集组)也可以是tmdb标准分季，
    函数同时尝试两个方向的匹配。

    返回:
    {
        "custom_season": int, "custom_episode": int,
        "tmdb_season": int, "tmdb_episode": int,
        "season_total_episodes": int,  # 该custom季度的总集数
        "episode_name": str,
        "match_direction": "custom_to_tmdb" 或 "tmdb_to_custom"
    }
    """
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
        result1 = await session.execute(stmt1)
        hit1 = result1.scalar_one_or_none()
        if hit1:
            # 统计该 custom 季度的总集数
            cnt_stmt = (
                select(func.count(TmdbEpisodeMapping.id))
                .where(
                    TmdbEpisodeMapping.tmdbEpisodeGroupId == group_id,
                    TmdbEpisodeMapping.customSeasonNumber == season,
                )
            )
            cnt_result = await session.execute(cnt_stmt)
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
        result2 = await session.execute(stmt2)
        hit2 = result2.scalar_one_or_none()
        if hit2:
            cnt_stmt = (
                select(func.count(TmdbEpisodeMapping.id))
                .where(
                    TmdbEpisodeMapping.tmdbEpisodeGroupId == group_id,
                    TmdbEpisodeMapping.customSeasonNumber == hit2.customSeasonNumber,
                )
            )
            cnt_result = await session.execute(cnt_stmt)
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


async def get_associated_anime_ids(session: AsyncSession, group_id: str) -> List[int]:
    """查询关联了指定剧集组的所有条目ID。"""
    stmt = select(AnimeMetadata.animeId).where(AnimeMetadata.tmdbEpisodeGroupId == group_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def delete_episode_group_mappings(session: AsyncSession, group_id: str) -> int:
    """删除指定 groupId 的所有映射记录，返回删除的行数。"""
    result = await session.execute(
        delete(TmdbEpisodeMapping).where(TmdbEpisodeMapping.tmdbEpisodeGroupId == group_id)
    )
    await session.commit()
    deleted = result.rowcount
    logger.info(f"已删除剧集组 {group_id} 的 {deleted} 条映射记录。")
    return deleted

