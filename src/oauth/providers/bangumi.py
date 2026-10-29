"""Bangumi OAuth 协议适配；不承担授权生命周期或数据库写入。"""

import time
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from src.core.timezone import get_now
from src.oauth.base import OAuthProvider
from src.services.config_service import get_config_service
from src.services.service_container import get_database_service


class BangumiOAuthProvider(OAuthProvider):
    """适配 Bangumi 授权码、用户资料和刷新协议。"""

    name = "bangumi"

    async def _config(self) -> dict[str, Any]:
        config = get_config_service()
        result = {"client_id": await config.get("bangumiClientId", ""),
                  "client_secret": await config.get("bangumiClientSecret", ""), "proxy": None}
        if not result["client_id"] or not result["client_secret"]:
            raise ValueError("Bangumi App ID 或 Secret 未配置")
        mode = await config.get("proxyMode", "none")
        if mode == "none" and str(await config.get("proxyEnabled", "false")).lower() == "true":
            mode = "http_socks"
        if mode == "http_socks":
            db = get_database_service()
            async with db.transaction():
                settings = await db.metadata_source.get_all_metadata_source_settings()
            if any(item.get("providerName") == self.name and item.get("useProxy") for item in settings):
                result["proxy"] = await config.get("proxyUrl", "") or None
        return result

    async def authorize_url(self, redirect_uri: str, state: str) -> str:
        """生成平台授权地址，state 由通用服务持有。"""
        config = await self._config()
        return "https://bgm.tv/oauth/authorize?" + urlencode({"client_id": config["client_id"],
            "response_type": "code", "redirect_uri": redirect_uri, "state": state})

    async def exchange(self, payload: dict[str, Any]) -> dict[str, Any]:
        """交换授权码并从平台读取可信用户资料。"""
        config = await self._config()
        code = payload.get("code")
        if not isinstance(code, str) or not code:
            raise ValueError("缺少授权码")
        async with httpx.AsyncClient(timeout=30, proxy=config["proxy"]) as client:
            response = await client.post("https://bgm.tv/oauth/access_token", data={
                "grant_type": "authorization_code", "client_id": config["client_id"],
                "client_secret": config["client_secret"], "code": code, "redirect_uri": payload["redirect_uri"]})
            response.raise_for_status()
            token = response.json()
            response = await client.get("https://api.bgm.tv/v0/me", headers={"Authorization": f"Bearer {token['access_token']}"})
            response.raise_for_status()
            user = response.json()
        avatar = user.get("avatar", {}).get("large")
        if avatar and avatar.startswith("//"):
            avatar = "https:" + avatar
        return {"accessToken": token["access_token"], "refreshToken": token.get("refresh_token"),
            "expiresAt": get_now() + timedelta(seconds=int(token.get("expires_in", 0))),
            "redirectUri": payload["redirect_uri"], "bangumiUserId": user.get("id"),
            "username": user.get("username"), "nickname": user.get("nickname"),
            "sign": user.get("sign", ""), "avatarUrl": avatar}

    async def remaining_seconds(self, credentials: dict[str, Any]) -> int | None:
        """保留远端 token_status 有效期检查，失败时回退本地时间。"""
        try:
            config = await self._config()
            async with httpx.AsyncClient(timeout=15, proxy=config["proxy"]) as client:
                response = await client.post("https://bgm.tv/oauth/token_status", data={"access_token": credentials["accessToken"]})
                response.raise_for_status()
                expires = response.json().get("expires")
                return int(expires) - int(time.time()) if expires is not None else None
        except Exception:
            return None

    async def refresh(self, credentials: dict[str, Any]) -> dict[str, Any]:
        """使用授权时的回调地址轮换凭证。"""
        config = await self._config()
        if not credentials.get("redirectUri"):
            raise ValueError("授权记录缺少回调地址，请重新授权")
        async with httpx.AsyncClient(timeout=30, proxy=config["proxy"]) as client:
            response = await client.post("https://bgm.tv/oauth/access_token", data={
                "grant_type": "refresh_token", "client_id": config["client_id"],
                "client_secret": config["client_secret"], "refresh_token": credentials["refreshToken"],
                "redirect_uri": credentials["redirectUri"]})
            response.raise_for_status()
            token = response.json()
        return {"accessToken": token["access_token"], "refreshToken": token.get("refresh_token") or credentials["refreshToken"],
                "expiresAt": get_now() + timedelta(seconds=int(token.get("expires_in", 604800)))}
