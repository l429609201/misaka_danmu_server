"""
ApiTokenRepository - API Token 数据访问层

负责 API Token 的数据库操作，返回 ORM 对象。
"""

import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from ..orm_models import ApiToken
from .base import BaseRepository
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


class ApiTokenRepository(BaseRepository[ApiToken]):
    """
    API Token Repository
    
    职责：
    - 封装 API Token 的数据库访问
    - 返回 ApiToken ORM 对象
    """
    
    async def get_by_id(self, token_id: int) -> Optional[ApiToken]:
        """
        根据 ID 获取 Token
        
        Args:
            token_id: Token ID
            
        Returns:
            ApiToken ORM 对象，不存在时返回 None
        """
        return await self._session.get(ApiToken, token_id)
    
    async def get_by_token_str(self, token_str: str) -> Optional[ApiToken]:
        """
        根据 Token 字符串获取 Token
        
        Args:
            token_str: Token 字符串
            
        Returns:
            ApiToken ORM 对象，不存在时返回 None
        """
        stmt = select(ApiToken).where(ApiToken.token == token_str)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_by_name(self, name: str) -> Optional[ApiToken]:
        """
        根据名称获取 Token
        
        Args:
            name: Token 名称
            
        Returns:
            ApiToken ORM 对象，不存在时返回 None
        """
        stmt = select(ApiToken).where(ApiToken.name == name)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_all(self, **filters) -> List[ApiToken]:
        """
        获取所有 Token

        Returns:
            ApiToken ORM 对象列表，按创建时间倒序
        """
        stmt = select(ApiToken).order_by(ApiToken.createdAt.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_all_as_dict(self) -> List[Dict[str, Any]]:
        """
        获取所有 Token 的字典形式。

        替代 crud.get_all_api_tokens。
        why: 调用方（API / AI 助手工具）需要可直接序列化的结构，
             而非绑定会话的 ORM 对象，避免会话关闭后访问属性报错。

        Returns:
            Token 字典列表，按创建时间倒序
        """
        tokens = await self.get_all()
        return [
            {
                "id": t.id,
                "name": t.name,
                "token": t.token,
                "isEnabled": t.isEnabled,
                "expiresAt": t.expiresAt,
                "createdAt": t.createdAt,
                "dailyCallLimit": t.dailyCallLimit,
                "dailyCallCount": t.dailyCallCount,
            }
            for t in tokens
        ]


    async def create(
        self,
        name: str,
        token: str,
        validity_period: str,
        daily_call_limit: int = -1,
    ) -> ApiToken:
        """
        创建新的 API Token
        
        Args:
            name: Token 名称
            token: Token 字符串
            validity_period: 有效期（'permanent' 或 '30d', '90d' 等）
            daily_call_limit: 每日调用限制（-1 表示无限制）
            
        Returns:
            创建的 ApiToken ORM 对象
            
        Raises:
            ValueError: 如果名称已存在
        """
        # 检查名称是否已存在
        existing = await self.get_by_name(name)
        if existing:
            raise ValueError(f"名称为 '{name}' 的 Token 已存在")
        
        # 计算过期时间
        expires_at = None
        if validity_period != "permanent":
            days = int(validity_period.replace('d', ''))
            expires_at = get_now() + timedelta(days=days)
        
        new_token = ApiToken(
            name=name,
            token=token,
            expiresAt=expires_at,
            createdAt=get_now(),
            dailyCallLimit=daily_call_limit,
            dailyCallCount=0,
            isEnabled=True,
        )
        self._session.add(new_token)
        await self._session.flush()
        return new_token
    
    async def update(
        self,
        token_id: int,
        name: Optional[str] = None,
        token_str: Optional[str] = None,
        daily_call_limit: Optional[int] = None,
        validity_period: Optional[str] = None,
        is_enabled: Optional[bool] = None,
    ) -> Optional[ApiToken]:
        """
        更新 API Token
        
        Args:
            token_id: Token ID
            name: 新名称
            token_str: 新 Token 字符串
            daily_call_limit: 新的每日调用限制
            validity_period: 新的有效期
            is_enabled: 是否启用
            
        Returns:
            更新后的 ApiToken ORM 对象，不存在时返回 None
        """
        api_token = await self.get_by_id(token_id)
        if not api_token:
            return None
        
        if name is not None:
            api_token.name = name
        if token_str is not None:
            api_token.token = token_str
        if daily_call_limit is not None:
            api_token.dailyCallLimit = daily_call_limit
        if is_enabled is not None:
            api_token.isEnabled = is_enabled
        
        if validity_period is not None:
            if validity_period == "permanent":
                api_token.expiresAt = None
            elif validity_period != "custom":
                days = int(validity_period.replace('d', ''))
                api_token.expiresAt = get_now() + timedelta(days=days)
        
        await self._session.flush()
        return api_token

    async def delete(self, token_id: int) -> bool:
        """
        删除 API Token

        Args:
            token_id: Token ID

        Returns:
            是否删除成功
        """
        api_token = await self.get_by_id(token_id)
        if not api_token:
            return False

        await self._session.delete(api_token)
        await self._session.flush()
        return True

    async def toggle_enable(self, token_id: int) -> Optional[ApiToken]:
        """
        切换 Token 启用状态

        Args:
            token_id: Token ID

        Returns:
            更新后的 ApiToken ORM 对象，不存在时返回 None
        """
        api_token = await self.get_by_id(token_id)
        if not api_token:
            return None

        api_token.isEnabled = not api_token.isEnabled
        await self._session.flush()
        return api_token

    async def increment_call_count(self, token_id: int) -> bool:
        """
        增加今日调用次数

        Args:
            token_id: Token ID

        Returns:
            是否成功
        """
        api_token = await self.get_by_id(token_id)
        if not api_token:
            return False

        api_token.dailyCallCount += 1
        await self._session.flush()
        return True

    async def validate(self, token_str: str) -> Optional[Dict[str, Any]]:
        """
        验证 Token 是否有效（存在、启用且未过期）

        Args:
            token_str: Token 字符串

        Returns:
            Token 信息字典（id, token, name, isEnabled, expiresAt, dailyCallLimit, dailyCallCount），
            无效时返回 None
        """
        api_token = await self.get_by_token_str(token_str)
        if not api_token:
            return None

        # 检查是否启用
        if not api_token.isEnabled:
            return None

        # 检查是否过期
        if api_token.expiresAt:
            expires_at = api_token.expiresAt
            if expires_at.tzinfo is None:
                from src.core.timezone import get_app_timezone
                expires_at = expires_at.replace(tzinfo=get_app_timezone())
            if expires_at < get_now():
                return None

        # 返回 Token 信息
        return {
            "id": api_token.id,
            "token": api_token.token,
            "name": api_token.name,
            "isEnabled": api_token.isEnabled,
            "expiresAt": api_token.expiresAt,
            "dailyCallLimit": api_token.dailyCallLimit,
            "dailyCallCount": api_token.dailyCallCount,
        }

    async def reset_daily_count(self, token_id: int) -> bool:
        """
        重置今日调用次数

        Args:
            token_id: Token ID

        Returns:
            是否成功
        """
        api_token = await self.get_by_id(token_id)
        if not api_token:
            return False

        api_token.dailyCallCount = 0
        await self._session.flush()
        return True

    async def is_valid(self, token_str: str) -> bool:
        """
        验证 Token 是否有效（存在、启用、未过期）

        Args:
            token_str: Token 字符串

        Returns:
            是否有效
        """
        api_token = await self.get_by_token_str(token_str)
        if not api_token:
            return False

        if not api_token.isEnabled:
            return False

        if api_token.expiresAt and api_token.expiresAt < get_now():
            return False

        return True

    async def reset_all_daily_counts(self) -> int:
        """
        重置所有 Token 的今日调用次数

        Returns:
            重置的 Token 数量
        """
        stmt = (
            update(ApiToken)
            .where(ApiToken.dailyCallCount > 0)
            .values(dailyCallCount=0)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount
