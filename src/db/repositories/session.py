"""
SessionRepository - 用户会话数据访问层（多端登录管理）

承接原 src/db/crud/session.py 的全部方法。
约定：本层只负责持久化，返回 ORM 对象；事务提交由上层 DatabaseService.transaction() 统一控制。
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, List

from sqlalchemy import case, func, select, update as sa_update, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.timezone import get_now
from ..orm_models import UserSession
from .base import BaseRepository

logger = logging.getLogger(__name__)


class SessionRepository(BaseRepository[UserSession]):
    """用户会话 Repository（对应表 user_sessions）"""

    async def get_stats(self) -> Dict[str, int]:
        """统计全部及未撤销会话；保留审计接口原有的活跃口径。"""
        # 在数据库内聚合，避免加载全部会话；不额外引入过期时间过滤。
        stmt = select(
            func.count(UserSession.id),
            func.count(case((UserSession.isRevoked.is_(False), 1))),
        )
        result = await self._session.execute(stmt)
        total, active = result.one()
        return {"totalSessions": total, "activeSessions": active}

    async def get_by_id(self, session_id: int) -> Optional[UserSession]:
        """根据主键 ID 获取会话"""
        return await self._session.get(UserSession, session_id)

    async def get_all(self, **filters) -> List[UserSession]:
        """获取所有会话"""
        stmt = select(UserSession)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_jti(self, jti: str) -> Optional[UserSession]:
        """通过 JWT ID 获取会话"""
        stmt = select(UserSession).where(UserSession.jti == jti)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_user_id(self, user_id: int) -> List[UserSession]:
        """获取指定用户的所有会话，按创建时间倒序"""
        stmt = (
            select(UserSession)
            .where(UserSession.userId == user_id)
            .order_by(UserSession.createdAt.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(
        self,
        user_id: int,
        jti: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        expires_minutes: Optional[int] = None,
    ) -> UserSession:
        """
        创建新的用户会话

        expires_minutes 为 None 或 -1 时表示永不过期（expiresAt 留空）。
        """
        now = get_now()
        expires_at: Optional[datetime] = None
        if expires_minutes and expires_minutes != -1:
            expires_at = now + timedelta(minutes=expires_minutes)

        # userAgent 字段长度上限 500，超长截断避免入库失败
        if user_agent and len(user_agent) > 500:
            user_agent = user_agent[:500]

        user_session = UserSession(
            userId=user_id,
            jti=jti,
            ipAddress=ip_address,
            userAgent=user_agent,
            createdAt=now,
            lastUsedAt=now,
            expiresAt=expires_at,
            isRevoked=False,
        )
        self._session.add(user_session)
        # flush 以获得自增主键 id，但不提交事务
        await self._session.flush()
        return user_session

    async def update(self, session_id: int, **kwargs) -> Optional[UserSession]:
        """按主键更新会话字段"""
        user_session = await self.get_by_id(session_id)
        if not user_session:
            return None

        for key, value in kwargs.items():
            if hasattr(user_session, key):
                setattr(user_session, key, value)

        await self._session.flush()
        return user_session

    async def validate(self, jti: str) -> bool:
        """验证会话是否有效（存在、未撤销、未过期）"""
        stmt = select(UserSession).where(
            UserSession.jti == jti,
            UserSession.isRevoked == False,  # noqa: E712 - SQLAlchemy 需要 == 比较
        )
        result = await self._session.execute(stmt)
        user_session = result.scalar_one_or_none()

        if not user_session:
            return False

        # expiresAt 为空表示永不过期
        if user_session.expiresAt and user_session.expiresAt < get_now():
            return False

        return True

    async def update_last_used(self, jti: str) -> bool:
        """刷新会话的最后使用时间"""
        stmt = sa_update(UserSession).where(UserSession.jti == jti).values(lastUsedAt=get_now())
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    async def revoke(self, session_id: int, user_id: int) -> bool:
        """撤销指定会话（限定归属用户，防越权）"""
        stmt = (
            sa_update(UserSession)
            .where(UserSession.id == session_id, UserSession.userId == user_id)
            .values(isRevoked=True)
        )
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    async def revoke_by_jti(self, jti: str) -> bool:
        """通过 jti 撤销会话"""
        stmt = sa_update(UserSession).where(UserSession.jti == jti).values(isRevoked=True)
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    async def revoke_others(self, user_id: int, current_jti: str) -> int:
        """撤销该用户除当前会话外的所有其他会话，返回撤销数量"""
        stmt = (
            sa_update(UserSession)
            .where(
                UserSession.userId == user_id,
                UserSession.jti != current_jti,
                UserSession.isRevoked == False,  # noqa: E712
            )
            .values(isRevoked=True)
        )
        result = await self._session.execute(stmt)
        return result.rowcount

    async def delete(self, session_id: int) -> bool:
        """按主键删除会话"""
        stmt = sa_delete(UserSession).where(UserSession.id == session_id)
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    async def delete_by_jti(self, jti: str) -> bool:
        """通过 jti 删除会话（用于白名单会话的重建）"""
        stmt = sa_delete(UserSession).where(UserSession.jti == jti)
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    async def cleanup_expired(self) -> int:
        """清理已撤销和已过期的会话，返回清理数量"""
        now = get_now()
        stmt = sa_delete(UserSession).where(
            (UserSession.isRevoked == True)  # noqa: E712
            | ((UserSession.expiresAt != None) & (UserSession.expiresAt < now))  # noqa: E711
        )
        result = await self._session.execute(stmt)
        return result.rowcount
