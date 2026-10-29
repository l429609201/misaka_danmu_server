"""
外部控制API - Token管理路由
包含: /tokens/*
"""

import secrets
from typing import List

from fastapi import APIRouter, HTTPException, status

from src.schemas.control import ControlActionResponse, ControlApiTokenUpdate
from src.schemas.ui_models import ApiTokenInfo, ApiTokenCreate, ApiTokenAccessLog
from src.services.service_container import get_database_service

router = APIRouter()


@router.get("/tokens", response_model=List[ApiTokenInfo], summary="获取所有Token")
async def get_tokens() -> List[ApiTokenInfo]:
    """获取所有为dandanplay客户端创建的API Token。"""
    db = get_database_service()
    async with db.transaction():
        tokens = await db.api_token.get_all()
        return [ApiTokenInfo.model_validate(t, from_attributes=True) for t in tokens]


@router.post("/tokens", response_model=ApiTokenInfo, status_code=201, summary="创建Token")
async def create_token(
    payload: ApiTokenCreate
) -> ApiTokenInfo:
    """创建一个新的API Token。"""
    token_str = secrets.token_urlsafe(16)
    db = get_database_service()
    async with db.transaction():
        try:
            # 仓储返回完整对象，不再将对象误当作 ID 二次查询。
            new_token = await db.api_token.create(
                payload.name, token_str, payload.validityPeriod, payload.dailyCallLimit
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
        return ApiTokenInfo.model_validate(new_token, from_attributes=True)


@router.get("/tokens/{tokenId}", response_model=ApiTokenInfo, summary="获取单个Token详情")
async def get_token(tokenId: int) -> ApiTokenInfo:
    """获取指定 Token 的详情。"""
    db = get_database_service()
    async with db.transaction():
        token = await db.api_token.get_by_id(tokenId)
        if token is None:
            raise HTTPException(404, "Token未找到")
        return ApiTokenInfo.model_validate(token, from_attributes=True)


@router.get("/tokens/{tokenId}/logs", response_model=List[ApiTokenAccessLog], summary="获取Token访问日志")
async def get_token_logs(
    tokenId: int
) -> List[ApiTokenAccessLog]:
    """获取指定 Token 的访问日志。"""
    db = get_database_service()
    async with db.transaction():
        logs = await db.token_log.get_by_token_id(tokenId)
        # 日志模型未默认启用属性读取，显式转换仓储返回的 ORM 对象。
        return [ApiTokenAccessLog.model_validate(log, from_attributes=True) for log in logs]


@router.put("/tokens/{tokenId}/toggle", response_model=ControlActionResponse, summary="启用/禁用Token")
async def toggle_token(tokenId: int) -> dict[str, str]:
    """切换 API Token 的启用状态。"""
    db = get_database_service()
    async with db.transaction():
        token = await db.api_token.toggle_enable(tokenId)
        if token is None:
            raise HTTPException(404, "Token未找到")
        message = "Token 已启用" if token.isEnabled else "Token 已禁用"
    return {"message": message}


@router.put("/tokens/{tokenId}", response_model=ControlActionResponse, summary="更新Token信息")
async def update_token(
    tokenId: int,
    payload: ControlApiTokenUpdate
) -> dict[str, str]:
    """更新指定 Token 的名称、限额和有效期。"""
    db = get_database_service()
    async with db.transaction():
        updated = await db.api_token.update(
            token_id=tokenId,
            name=payload.name,
            daily_call_limit=payload.dailyCallLimit,
            validity_period=payload.validityPeriod
        )
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    return {"message": "Token信息更新成功"}


@router.post("/tokens/{tokenId}/reset", response_model=ControlActionResponse, summary="重置Token调用次数")
async def reset_token_counter(
    tokenId: int
) -> dict[str, str]:
    """重置指定 Token 的今日调用次数。"""
    db = get_database_service()
    async with db.transaction():
        reset_ok = await db.api_token.reset_daily_count(tokenId)
    if not reset_ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    return {"message": "Token调用次数已重置为0"}


@router.delete("/tokens/{tokenId}", response_model=ControlActionResponse, summary="删除Token")
async def delete_token(tokenId: int) -> dict[str, str]:
    """删除指定的 API Token。"""
    db = get_database_service()
    async with db.transaction():
        deleted = await db.api_token.delete(tokenId)
    if not deleted:
        raise HTTPException(404, "Token未找到")
    return {"message": "Token 删除成功"}
