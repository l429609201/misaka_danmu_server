"""
OAuthRepository - 第三方授权数据访问层

覆盖三张授权表：OauthState（一次性状态令牌）、BangumiAuth（Bangumi 专用授权）、
OauthCredential（通用平台凭证）。均自 crud/user.py 迁入。
"""

import logging
import secrets
from datetime import timedelta
from typing import Optional, Dict, Any
from sqlalchemy import select, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..orm_models import OauthState, BangumiAuth, OauthCredential
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


class OAuthRepository:
    """第三方授权仓储（state / bangumi / 通用凭证）"""

    def __init__(self, session: AsyncSession):
        self._session = session

    # ==================== OAuth State ====================

    async def create_state(self, user_id: int, ttl_minutes: int = 10) -> str:
        """生成一次性 OAuth 状态令牌。

        Args:
            user_id: 发起授权的用户 ID
            ttl_minutes: 令牌有效期（分钟）

        Returns:
            新生成的 state 字符串
        """
        state = secrets.token_urlsafe(32)
        self._session.add(OauthState(
            stateKey=state,
            userId=user_id,
            expiresAt=get_now() + timedelta(minutes=ttl_minutes),
        ))
        await self._session.flush()
        return state

    async def consume_state(self, state: str) -> Optional[int]:
        """校验并消费 state 令牌（一次性，命中即删）。

        Args:
            state: 待校验的 state 字符串

        Returns:
            令牌对应的 user_id；令牌无效或已过期时返回 None
        """
        stmt = select(OauthState).where(
            and_(OauthState.stateKey == state, OauthState.expiresAt > get_now())
        )
        state_obj = (await self._session.execute(stmt)).scalar_one_or_none()
        if not state_obj:
            return None

        user_id = state_obj.userId
        await self._session.delete(state_obj)
        await self._session.flush()
        return user_id

    async def clear_expired_states(self) -> int:
        """清理所有已过期的 OAuth 状态令牌。

        Returns:
            清理的记录数
        """
        result = await self._session.execute(
            delete(OauthState).where(OauthState.expiresAt <= get_now())
        )
        await self._session.flush()
        return int(result.rowcount or 0)

    # ==================== Bangumi 授权 ====================

    async def get_bangumi_auth(self, user_id: int) -> Optional[BangumiAuth]:
        """获取 Bangumi 授权记录原始对象（含 token）。"""
        return await self._session.get(BangumiAuth, user_id)

    async def save_bangumi_auth(self, user_id: int, auth_data: Dict[str, Any]) -> BangumiAuth:
        """写入或更新 Bangumi 授权信息。

        why: expiresAt 统一剥离时区信息后落库，与既有存量数据保持一致，
             避免 naive/aware 混存导致比较报错。

        Args:
            user_id: 用户 ID
            auth_data: 含 bangumiUserId/nickname/avatarUrl/accessToken 等字段

        Returns:
            写入后的授权记录
        """
        expires_at = auth_data.get("expiresAt")
        if expires_at is not None and getattr(expires_at, "tzinfo", None):
            expires_at = expires_at.replace(tzinfo=None)

        auth = await self._session.get(BangumiAuth, user_id)
        if auth:
            auth.bangumiUserId = auth_data.get("bangumiUserId")
            auth.nickname = auth_data.get("nickname")
            auth.avatarUrl = auth_data.get("avatarUrl")
            auth.accessToken = auth_data.get("accessToken")
            auth.refreshToken = auth_data.get("refreshToken")
            auth.expiresAt = expires_at
            auth.authorizedAt = get_now()
        else:
            payload = dict(auth_data)
            payload["expiresAt"] = expires_at
            payload["userId"] = user_id
            payload["authorizedAt"] = get_now()
            auth = BangumiAuth(**payload)
            self._session.add(auth)

        await self._session.flush()
        return auth

    async def delete_bangumi_auth(self, user_id: int) -> bool:
        """删除 Bangumi 授权记录，返回是否实际删除。"""
        auth = await self._session.get(BangumiAuth, user_id)
        if not auth:
            return False

        await self._session.delete(auth)
        await self._session.flush()
        return True

    # ==================== 通用平台凭证 ====================

    async def get_credential(self, user_id: int, provider: str) -> Optional[OauthCredential]:
        """获取指定平台的完整凭证对象（含 token），供 API 调用方使用。"""
        stmt = select(OauthCredential).where(
            and_(OauthCredential.userId == user_id, OauthCredential.provider == provider)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_credential_status(self, user_id: int, provider: str) -> Dict[str, Any]:
        """获取指定平台的授权状态视图（不含 token），供前端展示。

        why: 与 get_credential 区分开——本方法刻意剔除 accessToken/refreshToken，
        避免授权状态接口把明文 token 回传到前端。

        Args:
            user_id: 用户 ID
            provider: 平台标识，如 "trakt"

        Returns:
            含 isAuthenticated 标志的状态字典；未授权时仅返回标志与 provider
        """
        cred = await self.get_credential(user_id, provider)
        if not cred:
            return {"isAuthenticated": False, "provider": provider}

        return {
            "isAuthenticated": True,
            "provider": cred.provider,
            "providerUsername": cred.providerUsername,
            "providerUserId": cred.providerUserId,
            "expiresAt": cred.expiresAt,
            "authorizedAt": cred.authorizedAt,
        }

    async def save_credential(
        self, user_id: int, provider: str, data: Dict[str, Any]
    ) -> OauthCredential:
        """写入或增量更新指定平台的凭证。

        Args:
            user_id: 用户 ID
            provider: 平台标识，如 "trakt"
            data: 待写入字段；更新时仅覆盖模型上已存在的属性

        Returns:
            写入后的凭证记录
        """
        cred = await self.get_credential(user_id, provider)
        if cred:
            for key, value in data.items():
                if hasattr(cred, key):
                    setattr(cred, key, value)
        else:
            payload = dict(data)
            payload["userId"] = user_id
            payload["provider"] = provider
            cred = OauthCredential(**payload)
            self._session.add(cred)

        await self._session.flush()
        return cred

    async def delete_credential(self, user_id: int, provider: str) -> bool:
        """删除指定平台的凭证，返回是否实际删除。"""
        cred = await self.get_credential(user_id, provider)
        if not cred:
            return False

        await self._session.delete(cred)
        await self._session.flush()
        return True
