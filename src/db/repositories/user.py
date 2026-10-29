"""
UserRepository - 用户数据访问层

注意：Repository 层只负责数据库读写，不处理加密/解密逻辑。
加密/解密应该在 Service 层或调用方完成。
"""

import logging
import hashlib
from typing import Optional, List, Dict, Any
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..orm_models import User
from .base import BaseRepository
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


class UserRepository(BaseRepository[User]):
    """用户 Repository"""
    
    async def get_first_user(self) -> Optional[User]:
        """返回首位用户，供无需请求上下文的后台日程同步使用。"""
        result = await self._session.execute(select(User).order_by(User.id).limit(1))
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: int) -> Optional[User]:
        """根据 ID 获取用户"""
        return await self._session.get(User, user_id)
    
    async def get_by_username(self, username: str) -> Optional[User]:
        """根据用户名获取用户"""
        stmt = select(User).where(User.username == username)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_all(self, **filters) -> List[User]:
        """获取所有用户"""
        stmt = select(User)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def create(self, username: str, hashed_password: str) -> User:
        """创建用户"""
        user = User(
            username=username,
            hashedPassword=hashed_password,
            createdAt=get_now()
        )
        self._session.add(user)
        await self._session.flush()
        return user
    
    async def update(self, user_id: int, **kwargs) -> Optional[User]:
        """更新用户"""
        user = await self.get_by_id(user_id)
        if not user:
            return None
        
        for key, value in kwargs.items():
            if hasattr(user, key):
                setattr(user, key, value)
        
        await self._session.flush()
        return user
    
    async def update_password(self, username: str, new_hashed_password: str) -> bool:
        """更新用户密码"""
        user = await self.get_by_username(username)
        if not user:
            return False
        
        user.hashedPassword = new_hashed_password
        await self._session.flush()
        return True
    
    async def update_login_info(self, username: str, token: str) -> bool:
        """更新用户登录信息（存储 token 的 SHA256 摘要）"""
        user = await self.get_by_username(username)
        if not user:
            return False
        
        token_hash = hashlib.sha256(token.encode()).hexdigest()[:32]
        user.token = token_hash
        user.tokenUpdate = get_now()
        await self._session.flush()
        return True
    
    async def update_otp_settings(
        self,
        username: str,
        is_otp: bool,
        otp_secret: str = None
    ) -> bool:
        """更新用户 OTP 设置（otp_secret 按原文写入，不做加密）"""
        user = await self.get_by_username(username)
        if not user:
            return False

        user.isOtp = is_otp
        if otp_secret is not None:
            user.otpSecret = otp_secret

        await self._session.flush()
        return True

    # ==================== OTP 加解密语义 ====================
    # why: 以下方法自 crud/user.py 迁入。otpSecret 在库中为加密态，
    #      加解密边界必须与读写成对出现，否则会写入明文或读出密文。

    async def get_auth_info_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """按用户名获取登录校验所需信息。

        注意：返回的 otpSecret 是加密后的密文，调用方需要自行解密。
        Repository 层不处理加密/解密逻辑。

        Args:
            username: 用户名

        Returns:
            含 id/username/hashedPassword/token/isOtp/otpSecret(密文) 的字典，
            用户不存在时返回 None
        """
        user = await self.get_by_username(username)
        if not user:
            return None

        return {
            "id": user.id,
            "username": user.username,
            "hashedPassword": user.hashedPassword,
            "token": user.token,
            "isOtp": user.isOtp,
            "otpSecret": user.otpSecret,  # 返回加密后的密文
        }

    async def enable_otp(self, username: str, encrypted_otp_secret: str) -> bool:
        """启用 TOTP 两步验证。

        注意：接收的 encrypted_otp_secret 必须是已加密的密文。
        加密逻辑应在 Service 层或调用方完成。

        Args:
            username: 用户名
            encrypted_otp_secret: 已加密的 TOTP secret（密文）

        Returns:
            是否更新成功（用户不存在时为 False）
        """
        return await self.update_otp_settings(username, True, encrypted_otp_secret)

    async def disable_otp(self, username: str) -> bool:
        """关闭 TOTP 两步验证并清空已存 secret。

        Args:
            username: 用户名

        Returns:
            是否更新成功（用户不存在时为 False）
        """
        user = await self.get_by_username(username)
        if not user:
            return False

        user.isOtp = False
        user.otpSecret = None
        await self._session.flush()
        return True
    
    async def delete(self, user_id: int) -> bool:
        """删除用户"""
        user = await self.get_by_id(user_id)
        if not user:
            return False
        
        await self._session.delete(user)
        await self._session.flush()
        return True
    
    async def verify_token(self, username: str, token: str) -> bool:
        """验证用户 token"""
        user = await self.get_by_username(username)
        if not user or not user.token:
            return False
        
        token_hash = hashlib.sha256(token.encode()).hexdigest()[:32]
        return user.token == token_hash
