"""日历领域能力层。

本包提供日历相关的纯业务能力，供 API 层与定时任务调用。

分层约定：
    本包内所有模块**不得**导入 services/task_manager.py，也不得提交任务。
"""

from .weekly_flow import (
    append_available_source,
    build_source_descriptor,
    calendar_title_key,
    get_subscription_providers,
    get_weekly_calendar_flow,
    normalize_calendar_title,
    sync_scraper_calendars,
)

__all__ = [
    "append_available_source",
    "build_source_descriptor",
    "calendar_title_key",
    "get_subscription_providers",
    "get_weekly_calendar_flow",
    "normalize_calendar_title",
    "sync_scraper_calendars",
]
