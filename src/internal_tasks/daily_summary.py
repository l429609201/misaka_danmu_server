"""
内置轮询任务：每日运行摘要 (7)

定时汇总系统运行情况，保存到 config 表。
"""
import json
import logging
from datetime import datetime, timedelta

from fastapi import FastAPI

from .base import BasePollingTask

logger = logging.getLogger("InternalTasks.DailySummary")


class DailySummaryTask(BasePollingTask):
    """每日系统运行摘要"""
    name = "daily_summary"
    enabled_key = "dailySummaryEnabled"
    interval_key = "dailySummaryInterval"
    default_interval = 60  # 每小时检查一次（handler 内部判断凌晨窗口+日期防重复）
    min_interval = 30
    startup_delay = 120  # 启动2分钟后开始

    @staticmethod
    async def handler(app: FastAPI) -> None:
        await _daily_summary_handler(app)


async def _daily_summary_handler(app: FastAPI) -> None:
    """每日摘要处理器：在每天 8:00-8:59 窗口执行一次"""
    from sqlalchemy import func, select
    from src.db import orm_models
    from src.core import get_now

    now = get_now()
    current_hour = now.hour

    # 只在 8:00-8:59 之间执行
    if current_hour != 8:
        return

    # 日期防重复
    if not hasattr(_daily_summary_handler, '_last_date'):
        _daily_summary_handler._last_date = None
    today = now.date()
    if _daily_summary_handler._last_date == today:
        return

    session_factory = app.state.db_session_factory
    config_manager = app.state.config_manager

    try:
        async with session_factory() as session:
            since = now - timedelta(hours=24)

            # 任务统计
            task_q = select(
                orm_models.TaskHistory.status,
                func.count(orm_models.TaskHistory.taskId)
            ).where(orm_models.TaskHistory.createdAt >= since).group_by(orm_models.TaskHistory.status)
            task_rows = (await session.execute(task_q)).all()
            task_stats = {row[0]: row[1] for row in task_rows}

            # 新增弹幕
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            ep_q = select(func.count(orm_models.Episode.id)).where(
                orm_models.Episode.fetchedAt >= today_start
            )
            new_danmaku = (await session.execute(ep_q)).scalar() or 0

            # 零弹幕
            zero_q = select(func.count(orm_models.Episode.id)).where(
                orm_models.Episode.commentCount == 0
            )
            zero_count = (await session.execute(zero_q)).scalar() or 0

        # 构建摘要
        lines = [f"📊 系统运行摘要 (最近24小时)", ""]
        for status, count in task_stats.items():
            emoji = "✅" if status in ("completed", "success") else "❌" if status in ("failed", "error") else "⏳"
            lines.append(f"  {emoji} {status}: {count}")
        lines.extend(["", f"💬 新增弹幕分集: {new_danmaku}", f"⚠️ 零弹幕分集: {zero_count}"])
        summary_text = "\n".join(lines)

        await config_manager.setValue("last_daily_summary", json.dumps({
            "text": summary_text,
            "generatedAt": now.isoformat(),
            "taskStats": task_stats,
            "newDanmaku": new_danmaku,
            "zeroEpisodes": zero_count,
        }, ensure_ascii=False))

        _daily_summary_handler._last_date = today
        logger.info(f"✓ 每日摘要已生成: 任务{sum(task_stats.values())}个, 新弹幕{new_danmaku}集")
    except Exception as e:
        logger.error(f"每日摘要生成失败: {e}", exc_info=True)
