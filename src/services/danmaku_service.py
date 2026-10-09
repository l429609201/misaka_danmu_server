"""弹幕只读服务：通过统一数据库入口定位文件并读取缓存。"""

import asyncio
import logging
from typing import List, Dict, Any, Optional

from src.services.database_service import DatabaseService
from src.services.service_container import get_database_service
from src.services.cache_service import get_cache_service
from src.services.file_storage_service import get_file_storage_service
from src.utils.parsing.danmaku_parser import parse_dandan_xml_to_comments

logger = logging.getLogger(__name__)


class DanmakuService:
    """只提供弹幕读取；保存和删除由上层调用既有弹幕 Workflow。"""

    def __init__(self, database_service: Optional[DatabaseService] = None) -> None:
        """注入数据库服务，不接收 Session 或自行实例化仓储。"""
        self._database_service = database_service

    async def fetch_comments(self, episode_id: int) -> List[Dict[str, Any]]:
        """在调用方事务中读取弹幕，缓存与文件错误不影响后备流程。"""
        cache_key = f"fetch_comments_{episode_id}"
        cache = None
        try:
            cache = get_cache_service()
            cached = await cache.get(key=cache_key, region="default")
            if cached is not None:
                return cached
        except Exception:
            pass

        db = self._database_service or get_database_service()
        episode = await db.episode.get_by_id(episode_id)
        if not episode or not episode.danmakuFilePath:
            return []
        try:
            fs = get_file_storage_service()
            absolute_path = fs.resolve_fs_path(episode.danmakuFilePath)
            if not absolute_path:
                return []
            xml_content = await fs.read_text(absolute_path)
            if xml_content is None:
                return []
            result = await asyncio.to_thread(parse_dandan_xml_to_comments, xml_content)
            if cache is not None:
                try:
                    await cache.set(key=cache_key, value=result, ttl=300, region="default")
                except Exception:
                    pass
            return result
        except Exception as exc:
            logger.error("读取或解析弹幕文件失败: %s。错误: %s", episode.danmakuFilePath, exc, exc_info=True)
            return []
