import asyncio
import importlib
import traceback
import inspect
import logging
import pkgutil
from pathlib import Path
from typing import Any, Dict, List, Set, Optional, Type, Tuple

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from fastapi import HTTPException, status, Request, APIRouter
import httpx

from src.db import crud, models, orm_models, ConfigManager, CacheManager
from .scraper_manager import ScraperManager
from src.metadata_sources.base import BaseMetadataSource

logger = logging.getLogger(__name__)

class MetadataSourceManager:
    """
    通过动态加载来管理元数据源的状态和状态。
    此类发现、初始化并协调位于 `src/metadata_sources` 目录中的元数据源插件。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], config_manager: ConfigManager, scraper_manager: ScraperManager, cache_manager: CacheManager):
        """
        初始化管理器。

        Args:
            session_factory: 用于数据库访问的异步会话工厂。
            config_manager: 应用的配置管理器。
            scraper_manager: 应用的弹幕抓取器管理器。
            cache_manager: 应用的缓存管理器。
        """
        self._session_factory = session_factory
        self._config_manager = config_manager
        self.cache_manager = cache_manager
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
        # 季度映射器(延迟初始化)
        self._season_mapper = None

    async def initialize(self):
        """在应用启动时加载并同步元数据源，并构建其API路由。"""
        await self.load_and_sync_sources()
        self._build_source_routers()
        # 初始化季度映射器
        from src.utils.season_mapper import SeasonMapper
        self._season_mapper = SeasonMapper(self, self._session_factory)
        logger.info("元数据源管理器已初始化。")

    @property
    def season_mapper(self):
        """获取季度映射器实例"""
        if self._season_mapper is None:
            raise RuntimeError("SeasonMapper未初始化,请先调用initialize()")
        return self._season_mapper

    def get_source(self, provider_name: str) -> Any:
        """
        根据提供方名称获取元数据源的实例。
        """
        source_instance = self.sources.get(provider_name)
        if not source_instance:
            # 抛出 ValueError 以匹配 ui_api.py 中已有的异常处理逻辑
            raise ValueError(f"未找到或未启用名为 '{provider_name}' 的元数据源。")
        return source_instance

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

    async def has_any_enabled_aux_source(self) -> bool:
        """
        Checks if there are any metadata sources enabled for auxiliary search,
        including those that are force-enabled.
        """
        for provider, settings in self.source_settings.items():
            if not settings.get('isEnabled'):
                continue
            
            # Check if it's enabled for aux search in its settings
            if settings.get('isAuxSearchEnabled'):
                return True
            
            # Check if it's force-enabled via global config
            force_enabled_str = await self._config_manager.get(f"{provider}_force_aux_search", "false")
            if force_enabled_str.lower() == 'true':
                return True
        
        return False

    async def load_and_sync_sources(self):
        """动态发现、同步到数据库并加载元数据源插件。"""
        await self.close_all()  # 在重新加载前确保旧连接已关闭
        self.sources.clear()
        self._source_classes.clear()
        self.source_settings.clear()

        discovered_providers = []

        # 检测环境并使用正确的路径
        def _is_docker_environment():
            """检测是否在Docker容器中运行"""
            import os
            # 方法1: 检查 /.dockerenv 文件（Docker标准做法）
            if Path("/.dockerenv").exists():
                return True
            # 方法2: 检查环境变量
            if os.getenv("DOCKER_CONTAINER") == "true" or os.getenv("IN_DOCKER") == "true":
                return True
            # 方法3: 检查当前工作目录是否为 /app
            if Path.cwd() == Path("/app"):
                return True
            return False

        if _is_docker_environment():
            sources_package_path = [str(Path("/app/src/metadata_sources"))]
        else:
            # 源码运行环境：__file__ 在 src/services/ 下，需要往上一级到 src/，再拼 metadata_sources
            sources_package_path = [str(Path(__file__).parent.parent / "metadata_sources")]
        for finder, name, ispkg in pkgutil.iter_modules(sources_package_path):
            if name.startswith("_") or name == "base":
                continue

            try:
                module_name = f"src.metadata_sources.{name}"
                module = importlib.import_module(module_name)
                for class_name, obj in inspect.getmembers(module, inspect.isclass):
                    # 修正：直接检查是否为 BaseMetadataSource 的子类，这比鸭子类型更可靠
                    if (issubclass(obj, BaseMetadataSource) and
                        obj is not BaseMetadataSource and
                        obj.__module__ == module_name):
                        provider_name = obj.provider_name
                        if provider_name in self._source_classes:
                            self.logger.warning(f"发现重复的元数据源 '{provider_name}'。将被覆盖。")

                        self._source_classes[provider_name] = obj
                        discovered_providers.append(provider_name)
            except Exception as e:
                self.logger.error(f"从模块 {name} 加载元数据源失败: {e}", exc_info=True)

        async with self._session_factory() as session:
            await crud.sync_metadata_sources_to_db(session, discovered_providers)
            settings_list = await crud.get_all_metadata_source_settings(session)

        self.source_settings = {s['providerName']: s for s in settings_list}

        for provider_name, source_class in self._source_classes.items():
            self.sources[provider_name] = source_class(self._session_factory, self._config_manager, self.scraper_manager, self.cache_manager)

        # 汇总输出
        _P = "           - "
        log_lines = [f"已加载 {len(self.sources)} 个元数据源"]
        for pn in sorted(self.sources.keys()):
            log_lines.append(f"{_P}{pn}")
        self.logger.info("\n".join(log_lines))

    async def search_aliases_from_enabled_sources(self, keyword: str, user: models.User) -> Set[str]:
        """从所有已启用的辅助元数据源并发获取别名。"""
        # 修正：调用新的、更通用的方法，并只返回别名部分
        aliases, _ = await self.search_supplemental_sources(keyword, user)
        return aliases

    async def search_supplemental_sources(self, keyword: str, user: models.User) -> Tuple[Set[str], List[models.ProviderSearchInfo]]:
        """
        从所有启用的辅助源（包括强制启用的）进行搜索。
        返回一个元组：(别名集合, 补充搜索结果列表)

        优化：对于 TMDB/Bangumi 等源，搜索结果不包含完整别名，
        需要对前几个结果调用 get_details 获取完整别名（包括中文别名）。
        """
        enabled_sources_settings = []
        for provider, settings in self.source_settings.items():
            if not settings.get('isEnabled'):
                continue

            force_enabled_str = await self._config_manager.get(f"{provider}_force_aux_search", "false")
            force_enabled = force_enabled_str.lower() == 'true'

            if settings.get('isAuxSearchEnabled') or force_enabled:
                enabled_sources_settings.append(settings)

        if not enabled_sources_settings:
            return set(), []

        tasks = []
        for source_setting in enabled_sources_settings:
            provider = source_setting['providerName']
            if source_instance := self.sources.get(provider):
                # 优化：为 TMDB 使用 multi 搜索，一次性搜索 tv 和 movie
                if provider == 'tmdb':
                    tasks.append(source_instance.search(keyword, user, mediaType='multi'))
                else:
                    # 对于其他源，正常调用
                    tasks.append(source_instance.search(keyword, user))
            else:
                self.logger.warning(f"已启用的元数据源 '{provider}' 未被成功加载，跳过辅助搜索。")

        if not tasks:
            return set(), []

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_aliases: Set[str] = set()
        supplemental_results: List[models.ProviderSearchInfo] = []

        # 收集需要获取详情的任务（用于获取完整别名）
        detail_tasks = []
        detail_task_info = []  # 记录每个任务对应的 (provider_name, source_instance, item)

        for i, res in enumerate(results):
            # 优化：现在每个源只产生一个任务，直接使用索引获取 provider_name
            provider_name = enabled_sources_settings[i]['providerName'] if i < len(enabled_sources_settings) else 'unknown'
            source_instance = self.sources.get(provider_name)

            if isinstance(res, list):
                self.logger.info(f"辅助源 '{provider_name}' 为关键词 '{keyword}' 找到了 {len(res)} 个结果。")

                # 对于 TMDB/TVDB/IMDB 等源，搜索结果不包含完整别名
                # 需要对前几个结果调用 get_details 获取完整别名
                # 注意：Bangumi 的 search 方法内部已经调用了 get_details，不需要再次获取
                needs_detail_fetch = provider_name in ['tmdb', 'tvdb', 'imdb']
                detail_fetch_count = 0
                max_detail_fetch = 3  # 最多获取前3个结果的详情

                for item in res:
                    all_aliases.add(item.title)

                    # 添加中文别名
                    if item.aliasesCn:
                        all_aliases.update(item.aliasesCn)

                    # 添加日文别名列表
                    if item.aliasesJp:
                        all_aliases.update(item.aliasesJp)

                    # 添加单个日文别名
                    if item.nameJp:
                        all_aliases.add(item.nameJp)

                    # 添加英文别名
                    if item.nameEn:
                        all_aliases.add(item.nameEn)

                    # 添加罗马音别名
                    if item.nameRomaji:
                        all_aliases.add(item.nameRomaji)

                    # 如果搜索结果没有别名，且源支持获取详情，则添加到详情获取队列
                    if needs_detail_fetch and source_instance and detail_fetch_count < max_detail_fetch:
                        # 检查是否已经有别名（如果有就不需要再获取详情）
                        has_aliases = bool(item.aliasesCn or item.aliasesJp or item.nameJp or item.nameEn)
                        if not has_aliases:
                            # 确定 mediaType
                            media_type = item.type if hasattr(item, 'type') and item.type else 'tv'
                            detail_tasks.append(source_instance.get_details(item.id, user, mediaType=media_type))
                            detail_task_info.append((provider_name, item))
                            detail_fetch_count += 1

                    # 如果是 'douban' 或 '360'，则将其结果添加到补充列表中
                    if provider_name in ['douban', '360']:
                        # 构建补充结果,包含extra字段用于传递原始数据
                        supp_info = models.ProviderSearchInfo(
                            provider=provider_name, mediaId=item.id, title=item.title,
                            type=item.type if hasattr(item, 'type') and item.type else 'unknown',
                            season=1,
                            year=item.year if hasattr(item, 'year') else None,
                            imageUrl=item.imageUrl,
                            supportsEpisodeUrls=item.supportsEpisodeUrls  # 传递是否支持分集URL的标志
                        )
                        supplemental_results.append(supp_info)
            elif isinstance(res, Exception):
                if isinstance(res, httpx.ConnectError):
                    self.logger.warning(f"无法连接到元数据源 '{provider_name}'。请检查网络连接或代理设置。")
                elif isinstance(res, (httpx.TimeoutException, httpx.ReadTimeout)):
                    self.logger.warning(f"连接元数据源 '{provider_name}' 超时。")
                else:
                    self.logger.error(f"元数据源 '{provider_name}' 的辅助搜索子任务失败: {res}", exc_info=False)

        # 并行获取详情以获取完整别名
        if detail_tasks:
            self.logger.info(f"正在获取 {len(detail_tasks)} 个搜索结果的详情以获取完整别名...")
            detail_results = await asyncio.gather(*detail_tasks, return_exceptions=True)

            for j, detail_res in enumerate(detail_results):
                if isinstance(detail_res, models.MetadataDetailsResponse):
                    provider_name, original_item = detail_task_info[j]

                    # 添加详情中的别名
                    if detail_res.aliasesCn:
                        self.logger.debug(f"从 {provider_name} 详情获取到中文别名: {detail_res.aliasesCn}")
                        all_aliases.update(detail_res.aliasesCn)
                    if detail_res.aliasesJp:
                        all_aliases.update(detail_res.aliasesJp)
                    if detail_res.nameJp:
                        all_aliases.add(detail_res.nameJp)
                    if detail_res.nameEn:
                        all_aliases.add(detail_res.nameEn)
                    if detail_res.nameRomaji:
                        all_aliases.add(detail_res.nameRomaji)
                elif isinstance(detail_res, Exception):
                    provider_name, _ = detail_task_info[j]
                    self.logger.debug(f"获取 {provider_name} 详情失败: {detail_res}")

        return {alias for alias in all_aliases if alias}, supplemental_results

    async def get_sources_with_status(self) -> List[Dict[str, Any]]:
        """获取所有元数据源及其持久化和临时状态。"""
        tasks = []
        # 确保我们只检查已加载且已启用的源
        loaded_providers = list(self.sources.keys())
        enabled_providers = []
        for provider_name in loaded_providers:
            # 检查源是否启用
            setting = self.source_settings.get(provider_name, {})
            if setting.get('isEnabled', True):  # 默认启用
                tasks.append(self.sources[provider_name].check_connectivity())
                enabled_providers.append(provider_name)

        connectivity_statuses = await asyncio.gather(*tasks, return_exceptions=True)
        status_map = dict(zip(enabled_providers, connectivity_statuses))

        full_status_list = []
        for provider_name, setting in self.source_settings.items():
            # 检查源是否启用
            is_enabled = setting.get('isEnabled', True)

            if is_enabled:
                status_text = "检查失败"
                status_result = status_map.get(provider_name)
                if isinstance(status_result, str):
                    status_text = status_result
                elif isinstance(status_result, Exception):
                    self.logger.error(f"检查 '{provider_name}' 连接状态时出错: {status_result}")
            else:
                # 禁用的源显示为"已禁用"状态
                status_text = "已禁用"

            full_status_list.append({
                "providerName": provider_name,
                "isEnabled": is_enabled,
                "isAuxSearchEnabled": setting.get('isAuxSearchEnabled', False),
                "isFailoverEnabled": setting.get('isFailoverEnabled', False),
                "displayOrder": setting.get('displayOrder', 99),
                "status": status_text,
                "useProxy": setting.get('useProxy', False),
                "logRawResponses": setting.get('log_raw_responses', False),
            })
        
        return sorted(full_status_list, key=lambda x: x['displayOrder'])

    async def update_source_settings(self, settings_payload: List[models.MetadataSourceSettingUpdate]):
        """
        Updates settings for multiple metadata sources and reloads them to reflect changes immediately.
        This is the correct way to update settings as it ensures the in-memory cache is invalidated.
        """
        async with self._session_factory() as session:
            # The CRUD function handles the update logic and commits the transaction.
            await crud.update_metadata_sources_settings(session, settings_payload)
        
        # After updating the DB, reload all sources to apply the new settings.
        # This ensures that enable/disable, proxy settings, etc., take effect immediately.
        await self.load_and_sync_sources()
        self.logger.info("元数据源设置已更新并重新加载。")

    async def get_failover_comments(self, title: str, season: int, episode_index: int, user: models.User) -> Optional[List[dict]]:
        """
        Iterates through enabled failover sources to find comments for a specific episode.
        """
        async with self._session_factory() as session:
            enabled_sources_settings = await crud.get_enabled_failover_sources(session)
        
        for source_setting in enabled_sources_settings:
            provider = source_setting['providerName']
            source_instance = self.sources.get(provider)
            if not source_instance:
                self.logger.warning(f"Enabled failover source '{provider}' was not loaded, skipping.")
                continue
            
            self.logger.info(f"Failover: Trying source '{provider}' for '{title}' S{season}E{episode_index}")
            try:
                comments = await source_instance.get_comments_by_failover(title, season, episode_index, user)
                if comments:
                    self.logger.info(f"Failover: Source '{provider}' successfully found {len(comments)} comments.")
                    return comments
            except Exception as e:
                self.logger.error(f"Failover source '{provider}' failed: {e}", exc_info=True)
        
        self.logger.info(f"Failover: No source could find comments for '{title}' S{season}E{episode_index}")
        return None

    async def supplement_search_result(self, target_provider: str, keyword: str, episode_info: Optional[Dict[str, Any]]) -> List[models.ProviderSearchInfo]:
        """
        当主搜索源未找到结果时，主动通过故障转移源（如360）查找对应平台的链接，并返回结果。
        """
        self.logger.info(f"主搜索源 '{target_provider}' 未找到结果，正在尝试故障转移...")
        
        async with self._session_factory() as session:
            failover_sources_settings = await crud.get_enabled_failover_sources(session)
        
        user = models.User(id=0, username="system")
        
        for source_setting in failover_sources_settings:
            provider_name = source_setting['providerName']
            source_instance = self.sources.get(provider_name)
            if not source_instance or not hasattr(source_instance, "find_url_for_provider"):
                continue

            self.logger.info(f"故障转移: 正在使用 '{provider_name}' 查找 '{keyword}' 的 '{target_provider}' 链接...")
            target_url = await source_instance.find_url_for_provider(keyword, target_provider, user)
            if not target_url:
                continue

            self.logger.info(f"故障转移成功: 从 '{provider_name}' 找到URL: {target_url}")
            try:
                target_scraper = self.scraper_manager.get_scraper(target_provider)
                info = await target_scraper.get_info_from_url(target_url)
                if info:
                    return [info]
            except Exception as e:
                self.logger.error(f"通过故障转移URL '{target_url}' 获取信息失败: {e}")
                continue

        return []

    async def find_new_media_id(self, source_info: Dict[str, Any]) -> Optional[str]:
        """
        当获取分集列表失败时，尝试通过故障转移源查找新的 mediaId。
        """
        target_provider = source_info["providerName"]
        title = source_info["title"]
        season = source_info.get("season", 1)
        self.logger.info(f"分集获取失败，正在为 '{title}' S{season} ({target_provider}) 尝试故障转移查找新 mediaId...")

        async with self._session_factory() as session:
            failover_sources_settings = await crud.get_enabled_failover_sources(session)
        
        user = models.User(id=0, username="system")

        for source_setting in failover_sources_settings:
            provider_name = source_setting['providerName']
            if source_instance := self.sources.get(provider_name):
                if hasattr(source_instance, "find_url_for_provider"):
                    target_url = await source_instance.find_url_for_provider(title, target_provider, user, season=season)
                    if target_url:
                        return await self.scraper_manager.get_scraper(target_provider).get_id_from_url(target_url)
        return None

    async def search(self, provider: str, keyword: str, user: models.User, mediaType: Optional[str] = None) -> List[models.MetadataDetailsResponse]:
        """从特定提供商搜索媒体。"""
        if source_instance := self.sources.get(provider):
            return await source_instance.search(keyword, user, mediaType=mediaType)
        raise HTTPException(status_code=404, detail=f"未找到元数据源: {provider}")

    async def get_details(self, provider: str, item_id: str, user: models.User, mediaType: Optional[str] = None) -> Optional[models.MetadataDetailsResponse]:
        """从特定提供商获取详细信息。"""
        if source_instance := self.sources.get(provider):
            try:
                return await source_instance.get_details(item_id, user, mediaType=mediaType)
            except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadTimeout) as e:
                # 捕获常见的网络错误，记录警告并返回None，以避免后台任务崩溃
                self.logger.warning(f"从 '{provider}' 获取详情 (ID: {item_id}) 时发生网络错误: {e}")
                return None
            except Exception as e:
                # 捕获其他潜在错误
                self.logger.error(f"从 '{provider}' 获取详情 (ID: {item_id}) 时发生未知错误: {e}")
                return None
        raise HTTPException(status_code=404, detail=f"未找到元数据源: {provider}")

    async def execute_action(self, provider: str, action_name: str, payload: Dict, user: models.User, request: Request) -> Any:
        """执行特定提供商的自定义操作。"""
        if source_instance := self.sources.get(provider):
            return await source_instance.execute_action(action_name, payload, user, request=request)
        raise HTTPException(status_code=404, detail=f"未找到元数据源: {provider}")

    async def getProviderConfig(self, providerName: str) -> Dict[str, Any]:
        """
        获取特定提供商（元数据源或搜索源）的配置。
        """

        # 将提供商名称映射到其在数据库中的配置键
        config_keys_map = {
            # Metadata Sources
            "tmdb": ["tmdbApiKey", "tmdbApiBaseUrl", "tmdbImageBaseUrl"],
            "bangumi": ["bangumiClientId", "bangumiClientSecret", "bangumiToken", "authMode"],
            "douban": ["doubanCookie"],
            "tvdb": ["tvdbApiKey"],
            "imdb": ["imdbUseApi", "imdbEnableFallback"],  # IMDb 配置
            # Scrapers
            "gamer": ["gamerCookie", "gamerUserAgent", "gamerEpisodeBlacklistRegex", "scraperGamerLogResponses"],
        }

        keys_to_fetch = config_keys_map.get(providerName)

        # 如果提供商没有特定的配置键，检查它是否是一个已知的提供商
        if keys_to_fetch is None:
            is_known_metadata_source = providerName in self.sources
            # 修正：即使没有特定配置键，只要是已知的元数据源，就继续执行
            if is_known_metadata_source:
                config_values = {}
            else:
                raise ValueError(f"未找到提供商: {providerName}")
        else:
            config_values = {}
            for key in keys_to_fetch:
                value_str = await self._config_manager.get(key, "")
                # 对于IMDB的布尔值配置,转换为布尔类型
                if key in ['imdbUseApi', 'imdbEnableFallback']:
                    config_values[key] = value_str.lower() == 'true' if value_str else True
                else:
                    config_values[key] = value_str

        # 新增：从数据库获取 useProxy 和 logRawResponses 并添加到配置中
        # 修正：将此逻辑移到更前面，确保所有源都能执行
        async with self._session_factory() as session:
            provider_settings = await crud.get_metadata_source_setting_by_name(session, providerName)
            if provider_settings:
                config_values.update(provider_settings)

        # 新增：如果源支持强制辅助搜索，则从config表获取其状态
        source_class = self._source_classes.get(providerName)
        if source_class and getattr(source_class, 'has_force_aux_search_toggle', False):
            force_enabled_str = await self._config_manager.get(f"{providerName}_force_aux_search", "false")
            config_values['forceAuxSearchEnabled'] = force_enabled_str.lower() == 'true'

        # 新增：告知前端此源是否为故障转移源，以决定是否显示“强制辅助”开关
        if source_class:
            config_values['isFailoverSource'] = getattr(source_class, 'is_failover_source', False)
            # 新增：告知前端此源是否支持获取分集URL (补充源功能)
            supports_episode_urls = getattr(source_class, 'supports_episode_urls', False)
            config_values['supportsEpisodeUrls'] = supports_episode_urls
            # 如果支持补充源，则读取其启用状态
            if supports_episode_urls:
                episode_urls_enabled_str = await self._config_manager.get(f"{providerName}_episode_urls_enabled", "false")
                config_values['episodeUrlsEnabled'] = episode_urls_enabled_str.lower() == 'true'


        # 添加特殊逻辑：Bangumi 认证模式
        if providerName == "bangumi":
            # 如果数据库中没有保存 authMode，设置默认值为 token
            if not config_values.get("authMode"):
                config_values["authMode"] = "token"

        return config_values

    async def updateProviderConfig(self, providerName: str, payload: Dict[str, Any]):
        """
        更新特定提供商（元数据源或搜索源）的配置。
        """
        # 1. 验证提供商是否存在
        if providerName not in self.sources:
            raise HTTPException(status_code=404, detail=f"提供商 '{providerName}' 不存在或未加载。")

        # 2. 准备要更新的字段
        db_fields_to_update = {}
        config_fields_to_update: Dict[str, str] = {}

        # 2a. 识别属于 metadata_sources 表的字段
        if 'logRawResponses' in payload:
            db_fields_to_update['logRawResponses'] = bool(payload.pop('logRawResponses', False))
        if 'useProxy' in payload:
            db_fields_to_update['useProxy'] = bool(payload.pop('useProxy', False))
        # 新增：将 isFailoverEnabled 的更新也移到此接口
        if 'isFailoverEnabled' in payload:
            db_fields_to_update['isFailoverEnabled'] = bool(payload.pop('isFailoverEnabled', False))
        
        # 新增：处理 forceAuxSearchEnabled，它现在存储在 config 表中
        if 'forceAuxSearchEnabled' in payload:
            force_enabled_value = str(payload.pop('forceAuxSearchEnabled', False)).lower()
            config_key = f"{providerName}_force_aux_search"
            config_fields_to_update[config_key] = force_enabled_value

        # 新增：处理 episodeUrlsEnabled (补充源功能)，存储在 config 表中
        if 'episodeUrlsEnabled' in payload:
            episode_urls_enabled_value = str(payload.pop('episodeUrlsEnabled', False)).lower()
            config_key = f"{providerName}_episode_urls_enabled"
            config_fields_to_update[config_key] = episode_urls_enabled_value

        # 2b. 识别属于 config 表的字段
        allowed_keys_map = {
            "tmdb": ["tmdbApiKey", "tmdbApiBaseUrl", "tmdbImageBaseUrl"],
            "bangumi": ["bangumiClientId", "bangumiClientSecret", "bangumiToken"],
            "douban": ["doubanCookie"],
            "tvdb": ["tvdbApiKey"],
            "imdb": ["imdbUseApi", "imdbEnableFallback"],
        }
        allowed_keys = allowed_keys_map.get(providerName)
        if allowed_keys:
            for key, value in payload.items():
                if key in allowed_keys:
                    # 对于布尔值,转换为字符串 "true" 或 "false"
                    if isinstance(value, bool):
                        config_fields_to_update[key] = str(value).lower()
                    else:
                        config_fields_to_update[key] = str(value if value is not None else "")

        # 3. 检查是否有任何需要更新的内容
        if not db_fields_to_update and not config_fields_to_update:
            self.logger.info(f"为提供商 '{providerName}' 收到配置更新请求，但没有可识别的字段需要更新。")
            return {"message": "没有可更新的配置项。"}

        # 4. 执行数据库操作
        async with self._session_factory() as session:
            if db_fields_to_update:
                await crud.update_metadata_source_specific_settings(session, providerName, db_fields_to_update)
            
            if config_fields_to_update:
                for key, value in config_fields_to_update.items():
                    await crud.update_config_value(session, key, value)
                    self._config_manager.invalidate(key)
            
            await session.commit()
        
        # 如果是元数据源的配置更新，重新加载它们以使更改生效
        if providerName in self.sources:
            await self.load_and_sync_sources()
            self.logger.info(f"元数据源 '{providerName}' 的配置已更新并重新加载。")

        return {"message": "配置已成功更新。"}

    async def update_tmdb_mappings(self, tmdb_tv_id: int, group_id: str, user: models.User):
        """协调TMDB分集组映射的更新。现在此操作将委托给TMDB源（如果存在且具有该方法）。"""
        tmdb_source = self.sources.get("tmdb")
        if tmdb_source and hasattr(tmdb_source, "update_tmdb_mappings"):
            self.logger.info(f"管理器: 正在为 TMDB TV ID {tmdb_tv_id} 和 Group ID {group_id} 委派映射更新。")
            # 该方法需要在 TmdbMetadataSource 类中定义
            await tmdb_source.update_tmdb_mappings(tmdb_tv_id, group_id, user)
        else:
            self.logger.warning("TMDB 元数据源未加载或不支持 `update_tmdb_mappings` 方法。")

    # 季度映射相关方法委托给 SeasonMapper
    async def get_season_name(self, *args, **kwargs):
        """委托给 SeasonMapper.get_season_name()"""
        return await self.season_mapper.get_season_name(*args, **kwargs)

    async def get_seasons(self, *args, **kwargs):
        """委托给 SeasonMapper.get_seasons_from_source()"""
        return await self.season_mapper.get_seasons_from_source(*args, **kwargs)

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
