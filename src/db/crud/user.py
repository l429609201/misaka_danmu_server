"""
用户相关的CRUD操作
"""

import hashlib
import logging
import secrets
from datetime import timedelta
from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from ..orm_models import User, BangumiAuth, OauthState, OauthCredential
from src.core.timezone import get_now
from src.core import settings
from src.utils.otp import encrypt_otp_secret, decrypt_otp_secret
from .. import models

logger = logging.getLogger(__name__)


# --- User Management ---

async def get_user_by_id(session: AsyncSession, user_id: int) -> Optional[Dict[str, Any]]:
    """通过ID查找用户"""
    stmt = select(User.id, User.username).where(User.id == user_id)
    result = await session.execute(stmt)
    row = result.mappings().first()
    return dict(row) if row else None


async def get_user_by_username(session: AsyncSession, username: str) -> Optional[Dict[str, Any]]:
    """通过用户名查找用户"""
    stmt = select(User).where(User.username == username)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if user:
        # 透明解密 OTP Secret（兼容旧版明文数据）
        otp_secret = user.otpSecret
        if otp_secret:
            otp_secret = decrypt_otp_secret(otp_secret, settings.jwt.secret_key)
        return {
            "id": user.id,
            "username": user.username,
            "hashedPassword": user.hashedPassword,
            "token": user.token,
            "isOtp": user.isOtp,
            "otpSecret": otp_secret,
        }
    return None


async def create_user(session: AsyncSession, user: models.UserCreate):
    """创建新用户"""
    from src import security
    hashed_password = security.get_password_hash(user.password)
    new_user = User(
        username=user.username,
        hashedPassword=hashed_password,
        createdAt=get_now()
    )
    session.add(new_user)
    await session.commit()


async def update_user_password(session: AsyncSession, username: str, new_hashed_password: str):
    """更新用户的密码"""
    stmt = update(User).where(User.username == username).values(hashedPassword=new_hashed_password)
    await session.execute(stmt)
    await session.commit()


async def update_user_login_info(session: AsyncSession, username: str, token: str):
    """更新用户的最后登录时间和当前令牌（存储 SHA256 摘要而非明文 token）"""
    token_hash = hashlib.sha256(token.encode()).hexdigest()[:32]
    stmt = update(User).where(User.username == username).values(
        token=token_hash,
        tokenUpdate=get_now()
    )
    await session.execute(stmt)
    await session.commit()


async def enable_user_otp(session: AsyncSession, username: str, otp_secret: str):
    """为用户启用 TOTP 两步验证（加密存储 OTP Secret）"""
    encrypted_secret = encrypt_otp_secret(otp_secret, settings.jwt.secret_key)
    stmt = update(User).where(User.username == username).values(
        isOtp=True, otpSecret=encrypted_secret
    )
    await session.execute(stmt)
    await session.commit()


async def disable_user_otp(session: AsyncSession, username: str):
    """为用户关闭 TOTP 两步验证"""
    stmt = update(User).where(User.username == username).values(
        isOtp=False, otpSecret=None
    )
    await session.execute(stmt)
    await session.commit()


# --- OAuth State Management ---

async def create_oauth_state(session: AsyncSession, user_id: int) -> str:
    """创建OAuth状态令牌"""
    state = secrets.token_urlsafe(32)
    expires_at = get_now() + timedelta(minutes=10)
    new_state = OauthState(
        stateKey=state,
        userId=user_id,
        expiresAt=expires_at
    )
    session.add(new_state)
    await session.commit()
    return state


async def consume_oauth_state(session: AsyncSession, state: str) -> Optional[int]:
    """消费OAuth状态令牌(验证并删除)"""
    stmt = select(OauthState).where(
        OauthState.stateKey == state,
        OauthState.expiresAt > get_now()
    )
    result = await session.execute(stmt)
    state_obj = result.scalar_one_or_none()
    if state_obj:
        user_id = state_obj.userId
        await session.delete(state_obj)
        await session.commit()
        return user_id
    return None


# --- Bangumi Auth Management ---

