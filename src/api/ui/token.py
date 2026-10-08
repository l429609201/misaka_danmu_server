"""
Token相关的API端点
"""
import logging
import re
import secrets
import string
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from src.schemas.auth import User
from src.schemas.control.token import ApiTokenCreate, ApiTokenInfo, TokenAccessLog
from src.schemas.ui_models import ApiTokenUpdate
from src.services.service_container import get_database_service
from src.utils.auth import security

logger = logging.getLogger(__name__)

router = APIRouter()

# Token 字符合法性正则：仅允许字母、数字、下划线、短横线
_TOKEN_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')


def _validate_custom_token(token_str: str) -> str:
    """校验自定义 Token 字符串的合法性"""
    token_str = token_str.strip()
    if len(token_str) < 5 or len(token_str) > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="自定义 Token 长度须在 5~100 字符之间"
        )
    if not _TOKEN_PATTERN.match(token_str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="自定义 Token 仅允许字母、数字、下划线和短横线"
        )
    return token_str

@router.get("/tokens", response_model=List[ApiTokenInfo], summary="获取所有弹幕API Token")
async def get_all_api_tokens(
    current_user: User = Depends(security.get_current_user),
) -> List[ApiTokenInfo]:
    """获取所有为第三方播放器创建的 API Token。"""
    db = get_database_service()
    async with db.transaction():
        tokens = await db.api_token.get_all_as_dict()
        return [ApiTokenInfo.model_validate(t) for t in tokens]


@router.post("/tokens", response_model=ApiTokenInfo, status_code=status.HTTP_201_CREATED, summary="创建一个新的API Token")
async def create_new_api_token(
    token_data: ApiTokenCreate,
    current_user: User = Depends(security.get_current_user),
) -> ApiTokenInfo:
    """创建一个新的 API Token，支持自定义 Token 字符串或自动生成。"""
    if token_data.customToken:
        new_token_str = _validate_custom_token(token_data.customToken)
    else:
        alphabet = string.ascii_letters + string.digits
        new_token_str = ''.join(secrets.choice(alphabet) for _ in range(20))
    db = get_database_service()
    try:
        async with db.transaction():
            if await db.api_token.get_by_token_str(new_token_str):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Token 已被使用，请换一个。")
            new_token = await db.api_token.create(
                name=token_data.name, token=new_token_str,
                validity_period=token_data.validityPeriod,
                daily_call_limit=token_data.dailyCallLimit,
            )
            # 仓储返回 ORM 对象，在事务结束前转换，避免脱离会话后读取属性。
            return ApiTokenInfo.model_validate(new_token, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除一个API Token")
async def delete_api_token(
    token_id: int,
    current_user: User = Depends(security.get_current_user),
) -> None:
    """根据ID删除一个 API Token。"""
    db = get_database_service()
    async with db.transaction():
        if not await db.api_token.delete(token_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")


@router.put("/tokens/{token_id}/toggle", status_code=status.HTTP_204_NO_CONTENT, summary="切换API Token的启用状态")
async def toggle_api_token_status(
    token_id: int,
    current_user: User = Depends(security.get_current_user),
) -> None:
    """切换指定 API Token 的启用/禁用状态。"""
    db = get_database_service()
    async with db.transaction():
        if await db.api_token.toggle_enable(token_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")


@router.put("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT, summary="更新API Token信息")
async def update_api_token(
    token_id: int,
    payload: ApiTokenUpdate,
    current_user: User = Depends(security.get_current_user),
) -> None:
    """更新指定API Token的名称、每日调用上限、有效期和Token字符串。"""
    new_token_str = _validate_custom_token(payload.customToken) if payload.customToken else None
    db = get_database_service()
    async with db.transaction():
        if new_token_str:
            existing = await db.api_token.get_by_token_str(new_token_str)
            # 新仓储返回对象而非字典，排除当前条目后判断字符串是否占用。
            if existing is not None and existing.id != token_id:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Token 已被其他条目使用，请换一个。")
        updated = await db.api_token.update(
            token_id=token_id, name=payload.name,
            daily_call_limit=payload.dailyCallLimit,
            validity_period=payload.validityPeriod,
            token_str=new_token_str,
        )
        if updated is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    logger.info(f"用户 '{current_user.username}' 更新了 Token (ID: {token_id}) 的信息。")


@router.post("/tokens/{token_id}/reset", status_code=status.HTTP_204_NO_CONTENT, summary="重置API Token的调用次数")
async def reset_api_token_counter(
    token_id: int,
    current_user: User = Depends(security.get_current_user),
) -> None:
    """将指定API Token的今日调用次数重置为0。"""
    db = get_database_service()
    async with db.transaction():
        if not await db.api_token.reset_daily_count(token_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    logger.info(f"用户 '{current_user.username}' 重置了 Token (ID: {token_id}) 的调用次数。")


@router.get("/tokens/{tokenId}/logs", response_model=List[TokenAccessLog], summary="获取Token的访问日志")
async def get_token_logs(
    tokenId: int,
    current_user: User = Depends(security.get_current_user),
) -> List[TokenAccessLog]:
    """获取指定 Token 最近的访问日志。"""
    db = get_database_service()
    async with db.transaction():
        logs = await db.token_log.get_by_token_id(tokenId)
        return [TokenAccessLog.model_validate(log, from_attributes=True) for log in logs]



