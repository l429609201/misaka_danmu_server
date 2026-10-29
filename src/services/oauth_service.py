"""通用 OAuth 生命周期服务，平台协议由启动时注册的处理器提供。"""

import asyncio
import hashlib
import logging
import secrets
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from jose import JWTError, jwt

from src.core.config import settings
from src.core.timezone import get_now
from src.oauth.base import OAuthProvider
from src.services.config_service import get_config_service
from src.services.service_container import get_database_service

logger = logging.getLogger(__name__)


class OAuthService:
    """统一管理平台注册、一次性授权状态与可撤销凭证。"""

    def __init__(self) -> None:
        self._providers: dict[str, OAuthProvider] = {}
        self._refresh_locks: dict[tuple[str, int], tuple[asyncio.Lock, int]] = {}

    @asynccontextmanager
    async def _refresh_guard(self, provider: str, user_id: int) -> AsyncIterator[None]:
        """计入持有者和等待者，最后使用者退出才删除锁，避免键无限增长。"""
        key = (provider, user_id)
        lock, users = self._refresh_locks.get(key, (asyncio.Lock(), 0))
        self._refresh_locks[key] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            _, users = self._refresh_locks[key]
            if users == 1:
                del self._refresh_locks[key]
            else:
                self._refresh_locks[key] = (lock, users - 1)

    def register_provider(self, provider: str, handler: OAuthProvider) -> None:
        """显式注册平台，拒绝重复覆盖处理器或错误的平台标识。"""
        if provider != handler.name:
            raise ValueError("OAuth 平台与处理器标识不一致")
        if provider in self._providers:
            raise ValueError(f"OAuth 平台已注册：{provider}")
        self._providers[provider] = handler

    def get_provider(self, name: str) -> OAuthProvider:
        """取得允许的平台，不支持的平台不能构造任意远端请求。"""
        if name not in self._providers:
            raise ValueError(f"OAuth 平台未注册：{name}")
        return self._providers[name]

    async def _state_secret(self) -> str:
        return await get_config_service().get("jwtSecretKey", settings.jwt.secret_key)

    async def authorize(self, provider: str, user_id: int, redirect_uri: str) -> dict[str, str]:
        """绑定平台、用户、回调地址并提交一次性授权状态。"""
        handler = self.get_provider(provider)
        parsed = urlsplit(redirect_uri)
        if parsed.scheme not in ("https", "http") or not parsed.netloc or parsed.username or parsed.fragment:
            raise ValueError("OAuth 回调地址无效")
        secret = await self._state_secret()
        nonce = secrets.token_urlsafe(32)
        state = jwt.encode({"purpose": "oauth-state", "provider": provider, "user": user_id, "nonce": nonce,
            "redirect": hashlib.sha256(redirect_uri.encode()).hexdigest(), "exp": get_now() + timedelta(minutes=10)},
            secret, algorithm=settings.jwt.algorithm)
        url = await handler.authorize_url(redirect_uri, state)
        db = get_database_service()
        async with db.transaction():
            await db.oauth.create_state(user_id, provider=provider, state=nonce)
        return {"url": url, "state": state}

    async def exchange(self, provider: str, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """验证本地 state 后执行平台交换，事务提交后才返回成功。"""
        handler = self.get_provider(provider)
        try:
            state = payload.get("state")
            redirect_uri = payload.get("redirect_uri")
            if not isinstance(state, str) or not isinstance(redirect_uri, str):
                raise ValueError("缺少授权状态或回调地址")
            binding = jwt.decode(state, await self._state_secret(), algorithms=[settings.jwt.algorithm])
            if (binding.get("purpose") != "oauth-state" or binding.get("provider") != provider
                or binding.get("user") != user_id or binding.get("redirect") != hashlib.sha256(redirect_uri.encode()).hexdigest()):
                raise ValueError("授权状态不匹配")
            nonce = binding.get("nonce")
            if not isinstance(nonce, str) or not nonce:
                raise ValueError("授权状态无效")
            db = get_database_service()
            async with db.transaction():
                consumed = await db.oauth.consume_state(nonce, provider=provider, user_id=user_id)
            if consumed != user_id:
                raise ValueError("授权状态已过期或已使用")
            data = await handler.exchange(payload)
            data["authorizedAt"] = get_now()
            async with db.transaction():
                await db.oauth.save_provider_credential(user_id, provider, data)
            return {"success": True, "message": "授权成功"}
        except (ValueError, JWTError):
            return {"success": False, "message": "授权状态或凭据无效，请重新授权"}
        except Exception as error:
            logger.error("OAuth 授权交换失败：平台=%s，错误类型=%s", provider, type(error).__name__)
            return {"success": False, "message": "授权交换失败，请稍后重试"}

    async def credentials(self, provider: str, user_id: int, auto_refresh: bool = True) -> dict[str, Any] | None:
        """返回仅供服务调用的凭证快照，公开状态接口不返回敏感字段。"""
        handler = self.get_provider(provider)
        db = get_database_service()
        async with db.transaction():
            data = await db.oauth.credential_snapshot(user_id, provider)
        if auto_refresh and data and data.get("refreshToken"):
            expires = data.get("expiresAt")
            if not expires or expires <= get_now() + timedelta(days=handler.refresh_days):
                if (await self.refresh(provider, user_id))["success"]:
                    async with db.transaction():
                        data = await db.oauth.credential_snapshot(user_id, provider)
        if data and data.get("expiresAt") and data["expiresAt"] <= get_now():
            return None
        return data

    async def status(self, provider: str, user_id: int, auto_refresh: bool = False) -> dict[str, Any]:
        """返回无凭证的公开授权状态，保留过期记录供界面说明。"""
        self.get_provider(provider)
        if auto_refresh:
            await self.credentials(provider, user_id, auto_refresh=True)
        db = get_database_service()
        async with db.transaction():
            data = await db.oauth.credential_snapshot(user_id, provider)
        if not data:
            return {"isAuthenticated": False}
        expires = data.get("expiresAt")
        expired = bool(expires and expires <= get_now())
        result = {key: value for key, value in data.items() if key in {
            "bangumiUserId", "nickname", "username", "sign", "avatarUrl", "providerUserId", "providerUsername", "expiresAt", "authorizedAt"}}
        result.update(isAuthenticated=not expired, isExpired=expired, daysLeft=(expires-get_now()).days if expires else 0)
        return result

    async def revoke(self, provider: str, user_id: int) -> dict[str, Any]:
        """提交授权注销，刷新中的旧快照不能恢复此记录。"""
        self.get_provider(provider)
        db = get_database_service()
        async with db.transaction():
            await db.oauth.revoke_provider_credential(user_id, provider)
        return {"success": True, "message": "授权已注销"}

    async def refresh(self, provider: str, user_id: int) -> dict[str, Any]:
        """进程内按用户串行轮换，仓储条件写防止覆盖重新授权。"""
        handler = self.get_provider(provider)
        async with self._refresh_guard(provider, user_id):
            try:
                db = get_database_service()
                async with db.transaction():
                    data = await db.oauth.credential_snapshot(user_id, provider)
                if not data or not data.get("refreshToken"):
                    return {"success": False, "message": "缺少刷新凭证，请重新授权"}
                updated = await handler.refresh(data)
                db = get_database_service()
                async with db.transaction():
                    saved = await db.oauth.rotate_provider_credential(user_id, provider, data["refreshToken"], updated)
                return {"success": bool(saved), "message": "令牌续期成功" if saved else "授权状态已变更，请重新查询"}
            except Exception as error:
                logger.error("OAuth 刷新失败：平台=%s，错误类型=%s", provider, type(error).__name__)
                return {"success": False, "message": "令牌续期失败，请重新授权"}

    async def count_valid(self, provider: str) -> int:
        """统计已注册平台有效授权。"""
        self.get_provider(provider)
        db = get_database_service()
        async with db.transaction():
            return await db.oauth.count_provider_credentials(provider)

    async def refresh_due(self, provider: str, threshold_days: int = 3) -> dict[str, int]:
        """统一轮询临期授权，不在网络请求期间持有数据库连接。"""
        handler = self.get_provider(provider)
        db = get_database_service()
        async with db.transaction():
            users = await db.oauth.list_provider_refresh_users(provider)
        counts = {"refreshed": 0, "skipped": 0, "failed": 0}
        for user_id in users:
            async with db.transaction():
                data = await db.oauth.credential_snapshot(user_id, provider)
            if not data:
                counts["skipped"] += 1
                continue
            expires = data.get("expiresAt")
            remaining = await handler.remaining_seconds(data)
            due = remaining <= threshold_days * 86400 if remaining is not None else (
                not expires or expires <= get_now() + timedelta(days=threshold_days))
            if not due:
                counts["skipped"] += 1
            else:
                result = await self.refresh(provider, user_id)
                counts["refreshed" if result["success"] else "failed"] += 1
        return counts


_oauth_service = OAuthService()


def get_oauth_service() -> OAuthService:
    """取得通用实例；平台注册只能由组合根显式执行。"""
    return _oauth_service
