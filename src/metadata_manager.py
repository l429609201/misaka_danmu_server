import asyncio
import logging
from typing import Dict, List, Any

import aiomysql
import httpx

from . import crud

logger = logging.getLogger(__name__)

class MetadataSourceManager:
    """
    Manages the state and status of metadata sources.
    """
    def __init__(self, pool: aiomysql.Pool):
        self.pool = pool
        self.providers = ['tmdb', 'bangumi', 'douban', 'imdb', 'tvdb']
        # Ephemeral status, checked on startup
        self.connectivity_status: Dict[str, str] = {}

    async def initialize(self):
        """Syncs providers with DB and performs initial checks."""
        await crud.sync_metadata_sources_to_db(self.pool, self.providers)
        await self._check_connectivity()
        logger.info("元数据源管理器已初始化。")

    async def _check_connectivity(self):
        """Performs connectivity checks for sources that need it."""
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            # Check Douban
            try:
                douban_cookie = await crud.get_config_value(self.pool, "douban_cookie", "")
                headers = {"User-Agent": "Mozilla/5.0"}
                if douban_cookie:
                    headers["Cookie"] = douban_cookie
                await client.get("https://movie.douban.com/", headers=headers)
                self.connectivity_status['douban'] = "可访问"
            except Exception:
                self.connectivity_status['douban'] = "访问失败"
            
            # Check IMDb
            try:
                headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"}
                await client.get("https://www.imdb.com/", headers=headers)
                self.connectivity_status['imdb'] = "可访问"
            except Exception:
                self.connectivity_status['imdb'] = "访问失败"
        logger.info(f"元数据源连接状态检查完成: {self.connectivity_status}")

    async def get_sources_with_status(self) -> List[Dict[str, Any]]:
        """Gets all metadata sources with their persistent and ephemeral status."""
        settings = await crud.get_all_metadata_source_settings(self.pool)
        
        # Get config statuses in parallel
        config_keys = ["tmdb_api_key", "bangumi_client_id", "tvdb_api_key"]
        config_values = await asyncio.gather(*[crud.get_config_value(self.pool, key, "") for key in config_keys])
        tmdb_key, bgm_id, tvdb_key = config_values
        
        full_status_list = []
        for s in settings:
            provider = s['provider_name']
            status_text = "可访问" # 默认状态
            if provider == 'tmdb':
                status_text = "已配置" if tmdb_key else "未配置"
            elif provider == 'bangumi':
                status_text = "已配置" if bgm_id else "未配置"
            elif provider == 'tvdb':
                status_text = "已配置" if tvdb_key else "未配置"
            elif provider in self.connectivity_status:
                status_text = self.connectivity_status[provider]
            
            # 移除 is_enabled 字段，因为现在所有源都默认启用
            full_status_list.append({
                "provider_name": provider,
                "is_aux_search_enabled": s['is_aux_search_enabled'],
                "display_order": s['display_order'],
                "status": status_text
            })
            
        return full_status_list
