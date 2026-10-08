"""
本地剧集组 (Local Episode Group) 相关的API端点
支持 StrmAssistant 格式的 episodegroup.json 导入
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Body, Query
from pydantic import BaseModel

from src.utils.auth import security
from src.services.database_service import DatabaseService
from src.services.service_container import get_database_service
from src.schemas import (
    TMDBEpisodeGroupDetails,
    TMDBGroupInGroupDetail,
    TMDBEpisodeInGroupDetail,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class FetchUrlRequest(BaseModel):
    url: str


class ApplyLocalEpisodeGroupRequest(BaseModel):
    tmdbId: int
    localEpisodeGroup: Dict[str, Any]


@router.post("/local-episode-group/fetch", summary="获取本地剧集组JSON（支持URL和本地路径）")
async def fetch_local_episode_group(
    payload: FetchUrlRequest,
    current_user = Depends(security.get_current_user),
):
    """
    获取 episodegroup.json 内容。
    - 如果 url 以 http:// 或 https:// 开头，代理获取远程文件
    - 否则当作服务器本地文件路径读取
    """
    source = payload.url.strip()
    is_remote = source.startswith("http://") or source.startswith("https://")

    if is_remote:
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                response = await client.get(source)
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException:
            raise HTTPException(status_code=status.HTTP_408_REQUEST_TIMEOUT, detail="请求超时，请检查URL是否可访问")
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"远程服务器返回错误: {e.response.status_code}")
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"获取或解析JSON失败: {str(e)}")
    else:
        # 本地文件路径
        file_path = Path(source)
        if not file_path.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"文件不存在: {source}")
        if not file_path.suffix.lower() == ".json":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仅支持 .json 文件")
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"读取或解析本地文件失败: {str(e)}")

    # 基本格式校验
    if not isinstance(data, dict) or "groups" not in data:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="JSON格式不正确，缺少 groups 字段")

    return data


@router.post("/local-episode-group/apply", summary="应用本地剧集组映射")
async def apply_local_episode_group(
    payload: ApplyLocalEpisodeGroupRequest,
    current_user = Depends(security.get_current_user),
    db_service: DatabaseService = Depends(get_database_service),
):
    """
    解析 StrmAssistant 格式的本地剧集组 JSON，转换为分集映射并保存到数据库。
    生成格式为 `local-{tmdbTvId}` 的合成剧集组ID。
    """
    local_data = payload.localEpisodeGroup
    tmdb_tv_id = payload.tmdbId

    groups_raw = local_data.get("groups", [])
    if not groups_raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="剧集组数据为空")

    # 将本地格式转换为 TMDBEpisodeGroupDetails 模型
    group_id = f"local-{tmdb_tv_id}"
    groups = []
    episode_counter = 0

    for g in groups_raw:
        episodes = []
        for ep in g.get("episodes", []):
            episode_counter += 1
            season_num = ep.get("season_number", 0)
            episode_num = ep.get("episode_number", 0)
            episodes.append(TMDBEpisodeInGroupDetail(
                id=episode_counter,  # 合成唯一ID
                name="",
                episodeNumber=episode_num,
                seasonNumber=season_num,
                order=ep.get("order", 0),
            ))
        groups.append(TMDBGroupInGroupDetail(
            id="",
            name=g.get("name", ""),
            order=g.get("order", 0),
            episodes=episodes,
        ))

    group_details = TMDBEpisodeGroupDetails(
        id=group_id,
        name=local_data.get("description", "") or "本地剧集组",
        description=local_data.get("description", ""),
        episodeCount=episode_counter,
        groupCount=len(groups),
        groups=groups,
        type=0,
    )

    db_service = get_database_service()
    async with db_service.transaction():
        await db_service.tmdb.save_episode_group_mappings(
            tmdb_tv_id=tmdb_tv_id,
            group_id=group_id,
            group_details=group_details,
        )

    logger.info(f"已为 TMDB TV ID {tmdb_tv_id} 应用本地剧集组映射，共 {episode_counter} 条。")
    return {"message": "本地剧集组映射更新成功", "groupId": group_id, "episodeCount": episode_counter}



@router.get("/local-episode-group/detail", summary="获取已保存的剧集组详情（从数据库读取）")
async def get_episode_group_detail(
    groupId: str = Query(..., description="剧集组ID，如 local-12345 或 TMDB 剧集组ID"),
    current_user = Depends(security.get_current_user),
    db_service: DatabaseService = Depends(get_database_service),
):
    """
    从数据库中读取已保存的剧集组映射，重建为分组结构返回。
    支持本地剧集组（local-xxx）和 TMDB 剧集组。
    """
    async with db_service.transaction():
        data = await db_service.tmdb.get_episode_group_mappings(groupId)

    if not data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"未找到剧集组 {groupId} 的映射数据，可能尚未保存到数据库"
        )
    return data