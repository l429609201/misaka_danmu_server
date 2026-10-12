"""Bangumi Token 定时刷新任务，授权与事务逻辑统一委托服务。"""

import logging

from fastapi import FastAPI

from src.services.oauth_service import get_oauth_service
from .base import BasePollingTask

logger = logging.getLogger("BgmTokenRefresh")
REFRESH_THRESHOLD_DAYS = 3


class BgmTokenRefreshTask(BasePollingTask):
    """定时检查即将过期与已过期的 Bangumi OAuth 凭证。"""

    name = "bgm_token_refresh"
    enabled_key = ""
    interval_key = ""
    default_interval = 720
    min_interval = 60
    startup_delay = 120

    @staticmethod
    async def handler(app: FastAPI) -> None:
        """委托真实授权服务刷新，不创建 Session 或访问 ORM。"""
        counts = await get_oauth_service().refresh_due("bangumi", REFRESH_THRESHOLD_DAYS)
        if counts["refreshed"] or counts["failed"]:
            logger.info(
                "Bangumi token 刷新完成: 成功 %s, 跳过 %s, 失败 %s",
                counts["refreshed"], counts["skipped"], counts["failed"],
            )
