"""
OAuthRepository - 第三方授权数据访问层

覆盖三张授权表：OauthState（一次性状态令牌）、BangumiAuth（Bangumi 专用授权）、
OauthCredential（通用平台凭证）。均自 crud/user.py 迁入。
"""

import logging
import secrets
from datetime import timedelta
from typing import Optional, Dict, Any
from sqlalchemy import select, and_, delete, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..orm_models import OauthState, BangumiAuth, OauthCredential
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


class OAuthRepository:
    """第三方授权仓储（state / bangumi / 通用凭证）"""

    def __init__(self, session: AsyncSession):
        self._session = session

    # ==================== OAuth State ====================

    async def create_state(self, user_id: int, ttl_minutes: int = 10, *, provider: str = "bangumi", state: Optional[str] = None) -> str:
        """生成一次性 OAuth 状态令牌。

        Args:
            user_id: 发起授权的用户 ID
            ttl_minutes: 令牌有效期（分钟）

        Returns:
            新生成的 state 字符串
        """
        state = state or secrets.token_urlsafe(32)
        self._session.add(OauthState(
            stateKey=state,
            userId=user_id,
            provider=provider,
            expiresAt=get_now() + timedelta(minutes=ttl_minutes),
        ))
        await self._session.flush()
        return state

    async def consume_state(self, state: str, *, provider: str = "bangumi", user_id: Optional[int] = None) -> Optional[int]:
        """校验并消费 state 令牌（一次性，命中即删）。

        Args:
            state: 待校验的 state 字符串

        Returns:
            令牌对应的 user_id；令牌无效或已过期时返回 None
        """
        stmt = select(OauthState).where(
            and_(OauthState.stateKey == state, OauthState.expiresAt > get_now())
        ).with_for_update()
        # 同一状态令牌的并发消费串行化，第二个请求不能重复取得授权。
        state_obj = (await self._session.execute(stmt)).scalar_one_or_none()
        if not state_obj or state_obj.provider != provider or (user_id is not None and state_obj.userId != user_id):
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
            # 重新授权同时更新资料与回调地址，避免沿用旧 OAuth 信息。
            for field in ("username", "sign", "redirectUri"):
                if field in auth_data:
                    setattr(auth, field, auth_data[field])
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

    async def rotate_bangumi_token(self, user_id: int, previous_refresh_token: str, auth_data: Dict[str, Any]) -> bool:
        """仅更新仍使用旧刷新凭证的现有记录，不复活注销或覆盖重新授权。"""
        result = await self._session.execute(
            update(BangumiAuth).where(
                BangumiAuth.userId == user_id,
                BangumiAuth.refreshToken == previous_refresh_token,
            ).values(
                accessToken=auth_data["accessToken"],
                refreshToken=auth_data["refreshToken"],
                expiresAt=auth_data["expiresAt"],
            )
        )
        await self._session.flush()
        return result.rowcount == 1

    async def list_refreshable_bangumi_auth(self) -> list[BangumiAuth]:
        """列出存在刷新令牌的授权记录，由服务层决定刷新策略。"""
        result = await self._session.execute(
            select(BangumiAuth).where(BangumiAuth.refreshToken.is_not(None), BangumiAuth.refreshToken != "")
        )
        return list(result.scalars().all())

    async def count_valid_bangumi_auth(self) -> int:
        """统计当前尚未过期的 Bangumi 授权。"""
        result = await self._session.execute(
            select(func.count()).select_from(BangumiAuth).where(BangumiAuth.expiresAt > get_now())
        )
        return int(result.scalar_one())

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

    async def credential_snapshot(self, user_id: int, provider: str) -> Optional[Dict[str, Any]]:
        """在仓储边界转换现存授权表，调用方不持有 ORM 实体。"""
        record = await self.get_bangumi_auth(user_id) if provider == "bangumi" else await self.get_credential(user_id, provider)
        if record is None:
            return None
        fields = ("userId", "accessToken", "refreshToken", "expiresAt", "authorizedAt")
        fields += (("bangumiUserId", "nickname", "username", "sign", "avatarUrl", "redirectUri")
                   if provider == "bangumi" else ("providerUserId", "providerUsername", "extraData"))
        return {field: getattr(record, field, None) for field in fields}

    async def save_provider_credential(self, user_id: int, provider: str, data: Dict[str, Any]) -> None:
        """通过一个仓储契约保存平台授权，保持既有存储无需迁移。"""
        if provider == "bangumi":
            await self.save_bangumi_auth(user_id, data)
        else:
            await self.save_credential(user_id, provider, data)

    async def revoke_provider_credential(self, user_id: int, provider: str) -> bool:
        """删除指定平台的授权记录。"""
        return await self.delete_bangumi_auth(user_id) if provider == "bangumi" else await self.delete_credential(user_id, provider)

    async def rotate_provider_credential(self, user_id: int, provider: str, previous_refresh_token: str, data: Dict[str, Any]) -> bool:
        """条件更新凭证，刷新结果不能复活注销或覆盖重新授权。"""
        if provider == "bangumi":
            return await self.rotate_bangumi_token(user_id, previous_refresh_token, data)
        result = await self._session.execute(update(OauthCredential).where(
            OauthCredential.userId == user_id, OauthCredential.provider == provider,
            OauthCredential.refreshToken == previous_refresh_token,
        ).values(**data))
        await self._session.flush()
        return result.rowcount == 1

    async def count_provider_credentials(self, provider: str) -> int:
        """统计当前平台未过期的授权。"""
        if provider == "bangumi":
            return await self.count_valid_bangumi_auth()
        result = await self._session.execute(select(func.count()).select_from(OauthCredential).where(
            OauthCredential.provider == provider, OauthCredential.expiresAt > get_now()))
        return int(result.scalar_one())

    async def list_provider_refresh_users(self, provider: str) -> list[int]:
        """列出平台可刷新的用户 ID，不向服务暴露实体。"""
        model = BangumiAuth if provider == "bangumi" else OauthCredential
        statement = select(model.userId).where(model.refreshToken.is_not(None), model.refreshToken != "")
        if provider != "bangumi":
            statement = statement.where(OauthCredential.provider == provider)
        return list((await self._session.execute(statement)).scalars().all())

    async def delete_credential(self, user_id: int, provider: str) -> bool:
        """删除指定平台的凭证，返回是否实际删除。"""
        cred = await self.get_credential(user_id, provider)
        if not cred:
            return False

        await self._session.delete(cred)
        await self._session.flush()
        return True
