"""弹幕源自动更新轮询入口，业务由资源编排层执行。"""
from fastapi import FastAPI

from src.internal_tasks.base import BasePollingTask
from src.workflows.scraper_resources.auto_update import scraper_auto_update_handler


class ScraperAutoUpdateTask(BasePollingTask):
    """弹幕源自动更新轮询任务"""
    name = "scraper_auto_update"
    enabled_key = "scraperAutoUpdateEnabled"
    interval_key = "scraperAutoUpdateInterval"
    default_interval = 30   # 30分钟
    min_interval = 15       # 最小15分钟
    startup_delay = 60      # 启动后60秒开始

    @staticmethod
    async def handler(app: FastAPI) -> None:
        """弹幕源自动更新处理器"""
        await scraper_auto_update_handler(app)


