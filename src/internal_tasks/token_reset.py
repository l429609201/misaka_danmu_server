"""
内置轮询任务：每日重置 API Token 调用次数

每天凌晨自动重置所有 API Token 的每日调用次数统计。
"""
import logging
from datetime import datetime, time as dt_time

from fastapi import FastAPI

from .base import BasePollingTask

logger = logging.getLogger("InternalTasks.TokenReset")


class TokenResetTask(BasePollingTask):
    """每日重置 API Token 调用次数"""
    name = "token_reset"
    enabled_key = ""  # 空字符串表示始终启用
    interval_key = ""  # 空字符串表示使用硬编码默认值
    default_interval = 1440  # 24 小时（分钟）
    min_interval = 60  # 最小 1 小时
    startup_delay = 10  # 启动后 10 秒开始

    @staticmethod
    async def handler(app: FastAPI) -> None:
        """Token 重置处理器"""
        await _token_reset_handler(app)


async def _token_reset_handler(app: FastAPI) -> None:
    """
    Token 重置处理器
    
    检查当前时间，如果是凌晨 0:00-0:59 之间且今天还未重置过，则执行重置。
    """
    from src.db import crud
    
    session_factory = app.state.db_session_factory
    
    # 获取当前时间
    now = datetime.now()
    current_hour = now.hour
    
    # 只在凌晨 0:00-0:59 之间执行
    if current_hour != 0:
        logger.debug(f"当前时间 {now.strftime('%H:%M')}，不在重置时间窗口内（00:00-00:59），跳过")
        return
    
    # 检查今天是否已经重置过（使用简单的内存标记）
    if not hasattr(_token_reset_handler, '_last_reset_date'):
        _token_reset_handler._last_reset_date = None
    
    today = now.date()
    if _token_reset_handler._last_reset_date == today:
        logger.debug(f"今天 {today} 已经重置过 Token，跳过")
        return
    
    # 执行重置
    try:
        async with session_factory() as session:
            reset_count = await crud.reset_all_token_daily_counts(session)
            logger.info(f"✓ 每日 Token 重置完成：已重置 {reset_count} 个 Token 的调用次数")
            
            # 标记今天已重置
            _token_reset_handler._last_reset_date = today
    except Exception as e:
        logger.error(f"Token 重置失败: {e}", exc_info=True)

