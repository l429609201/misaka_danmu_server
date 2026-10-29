"""
PerformanceRepository - 性能监测数据访问层
"""

import logging
import json
from typing import Optional, List, Dict, Any
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..orm_models import SystemMetric, PerformanceAlert
from .base import BaseRepository
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


class PerformanceRepository(BaseRepository[SystemMetric]):
    """性能监测 Repository"""
    
    async def get_by_id(self, metric_id: int) -> Optional[SystemMetric]:
        """根据 ID 获取指标"""
        return await self._session.get(SystemMetric, metric_id)
    
    async def get_all(self, **filters) -> List[SystemMetric]:
        """获取所有指标"""
        stmt = select(SystemMetric).order_by(SystemMetric.recordTime.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def get_by_category(self, category: str, limit: int = 100) -> List[SystemMetric]:
        """根据类别获取指标"""
        stmt = (
            select(SystemMetric)
            .where(SystemMetric.category == category)
            .order_by(SystemMetric.recordTime.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def create(
        self,
        category: str,
        metric_name: str,
        value_int: int = None,
        value_float: float = None,
        value_text: str = None,
        value_json: Dict[str, Any] = None,
        **kwargs
    ) -> SystemMetric:
        """创建性能指标"""
        metric = SystemMetric(
            category=category,
            metricName=metric_name,
            valueInt=value_int,
            valueFloat=value_float,
            valueText=value_text,
            valueJson=json.dumps(value_json) if value_json else None,
            recordTime=get_now(),
            **kwargs
        )
        self._session.add(metric)
        await self._session.flush()
        return metric
    
    async def update(self, metric_id: int, **kwargs) -> Optional[SystemMetric]:
        """更新指标"""
        metric = await self.get_by_id(metric_id)
        if not metric:
            return None
        
        for key, value in kwargs.items():
            if hasattr(metric, key):
                setattr(metric, key, value)
        
        await self._session.flush()
        return metric
    
    async def delete(self, metric_id: int) -> bool:
        """删除指标"""
        metric = await self.get_by_id(metric_id)
        if not metric:
            return False
        
        await self._session.delete(metric)
        await self._session.flush()
        return True
    
    async def delete_old_metrics(self, before_date: datetime) -> int:
        """删除指定日期之前的指标"""
        stmt = select(SystemMetric).where(SystemMetric.recordTime < before_date)
        result = await self._session.execute(stmt)
        metrics = result.scalars().all()
        
        count = 0
        for metric in metrics:
            await self._session.delete(metric)
            count += 1
        
        await self._session.flush()
        return count


class PerformanceAlertRepository(BaseRepository[PerformanceAlert]):
    """性能告警 Repository"""
    
    async def get_by_id(self, alert_id: int) -> Optional[PerformanceAlert]:
        """根据 ID 获取告警"""
        return await self._session.get(PerformanceAlert, alert_id)
    
    async def get_all(self, **filters) -> List[PerformanceAlert]:
        """获取所有告警"""
        stmt = select(PerformanceAlert).order_by(PerformanceAlert.alertTime.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def get_active(self) -> List[PerformanceAlert]:
        """获取所有活跃告警"""
        stmt = (
            select(PerformanceAlert)
            .where(PerformanceAlert.isResolved == False)
            .order_by(PerformanceAlert.alertTime.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def create(self, **kwargs) -> PerformanceAlert:
        """创建告警"""
        alert = PerformanceAlert(alertTime=get_now(), **kwargs)
        self._session.add(alert)
        await self._session.flush()
        return alert
    
    async def update(self, alert_id: int, **kwargs) -> Optional[PerformanceAlert]:
        """更新告警"""
        alert = await self.get_by_id(alert_id)
        if not alert:
            return None
        
        for key, value in kwargs.items():
            if hasattr(alert, key):
                setattr(alert, key, value)
        
        await self._session.flush()
        return alert
    
    async def delete(self, alert_id: int) -> bool:
        """删除告警"""
        alert = await self.get_by_id(alert_id)
        if not alert:
            return False
        
        await self._session.delete(alert)
        await self._session.flush()
        return True
