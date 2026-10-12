"""HTTP 鉴权适配：业务委托认证服务，不直接持有数据库连接。"""

import logging
from typing import Any, Optional, Tuple

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from src.schemas import User
from src.services import auth_service
from src.services.config_service import get_config_service
from src.utils.auth.ip import resolve_client_ip

logger = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/ui/auth/token")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/ui/auth/token", auto_error=False)


def _credentials_error() -> HTTPException:
    """生成统一的认证拒绝响应。"""
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                         detail="Could not validate credentials",
                         headers={"WWW-Authenticate": "Bearer"})


async def get_real_client_ip(request: Request, config_service: Any) -> str:
    """读取可信代理配置，将请求地址交给纯函数解析。"""
    try:
        trusted = await config_service.get("trustedProxies", "")
    except Exception as error:
        logger.error("读取可信代理配置失败", exc_info=True)
        raise HTTPException(status_code=503, detail="Authentication service unavailable") from error
    # 缺少直连地址时不假定请求来自本机，以免意外命中本机白名单。
    return resolve_client_ip(request.client.host if request.client else "", request.headers, trusted or "")


async def authenticate_token(token: str) -> Tuple[User, Optional[str]]:
    """将服务层认证错误转换为 HTTP 响应。"""
    try:
        return await auth_service.get_user_from_token(token)
    except auth_service.AuthenticationError as error:
        raise _credentials_error() from error
    except auth_service.AuthUnavailableError as error:
        logger.error("认证服务不可用：%s", error, exc_info=True)
        raise HTTPException(status_code=503, detail="Authentication service unavailable") from error


async def authenticate_whitelisted_request(request: Request) -> Optional[Tuple[User, str]]:
    """检查无令牌请求的白名单，返回已提交的有效会话。"""
    try:
        client_ip = await get_real_client_ip(request, get_config_service())
        return await auth_service.check_ip_whitelist(client_ip, request.headers.get("user-agent", ""))
    except auth_service.AuthUnavailableError as error:
        logger.error("认证服务不可用：%s", error, exc_info=True)
        raise HTTPException(status_code=503, detail="Authentication service unavailable") from error


async def get_current_user_with_jti(
    request: Request, token: Optional[str] = Depends(oauth2_scheme_optional),
) -> Tuple[User, Optional[str]]:
    """提供令牌时必须校验；仅无令牌时允许白名单免登录。"""
    if token:
        return await authenticate_token(token)
    result = await authenticate_whitelisted_request(request)
    if result:
        return result
    raise _credentials_error()


async def get_current_user(
    request: Request, token: Optional[str] = Depends(oauth2_scheme_optional),
) -> User:
    """认证用户，不通过请求级 Depends 长时间占用数据库连接。"""
    user, _ = await get_current_user_with_jti(request, token)
    return user


async def auto_login(request: Request) -> dict:
    """执行白名单自动登录，持久化失败不得返回成功令牌。"""
    try:
        client_ip = await get_real_client_ip(request, get_config_service())
        return await auth_service.auto_login(client_ip, request.headers.get("user-agent", ""))
    except auth_service.AuthenticationError as error:
        raise _credentials_error() from error
    except auth_service.AuthUnavailableError as error:
        logger.error("认证服务不可用：%s", error, exc_info=True)
        raise HTTPException(status_code=503, detail="Authentication service unavailable") from error
