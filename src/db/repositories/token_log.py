"""
TokenLogRepository - Token访问日志数据访问层
"""

import logging
from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..orm_models import TokenAccessLog
from .base import BaseRepository
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


class TokenLogRepository(BaseRepository[TokenAccessLog]):
    """Token访问日志 Repository"""
    
    async def get_by_id(self, log_id: int) -> Optional[TokenAccessLog]:
        """根据 ID 获取日志"""
        return await self._session.get(TokenAccessLog, log_id)
    
    async def get_all(self, **filters) -> List[TokenAccessLog]:
        """获取所有日志"""
        stmt = select(TokenAccessLog).order_by(TokenAccessLog.accessTime.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def get_by_token_id(self, token_id: int, limit: int = 100) -> List[TokenAccessLog]:
        """获取指定 Token 的访问日志"""
        stmt = (
            select(TokenAccessLog)
            .where(TokenAccessLog.tokenId == token_id)
            .order_by(TokenAccessLog.accessTime.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def create(
        self,
        token_id: int,
        ip_address: str,
        status: str,
    ) -> TokenAccessLog:
        """创建访问日志"""
        log = TokenAccessLog(
            tokenId=token_id,
            ipAddress=ip_address,
            status=status,
            accessTime=get_now()
        )
        self._session.add(log)
        await self._session.flush()
        return log

    async def create_access_log(
        self,
        token_id: int,
        ip_address: str,
        user_agent: Optional[str],
        log_status: str,
        path: Optional[str] = None,
        method: Optional[str] = None,
        request_headers: Optional[str] = None,
        request_body: Optional[str] = None,
    ) -> int:
        """
        创建访问日志（带完整字段，返回日志 ID）

        Args:
            token_id: Token ID
            ip_address: 客户端 IP
            user_agent: User-Agent
            log_status: 日志状态（allowed, denied_*, etc.）
            path: 请求路径
            method: HTTP 方法
            request_headers: 请求头（JSON 字符串）
            request_body: 请求体（JSON 字符串）

        Returns:
            创建的日志 ID
        """
        log = TokenAccessLog(
            tokenId=token_id,
            ipAddress=ip_address,
            userAgent=user_agent,
            status=log_status,
            path=path,
            method=method,
            requestHeaders=request_headers,
            requestBody=request_body,
            accessTime=get_now()
        )
        self._session.add(log)
        await self._session.flush()
        return log.id

    def create_access_log_sync(
        self,
        token_id: int,
        ip_address: str,
        user_agent: Optional[str],
        log_status: str,
        path: Optional[str] = None,
    ) -> None:
        """
        同步创建访问日志（不等待 flush，用于非阻塞场景）

        Args:
            token_id: Token ID
            ip_address: 客户端 IP
            user_agent: User-Agent
            log_status: 日志状态
            path: 请求路径
        """
        log = TokenAccessLog(
            tokenId=token_id,
            ipAddress=ip_address,
            userAgent=user_agent,
            status=log_status,
            path=path,
            accessTime=get_now()
        )
        self._session.add(log)
        # 不 flush，由外层事务统一提交
    
    async def update(self, log_id: int, **kwargs) -> Optional[TokenAccessLog]:
        """更新日志"""
        log = await self.get_by_id(log_id)
        if not log:
            return None
        
        for key, value in kwargs.items():
            if hasattr(log, key):
                setattr(log, key, value)
        
        await self._session.flush()
        return log
    
    async def delete(self, log_id: int) -> bool:
        """删除日志"""
        log = await self.get_by_id(log_id)
        if not log:
            return False
        
        await self._session.delete(log)
        await self._session.flush()
        return True
