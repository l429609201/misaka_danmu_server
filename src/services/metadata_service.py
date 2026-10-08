import asyncio
import hashlib
import traceback
import logging
import time as _time
from typing import Any, Dict, List, Set, Optional, Sequence, Type, Tuple
import json

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from fastapi import HTTPException, status, Request, APIRouter
import httpx

from src.services.cache_service import get_cache_service
from src.services.bangumi_data_service import BangumiDataService
from src.services.config_service import ConfigService
from src.services.service_container import get_database_service
from src.schemas.auth import User
# 元数据补充结果需保留年份、海报、集数，不属于带 result_index 的导入请求。
from src.schemas.ui.search import ProviderSearchInfo
from src.schemas.metadata import MetadataDetailsResponse
from src.schemas.control.source import MetadataSourceSettingUpdate
from .scraper_manager import ScraperManager
from src.metadata_sources.base import BaseMetadataSource
from src.utils.parsing.season_mapper import SeasonMapper
from src.utils.parsing.filename_parser import is_chinese_title

logger = logging.getLogger(__name__)


def _sanitize_name(value: str) -> str:
    """清除名称中的控制字符（\\r、\\n、\\t 等）并去掉首尾空白。

    why：这些名称会被拼进多行汇总日志。名字里只要混入 \\r，终端渲染时光标
    会退回行首覆盖已输出内容，导致日志出现残缺的孤立字符与空行。
    """
    return "".join(ch for ch in value if ch.isprintable()).strip()


