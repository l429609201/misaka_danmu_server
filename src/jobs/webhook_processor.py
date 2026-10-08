"""延时 Webhook 调度入口，业务处理统一交给 Workflow。"""

from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from .base import BaseJob
from src.services.task_profiler import profile_flow, FLOW_WEBHOOK_PROCESSOR
from src.workflows.webhook_dispatch import WebhookDispatchContext, dispatch_due_webhooks


class WebhookProcessorJob(BaseJob):
    job_type = "webhookProcessor"
    job_name = "Webhook 延时任务处理器"
    job_name_en = "Webhook Delayed Task Processor"
    job_name_tw = "Webhook 延時任務處理器"
    description = "定期检查并处理来自Emby/Jellyfin等媒体服务器的延时Webhook请求，自动导入新增的剧集弹幕。"
    description_en = "Periodically check and process delayed Webhook requests from media servers (Emby/Jellyfin), auto-importing new episode danmaku."
    description_tw = "定期檢查並處理來自Emby/Jellyfin等媒體伺服器的延時Webhook請求，自動匯入新增的劇集彈幕。"

    @profile_flow(FLOW_WEBHOOK_PROCESSOR)
    async def run(self, session: AsyncSession, progress_callback: Callable) -> None:
        """委托 Workflow 提交到期任务；调度层不操作 ORM 或事务。"""
        del session
        await progress_callback(0, "开始检查待处理的 Webhook 任务...")
        await dispatch_due_webhooks(
            WebhookDispatchContext(
                task_manager=self.task_manager,
                scraper_manager=self.scraper_manager,
                metadata_manager=self.metadata_manager,
                config_service=self.config_service,
                ai_service=self.ai_service,
                rate_limiter=self.rate_limiter,
                title_recognition_manager=self.title_recognition_manager,
            ),
            progress_callback,
        )
