import asyncio
import logging
from typing import Any, Callable, Dict, List, Set

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import crud, models, security
from .api.bangumi_api import get_bangumi_client, search_bangumi_aliases
from .api.douban_api import get_douban_client, search_douban_aliases
from .api.tmdb_api import get_tmdb_client, search_tmdb_aliases, update_tmdb_mappings_for_tv_group
from .api.imdb_api import get_imdb_client, search_imdb_aliases
from .api.tvdb_api import get_tvdb_client, search_tvdb_aliases

logger = logging.getLogger(__name__)

class MetadataSourceManager:
    """
    Manages the state and status of metadata sources, and orchestrates auxiliary searches.
    """
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory
        self.logger = logging.getLogger(self.__class__.__name__)
        self.providers = ['tmdb', 'bangumi', 'douban', 'imdb', 'tvdb']
        # Ephemeral status, checked on startup
        self._provider_configs: Dict[str, List[str]] = {
            "tmdb": ["tmdb_api_key", "tmdb_api_base_url", "tmdb_image_base_url"],
            "bangumi": ["bangumi_client_id", "bangumi_client_secret"],
            "douban": ["douban_cookie"],
            "tvdb": ["tvdb_api_key"],
            "imdb": [], # IMDb has no specific config keys in this system
        }
        self.connectivity_status: Dict[str, str] = {}
        # Register the search functions
        self._search_functions: Dict[str, Callable] = {
            "tmdb": self._tmdb_alias_search_wrapper,
            "bangumi": self._bangumi_alias_search_wrapper,
            "douban": self._douban_alias_search_wrapper,
            "imdb": self._imdb_alias_search_wrapper,
            "tvdb": self._tvdb_alias_search_wrapper,
        }

    async def get_provider_config(self, provider_name: str, session: AsyncSession) -> Dict[str, str]:
        """
        获取指定元数据源的所有相关配置项。
        """
        if provider_name not in self.providers:
            raise ValueError(f"未知的元数据源: {provider_name}")
        
        keys_to_fetch = self._provider_configs.get(provider_name, [])
        if not keys_to_fetch:
            return {}
            
        tasks = [crud.get_config_value(session, key, "") for key in keys_to_fetch]
        values = await asyncio.gather(*tasks)
        return dict(zip(keys_to_fetch, values))


    async def initialize(self):
        """Syncs providers with DB and performs initial checks."""
        async with self._session_factory() as session:
            await crud.sync_metadata_sources_to_db(session, self.providers)
        await self._check_connectivity()
        logger.info("元数据源管理器已初始化。")

    # --- Wrappers to provide a consistent interface for search functions ---
    async def _tmdb_alias_search_wrapper(self, keyword: str, user: models.User, session: AsyncSession) -> Set[str]:
        async with await get_tmdb_client(user, session) as client:
            return await search_tmdb_aliases(keyword, client)

    async def _bangumi_alias_search_wrapper(self, keyword: str, user: models.User, session: AsyncSession) -> Set[str]:
        # Bangumi search doesn't strictly need auth, but the client getter is there.
        async with await get_bangumi_client(user, session) as client:
            return await search_bangumi_aliases(keyword, client)

    async def _douban_alias_search_wrapper(self, keyword: str, user: models.User, session: AsyncSession) -> Set[str]:
        async with await get_douban_client(user, session) as client:
            return await search_douban_aliases(keyword, client)

    async def update_tmdb_mappings(self, tmdb_tv_id: int, group_id: str, user: models.User, session: AsyncSession):
        """
        通过调用特定的tmdb_api函数来协调更新TMDB分集组映射。
        """
        self.logger.info(f"管理器: 开始为 TMDB TV ID {tmdb_tv_id} 和 Group ID {group_id} 更新映射。")
        try:
            # 管理器负责获取正确的客户端
            async with await get_tmdb_client(user, session) as client:
                # 并调用具体的实现
                await update_tmdb_mappings_for_tv_group(session, client, tmdb_tv_id, group_id)
            self.logger.info(f"管理器: 成功更新了 TV ID {tmdb_tv_id} 和 Group ID {group_id} 的TMDB映射。")
        except httpx.TimeoutException:
            self.logger.error(f"管理器: 更新TMDB映射时发生超时错误 (ID: {tmdb_tv_id})。请检查您的网络连接、代理设置或TMDB服务器状态。")
        except httpx.ConnectError as e:
            self.logger.error(f"管理器: 更新TMDB映射时发生连接错误 (ID: {tmdb_tv_id})。请检查TMDB服务器是否可达或您的网络/代理设置。错误: {e}")
        except httpx.HTTPStatusError as e:
            self.logger.error(f"管理器: 更新TMDB映射时收到非2xx的HTTP状态码 (ID: {tmdb_tv_id}): {e.response.status_code} - 响应: {e.response.text}")
        except Exception as e:
            self.logger.error(f"管理器: 更新TMDB映射时发生错误: {e}", exc_info=True)
            # 不重新抛出异常，只记录错误。主操作不应因此失败。

    async def _imdb_alias_search_wrapper(self, keyword: str, user: models.User, session: AsyncSession) -> Set[str]:
        async with await get_imdb_client(user, session) as client:
            return await search_imdb_aliases(keyword, client)

    async def _tvdb_alias_search_wrapper(self, keyword: str, user: models.User, session: AsyncSession) -> Set[str]:
        async with await get_tvdb_client(user, session) as client:
            return await search_tvdb_aliases(keyword, client)

    async def search_aliases_from_enabled_sources(self, keyword: str, user: models.User, session: AsyncSession) -> Set[str]:
        """
        From all enabled auxiliary metadata sources, concurrently fetch aliases.
        This method now accepts user and session to pass down to client getters.
        """
        enabled_sources = await crud.get_enabled_aux_metadata_sources(session)
        tmdb_api_key = await crud.get_config_value(session, "tmdb_api_key", "")

        tasks = []
        for source_setting in enabled_sources:
            provider = source_setting['providerName']
            if provider == 'tmdb' and not tmdb_api_key:
                continue # Skip TMDB if key is not set

            if search_func_wrapper := self._search_functions.get(provider):
                tasks.append(search_func_wrapper(keyword, user, session))

        if not tasks:
            return set()

        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_aliases: Set[str] = set()
        for res in results:
            if isinstance(res, set):
                all_aliases.update(res)
            elif isinstance(res, Exception):
                self.logger.error(f"Auxiliary search sub-task failed: {res}", exc_info=False)
        return all_aliases

    async def _check_connectivity(self):
        """Performs connectivity checks for sources that need it."""
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            async with self._session_factory() as session:
                # Check Douban
                try:
                    douban_cookie = await crud.get_config_value(session, "douban_cookie", "")
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
        async with self._session_factory() as session:
            settings = await crud.get_all_metadata_source_settings(session)
            
            # Get config statuses in parallel
            config_keys = ["tmdb_api_key", "bangumi_client_id", "tvdb_api_key"]
            config_values = await asyncio.gather(*[crud.get_config_value(session, key, "") for key in config_keys])
        tmdb_key, bgm_id, tvdb_key = config_values
        
        full_status_list = []
        for s in settings:
            provider = s['providerName']
            status_text = "可访问" # 默认状态
            if provider == 'tmdb':
                status_text = "已配置" if tmdb_key else "未配置"
            elif provider == 'bangumi':
                status_text = "已配置" if bgm_id else "未配置"
            elif provider == 'tvdb':
                status_text = "已配置" if tvdb_key else "未配置"
            elif provider in self.connectivity_status:
                status_text = self.connectivity_status[provider]
            
            full_status_list.append({
                "providerName": provider,
                "isAuxSearchEnabled": s['isAuxSearchEnabled'],
                "displayOrder": s['displayOrder'],
                "status": status_text,
                "useProxy": s['useProxy']
            })
            
        return full_status_list
