import logging
import json
from typing import Callable
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import crud
from src.tasks import webhook_search_and_dispatch_task
from .base import BaseJob

logger = logging.getLogger(__name__)

class WebhookProcessorJob(BaseJob):
    job_type = "webhookProcessor"
    job_name = "Webhook 延时任务处理器"
    job_name_en = "Webhook Delayed Task Processor"
    job_name_tw = "Webhook 延時任務處理器"
    description = "定期检查并处理来自Emby/Jellyfin等媒体服务器的延时Webhook请求，自动导入新增的剧集弹幕。"
    description_en = "Periodically check and process delayed Webhook requests from media servers (Emby/Jellyfin), auto-importing new episode danmaku."
    description_tw = "定期檢查並處理來自Emby/Jellyfin等媒體伺服器的延時Webhook請求，自動匯入新增的劇集彈幕。"

    async def run(self, session: AsyncSession, progress_callback: Callable):
        """
        执行 Webhook 延时任务处理。
        """
        await progress_callback(0, "开始检查待处理的 Webhook 任务...")
        
        due_tasks = await crud.get_due_webhook_tasks(session)
        if not due_tasks:
            await progress_callback(100, "没有需要处理的 Webhook 任务。")
            return

        total_tasks = len(due_tasks)
        logger.info(f"找到 {total_tasks} 个待处理的 Webhook 任务，开始执行...")

        for i, task in enumerate(due_tasks):
            progress = int(((i + 1) / total_tasks) * 100)
            await progress_callback(progress, f"正在处理任务 {i+1}/{total_tasks}: {task.taskTitle}")

            try:
                # 解析 payload 并提交到 TaskManager
                payload = task.payload
                if isinstance(payload, str):
                    payload = json.loads(payload)

                # 使用 webhook_search_and_dispatch_task 的逻辑
                # 使用默认参数 t=task, p=payload 捕获当前循环变量的值,避免闭包问题
                task_coro = lambda s, cb, t=task, p=payload: webhook_search_and_dispatch_task(
                    webhookSource=t.webhookSource,
                    progress_callback=cb,
                    session=s,
                    manager=self.scraper_manager,
                    task_manager=self.task_manager,
                    metadata_manager=self.metadata_manager,
                    config_manager=self.config_manager,
                    ai_matcher_manager=self.ai_matcher_manager,
                    rate_limiter=self.rate_limiter,
                    title_recognition_manager=self.title_recognition_manager,
                    **p
                )
                # 修正：不使用 run_immediately，让任务正常进入队列
                # 这样任务可以正确处理流控暂停和恢复
                task_id, _ = await self.task_manager.submit_task(
                    task_coro, task.taskTitle, unique_key=task.uniqueKey
                )
                # 修正：不等待任务完成，直接删除 webhook 记录
                # 任务会在队列中正常执行，不会因为流控暂停而卡住
                await session.delete(task)
                logger.info(f"Webhook 任务 '{task.taskTitle}' 已提交到任务队列 (ID: {task_id})")

            except Exception as e:
                logger.error(f"处理 Webhook 任务 (ID: {task.id}) 时失败: {e}", exc_info=True)
                # 如果提交失败，则将任务标记为失败，以便用户可以手动重试
                await crud.update_webhook_task_status(session, task.id, "failed")
            finally:
                await session.commit() # 确保状态更新或删除被提交