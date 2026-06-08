"""
内置轮询任务：容量趋势记录 (21)

定期记录数据库和缓存容量快照，供前端展示增长趋势。
"""
import json
import logging
import os
from datetime import datetime

from fastapi import FastAPI

from .base import BasePollingTask

logger = logging.getLogger("InternalTasks.CapacityTrend")

MAX_TREND_POINTS = 90


class CapacityTrendTask(BasePollingTask):
    """容量趋势记录"""
    name = "capacity_trend"
    enabled_key = "capacityTrendEnabled"
    interval_key = "capacityTrendInterval"
    default_interval = 720  # 每12小时记录一次
    min_interval = 60  # 最小1小时
    startup_delay = 180  # 启动3分钟后开始

    @staticmethod
    async def handler(app: FastAPI) -> None:
        await _capacity_trend_handler(app)


async def _capacity_trend_handler(app: FastAPI) -> None:
    """容量趋势记录处理器"""
    from sqlalchemy import func, select
    from src.db import orm_models
    from src.core import get_now

    session_factory = app.state.db_session_factory
    config_manager = app.state.config_manager
    now = get_now()

    try:
        async with session_factory() as session:
            tables = [
                ("anime", orm_models.Anime),
                ("episode", orm_models.Episode),
                ("anime_sources", orm_models.AnimeSource),
                ("task_history", orm_models.TaskHistory),
                ("cache_data", orm_models.CacheData),
                ("media_items", orm_models.MediaItem),
            ]
            counts = {}
            for name, model in tables:
                pk = list(model.__table__.primary_key.columns)[0]
                q = select(func.count(pk))
                counts[name] = (await session.execute(q)).scalar() or 0

        # 数据库文件大小
        db_size = 0
        db_path = os.path.join("config", "data.db")
        if os.path.exists(db_path):
            db_size = os.path.getsize(db_path)

        # 读取现有数据
        raw = await config_manager.get("capacity_trend_data", "[]")
        try:
            trend_data = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            trend_data = []

        trend_data.append({
            "timestamp": now.isoformat(),
            "tableCounts": counts,
            "dbSizeBytes": db_size,
        })

        if len(trend_data) > MAX_TREND_POINTS:
            trend_data = trend_data[-MAX_TREND_POINTS:]

        await config_manager.setValue("capacity_trend_data", json.dumps(trend_data))

        total = sum(counts.values())
        logger.info(f"✓ 容量趋势已记录: 总记录{total}, DB大小{db_size // 1024}KB")
    except Exception as e:
        logger.error(f"容量趋势记录失败: {e}", exc_info=True)
