"""
外部控制API - 剧集组管理路由
包含: /episode-groups (CRUD)
支持 TMDB 原生剧集组和本地剧集组 (local-{tmdbTvId})
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import crud, models, get_db_session

from .models import (
    ControlActionResponse,
    EpisodeGroupSummary,
    EpisodeGroupCreateRequest,
    EpisodeGroupUpdateRequest,
    EpisodeGroupAssociateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _build_group_details(
    group_id: str,
    name: str,
    groups_data,
) -> models.TMDBEpisodeGroupDetails:
    """将请求中的 groups 结构转换为 TMDBEpisodeGroupDetails 模型。"""
    tmdb_groups = []
    episode_count = 0
    for g in groups_data:
        episodes = [
            models.TMDBEpisodeInGroupDetail(
                id=ep.id, name=ep.name,
                episodeNumber=ep.episodeNumber, seasonNumber=ep.seasonNumber,
                order=ep.order,
            )
            for ep in g.episodes
        ]
        episode_count += len(episodes)
        tmdb_groups.append(models.TMDBGroupInGroupDetail(
            id="", name=g.name, order=g.order, episodes=episodes,
        ))
    return models.TMDBEpisodeGroupDetails(
        id=group_id, name=name, description="",
        episodeCount=episode_count, groupCount=len(tmdb_groups),
        groups=tmdb_groups, type=0,
    )


@router.get("/episode-groups", response_model=List[EpisodeGroupSummary], summary="列出所有剧集组")
async def list_episode_groups(
    tmdbTvId: Optional[int] = Query(None, description="按 TMDB TV ID 过滤"),
    session: AsyncSession = Depends(get_db_session),
):
    """
    ### 功能
    列出数据库中所有已保存的剧集组摘要信息。
    支持通过 `tmdbTvId` 查询参数过滤特定作品的剧集组。
    """
    return await crud.list_episode_groups(session, tmdb_tv_id=tmdbTvId)


@router.get("/episode-groups/{groupId}", summary="查看剧集组详情")
async def get_episode_group(
    groupId: str,
    session: AsyncSession = Depends(get_db_session),
):
    """
    ### 功能
    获取指定剧集组的完整分组结构，包括每个分组下的所有分集映射信息。
    支持 TMDB 原生剧集组ID 和本地剧集组ID（`local-{tmdbTvId}`）。
    """
    data = await crud.get_episode_group_mappings(session, groupId)
    if not data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"未找到剧集组 {groupId}")
    return data


@router.post("/episode-groups", status_code=status.HTTP_201_CREATED, summary="创建剧集组")
async def create_episode_group(
    payload: EpisodeGroupCreateRequest,
    session: AsyncSession = Depends(get_db_session),
):
    """
    ### 功能
    创建一个新的剧集组映射。
    ### 规则
    - 不传 `groupId`：自动生成本地剧集组，ID 为 `local-{tmdbTvId}`。
    - 传入 `groupId`：必须为 TMDB 原生剧集组ID，不能以 `local-` 开头。
    - 如果该 `groupId` 已存在，返回 409 冲突。
    - `animeId`（可选）：传入条目ID，创建后自动将该条目与剧集组绑定（设置 `anime_metadata.tmdbEpisodeGroupId`）。
    """
    if payload.groupId:
        if payload.groupId.startswith("local-"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="groupId 不能以 'local-' 开头，本地剧集组请勿传入 groupId")
        group_id = payload.groupId
    else:
        group_id = f"local-{payload.tmdbTvId}"

    existing = await crud.get_episode_group_mappings(session, group_id)
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"剧集组 {group_id} 已存在，请使用 PUT 接口更新")

    group_details = _build_group_details(group_id, payload.name, payload.groups)
    await crud.save_tmdb_episode_group_mappings(session, payload.tmdbTvId, group_id, group_details)

    # 如果传了 animeId，自动关联
    if payload.animeId is not None:
        await crud.update_anime_tmdb_group_id(session, payload.animeId, group_id)

    return {"message": f"剧集组 {group_id} 创建成功", "groupId": group_id, "episodeCount": group_details.episodeCount}


@router.put("/episode-groups/{groupId}", summary="编辑剧集组")
async def update_episode_group(
    groupId: str,
    payload: EpisodeGroupUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
):
    """
    ### 功能
    全量更新指定剧集组的映射数据（先删后插）。
    支持 TMDB 原生剧集组和本地剧集组。
    """
    existing = await crud.get_episode_group_mappings(session, groupId)
    if not existing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"未找到剧集组 {groupId}")

    group_details = _build_group_details(groupId, payload.name, payload.groups)
    await crud.save_tmdb_episode_group_mappings(session, payload.tmdbTvId, groupId, group_details)

    return {"message": f"剧集组 {groupId} 更新成功", "episodeCount": group_details.episodeCount}


@router.put("/episode-groups/{groupId}/associate", summary="关联剧集组与条目")
async def associate_episode_group(
    groupId: str,
    payload: EpisodeGroupAssociateRequest,
    session: AsyncSession = Depends(get_db_session),
):
    """
    ### 功能
    将指定条目与剧集组进行关联，设置 `anime_metadata.tmdbEpisodeGroupId`。
    如需解关联，请使用 DELETE `/episode-groups/{groupId}/associate/{animeId}`。
    """
    existing = await crud.get_episode_group_mappings(session, groupId)
    if not existing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"未找到剧集组 {groupId}")

    await crud.update_anime_tmdb_group_id(session, payload.animeId, groupId)
    return {"message": f"条目 {payload.animeId} 已关联到剧集组 {groupId}"}


@router.delete("/episode-groups/{groupId}/associate/{animeId}", summary="解关联剧集组与条目")
async def disassociate_episode_group(
    groupId: str,
    animeId: int,
    session: AsyncSession = Depends(get_db_session),
):
    """
    ### 功能
    解除指定条目与剧集组的关联，将 `anime_metadata.tmdbEpisodeGroupId` 置空。
    """
    await crud.update_anime_tmdb_group_id(session, animeId, "")
    return {"message": f"条目 {animeId} 已解除与剧集组 {groupId} 的关联"}


@router.delete("/episode-groups/{groupId}", summary="删除剧集组")
async def delete_episode_group(
    groupId: str,
    session: AsyncSession = Depends(get_db_session),
):
    """
    ### 功能
    删除指定剧集组的所有映射记录。
    支持 TMDB 原生剧集组和本地剧集组。
    """
    deleted = await crud.delete_episode_group_mappings(session, groupId)
    if deleted == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"未找到剧集组 {groupId}")
    return {"message": f"剧集组 {groupId} 已删除，共移除 {deleted} 条映射记录"}