class MetadataService:
    """维护已显式注册的元数据源实例、搜索与持久配置。"""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession],
        config_service: ConfigService, scraper_manager: ScraperManager,
        *, source_classes: Sequence[Type[BaseMetadataSource]],
    ):
        """
        初始化管理器。

        Args:
            session_factory: 用于数据库访问的异步会话工厂。
            config_service: 应用的配置服务。
            scraper_manager: 应用的弹幕抓取器管理器。
        """
        self._registered_source_classes = tuple(source_classes)
        for source_class in self._registered_source_classes:
            if not isinstance(source_class, type) or not issubclass(source_class, BaseMetadataSource) or source_class is BaseMetadataSource:
                raise TypeError(f"无效的元数据源类: {source_class!r}")
        self._session_factory = session_factory
        self._config_service = config_service
        # 离线索引仅在元数据服务中创建，旧插件入口只引用这一份实例。
        self._bangumi_data = BangumiDataService(config_service)
        self.logger = logging.getLogger(self.__class__.__name__)

        # 按 provider_name 存储实例化的源对象。
        self.sources: Dict[str, Any] = {}
        # 在实例化之前存储发现的源类。
        self._source_classes: Dict[str, Type[Any]] = {}
        # 从数据库缓存所有源的持久设置。
        self.source_settings: Dict[str, Dict[str, Any]] = {}
        self.scraper_manager = scraper_manager
        # 存储最后一次辅助搜索的单源耗时: [(provider_name, duration_ms, result_count), ...]
        self.last_aux_search_timing: List[Tuple[str, float, int]] = []
        # 新增：为所有元数据源创建一个父级路由器
        self.router = APIRouter()
        # 季度映射器(延迟初始化)
        self._season_mapper = None

    def get_offline_bangumi_service(self) -> BangumiDataService:
        """返回共享离线番剧数据服务实例。"""
        return self._bangumi_data

    async def get_offline_air_schedule(self) -> Dict[str, Dict[str, Any]]:
        """统一提供 Bangumi 离线在播日程，供日历流程与任务使用。"""
        return await self._bangumi_data.get_offline_air_schedule()

    async def is_offline_bangumi_enabled(self) -> bool:
        """读取离线索引总开关。"""
        return await self._bangumi_data.is_offline_enabled()

    async def get_offline_aliases_by_bangumi_id(self, bangumi_id: str) -> Dict[str, Any] | None:
        """按 Bangumi 标识从离线索引获取作品别名。"""
        return await self._bangumi_data.get_aliases_by_bangumi_id(bangumi_id)

    async def get_offline_aliases_by_title(self, title: str) -> Dict[str, Any] | None:
        """按作品标题从离线索引获取别名。"""
        return await self._bangumi_data.get_aliases_by_title(title)

    async def get_offline_search_aliases(self, title: str, limit: int = 3) -> list[str]:
        """获取搜索关键词所需的离线多语言别名。"""
        return await self._bangumi_data.get_search_aliases(title, limit=limit)

    async def resolve_offline_sources_by_title(self, title: str) -> list[dict]:
        """将离线索引平台标识映射为可导入的弹幕源。"""
        return await self._bangumi_data.resolve_sources_by_title(title)

    async def discover_offline_subjects(self, query: str) -> list[dict]:
        """从离线索引探索作品及其平台映射。"""
        return await self._bangumi_data.discover_offline(query)

    async def get_bangumi_platform_urls(self, bangumi_id: str) -> list[dict]:
        """返回离线索引里的平台链接。"""
        return await self._bangumi_data.build_platform_urls(bangumi_id)

    async def get_bangumi_data_status(self) -> dict:
        """返回离线索引的可用状态及记录数。"""
        count = await self._bangumi_data.count()
        return {"ready": count > 0, "count": count}

    async def sync_bangumi_data(self, progress_callback=None) -> dict:
        """由元数据服务执行离线索引同步。"""
        return await self._bangumi_data.sync(progress_callback=progress_callback)

    async def clear_bangumi_data(self) -> dict:
        """由元数据服务清除离线索引。"""
        return await self._bangumi_data.clear()

    async def load_local_bangumi_data(self) -> dict:
        """启动时读取本地离线索引。"""
        return await self._bangumi_data.sync_from_local()

    async def get_tmdb_poster_url(self, tmdb_id: int) -> str | None:
        """通过已启用的 TMDB 源获取作品海报。"""
        source = self.sources.get("tmdb")
        if source is None or not self.source_settings.get("tmdb", {}).get("isEnabled", True):
            return None
        return await source.get_poster_url(tmdb_id)

    async def get_tmdb_title_year(self, tmdb_id: int) -> dict | None:
        """通过已启用的 TMDB 源获取标题和年份。"""
        source = self.sources.get("tmdb")
        if source is None or not self.source_settings.get("tmdb", {}).get("isEnabled", True):
            return None
        return await source.get_title_year(tmdb_id)

    async def get_bangumi_calendar_items(self, user: User) -> list[dict]:
        """通过已启用的 Bangumi 元数据源取得周历，不在 API 层直连远端。"""
        source = self.sources.get("bangumi")
        if source is None or not self.source_settings.get("bangumi", {}).get("isEnabled", True):
            return []
        return await source.get_calendar(user)

    async def initialize(self):
        """在应用启动时加载并同步元数据源，并构建其API路由。"""
        await self.load_and_sync_sources()
        self._build_source_routers()
        # 初始化季度映射器

        self._season_mapper = SeasonMapper(self, self._session_factory)
        logger.info("MetadataService 已初始化。")

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
            force_enabled_str = await self._config_service.get(f"{provider}_force_aux_search", "false")
            if force_enabled_str.lower() == 'true':
                return True

        return False

    async def load_and_sync_sources(self):
        """同步已注册元数据源的设置并重建实例。"""
        await self.close_all()  # 在重新加载前确保旧连接已关闭
        self.sources.clear()
        self._source_classes.clear()
        self.source_settings.clear()

        discovered_providers = []
        for source_class in self._registered_source_classes:
            raw_provider_name = getattr(source_class, "provider_name", None)
            if not isinstance(raw_provider_name, str) or not raw_provider_name:
                self.logger.warning("跳过 %s：provider_name 缺失或非字符串", source_class.__name__)
                continue
            provider_name = _sanitize_name(raw_provider_name)
            if not provider_name:
                self.logger.warning("跳过 %s：provider_name 仅含空白或控制字符", source_class.__name__)
                continue
            if provider_name != raw_provider_name:
                self.logger.warning(
                    "%s.provider_name 含空白或控制字符，已规范化为 %r（原值=%r）",
                    source_class.__name__, provider_name, raw_provider_name,
                )
            if provider_name in self._source_classes:
                raise ValueError(f"重复的元数据源: {provider_name}")
            self._source_classes[provider_name] = source_class
            discovered_providers.append(provider_name)

        # 经服务层统一访问数据库：同步已发现的源并回读设置
        db = get_database_service()
        async with db.transaction():
            await db.metadata_source.sync_metadata_sources_to_db(discovered_providers)
            settings_list = await db.metadata_source.get_all_metadata_source_settings()

        self.source_settings = {s['providerName']: s for s in settings_list}

        # why：__init__ 是各元数据源自己的代码，可能因内部错误抛异常。原实现无保护，
        # 一个源构造失败会中断整个循环，后续源全部不会被实例化，直接拖垮启动。
        # 改为逐源隔离：坏源跳过并记录，其余源照常可用。
        for provider_name, source_class in list(self._source_classes.items()):
            try:
                self.sources[provider_name] = source_class(
                    self._session_factory, self._config_service,
                    self.scraper_manager,
                )
            except Exception as e:
                self.logger.error(
                    f"实例化元数据源 '{provider_name}' 失败，已跳过该源: {e}", exc_info=True
                )
                self._source_classes.pop(provider_name, None)

        # 汇总输出（名称再过一遍控制字符清理，避免单个源污染整段多行日志）
        _P = "  - "
        log_lines = [f"已加载 {len(self.sources)} 个元数据源"]
        for pn in sorted(self.sources.keys()):
            log_lines.append(f"{_P}{_sanitize_name(pn)}")
        self.logger.info("\n".join(log_lines))

    async def search_aliases_from_enabled_sources(self, keyword: str, user: User) -> Set[str]:
        """从所有已启用的辅助元数据源并发获取别名。"""
        # 修正：调用新的、更通用的方法，并只返回别名部分
        aliases, _, _, _ = await self.search_supplemental_sources(keyword, user)
        return aliases

    async def search_supplemental_sources(self, keyword: str, user: User) -> Tuple[Set[str], List[ProviderSearchInfo], Dict[str, str], Dict[str, List[str]]]:
        """
        从所有启用的辅助源（包括强制启用的）进行搜索。
        返回一个元组：(别名集合, 补充搜索结果列表, 标题→类型映射, 别名来源映射)

        优化：对于 TMDB/Bangumi 等源，搜索结果不包含完整别名，
        需要对前几个结果调用 get_details 获取完整别名（包括中文别名）。

        别名来源映射格式：{"TMDB": ["别名1", "别名2"], "Bangumi": ["别名3"], ...}
        """
        # 并行读取所有 provider 的 force_aux_search 配置（避免串行 await 阻塞）
        enabled_providers = [
            (provider, settings) for provider, settings in self.source_settings.items()
            if settings.get('isEnabled')
        ]

        if not enabled_providers:
            # 无可用源时仍保持四项返回契约，供搜索流程统一解包。
            return set(), [], {}, {}

        # 并行读取 config
        force_config_tasks = [
            self._config_service.get(f"{provider}_force_aux_search", "false")
            for provider, _ in enabled_providers
        ]
        force_results = await asyncio.gather(*force_config_tasks)

        enabled_sources_settings = []
        for (provider, settings), force_enabled_str in zip(enabled_providers, force_results):
            force_enabled = force_enabled_str.lower() == 'true'
            if settings.get('isAuxSearchEnabled') or force_enabled:
                enabled_sources_settings.append(settings)

        if not enabled_sources_settings:
            return set(), [], {}, {}

        # 每个源独立流水线：搜索 → 需要时立即 get_details，各源之间并行
        async def _source_pipeline(source_instance, provider, kw, usr):
            """单个辅助源的完整流水线：搜索 + 按需获取详情"""
            _start = _time.monotonic()
            try:
                if provider == 'tmdb':
                    res = await source_instance.search(kw, usr, mediaType='multi')
                else:
                    res = await source_instance.search(kw, usr)
            except Exception as e:
                _dur = (_time.monotonic() - _start) * 1000
                return provider, None, _dur, None, e

            _search_dur = (_time.monotonic() - _start) * 1000

            if not isinstance(res, list):
                return provider, None, _search_dur, None, None

            # 对需要获取详情的源（tmdb/tvdb/imdb），立即并行获取详情
            detail_aliases = set()
            detail_dur = 0.0
            needs_detail_fetch = provider in ['tmdb', 'tvdb', 'imdb']
            if needs_detail_fetch and res:
                detail_tasks_local = []
                max_detail_fetch = 3
                detail_count = 0
                for item in res:
                    if detail_count >= max_detail_fetch:
                        break
                    has_aliases = bool(item.aliasesCn or item.aliasesJp or item.nameJp or item.nameEn)
                    if not has_aliases:
                        media_type = item.type if hasattr(item, 'type') and item.type else 'tv'
                        detail_tasks_local.append(source_instance.get_details(item.id, usr, mediaType=media_type))
                        detail_count += 1

                if detail_tasks_local:
                    _detail_start = _time.monotonic()
                    detail_results = await asyncio.gather(*detail_tasks_local, return_exceptions=True)
                    detail_dur = (_time.monotonic() - _detail_start) * 1000
                    for detail_res in detail_results:
                        if isinstance(detail_res, MetadataDetailsResponse):
                            if detail_res.aliasesCn:
                                detail_aliases.update(detail_res.aliasesCn)
                            if detail_res.aliasesJp:
                                detail_aliases.update(detail_res.aliasesJp)
                            if detail_res.nameJp:
                                detail_aliases.add(detail_res.nameJp)
                            if detail_res.nameEn:
                                detail_aliases.add(detail_res.nameEn)
                            if detail_res.nameRomaji:
                                detail_aliases.add(detail_res.nameRomaji)

            total_dur = (_time.monotonic() - _start) * 1000  # noqa: F841
            return provider, res, _search_dur, (detail_aliases, detail_dur), None

        tasks = []
        for source_setting in enabled_sources_settings:
            provider = source_setting['providerName']
            if source_instance := self.sources.get(provider):
                tasks.append(_source_pipeline(source_instance, provider, keyword, user))
            else:
                self.logger.warning(f"已启用的元数据源 '{provider}' 未被成功加载，跳过辅助搜索。")

        if not tasks:
            return set(), [], {}, {}

        pipeline_results = await asyncio.gather(*tasks)

        all_aliases: Set[str] = set()
        supplemental_results: List[ProviderSearchInfo] = []
        # 标题→类型映射：同一标题出现类型冲突时标记为 ambiguous，禁止自动覆盖。
        title_type_map: Dict[str, str] = {}
        # 别名来源映射：记录每个别名来自哪个元数据源
        alias_sources: Dict[str, List[str]] = {}

        def _record_title_type(title: Optional[str], media_type: Optional[str]) -> None:
            if not title or not media_type:
                return
            previous = title_type_map.get(title)
            if previous and previous != media_type:
                # why：多个元数据候选对同一标题给出不同类型时，不能把任一结果当成高置信度。
                title_type_map[title] = "ambiguous"
            else:
                title_type_map[title] = media_type
        self.last_aux_search_timing = []

        for provider_name, res, search_dur, detail_info, error in pipeline_results:
            if error:
                self.last_aux_search_timing.append((provider_name, search_dur, 0))
                if isinstance(error, httpx.ConnectError):
                    self.logger.warning(f"无法连接到元数据源 '{provider_name}'。({search_dur:.0f}ms)")
                elif isinstance(error, (httpx.TimeoutException, httpx.ReadTimeout)):
                    self.logger.warning(f"连接元数据源 '{provider_name}' 超时。({search_dur:.0f}ms)")
                else:
                    self.logger.error(f"元数据源 '{provider_name}' 辅助搜索失败: {error} ({search_dur:.0f}ms)", exc_info=False)
                continue

            if not res:
                self.last_aux_search_timing.append((provider_name, search_dur, 0))
                continue

            # 计算总耗时（搜索 + 详情获取）
            total_provider_dur = search_dur
            detail_alias_count = 0
            if detail_info:
                _, detail_dur = detail_info
                if detail_dur > 0:
                    total_provider_dur = search_dur + detail_dur
                detail_alias_count = len(detail_info[0]) if detail_info[0] else 0

            self.last_aux_search_timing.append((provider_name, total_provider_dur, len(res)))
            self.logger.info(f"辅助源 '{provider_name}' 为关键词 '{keyword}' 找到了 {len(res)} 个结果, {detail_alias_count} 个别名。({total_provider_dur:.0f}ms)")

            # 收集别名 + 构建标题→类型映射 + 记录别名来源
            for item in res:
                # 标准化 type：TMDB 返回 "tv"，统一为 "tv_series"
                item_type = item.type if hasattr(item, 'type') and item.type else None
                if item_type == 'tv':
                    item_type = 'tv_series'

                all_aliases.add(item.title)
                _record_title_type(item.title, item_type)
                alias_sources.setdefault(provider_name, []).append(item.title)

                if item.aliasesCn:
                    all_aliases.update(item.aliasesCn)
                    for alias in item.aliasesCn:
                        _record_title_type(alias, item_type)
                        alias_sources.setdefault(provider_name, []).append(alias)
                if item.aliasesJp:
                    all_aliases.update(item.aliasesJp)
                    for alias in item.aliasesJp:
                        alias_sources.setdefault(provider_name, []).append(alias)
                if item.nameJp:
                    all_aliases.add(item.nameJp)
                    alias_sources.setdefault(provider_name, []).append(item.nameJp)
                if item.nameEn:
                    all_aliases.add(item.nameEn)
                    alias_sources.setdefault(provider_name, []).append(item.nameEn)
                if item.nameRomaji:
                    all_aliases.add(item.nameRomaji)
                    alias_sources.setdefault(provider_name, []).append(item.nameRomaji)

                # 补充列表
                if provider_name in ['douban', '360']:
                    supp_info = ProviderSearchInfo(
                        provider=provider_name, mediaId=item.id, title=item.title,
                        type=item.type if hasattr(item, 'type') and item.type else 'unknown',
                        season=1,
                        year=item.year if hasattr(item, 'year') else None,
                        imageUrl=item.imageUrl,
                        supportsEpisodeUrls=item.supportsEpisodeUrls
                    )
                    supplemental_results.append(supp_info)

            # 合并详情获取的别名
            if detail_info:
                detail_aliases, detail_dur = detail_info
                if detail_aliases:
                    all_aliases.update(detail_aliases)

        # A2 匹配增强：用 bangumi-data 本地离线索引补充多语言别名（日↔中↔英），离线零网络成本
        # 受 bangumiDataOfflineEnabled 开关控制：关闭时仅用在线 API，不走离线库
        try:
            offline_enabled = (await self._config_service.get("bangumiDataOfflineEnabled", "true")).lower() == "true"
            if offline_enabled:
                bgm_data = self._bangumi_data
                local = await bgm_data.get_aliases_by_title(keyword)
                if local:
                    bgm_aliases = []
                    if local.get("name_jp"):
                        all_aliases.add(local["name_jp"])
                        bgm_aliases.append(local["name_jp"])
                    if local.get("name_en"):
                        all_aliases.add(local["name_en"])
                        bgm_aliases.append(local["name_en"])
                    for cn in (local.get("aliases_cn") or []):
                        all_aliases.add(cn)
                        bgm_aliases.append(cn)
                    if bgm_aliases:
                        alias_sources.setdefault("bangumi-data", []).extend(bgm_aliases)
        except Exception as e:
            self.logger.debug(f"bangumi-data 本地别名补充失败: {e}")

        return {alias for alias in all_aliases if alias}, supplemental_results, title_type_map, alias_sources

    async def supplement_empty_search_results(
        self,
        keyword: str,
        empty_providers: Set[str]
    ) -> List[ProviderSearchInfo]:
        """调用所有启用的搜索补充源，并行请求后统一汇总结果。

        流程：
        1. 收集所有已启用的补充源
        2. 并行调用各补充源的 supplement_search()
        3. 汇总去重后返回

        Args:
            keyword: 搜索关键词
            empty_providers: 返回0结果的弹幕源名称集合

        Returns:
            以对应弹幕源 provider 名义生成的 ProviderSearchInfo 列表
        """
        if not empty_providers:
            return []

        supplement_sources = []
        # 先筛选候选补充源
        candidate_sources = []
        for provider_name, source in self.sources.items():
            if not getattr(source, 'is_search_supplement_source', False):
                continue
            if not self.source_settings.get(provider_name, {}).get('isEnabled'):
                continue
            candidate_sources.append((provider_name, source))

        if not candidate_sources:
            return []

        # 并行读取所有补充源的开关配置
        config_tasks = [
            self._config_service.get(f"{pn}_searchSupplementEnabled", "false")
            for pn, _ in candidate_sources
        ]
        config_results = await asyncio.gather(*config_tasks)

        for (provider_name, source), enabled_str in zip(candidate_sources, config_results):
            if enabled_str.lower() == 'true':
                supplement_sources.append(source)

        if not supplement_sources:
            return []

        user = User(id=0, username="system")

        # 并行调用所有补充源，带计时
        async def _call_source(source):
            _start = _time.monotonic()
            try:
                results = await source.supplement_search(keyword, empty_providers, user)
                _dur = (_time.monotonic() - _start) * 1000
                return source.provider_name, results, _dur
            except Exception as e:
                _dur = (_time.monotonic() - _start) * 1000
                self.logger.warning(f"搜索补充源 '{source.provider_name}' 调用失败: {e}")
                return source.provider_name, [], _dur

        timed_list = await asyncio.gather(*[_call_source(s) for s in supplement_sources])

        # 记录各补充源耗时
        self.last_supplement_timing = [
            (name, dur, len(results)) for name, results, dur in timed_list
        ]

        # 汇总去重
        all_supplements: List[ProviderSearchInfo] = []
        seen_ids: Set[str] = set()
        for _, results, _ in timed_list:
            for item in results:
                unique_id = (item.provider, item.mediaId)
                if unique_id not in seen_ids:
                    all_supplements.append(item)
                    seen_ids.add(unique_id)

        return all_supplements

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
                status_code = "error"
                status_result = status_map.get(provider_name)
                if isinstance(status_result, dict):
                    status_text = status_result.get("message", "检查失败")
                    status_code = status_result.get("code", "error")
                elif isinstance(status_result, Exception):
                    self.logger.error(f"检查 '{provider_name}' 连接状态时出错: {status_result}")
            else:
                # 禁用的源显示为"已禁用"状态
                status_text = "已禁用"
                status_code = "disabled"

            source_instance = self.sources.get(provider_name)
            is_supplement_source = getattr(source_instance, 'is_search_supplement_source', False) if source_instance else False

            # 从 config 表读取补充源开关
            supplement_enabled = False
            if is_supplement_source:
                # 统一使用构造时注入的配置服务，避免旧属性导致补充源状态读取失败。
                enabled_str = await self._config_service.get(f"{provider_name}_searchSupplementEnabled", "false")
                supplement_enabled = enabled_str.lower() == 'true'

            full_status_list.append({
                "providerName": provider_name,
                "isEnabled": is_enabled,
                "isAuxSearchEnabled": setting.get('isAuxSearchEnabled', False),
                "isFailoverEnabled": setting.get('isFailoverEnabled', False),
                "displayOrder": setting.get('displayOrder', 99),
                "status": status_text,
                "statusCode": status_code,
                "useProxy": setting.get('useProxy', False),
                "logRawResponses": setting.get('log_raw_responses', False),
                "isSearchSupplementSource": is_supplement_source,
                "isSearchSupplementEnabled": supplement_enabled,
            })

        return sorted(full_status_list, key=lambda x: x['displayOrder'])

    async def update_source_settings(self, settings_payload: List[MetadataSourceSettingUpdate]):
        """
        Updates settings for multiple metadata sources and reloads them to reflect changes immediately.
        This is the correct way to update settings as it ensures the in-memory cache is invalidated.
        """
        # 经服务层统一访问数据库：批量更新元数据源设置
        db = get_database_service()
        async with db.transaction():
            await db.metadata_source.update_metadata_sources_settings(settings_payload)

        # After updating the DB, reload all sources to apply the new settings.
        # This ensures that enable/disable, proxy settings, etc., take effect immediately.
        await self.load_and_sync_sources()
        self.logger.info("元数据源设置已更新并重新加载。")

    async def get_failover_comments(self, title: str, season: int, episode_index: int, user: User) -> Optional[List[dict]]:
        """
        Iterates through enabled failover sources to find comments for a specific episode.
        """
        db = get_database_service()
        async with db.transaction():
            enabled_sources_settings = await db.metadata_source.get_enabled_failover_sources()

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

    async def supplement_search_result(self, target_provider: str, keyword: str, episode_info: Optional[Dict[str, Any]]) -> List[ProviderSearchInfo]:
        """
        当主搜索源未找到结果时，主动通过故障转移源（如360）查找对应平台的链接，并返回结果。
        """
        self.logger.info(f"主搜索源 '{target_provider}' 未找到结果，正在尝试故障转移...")

        db = get_database_service()
        async with db.transaction():
            failover_sources_settings = await db.metadata_source.get_enabled_failover_sources()

        user = User(id=0, username="system")

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

        db = get_database_service()
        async with db.transaction():
            failover_sources_settings = await db.metadata_source.get_enabled_failover_sources()

        user = User(id=0, username="system")

        for source_setting in failover_sources_settings:
            provider_name = source_setting['providerName']
            if source_instance := self.sources.get(provider_name):
                if hasattr(source_instance, "find_url_for_provider"):
                    target_url = await source_instance.find_url_for_provider(title, target_provider, user, season=season)
                    if target_url:
                        return await self.scraper_manager.get_scraper(target_provider).get_id_from_url(target_url)
        return None

    async def is_tmdb_reverse_lookup_enabled(self, source_type: str) -> bool:
        """通过配置服务检查反查总开关及允许的元数据源。"""
        try:
            # 使用已有配置服务管理缓存和事务，不再借用任务数据库会话。
            enabled = await self._config_service.get("tmdbReverseLookupEnabled", "false")
            if str(enabled).lower() != "true":
                return False
            defaults = ["imdb", "tvdb", "douban", "bangumi"]
            raw = await self._config_service.get("tmdbReverseLookupSources", json.dumps(defaults))
            try:
                sources = json.loads(raw)
            except (TypeError, ValueError):
                sources = defaults
            return source_type in (sources if isinstance(sources, list) else defaults)
        except Exception as exc:
            logger.warning(f"检查TMDB反查配置失败: {exc}")
            return False

    async def reverse_lookup_tmdb_chinese_title(
        self, user: User, source_type: str, source_id: str,
        tmdb_id: Optional[str], imdb_id: Optional[str], tvdb_id: Optional[str],
        douban_id: Optional[str], bangumi_id: Optional[str],
    ) -> Optional[str]:
        """优先通过已知TMDB编号获取中文标题，失败时使用外部编号反查。"""
        try:
            if tmdb_id:
                title = await self._get_tmdb_chinese_title(tmdb_id, user)
                if title:
                    return title
            external_ids = {
                key: value for key, value in (
                    ("imdb_id", imdb_id), ("tvdb_id", tvdb_id),
                    ("douban_id", douban_id), ("bangumi_id", bangumi_id),
                ) if value
            }
            if external_ids:
                found_id = await self.find_tmdb_by_external_ids(user, external_ids)
                if found_id:
                    title = await self._get_tmdb_chinese_title(found_id, user)
                    if title:
                        return title
            logger.info(f"未能通过 {source_type} ID {source_id} 反查到中文标题")
        except Exception as exc:
            logger.warning(f"TMDB反查失败: {exc}")
        return None

    async def _get_tmdb_chinese_title(self, tmdb_id: str, user: User) -> Optional[str]:
        """保持原有先电视剧、详情不存在时再电影的查询顺序。"""
        details = await self.get_details(provider="tmdb", item_id=tmdb_id, user=user, mediaType="tv")
        if not details:
            details = await self.get_details(provider="tmdb", item_id=tmdb_id, user=user, mediaType="movie")
        if details and details.title and is_chinese_title(details.title):
            return details.title
        return None

    async def find_tmdb_by_external_ids(self, user: User, external_ids: Dict[str, str]) -> Optional[str]:
        """使用TMDB外部编号接口查询，失败时回退到各元数据源搜索。"""
        tmdb_source = self.sources.get("tmdb")
        if tmdb_source:
            # 源实例与网络请求由元数据管理器协调，不暴露给任务层。
            for key in ("imdb_id", "tvdb_id"):
                if key not in external_ids:
                    continue
                ext_id = external_ids[key]
                try:
                    async with await tmdb_source._create_client() as client:
                        response = await client.get(f"/find/{ext_id}", params={"external_source": key})
                        response.raise_for_status()
                        data = response.json()
                        for result_key in ("tv_results", "movie_results"):
                            results = data.get(result_key, [])
                            if results:
                                return str(results[0]["id"])
                except Exception as exc:
                    logger.warning(f"TMDB find API 查找失败 ({key}={ext_id}): {exc}")
        for key, provider in (
            ("imdb_id", "imdb"), ("tvdb_id", "tvdb"),
            ("douban_id", "douban"), ("bangumi_id", "bangumi"),
        ):
            if key not in external_ids:
                continue
            try:
                results = await self.search(provider, external_ids[key], user)
                for result in results:
                    if getattr(result, "tmdbId", None):
                        return result.tmdbId
            except Exception as exc:
                logger.warning(f"通过 {provider} 查找 TMDB 失败: {exc}")
        return None


    async def search(self, provider: str, keyword: str, user: User, mediaType: Optional[str] = None) -> List[MetadataDetailsResponse]:
        """从特定提供商搜索媒体。"""
        if source_instance := self.sources.get(provider):
            return await source_instance.search(keyword, user, mediaType=mediaType)
        raise HTTPException(status_code=404, detail=f"未找到元数据源: {provider}")

    async def search_cached(
        self, keyword: str, source: str = "tmdb", log: Optional[logging.Logger] = None,
    ) -> List[MetadataDetailsResponse]:
        """查询元数据并缓存六小时，缓存服务不可用时回退到数据库。"""
        search_logger = log if log is not None else logger
        cache_key = f"{source}_search_{hashlib.md5(keyword.encode('utf-8')).hexdigest()}"
        cache_service = get_cache_service()
        if cache_service is not None:
            try:
                cached = await cache_service.get(cache_key, region="metadata")
                if cached:
                    search_logger.info(f"[{source}] 搜索缓存命中: {keyword}")
                    return [MetadataDetailsResponse(**item) for item in cached]
            except Exception as exc:
                search_logger.warning(f"缓存服务读取失败，回退到数据库: {exc}")

        # 保留历史无 region 前缀的数据库缓存，避免迁移后丢失可见性。
        db = get_database_service()
        try:
            async with db.transaction():
                cached = await db.cache.get_json(cache_key)
            if cached:
                search_logger.info(f"[{source}] 搜索缓存命中(数据库): {keyword}")
                return [MetadataDetailsResponse(**item) for item in cached]
        except Exception as exc:
            search_logger.warning(f"数据库缓存读取失败，继续执行搜索: {exc}")

        search_logger.debug(f"[{source}] 搜索缓存未命中，执行搜索: {keyword}")
        try:
            results = await self.search(source, keyword, None, mediaType="multi")
        except Exception as exc:
            search_logger.error(f"[{source}] 搜索失败: {keyword}, 错误: {exc}")
            return []

        results_data = [item.model_dump() for item in results]
        if cache_service is not None:
            try:
                await cache_service.set(cache_key, results_data, ttl=21600, region="metadata")
                search_logger.info(f"[{source}] 搜索结果已缓存: {keyword} (6小时)")
                return results
            except Exception as exc:
                search_logger.warning(f"缓存服务写入失败，回退到数据库: {exc}")
        try:
            async with db.transaction():
                await db.cache.set_json(cache_key, results_data, ttl_seconds=21600)
            search_logger.info(f"[{source}] 搜索结果已缓存(数据库): {keyword} (6小时)")
        except Exception as exc:
            search_logger.warning(f"数据库缓存写入失败: {exc}")
        return results


    async def get_details(self, provider: str, item_id: str, user: User, mediaType: Optional[str] = None) -> Optional[MetadataDetailsResponse]:
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

    async def execute_action(self, provider: str, action_name: str, payload: Dict, user: User, request: Request) -> Any:
        """执行特定提供商的自定义操作。"""
        if source_instance := self.sources.get(provider):
            return await source_instance.execute_action(action_name, payload, user, request=request)
        raise HTTPException(status_code=404, detail=f"未找到元数据源: {provider}")

    def get_config_keys(self, providerName: str) -> list:
        """从源类的 config_keys 属性获取用户可配置的 key 列表。

        优先从元数据源类读取，如果不存在则检查 scraper 等其他注册源。
        """
        source_class = self._source_classes.get(providerName)
        if source_class:
            return list(getattr(source_class, 'config_keys', []))
        # scraper 等非元数据源的兼容 fallback（如 gamer）
        scraper_keys_map = {
            "gamer": ["gamerCookie", "gamerUserAgent", "gamerEpisodeBlacklistRegex", "scraperGamerLogResponses"],
        }
        return scraper_keys_map.get(providerName, [])

    def get_bool_config_keys(self, providerName: str) -> list:
        """从源类的 bool_config_keys 属性获取需要布尔转换的 key 列表。"""
        source_class = self._source_classes.get(providerName)
        if source_class:
            return list(getattr(source_class, 'bool_config_keys', []))
        return []

    async def getProviderConfig(self, providerName: str) -> Dict[str, Any]:
        """
        获取特定提供商（元数据源或搜索源）的配置。
        config keys 从源类的 config_keys 属性自动获取，无需在此硬编码。
        """

        source_class = self._source_classes.get(providerName)
        configurable_fields = getattr(source_class, 'configurable_fields', {}) if source_class else {}
        keys_to_fetch = self.get_config_keys(providerName)
        bool_keys = set(self.get_bool_config_keys(providerName))

        if not keys_to_fetch:
            # 没有声明 config_keys，检查是否是已知的源
            is_known_metadata_source = providerName in self.sources
            if is_known_metadata_source:
                config_values = {}
            else:
                raise ValueError(f"未找到提供商: {providerName}")
        else:
            config_values = {}
            for key in keys_to_fetch:
                field_info = configurable_fields.get(key, {})
                default_value = field_info.get('default', '') if isinstance(field_info, dict) else ''
                # why：首次读取即使用源声明的默认值，避免 ConfigService 把空值缓存后覆盖运行时默认地址。
                value_str = await self._config_service.get(key, default_value)
                if key in bool_keys:
                    config_values[key] = value_str.lower() == 'true' if value_str else True
                else:
                    config_values[key] = value_str

        # 新增：从数据库获取 useProxy 和 logRawResponses 并添加到配置中
        # 修正：将此逻辑移到更前面，确保所有源都能执行
        db = get_database_service()
        async with db.transaction():
            provider_settings = await db.metadata_source.get_metadata_source_setting_by_name(providerName)
            if provider_settings:
                config_values.update(provider_settings)

        # 新增：如果源支持强制辅助搜索，则从config表获取其状态
        source_class = self._source_classes.get(providerName)
        if source_class and getattr(source_class, 'has_force_aux_search_toggle', False):
            force_enabled_str = await self._config_service.get(f"{providerName}_force_aux_search", "false")
            config_values['forceAuxSearchEnabled'] = force_enabled_str.lower() == 'true'

        # 新增：告知前端此源是否为故障转移源，以决定是否显示“强制辅助”开关
        if source_class:
            config_values['isFailoverSource'] = getattr(source_class, 'is_failover_source', False)

            # 返回 configurableFields 元数据，让前端动态渲染。
            # why：config_keys 使用原始键名，旧动态字段使用 provider_ 前缀；这里统一兼容两种存储约定。
            cf = getattr(source_class, 'configurable_fields', {})
            if cf:
                config_values['configurableFields'] = cf
                declared_config_keys = set(getattr(source_class, 'config_keys', []))
                for field_key, field_info in cf.items():
                    field_meta = field_info if isinstance(field_info, dict) else {}
                    storage_key = field_meta.get('configKey') or (
                        field_key if field_key in declared_config_keys else f"{providerName}_{field_key}"
                    )
                    default_value = field_meta.get('default', '')
                    stored_value = config_values.get(field_key)
                    if stored_value in (None, ''):
                        stored_value = await self._config_service.get(storage_key, default_value)

                    field_type = (
                        field_info[1] if isinstance(field_info, (list, tuple))
                        else field_meta.get('type', 'string')
                    )
                    if field_type == 'boolean':
                        if isinstance(stored_value, bool):
                            config_values[field_key] = stored_value
                        else:
                            config_values[field_key] = str(stored_value).lower() == 'true'
                    else:
                        config_values[field_key] = stored_value


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

        # 动态处理 configurable_fields 中声明的字段，存储到 config 表。
        # why：config_keys 使用原始键名，旧动态字段使用 provider_ 前缀；保存时必须与读取规则一致。
        source_class = self._source_classes.get(providerName)
        cf = getattr(source_class, 'configurable_fields', {}) if source_class else {}
        declared_config_keys = set(getattr(source_class, 'config_keys', [])) if source_class else set()
        self.logger.info(f"updateProviderConfig: provider={providerName}, source_class={'found' if source_class else 'NOT FOUND'}, cf_keys={list(cf.keys())}, remaining_payload={list(payload.keys())}")
        for field_key, field_info in cf.items():
            if field_key in payload:
                value = payload.pop(field_key)
                field_meta = field_info if isinstance(field_info, dict) else {}
                config_key = field_meta.get('configKey') or (
                    field_key if field_key in declared_config_keys else f"{providerName}_{field_key}"
                )
                if isinstance(value, bool):
                    config_fields_to_update[config_key] = str(value).lower()
                else:
                    config_fields_to_update[config_key] = str(value if value is not None else "")

        # 2b. 按源类声明的 config_keys 通用保存，避免新增元信息源时重复维护硬编码白名单。
        allowed_keys = declared_config_keys
        for key, value in payload.items():
            if key not in allowed_keys:
                continue
            if isinstance(value, bool):
                config_fields_to_update[key] = str(value).lower()
            else:
                config_fields_to_update[key] = str(value if value is not None else "")

        # 3. 检查是否有任何需要更新的内容
        if not db_fields_to_update and not config_fields_to_update:
            self.logger.info(f"为提供商 '{providerName}' 收到配置更新请求，但没有可识别的字段需要更新。")
            return {"message": "没有可更新的配置项。"}

        # 4. 在同一事务内写入源设置与关联配置。
        # why: 提供商配置是一个整体；逐键提交会在中途失败时留下半套配置，
        # 且会把 MetadataSource 的更新提前提交。统一交由 db.transaction() 收口提交。
        db = get_database_service()
        async with db.transaction():
            if db_fields_to_update:
                await db.metadata_source.update_metadata_source_specific_settings(
                    providerName, db_fields_to_update
                )

            if config_fields_to_update:
                await db.config.update_config_values_atomic(config_fields_to_update)

        # 数据库全部提交成功后再失效缓存，避免失败事务对应的旧值被提前清除。
        for key in config_fields_to_update:
            self._config_service.invalidate(key)

        # 如果是元数据源的配置更新，重新加载它们以使更改生效
        if providerName in self.sources:
            await self.load_and_sync_sources()
            self.logger.info(f"元数据源 '{providerName}' 的配置已更新并重新加载。")

        # 通用钩子：源可在配置保存后据此同步订阅目标（如 AniBT 私有 RSS）。
        # why：避免在此处针对具体 provider 硬编码；实现该钩子的源自行处理配置→订阅联动。
        source = self.sources.get(providerName)
        if source is not None and hasattr(source, "sync_config_subscriptions"):
            try:
                await source.sync_config_subscriptions()
                self.logger.info(f"元数据源 '{providerName}' 已同步配置驱动的订阅目标。")
            except Exception as e:
                self.logger.error(f"元数据源 '{providerName}' 同步订阅目标失败: {e}", exc_info=True)

        return {"message": "配置已成功更新。"}

    async def update_tmdb_mappings(self, tmdb_tv_id: int, group_id: str, user: User):
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

    async def get_all_calendars(self, user: User, force_refresh: bool = False) -> Dict[str, List[Dict[str, Any]]]:
        """从所有已启用的元数据源获取日历数据（三层架构）。

        架构说明：
            Layer 1: 内存/Redis 缓存（region=external_calendar，TTL 2h）
            Layer 2: 持久化表 external_calendar_item（24h 内有效）
            Layer 3: 调用 metadata source 的 get_calendar()，结果双写表+缓存

        :param force_refresh: 跳过 L1/L2 缓存，强制走外部源重新拉取（用于「同步日程」按钮）
        :return: { "bangumi": [...], "trakt": [...] } 仅包含实际有数据的源
        """
        CACHE_REGION = "external_calendar"
        CACHE_KEY = "weekly_all"
        CACHE_TTL = 2 * 60 * 60   # 2 小时
        TABLE_MAX_AGE_HOURS = 24  # 表数据 24h 内视为有效

        # ---- Layer 1: 内存/Redis 缓存 ----
        if not force_refresh:
            try:
                cached = await self.cache_manager.get(CACHE_REGION, CACHE_KEY)
                if cached:
                    self.logger.debug("get_all_calendars: cache HIT (L1)")
                    return cached
            except Exception as e:
                self.logger.debug(f"L1 缓存读取失败（忽略）: {e}")

        # ---- Layer 2: 数据库表 ----
        if not force_refresh:
            try:
                db = get_database_service()
                async with db.transaction():
                    grouped = await db.external_calendar.get_all_fresh(max_age_hours=TABLE_MAX_AGE_HOURS)
                if grouped:
                    self.logger.debug(f"get_all_calendars: table HIT (L2) providers={list(grouped.keys())}")
                    # 回填 L1 缓存（不阻塞返回）
                    try:
                        await self.cache_manager.set(CACHE_REGION, CACHE_KEY, grouped, ttl_seconds=CACHE_TTL)
                    except Exception as e:
                        self.logger.debug(f"L1 缓存回填失败（忽略）: {e}")
                    return grouped
            except Exception as e:
                self.logger.warning(f"L2 表读取失败，回退到外部源: {e}")

        # ---- Layer 3: 调用外部 API（与原逻辑保持一致） ----
        results: Dict[str, List[Dict[str, Any]]] = {}

        async def _fetch(provider_name: str, source_instance):
            try:
                items = await source_instance.get_calendar(user)
                if items:
                    return provider_name, items
            except Exception as e:
                self.logger.warning(f"获取 {provider_name} 日历失败: {e}")
            return provider_name, []

        tasks = []
        for provider_name, setting in self.source_settings.items():
            if not setting.get('isEnabled'):
                continue
            source = self.sources.get(provider_name)
            if source and hasattr(source, 'get_calendar'):
                tasks.append(_fetch(provider_name, source))

        if tasks:
            fetched = await asyncio.gather(*tasks, return_exceptions=True)
            for item in fetched:
                if isinstance(item, Exception):
                    continue
                name, items = item
                if items:
                    results[name] = items

        # ---- 双写：持久化到表 + 写缓存 ----
        if results:
            # 写表（按 provider 分别 upsert）
            try:
                db = get_database_service()
                async with db.transaction():
                    for provider_name, items in results.items():
                        await db.external_calendar.upsert_items(provider_name, items)
                self.logger.info(f"get_all_calendars: 已持久化 {sum(len(v) for v in results.values())} 条到 external_calendar_item 表")
            except Exception as e:
                self.logger.warning(f"L2 表写入失败（不影响返回）: {e}")

            # 同步「平台用户私人在追状态」（OAuth 账号下的 watching/wish/done 等）
            # 这个调用是可选的：未授权时各源会自动跳过返回 {}
            try:
                await self.sync_user_platform_status(user)
                # 平台状态写入后，重新从表读取以保证返回的 results 含最新状态
                db = get_database_service()
                async with db.transaction():
                    refreshed = await db.external_calendar.get_all_fresh(max_age_hours=TABLE_MAX_AGE_HOURS)
                if refreshed:
                    results = refreshed
            except Exception as e:
                self.logger.warning(f"同步平台用户状态失败（不影响返回）: {e}")

            # 写 L1 缓存
            try:
                await self.cache_manager.set(CACHE_REGION, CACHE_KEY, results, ttl_seconds=CACHE_TTL)
            except Exception as e:
                self.logger.debug(f"L1 缓存写入失败（忽略）: {e}")

        return results

    async def sync_user_platform_status(self, user: User) -> Dict[str, int]:
        """同步「平台账号下我的在追/想看」状态到 external_calendar_item 表。

        遍历所有已启用的元数据源，如果该源实现了 get_user_watching_collection，
        则拉取用户在该平台的私人收藏状态，并 Upsert 到表中（仅更新 platformWatchStatus 等字段）。

        :return: { provider_name: updated_rows_count }
        """

        result: Dict[str, int] = {}
        for provider_name, setting in self.source_settings.items():
            if not setting.get("isEnabled"):
                continue
            source = self.sources.get(provider_name)
            if not source or not hasattr(source, "get_user_watching_collection"):
                continue
            try:
                statuses = await source.get_user_watching_collection(user)
                if not statuses:
                    continue
                db = get_database_service()
                async with db.transaction():
                    updated = await db.external_calendar.update_platform_status(provider_name, statuses)
                result[provider_name] = updated
                self.logger.info(
                    f"sync_user_platform_status: provider={provider_name} 拉取 {len(statuses)} 条，更新 {updated} 行"
                )
            except Exception as e:
                self.logger.warning(f"sync_user_platform_status 调用 {provider_name} 失败: {e}")
        return result

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
