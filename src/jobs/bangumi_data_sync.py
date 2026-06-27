"""
bangumi-data 离线索引同步定时任务（cron 调度）

由 BGM 元数据源特殊配置中的「启用定时同步 + cron」驱动（方案甲）：
保存配置时后端自动在 scheduled_tasks 表创建/更新本任务实例，按 cron 周期执行。
执行时读取全局开关 bangumiDataSyncEnabled，关闭则跳过；开启则调 BangumiDataManager.sync()。
"""
import logging
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from src.jobs.base import BaseJob
from src.services import TaskSuccess

logger = logging.getLogger(__name__)


class BangumiDataSyncJob(BaseJob):
    """bangumi-data 离线索引同步任务"""
    job_type = "bangumiDataSync"
    job_name = "bangumi-data 离线索引同步"
    job_name_en = "bangumi-data Offline Index Sync"
    job_name_tw = "bangumi-data 離線索引同步"
    description = "定期从 CDN 拉取 bangumi-data 数据集并同步到本地离线索引，用于别名补全与平台映射。受 Bangumi 源配置中的开关控制。"
    description_en = "Periodically fetch the bangumi-data dataset from CDN into the local offline index for alias enrichment and platform mapping. Controlled by the switch in Bangumi source config."
    description_tw = "定期從 CDN 拉取 bangumi-data 資料集並同步到本地離線索引，用於別名補全與平台對映。受 Bangumi 來源設定中的開關控制。"
    is_system_task = False
    config_schema = []  # 配置集中在 Bangumi 源特殊配置，这里不暴露额外项

    async def run(self, session: AsyncSession, progress_callback: Callable):
        from src.services import get_bangumi_data_manager

        # 读取 Bangumi 源配置中的开关；关闭则跳过（方案甲：开关在 BGM 配置里）
        enabled = (await self.config_manager.get("bangumiDataSyncEnabled", "false")).lower() == "true"
        if not enabled:
            raise TaskSuccess("bangumi-data 定时同步未启用，已跳过。")

        manager = get_bangumi_data_manager()
        if manager is None:
            raise TaskSuccess("bangumi-data 管理器未就绪，已跳过。")

        await progress_callback(10, "正在从 CDN 拉取 bangumi-data...")
        result = await manager.sync()
        if result.get("success"):
            await progress_callback(100, f"同步完成，共 {result.get('count')} 条")
            raise TaskSuccess(f"bangumi-data 同步完成，共 {result.get('count')} 条。")
        else:
            raise TaskSuccess(f"bangumi-data 同步失败：{result.get('message')}")
