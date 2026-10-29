"""
PasskeyRepository - PassKey (WebAuthn/FIDO2) 凭证数据访问层

承接原 src/db/crud/passkey.py 的全部方法。
约定：本层只负责持久化，返回 ORM 对象；事务提交由上层 DatabaseService.transaction() 统一控制。
"""

import logging
from typing import Optional, List

from sqlalchemy import func, select, update as sa_update, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.timezone import get_now
from ..orm_models import UserPassKey
from .base import BaseRepository

logger = logging.getLogger(__name__)


class PasskeyRepository(BaseRepository[UserPassKey]):
    """PassKey 凭证 Repository（对应表 user_passkeys）"""

    async def get_by_id(self, passkey_id: int) -> Optional[UserPassKey]:
        """根据主键 ID 获取 PassKey"""
        return await self._session.get(UserPassKey, passkey_id)

    async def get_all(self, **filters) -> List[UserPassKey]:
        """获取所有 PassKey"""
        stmt = select(UserPassKey)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_user_id(self, user_id: int) -> List[UserPassKey]:
        """获取指定用户的所有 PassKey，按创建时间倒序"""
        stmt = (
            select(UserPassKey)
            .where(UserPassKey.userId == user_id)
            .order_by(UserPassKey.createdAt.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_credential_id(self, credential_id: str) -> Optional[UserPassKey]:
        """通过 WebAuthn 凭证 ID 查找 PassKey"""
        stmt = select(UserPassKey).where(UserPassKey.credentialId == credential_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self,
        user_id: int,
        credential_id: str,
        public_key: str,
        sign_count: int,
        device_name: Optional[str] = None,
        transports: Optional[str] = None,
    ) -> UserPassKey:
        """创建新的 PassKey 凭证"""
        passkey = UserPassKey(
            userId=user_id,
            credentialId=credential_id,
            publicKey=public_key,
            signCount=sign_count,
            deviceName=device_name,
            transports=transports,
            createdAt=get_now(),
        )
        self._session.add(passkey)
        # flush 以获得自增主键 id，但不提交事务
        await self._session.flush()
        return passkey

    async def update(self, passkey_id: int, **kwargs) -> Optional[UserPassKey]:
        """按主键更新 PassKey 字段"""
        passkey = await self.get_by_id(passkey_id)
        if not passkey:
            return None

        for key, value in kwargs.items():
            if hasattr(passkey, key):
                setattr(passkey, key, value)

        await self._session.flush()
        return passkey

    async def update_sign_count(self, credential_id: str, new_sign_count: int) -> bool:
        """更新签名计数器并刷新最后使用时间（防重放）"""
        stmt = (
            sa_update(UserPassKey)
            .where(UserPassKey.credentialId == credential_id)
            .values(signCount=new_sign_count, lastUsedAt=get_now())
        )
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    async def rename(self, passkey_id: int, user_id: int, device_name: str) -> bool:
        """重命名 PassKey（限定归属用户，防越权）"""
        stmt = (
            sa_update(UserPassKey)
            .where(UserPassKey.id == passkey_id, UserPassKey.userId == user_id)
            .values(deviceName=device_name)
        )
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    async def delete(self, passkey_id: int, user_id: Optional[int] = None) -> bool:
        """删除 PassKey；传入 user_id 时限定归属用户，防越权删除"""
        stmt = sa_delete(UserPassKey).where(UserPassKey.id == passkey_id)
        if user_id is not None:
            stmt = stmt.where(UserPassKey.userId == user_id)
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    async def delete_all_by_user_id(self, user_id: int) -> int:
        """删除指定用户的所有 PassKey，返回删除数量"""
        stmt = sa_delete(UserPassKey).where(UserPassKey.userId == user_id)
        result = await self._session.execute(stmt)
        return result.rowcount

    async def count_by_user_id(self, user_id: int) -> int:
        """统计指定用户的 PassKey 数量"""
        stmt = select(func.count()).select_from(UserPassKey).where(UserPassKey.userId == user_id)
        result = await self._session.execute(stmt)
        return result.scalar_one() or 0

    async def exists(self, credential_id: str) -> bool:
        """检查指定凭证 ID 的 PassKey 是否存在"""
        return await self.get_by_credential_id(credential_id) is not None
