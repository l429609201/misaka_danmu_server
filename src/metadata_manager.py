import asyncio
import importlib
import traceback
import inspect
import logging
import pkgutil
from pathlib import Path
from typing import Any, Dict, List, Set, Optional, Type

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from fastapi import HTTPException, status, Request, APIRouter

from . import crud, models, orm_models
from .config_manager import ConfigManager
from .scraper_manager import ScraperManager

logger = logging.getLogger(__name__)
import httpx
class MetadataSourceManager:
    """
    通过动态加载来管理元数据源的状态和状态。
    此类发现、初始化并协调位于 `src/metadata_sources` 目录中的元数据源插件。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], config_manager: ConfigManager, scraper_manager: ScraperManager):
        """
        初始化管理器。

        Args:
            session_factory: 用于数据库访问的异步会话工厂。
            config_manager: 应用的配置管理器。
            scraper_manager: 应用的弹幕抓取器管理器。
        """
        self._session_factory = session_factory
        self._config_manager = config_manager
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # 按 provider_name 存储实例化的源对象。
        self.sources: Dict[str, Any] = {}
        # 在实例化之前存储发现的源类。
        self._source_classes: Dict[str, Type[Any]] = {}
        # 从数据库缓存所有源的持久设置。
        self.source_settings: Dict[str, Dict[str, Any]] = {}
        self.scraper_manager = scraper_manager
        # 新增：为所有元数据源创建一个父级路由器
        self.router = APIRouter()

    async def initialize(self):
        """在应用启动时加载并同步元数据源，并构建其API路由。"""
        await self.load_and_sync_sources()
        self._build_source_routers()
        logger.info("元数据源管理器已初始化。")

    def _build_source_routers(self):
        """
        遍历所有已加载的源，并将其API路由注册到管理器的路由器中。
        """
        self.logger.info("正在注册元数据源提供的API路由...")
        for provider_name, source_instance in self.sources.items():
            # 检查源实例是否有 'api_router' 属性，并且它是一个 APIRouter
            if hasattr(source_instance, 'api_router') and isinstance(getattr(source_instance, 'api_router', None), APIRouter):
                # 将每个源的路由包含到管理器的父级路由中，使用提供商名称作为前缀
                self.router.include_router(
                    source_instance.api_router,
                    prefix=f"/{provider_name}",
                    tags=[f"Metadata - {provider_name.capitalize()}"]
                )
                self.logger.info(f"已为源 '{provider_name}' 添加API路由，子前缀: /{provider_name}")

    async def load_and_sync_sources(self):
        """动态发现、同步到数据库并加载元数据源插件。"""
        await self.close_all()  # 在重新加载前确保旧连接已关闭
        self.sources.clear()
        self._source_classes.clear()
        self.source_settings.clear()

        discovered_providers = []
        
        sources_package_path = [str(Path(__file__).parent / "metadata_sources")]
        for finder, name, ispkg in pkgutil.iter_modules(sources_package_path):
            if name.startswith("_") or name == "base":
                continue

            try:
                module_name = f"src.metadata_sources.{name}"
                module = importlib.import_module(module_name)
                for class_name, obj in inspect.getmembers(module, inspect.isclass):
                    # 使用鸭子类型（duck typing）来识别插件，而不是依赖于一个共享的基类。
                    # 如果一个类有 'provider_name' 属性和 'search_aliases' 方法，我们就认为它是一个元数据源插件。
                    if (hasattr(obj, 'provider_name') and
                        hasattr(obj, 'search_aliases') and
                        hasattr(obj, 'get_details') and
                        obj.__module__ == module_name):
                        provider_name = obj.provider_name
                        if provider_name in self._source_classes:
                            self.logger.warning(f"发现重复的元数据源 '{provider_name}'。将被覆盖。")
                        
                        self._source_classes[provider_name] = obj
                        discovered_providers.append(provider_name)
                        self.logger.info(f"元数据源 '{provider_name}' (来自模块 {name}) 已发现。")
            except Exception as e:
                self.logger.error(f"从模块 {name} 加载元数据源失败: {e}", exc_info=True)

        async with self._session_factory() as session:
            await crud.sync_metadata_sources_to_db(session, discovered_providers)
            settings_list = await crud.get_all_metadata_source_settings(session)
        
        self.source_settings = {s['providerName']: s for s in settings_list}

        for provider_name, source_class in self._source_classes.items():
            self.sources[provider_name] = source_class(self._session_factory, self._config_manager, self.scraper_manager)
            self.logger.info(f"已加载元数据源 '{provider_name}'。")

    async def search_aliases_from_enabled_sources(self, keyword: str, user: models.User) -> Set[str]:
        """从所有已启用的辅助元数据源并发获取别名。"""
        async with self._session_factory() as session:
            enabled_sources_settings = await crud.get_enabled_aux_metadata_sources(session)
        
        tasks = []
        for source_setting in enabled_sources_settings:
            provider = source_setting['providerName']
            if source_instance := self.sources.get(provider):
                tasks.append(source_instance.search_aliases(keyword, user))
            else:
                self.logger.warning(f"已启用的元数据源 '{provider}' 未被成功加载，跳过别名搜索。")

        if not tasks:
            return set()

        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_aliases: Set[str] = set()
        for res in results:
            if isinstance(res, set):
                all_aliases.update(res)
            elif isinstance(res, Exception):
                self.logger.error(f"Auxiliary search sub-task failed: {res}", exc_info=False)
        
        # 过滤掉潜在的 None 或空字符串
        return {alias for alias in all_aliases if alias}

    async def get_sources_with_status(self) -> List[Dict[str, Any]]:
        """获取所有元数据源及其持久化和临时状态。"""
        tasks = []
        # 确保我们只检查已加载的源
        loaded_providers = list(self.sources.keys())
        for provider_name in loaded_providers:
            tasks.append(self.sources[provider_name].check_connectivity())
        
        connectivity_statuses = await asyncio.gather(*tasks, return_exceptions=True)
        status_map = dict(zip(loaded_providers, connectivity_statuses))

        full_status_list = []
        for provider_name, setting in self.source_settings.items():
            status_text = "检查失败"
            status_result = status_map.get(provider_name)
            if isinstance(status_result, str):
                status_text = status_result
            elif isinstance(status_result, Exception):
                self.logger.error(f"检查 '{provider_name}' 连接状态时出错: {status_result}")

            full_status_list.append({
                "providerName": provider_name,
                "isAuxSearchEnabled": setting.get('isAuxSearchEnabled', False),
                "displayOrder": setting.get('displayOrder', 99),
                "status": status_text,
                "useProxy": setting.get('useProxy', False)
            })
        
        return sorted(full_status_list, key=lambda x: x['displayOrder'])

    async def get_failover_comments(self, title: str, season: int, episode_index: int, user: models.User) -> Optional[List[dict]]:
        """
        Iterates through enabled failover sources to find comments for a specific episode.
        """
        async with self._session_factory() as session:
            enabled_sources_settings = await crud.get_enabled_failover_sources(session)
        
        for source_setting in enabled_sources_settings:
            provider = source_setting['providerName']
            if source_instance := self.sources.get(provider):
                self.logger.info(f"Failover: Trying source '{provider}' for '{title}' S{season}E{episode_index}")
                try:
                    comments = await source_instance.get_comments_by_failover(title, season, episode_index, user)
                    if comments:
                        self.logger.info(f"Failover: Source '{provider}' successfully found {len(comments)} comments.")
                        return comments
                except Exception as e:
                    self.logger.error(f"Failover source '{provider}' failed: {e}", exc_info=True)
            else:
                self.logger.warning(f"Enabled failover source '{provider}' was not loaded, skipping.")
        
        self.logger.info(f"Failover: No source could find comments for '{title}' S{season}E{episode_index}")
        return None

    async def search(self, provider: str, keyword: str, user: models.User, mediaType: Optional[str] = None) -> List[models.MetadataDetailsResponse]:
        """从特定提供商搜索媒体。"""
        if source_instance := self.sources.get(provider):
            return await source_instance.search(keyword, user, mediaType=mediaType)
        raise HTTPException(status_code=404, detail=f"未找到元数据源: {provider}")

    async def get_details(self, provider: str, item_id: str, user: models.User, mediaType: Optional[str] = None) -> Optional[models.MetadataDetailsResponse]:
        """从特定提供商获取详细信息。"""
        if source_instance := self.sources.get(provider):
            return await source_instance.get_details(item_id, user, mediaType=mediaType)
        raise HTTPException(status_code=404, detail=f"未找到元数据源: {provider}")

    async def execute_action(self, provider: str, action_name: str, payload: Dict, user: models.User, request: Request) -> Any:
        """执行特定提供商的自定义操作。"""
        if source_instance := self.sources.get(provider):
            return await source_instance.execute_action(action_name, payload, user, request=request)
        raise HTTPException(status_code=404, detail=f"未找到元数据源: {provider}")

    async def getProviderConfig(self, providerName: str) -> Dict[str, Any]:
        """
        获取特定元数据提供商的配置。
        """
        if providerName not in self.sources:
            raise HTTPException(status_code=404, detail=f"未找到元数据源: {providerName}")

        # 将提供商名称映射到其在数据库中的配置键
        config_keys_map = {
            "tmdb": ["tmdbApiKey", "tmdbApiBaseUrl", "tmdbImageBaseUrl"],
            "bangumi": ["bangumiClientId", "bangumiClientSecret", "bangumiToken"],
            "douban": ["doubanCookie"],
            "tvdb": ["tvdbApiKey"],
            "imdb": []  # IMDb 目前没有特定配置
        }

        keys_to_fetch = config_keys_map.get(providerName)
        if keys_to_fetch is None:
            self.logger.warning(f"提供商 '{providerName}' 已加载，但没有定义的配置键。")
            return {}

        config_values = {key: await self._config_manager.get(key, "") for key in keys_to_fetch}

        # 为单值配置提供特殊处理，以匹配前端期望的格式
        if providerName in ["douban", "tvdb"]:
            return {"value": next(iter(config_values.values()), "")}

        # 新增：为Bangumi添加 authMode 字段，以明确告知前端当前应显示哪种模式
        if providerName == "bangumi":
            if config_values.get("bangumiToken"):
                config_values["authMode"] = "token"
            else:
                config_values["authMode"] = "oauth"

        return config_values

    async def update_tmdb_mappings(self, tmdb_tv_id: int, group_id: str, user: models.User):
        """协调TMDB分集组映射的更新。现在此操作将委托给TMDB源（如果存在且具有该方法）。"""
        tmdb_source = self.sources.get("tmdb")
        if tmdb_source and hasattr(tmdb_source, "update_tmdb_mappings"):
            self.logger.info(f"管理器: 正在为 TMDB TV ID {tmdb_tv_id} 和 Group ID {group_id} 委派映射更新。")
            # 该方法需要在 TmdbMetadataSource 类中定义
            await tmdb_source.update_tmdb_mappings(tmdb_tv_id, group_id, user)
        else:
            self.logger.warning("TMDB 元数据源未加载或不支持 `update_tmdb_mappings` 方法。")

    async def close_all(self):
        """在应用关闭时关闭所有元数据源客户端。"""
        self.logger.info("正在关闭所有元数据源...")
        tasks = [source.close() for source in self.sources.values()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                self.logger.error(f"在清理的过程中发现了错误{result} 详细信息{traceback.format_exc()}")
                provider_name = list(self.sources.keys())[i]
                self.logger.error(f"关闭元数据源 '{provider_name}' 时出错: {result}")
        self.logger.info("所有元数据源已关闭。")
