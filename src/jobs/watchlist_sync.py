"""
主动订阅同步定时任务
支持从 Bangumi 收藏 / Trakt Watchlist 自动同步追更列表

工作流程:
1. 拉取用户在第三方平台的「在看」/「Watchlist」列表
2. 与本地弹幕库做 bangumiId/traktId 匹配
3. 对于已有但未追更的 → 自动开启追更
4. 对于本地没有的 → 通过搜索弹幕源自动导入
"""
import logging
from typing import Callable
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.db import crud, orm_models
from src.jobs.base import BaseJob
from src.services import TaskSuccess

logger = logging.getLogger(__name__)


class WatchlistSyncJob(BaseJob):
    """主动订阅同步 — 从第三方平台自动同步追更列表"""
    job_type = "watchlistSync"
    job_name = "收藏列表同步"
    job_name_en = "Watchlist Sync"
    job_name_tw = "收藏列表同步"
    description = "自动从 Bangumi「在看」或 Trakt Watchlist 同步追更列表，新增的作品将自动搜索弹幕源并开启追更。"
    description_en = "Auto-sync watchlist from Bangumi or Trakt. New entries will be auto-matched with danmaku sources and tracked."
    description_tw = "自動從 Bangumi「在看」或 Trakt Watchlist 同步追更列表，新增的作品將自動搜尋彈幕源並開啟追更。"

    # 定时任务配置 schema（用户可配置同步哪些平台）
    config_schema = [
        {
            "key": "syncBangumi",
            "type": "boolean",
            "label": "同步 Bangumi 在看列表",
            "label_en": "Sync Bangumi Watching List",
            "label_tw": "同步 Bangumi 在看列表",
            "default": True,
        },
        {
            "key": "syncTrakt",
            "type": "boolean",
            "label": "同步 Trakt Watchlist",
            "label_en": "Sync Trakt Watchlist",
            "label_tw": "同步 Trakt Watchlist",
            "default": False,
        },
    ]

    async def run(self, session: AsyncSession, progress_callback: Callable):
        """主同步逻辑"""
        config = self.task_config or {}
        sync_bangumi = config.get("syncBangumi", True)
        sync_trakt = config.get("syncTrakt", False)

        total_synced = 0
        results = []

        if sync_bangumi:
            await progress_callback(10, "正在同步 Bangumi 在看列表...")
            count = await self._sync_bangumi(session)
            total_synced += count
            results.append(f"Bangumi: {count} 部")

        if sync_trakt:
            await progress_callback(50, "正在同步 Trakt Watchlist...")
            count = await self._sync_trakt(session)
            total_synced += count
            results.append(f"Trakt: {count} 部")

        summary = "、".join(results) if results else "未启用任何同步源"
        raise TaskSuccess(f"收藏列表同步完成。{summary}，共同步 {total_synced} 部。")

    async def _sync_bangumi(self, session: AsyncSession) -> int:
        """从 Bangumi 的「在看」收藏列表同步
        
        TODO: 实现以下逻辑
        1. 获取第一个用户的 Bangumi OAuth token
        2. 调用 GET /v0/users/{username}/collections?subject_type=2&type=3 (type=3 是「在看」)
        3. 遍历返回的 subject 列表
        4. 用 bangumiId 匹配本地的 anime_metadata
        5. 已有但未追更的 → 开启追更
        6. 本地没有的 → 记录日志（后续可接入自动导入）
        """
        self.logger.info("Bangumi 在看列表同步: 功能骨架已就绪，待完善具体同步逻辑")
        return 0

    async def _sync_trakt(self, session: AsyncSession) -> int:
        """从 Trakt Watchlist 同步
        
        TODO: 实现以下逻辑
        1. 获取用户的 Trakt OAuth token (从 oauth_credentials 表)
        2. 调用 GET /calendars/my/shows 或 GET /users/me/watchlist/shows
        3. 遍历返回的 show 列表
        4. 用 tmdbId/imdbId 匹配本地的 anime_metadata
        5. 已有但未追更的 → 开启追更
        6. 本地没有的 → 记录日志
        """
        self.logger.info("Trakt Watchlist 同步: 功能骨架已就绪，待完善具体同步逻辑")
        return 0
