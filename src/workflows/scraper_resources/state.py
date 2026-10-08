"""资源下载共享缓存状态，不执行下载、部署或跨服务编排。"""
import asyncio
from datetime import datetime
from typing import Any, Dict, Optional


# 下载状态区域由读写双方共享，不再从执行器反向取常量。
SCRAPER_DOWNLOAD_TASK_CACHE_PREFIX = "scraper_download_task_"
SCRAPER_DOWNLOAD_TASK_CACHE_TTL = 3600
_download_lock = asyncio.Lock()


class ResourceVersionCache:
    """共享版本缓存状态，避免执行器依赖 HTTP 模块。"""

    def __init__(self) -> None:
        self.value: Optional[Dict[str, Any]] = None
        self.updated_at: Optional[datetime] = None

    def clear(self) -> None:
        """使下载后的版本查询重新读取文件。"""
        self.value = None
        self.updated_at = None


resource_version_cache = ResourceVersionCache()


