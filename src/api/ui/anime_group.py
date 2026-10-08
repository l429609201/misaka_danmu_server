"""
AnimeGroup 分组相关 API 端点
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.services.database_service import DatabaseService
from src.services.service_container import get_database_service
from src.utils.auth import security

logger = logging.getLogger(__name__)

router = APIRouter()


# ---- Pydantic 请求/响应模型 ----

class GroupCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class GroupRenameRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class GroupReorderRequest(BaseModel):
    groupIds: List[int] = Field(..., description="按新顺序排列的分组 ID 列表")


class SetAnimeGroupRequest(BaseModel):
    groupId: Optional[int] = Field(None, description="目标分组 ID，null 表示移出分组")


class GroupInfo(BaseModel):
    id: int
    name: str
    sortOrder: int

    class Config:
        from_attributes = True


# ---- API 端点 ----

@router.get("/anime/groups", response_model=List[GroupInfo], summary="获取所有分组")
async def list_groups(
    _current_user=Depends(security.get_current_user),
    db_service: DatabaseService = Depends(get_database_service),
):
    """获取所有分组，按 sortOrder 排序。"""
    async with db_service.transaction() as session:
        groups = await db_service.anime_group.get_all_groups()
    return groups


@router.post("/anime/groups", response_model=GroupInfo, status_code=201, summary="创建分组")
async def create_group(
    payload: GroupCreateRequest,
    _current_user=Depends(security.get_current_user),
    db_service: DatabaseService = Depends(get_database_service),
):
    """创建新分组。"""
    async with db_service.transaction() as session:
        group = await db_service.anime_group.create_group(name=payload.name)
    return group


@router.patch("/anime/groups/reorder", summary="批量更新分组排序")
async def reorder_groups(
    payload: GroupReorderRequest,
    _current_user=Depends(security.get_current_user),
    db_service: DatabaseService = Depends(get_database_service),
):
    """按传入的 groupIds 顺序批量更新 sortOrder。"""
    async with db_service.transaction() as session:
        await db_service.anime_group.reorder_groups(group_ids=payload.groupIds)
    return {"success": True}


@router.patch("/anime/groups/{group_id}", response_model=GroupInfo, summary="重命名分组")
async def rename_group(
    group_id: int,
    payload: GroupRenameRequest,
    _current_user=Depends(security.get_current_user),
    db_service: DatabaseService = Depends(get_database_service),
):
    """重命名指定分组。"""
    async with db_service.transaction() as session:
        success = await db_service.anime_group.rename_group(group_id=group_id, name=payload.name)
        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分组不存在")
        group = await db_service.anime_group.get_group_by_id(group_id)
    return group


@router.delete("/anime/groups/{group_id}", status_code=204, summary="删除分组")
async def delete_group(
    group_id: int,
    _current_user=Depends(security.get_current_user),
    db_service: DatabaseService = Depends(get_database_service),
):
    """删除分组。关联条目的 groupId 自动置 null（ON DELETE SET NULL）。"""
    async with db_service.transaction() as session:
        success = await db_service.anime_group.delete_group(group_id=group_id)
        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分组不存在")


@router.patch("/anime/{anime_id}/group", summary="设置或清除条目的分组")
async def set_anime_group(
    anime_id: int,
    payload: SetAnimeGroupRequest,
    _current_user=Depends(security.get_current_user),
    db_service: DatabaseService = Depends(get_database_service),
):
    """将条目加入分组或移出分组（groupId=null）。"""
    async with db_service.transaction() as session:
        success = await db_service.anime_group.set_anime_group(anime_id=anime_id, group_id=payload.groupId)
        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="条目不存在")
    return {"success": True, "animeId": anime_id, "groupId": payload.groupId}

