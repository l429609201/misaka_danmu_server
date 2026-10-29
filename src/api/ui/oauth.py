"""第三方 OAuth 通用 HTTP 接入，由注册服务分发平台协议。"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from pydantic import BaseModel

from src.api.auth import get_current_user
from src.schemas.auth import User
from src.services.oauth_service import get_oauth_service

router = APIRouter(prefix="/oauth")


class OAuthAuthorizeRequest(BaseModel):
    """发起授权时使用的前端回调地址。"""

    redirect_uri: str


def _registered_service(provider: str) -> Any:
    """未注册的平台明确返回 404，不隐式加载或猜测处理器。"""
    service = get_oauth_service()
    try:
        service.get_provider(provider)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="OAuth provider not registered") from error
    return service


@router.post("/{provider}/authorize")
async def authorize(provider: str, payload: OAuthAuthorizeRequest, user: User = Depends(get_current_user)) -> dict[str, Any]:
    """经统一服务创建授权状态与跳转地址。"""
    try:
        return await _registered_service(provider).authorize(provider, user.id, payload.redirect_uri)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/{provider}/exchange")
async def exchange(provider: str, payload: dict[str, Any], user: User = Depends(get_current_user)) -> dict[str, Any]:
    """消费授权状态，委托已注册处理器交换或验证平台凭证。"""
    return await _registered_service(provider).exchange(provider, user.id, payload)


@router.get("/{provider}/status")
async def auth_status(provider: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    """返回不包含访问令牌或刷新令牌的授权视图。"""
    return await _registered_service(provider).status(provider, user.id)


@router.post("/{provider}/refresh")
async def refresh(provider: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    """通过统一生命周期刷新当前用户授权。"""
    return await _registered_service(provider).refresh(provider, user.id)


@router.delete("/{provider}")
async def revoke(provider: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    """撤销本地持久化授权，不保留平台专属路由。"""
    await _registered_service(provider).revoke(provider, user.id)
    return {"success": True, "message": "已注销授权"}
