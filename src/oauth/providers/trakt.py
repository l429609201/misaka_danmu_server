"""Trakt Worker OAuth 协议适配，用户身份以平台验证结果为准。"""

import json
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from src.core.timezone import get_now
from src.oauth.base import OAuthProvider
from src.services.config_service import get_config_service
from src.services.service_container import get_database_service

WORKER_URL = "https://danmu-api.misaka10876.top"


class TraktOAuthProvider(OAuthProvider):
    """通过 Worker 发起和刷新授权，直接校验 Trakt 用户身份。"""

    name = "trakt"
    refresh_days = 7

    async def _proxy(self) -> str | None:
        """读取平台代理设置，保留 Trakt 原有网络代理支持。"""
        config = get_config_service()
        mode = await config.get("proxyMode", "none")
        if mode == "none" and str(await config.get("proxyEnabled", "false")).lower() == "true":
            mode = "http_socks"
        if mode != "http_socks":
            return None
        db = get_database_service()
        async with db.transaction():
            source_settings = await db.metadata_source.get_all_metadata_source_settings()
        if any(item.get("providerName") == self.name and item.get("useProxy") for item in source_settings):
            return await config.get("proxyUrl", "") or None
        return None

    async def authorize_url(self, redirect_uri: str, state: str) -> str:
        """将本地 state 放入 Worker 回调地址，不依赖额外参数透传。"""
        parsed = urlsplit(redirect_uri)
        query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "state"]
        callback = urlunsplit(parsed._replace(query=urlencode([*query, ("state", state)])))
        return WORKER_URL + "/oauth/login?" + urlencode({"provider": self.name, "redirect_uri": callback})

    async def exchange(self, payload: dict[str, Any]) -> dict[str, Any]:
        """校验回调 token，忽略客户端提交的用户名称和 ID。"""
        access = payload.get("accessToken") or payload.get("access_token")
        client_id = payload.get("clientId") or payload.get("client_id")
        if not isinstance(access, str) or not access or not isinstance(client_id, str) or not client_id:
            raise ValueError("Trakt 回调缺少 token 或 client ID")
        async with httpx.AsyncClient(timeout=30, proxy=await self._proxy()) as client:
            response = await client.get("https://api.trakt.tv/users/settings", headers={
                "Authorization": f"Bearer {access}", "trakt-api-key": client_id, "trakt-api-version": "2"})
            response.raise_for_status()
            user = response.json()["user"]
        expires = payload.get("expiresIn") or payload.get("expires_in")
        if expires is None or int(expires) <= 0:
            raise ValueError("Trakt 回调缺少有效的令牌期限")
        extra = {"clientId": client_id, "redirectUri": payload["redirect_uri"]}
        return {"accessToken": access, "refreshToken": payload.get("refreshToken") or payload.get("refresh_token"),
                "expiresAt": get_now() + timedelta(seconds=int(expires)),
                "providerUserId": str(user.get("ids", {}).get("slug") or user.get("username", "")),
                "providerUsername": user.get("username", ""), "extraData": json.dumps(extra)}

    async def refresh(self, credentials: dict[str, Any]) -> dict[str, Any]:
        """通过固定 Worker 端点轮换凭证，不接受任意远端 URL。"""
        async with httpx.AsyncClient(timeout=30, proxy=await self._proxy()) as client:
            response = await client.post(WORKER_URL + "/oauth/refresh", json={
                "provider": self.name, "refresh_token": credentials["refreshToken"]})
            response.raise_for_status()
            token = response.json()
        return {"accessToken": token["access_token"],
                "refreshToken": token.get("refresh_token") or credentials["refreshToken"],
                "expiresAt": get_now() + timedelta(seconds=int(token.get("expires_in", 7776000)))}
