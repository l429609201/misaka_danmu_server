"""
ExternalLogRepository - 外部API日志数据访问层
"""

import logging
from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..orm_models import ExternalApiLog
from .base import BaseRepository
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


class ExternalLogRepository(BaseRepository[ExternalApiLog]):
    """外部API日志 Repository"""
    
    async def get_by_id(self, log_id: int) -> Optional[ExternalApiLog]:
        """根据 ID 获取日志"""
        return await self._session.get(ExternalApiLog, log_id)
    
    async def get_all(self, **filters) -> List[ExternalApiLog]:
        """按访问时间倒序获取所有日志。"""
        stmt = select(ExternalApiLog).order_by(ExternalApiLog.accessTime.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_recent_logs(self, limit: int = 100) -> List[ExternalApiLog]:
        """在数据库内限制最近日志数量，避免日志页面读取全表。"""
        stmt = select(ExternalApiLog).order_by(
            ExternalApiLog.accessTime.desc(), ExternalApiLog.id.desc()
        ).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(
        self,
        ip_address: str,
        endpoint: str,
        status_code: int,
        message: Optional[str] = None,
        request_headers: Optional[str] = None,
        request_body: Optional[str] = None,
    ) -> ExternalApiLog:
        """按实际审计表字段创建日志，提交由服务层事务负责。"""
        log = ExternalApiLog(
            ipAddress=ip_address,
            endpoint=endpoint,
            statusCode=status_code,
            message=message,
            requestHeaders=request_headers,
            requestBody=request_body,
            accessTime=get_now(),
        )
        self._session.add(log)
        await self._session.flush()
        return log
    
    async def update(self, log_id: int, **kwargs) -> Optional[ExternalApiLog]:
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
