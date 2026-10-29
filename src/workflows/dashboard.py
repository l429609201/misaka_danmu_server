"""仪表盘聚合工作流：汇总弹幕库、数据源、任务队列、媒体服务器与系统资源的只读快照。"""

import asyncio
import logging
import os
from datetime import timedelta
from typing import Any, Dict

import psutil

from src.core import get_now
from src.services.service_container import get_database_service
from src.services.task_manager import TaskManager

logger = logging.getLogger(__name__)


def _collect_system_resources() -> Dict[str, Any]:
    """同步采集 CPU/内存/磁盘与本进程内存；在线程中调用，避免阻塞事件循环。"""
    process = psutil.Process(os.getpid())
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(os.getcwd())
    return {
        # interval=None 返回自上次调用以来的占用，首次可能为 0，但不会阻塞
        "cpuPercent": psutil.cpu_percent(interval=None),
        "memoryPercent": memory.percent,
        "memoryUsed": memory.used,
        "memoryTotal": memory.total,
        "processMemory": process.memory_info().rss,
        "diskPercent": disk.percent,
        "diskUsed": disk.used,
        "diskTotal": disk.total,
    }


async def workflow_get_dashboard_overview(task_manager: TaskManager) -> Dict[str, Any]:
    """聚合仪表盘数据；数据库只在短事务内读取，系统资源采集在线程中进行。"""
    now = get_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # 本周从周一 0 点起算
    week_start = today_start - timedelta(days=today_start.weekday())

    db = get_database_service()
    async with db.transaction():
        library = await db.health_query.get_dashboard_library_counts(today_start, week_start)
        sources = await db.health_query.get_dashboard_source_counts()
        media_servers = await db.health_query.get_dashboard_media_servers()
        task_summary = await db.health_query.get_task_counts_since(now - timedelta(hours=24))

    try:
        system = await asyncio.to_thread(_collect_system_resources)
    except Exception as exc:  # 资源采集失败不影响其他卡片
        logger.warning(f"仪表盘系统资源采集失败: {exc}")
        system = {}

    return {
        "library": library,
        "sources": sources,
        "mediaServers": media_servers,
        "tasks": {**task_manager.get_queue_snapshot(), "last24h": task_summary},
        "system": system,
        "generatedAt": now.isoformat(),
    }