async def get_bangumi_auth(session: AsyncSession, user_id: int) -> Dict[str, Any]:
    """
    获取用户的Bangumi授权状态。
    注意：此函数现在返回一个为UI定制的字典，而不是完整的认证对象。
    """
    auth = await session.get(BangumiAuth, user_id)
    if auth:
        return {
            "isAuthenticated": True,
            "nickname": auth.nickname,
            "avatarUrl": auth.avatarUrl,
            "bangumiUserId": auth.bangumiUserId,
            "authorizedAt": auth.authorizedAt,
            "expiresAt": auth.expiresAt,
        }
    return {"isAuthenticated": False}


async def save_bangumi_auth(session: AsyncSession, user_id: int, auth_data: Dict[str, Any]):
    """保存或更新Bangumi授权信息"""
    auth = await session.get(BangumiAuth, user_id)
    expires_at = auth_data.get('expiresAt')
    if expires_at and hasattr(expires_at, 'tzinfo') and expires_at.tzinfo:
        expires_at = expires_at.replace(tzinfo=None)

    if auth:
        # 更新现有授权
        auth.bangumiUserId = auth_data.get('bangumiUserId')
        auth.nickname = auth_data.get('nickname')
        auth.avatarUrl = auth_data.get('avatarUrl')
        auth.accessToken = auth_data.get('accessToken')
        auth.refreshToken = auth_data.get('refreshToken')
        auth.expiresAt = expires_at
        auth.authorizedAt = get_now()
    else:
        # 创建新授权
        auth_data_copy = auth_data.copy()
        auth_data_copy['expiresAt'] = expires_at
        auth_data_copy['userId'] = user_id
        auth_data_copy['authorizedAt'] = get_now()
        new_auth = BangumiAuth(**auth_data_copy)
        session.add(new_auth)
    await session.commit()


async def delete_bangumi_auth(session: AsyncSession, user_id: int) -> bool:
    """删除Bangumi授权信息"""
    auth = await session.get(BangumiAuth, user_id)
    if auth:
        await session.delete(auth)
        await session.commit()
        return True
    return False



# --- Generic OAuth Credential Management ---

async def get_oauth_credential(session: AsyncSession, user_id: int, provider: str) -> Dict[str, Any]:
    """获取用户在某平台的 OAuth 授权状态。"""
    from sqlalchemy import and_
    stmt = select(OauthCredential).where(
        and_(OauthCredential.userId == user_id, OauthCredential.provider == provider)
    )
    cred = (await session.execute(stmt)).scalar_one_or_none()
    if cred:
        return {
            "isAuthenticated": True,
            "provider": cred.provider,
            "providerUsername": cred.providerUsername,
            "providerUserId": cred.providerUserId,
            "expiresAt": cred.expiresAt,
            "authorizedAt": cred.authorizedAt,
        }
    return {"isAuthenticated": False, "provider": provider}


async def save_oauth_credential(session: AsyncSession, user_id: int, provider: str, data: Dict[str, Any]):
    """保存或更新某平台的 OAuth 授权信息。"""
    from sqlalchemy import and_
    stmt = select(OauthCredential).where(
        and_(OauthCredential.userId == user_id, OauthCredential.provider == provider)
    )
    cred = (await session.execute(stmt)).scalar_one_or_none()
    if cred:
        for key, value in data.items():
            if hasattr(cred, key):
                setattr(cred, key, value)
    else:
        data["userId"] = user_id
        data["provider"] = provider
        new_cred = OauthCredential(**data)
        session.add(new_cred)
    await session.commit()


async def delete_oauth_credential(session: AsyncSession, user_id: int, provider: str) -> bool:
    """删除某平台的 OAuth 授权信息。"""
    from sqlalchemy import and_
    stmt = select(OauthCredential).where(
        and_(OauthCredential.userId == user_id, OauthCredential.provider == provider)
    )
    cred = (await session.execute(stmt)).scalar_one_or_none()
    if cred:
        await session.delete(cred)
        await session.commit()
        return True
    return False


async def get_oauth_credential_with_token(session: AsyncSession, user_id: int, provider: str) -> Optional[OauthCredential]:
    """获取完整的 OAuth 凭证对象（含 token），用于 API 调用。"""
    from sqlalchemy import and_
    stmt = select(OauthCredential).where(
        and_(OauthCredential.userId == user_id, OauthCredential.provider == provider)
    )
    return (await session.execute(stmt)).scalar_one_or_none()

