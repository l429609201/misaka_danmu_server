"""主页结果编排：组合通用搜索缓存、辅助刷新及识别词响应标记。"""
import asyncio
import logging
from copy import deepcopy
from typing import Any, Callable, Optional, Dict, List, Set, Tuple
import time as _time

import httpx

from src.schemas.auth import User
from src.schemas.metadata import MetadataDetailsResponse
from src.schemas.ui.search import ProviderSearchInfo

from fastapi import HTTPException

from src.utils.title_recognition_engine import TitleRecognitionEngine
from src.services.config_service import get_config_service
from src.services.service_container import get_task_manager
from src.utils.diagnostics.task_exceptions import TaskFailed, TaskSuccess
from src.workflows.search.cache_policy import read_search_cache_ttl
from src.workflows.search.result_cache import (
    read_search_results, write_search_results, select_search_results,
)

logger = logging.getLogger(__name__)


async def _search_cache_ttl() -> int:
    """复用搜索缓存寿命配置，服务尚未初始化时回退三小时。"""
    try:
        return await read_search_cache_ttl(get_config_service())
    except RuntimeError as exc:
        logger.warning("配置服务不可用，主页搜索缓存使用10800秒: %s", exc)
        return 10800


def search_cache_keys(keyword: str, season: Optional[int]) -> tuple[str, str]:
    """使用原始请求生成既有业务键，避免名称转换改变翻页身份。"""
    season_key = season if season is not None else "all"
    return (f"provider_search_v3_{keyword}_{season_key}",
            f"supplemental_search_v2_{keyword}")


async def read_full_results(key: str) -> Optional[dict[str, Any]]:
    """主页需要有效搜索上下文；旧列表首次重搜后更新同一个键。"""
    data = await read_search_results(key)
    required = {"search_title", "search_season", "search_episode"}
    if data is not None and required.issubset(data):
        return data
    if data is not None:
        logger.info("主页全量缓存缺少搜索上下文，重新生成: %r", key)
    return None


async def write_full_results(key: str, results: list[Any], title: str,
                             season: Optional[int], episode: Optional[int], *,
                             cache_validation: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """缓存最终结果及预处理输入，供命中时按当前规则校验。"""
    data = {"results": [item.model_dump() for item in results],
            "search_title": title, "search_season": season, "search_episode": episode,
            "cache_validation": deepcopy(cache_validation)}
    for item in data["results"]:
        item["recognitionTitle"] = None
        item["currentEpisodeIndex"] = None
    # 瞬时源故障产生的空结果不长期锁定；非空结果与原始搜索使用相同配置。
    if results:
        context = {key: value for key, value in data.items() if key != "results"}
        await write_search_results(key, data["results"], context=context, ttl=await _search_cache_ttl())
    return data


async def write_supplemental_results(key: str, results: list[Any]) -> list[dict[str, Any]]:
    """辅助非空结果复用搜索寿命，空结果保留五分钟以便重试。"""
    data = [item.model_dump() for item in results]
    ttl = await _search_cache_ttl() if data else 300
    await write_search_results(key, data, ttl=ttl)
    return data


async def _refresh_supplemental_results(
    key: str, title: str, metadata_manager: Any, user: Any,
    progress_callback: Callable,
) -> None:
    """在队列内复查并刷新辅助缓存，失败不修改主搜索缓存。"""
    async def refresh() -> None:
        # 排队期间其他请求可能已写入缓存，空结果缓存也属于有效命中。
        if await read_search_results(key) is not None:
            return
        await progress_callback(10, "正在刷新搜索辅助结果")
        results = []
        if metadata_manager and await metadata_manager.has_any_enabled_aux_source():
            _, results, _, _ = await search_supplemental_sources(metadata_manager, title, user)
        await write_supplemental_results(key, results)
        await progress_callback(100, "搜索辅助结果刷新完成")

    try:
        # 上限覆盖二次缓存检查、源查询及缓存写入，不长期占用搜索队列。
        await asyncio.wait_for(refresh(), timeout=30.0)
    except asyncio.TimeoutError as exc:
        logger.warning("辅助搜索后台刷新超过30秒，保留主缓存结果")
        raise TaskFailed("辅助搜索后台刷新超时") from exc
    except Exception as exc:
        logger.warning("辅助搜索后台刷新失败，保留主缓存结果", exc_info=True)
        raise TaskFailed("辅助搜索后台刷新失败") from exc
    raise TaskSuccess("搜索辅助缓存已更新或由其他请求填充")


async def read_supplemental_results(key: str, title: str, metadata_manager: Any,
                                    user: Any) -> list[dict[str, Any]]:
    """热路径返回现有辅助缓存，过期刷新交给去重搜索任务。"""
    data = await read_search_results(key)
    if data is not None:
        return data["results"]
    try:
        manager = get_task_manager()
        # 不捕获请求会话；任务接收框架提供的会话，但缓存服务自持短事务。
        await manager.submit_task(
            lambda _session, callback: _refresh_supplemental_results(
                key, title, metadata_manager, user, callback,
            ),
            f"刷新搜索辅助缓存: {title}",
            unique_key=f"search-aux-refresh:{key}",
            task_type="search_aux_refresh",
            queue_type="search",
        )
    except HTTPException as exc:
        if exc.status_code == 409:
            logger.debug("辅助搜索刷新已在队列中，直接返回主缓存结果")
        else:
            logger.warning("辅助搜索刷新提交失败，直接返回主缓存结果", exc_info=True)
    except Exception:
        # 服务未初始化或提交失败时不退回同步查询，主缓存仍可立即使用。
        logger.warning("辅助搜索刷新无法提交，直接返回主缓存结果", exc_info=True)
    return []


def build_search_page(data: dict[str, Any], supplemental: list[dict[str, Any]],
                      page: int, page_size: int, type_filter: Optional[str],
                      year_filter: Optional[int], provider_filter: Optional[str],
                      title_filter: Optional[str], recognition_title: Optional[str],
                      recognition_source: str, recognition_rule: Optional[str],
                      recognition_manager: Any) -> dict[str, Any]:
    """首搜和翻页共用通用筛选分页，仅对响应副本注入主页标记。"""
    results = data["results"]
    title_kw = title_filter.lower() if title_filter else None

    def matches_filters(item: dict[str, Any]) -> bool:
        return ((not type_filter or item.get("type") == type_filter)
                and (not year_filter or item.get("year") == year_filter)
                and (not provider_filter or item.get("provider") == provider_filter)
                and (not title_kw or title_kw in (item.get("title") or "").lower()))

    selection = select_search_results(results, predicate=matches_filters, page=page, page_size=page_size)
    for item in selection["results"]:
        matches = recognition_source == "all" or item.get("provider") == recognition_source
        if recognition_rule:
            matches = matches and recognition_manager is not None and TitleRecognitionEngine().exact_match(
                item.get("title") or "", recognition_rule)
        item["recognitionTitle"] = recognition_title if matches else None
        item["currentEpisodeIndex"] = data["search_episode"]
    return {**selection, "supplemental_results": deepcopy(supplemental),
            "search_season": data["search_season"], "search_episode": data["search_episode"],
            "page": page, "pageSize": page_size,
            "available_years": sorted({i["year"] for i in results if i.get("year")}, reverse=True),
            "available_providers": sorted({i["provider"] for i in results if i.get("provider")}),
            "available_types": sorted({i["type"] for i in results if i.get("type")})}


async def search_aliases_from_enabled_sources(metadata_manager: Any, keyword: str, user: User) -> Set[str]:
    """从所有已启用的辅助元数据源并发获取别名。"""
    # 修正：调用新的、更通用的方法，并只返回别名部分
    aliases, _, _, _ = await search_supplemental_sources(metadata_manager, keyword, user)
    return aliases



async def search_supplemental_sources(metadata_manager: Any, keyword: str, user: User) -> Tuple[Set[str], List[ProviderSearchInfo], Dict[str, str], Dict[str, List[str]]]:
    """
    从所有启用的辅助源（包括强制启用的）进行搜索。
    返回一个元组：(别名集合, 补充搜索结果列表, 标题→类型映射, 别名来源映射)

    优化：对于 TMDB/Bangumi 等源，搜索结果不包含完整别名，
    需要对前几个结果调用 get_details 获取完整别名（包括中文别名）。

    别名来源映射格式：{"TMDB": ["别名1", "别名2"], "Bangumi": ["别名3"], ...}
    """
    # 并行读取所有 provider 的 force_aux_search 配置（避免串行 await 阻塞）
    enabled_providers = [
        (provider, settings) for provider, settings in metadata_manager.source_settings.items()
        if settings.get('isEnabled')
    ]

    if not enabled_providers:
        # 无可用源时仍保持四项返回契约，供搜索流程统一解包。
        return set(), [], {}, {}

    # 并行读取 config
    force_config_tasks = [
        metadata_manager._config_service.get(f"{provider}_force_aux_search", "false")
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
        if source_instance := metadata_manager.sources.get(provider):
            tasks.append(_source_pipeline(source_instance, provider, keyword, user))
        else:
            metadata_manager.logger.warning(f"已启用的元数据源 '{provider}' 未被成功加载，跳过辅助搜索。")

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
    metadata_manager.last_aux_search_timing = []

    for provider_name, res, search_dur, detail_info, error in pipeline_results:
        if error:
            metadata_manager.last_aux_search_timing.append((provider_name, search_dur, 0))
            if isinstance(error, httpx.ConnectError):
                metadata_manager.logger.warning(f"无法连接到元数据源 '{provider_name}'。({search_dur:.0f}ms)")
            elif isinstance(error, (httpx.TimeoutException, httpx.ReadTimeout)):
                metadata_manager.logger.warning(f"连接元数据源 '{provider_name}' 超时。({search_dur:.0f}ms)")
            else:
                metadata_manager.logger.error(f"元数据源 '{provider_name}' 辅助搜索失败: {error} ({search_dur:.0f}ms)", exc_info=False)
            continue

        if not res:
            metadata_manager.last_aux_search_timing.append((provider_name, search_dur, 0))
            continue

        # 计算总耗时（搜索 + 详情获取）
        total_provider_dur = search_dur
        detail_alias_count = 0
        if detail_info:
            _, detail_dur = detail_info
            if detail_dur > 0:
                total_provider_dur = search_dur + detail_dur
            detail_alias_count = len(detail_info[0]) if detail_info[0] else 0

        metadata_manager.last_aux_search_timing.append((provider_name, total_provider_dur, len(res)))
        metadata_manager.logger.info(f"辅助源 '{provider_name}' 为关键词 '{keyword}' 找到了 {len(res)} 个结果, {detail_alias_count} 个别名。({total_provider_dur:.0f}ms)")

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
        offline_enabled = (await metadata_manager._config_service.get("bangumiDataOfflineEnabled", "true")).lower() == "true"
        if offline_enabled:
            bgm_data = metadata_manager.get_offline_bangumi_service()
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
        metadata_manager.logger.debug(f"bangumi-data 本地别名补充失败: {e}")

    return {alias for alias in all_aliases if alias}, supplemental_results, title_type_map, alias_sources



async def supplement_empty_search_results(
    metadata_manager: Any,
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
    for provider_name, source in metadata_manager.sources.items():
        if not getattr(source, 'is_search_supplement_source', False):
            continue
        if not metadata_manager.source_settings.get(provider_name, {}).get('isEnabled'):
            continue
        candidate_sources.append((provider_name, source))

    if not candidate_sources:
        return []

    # 并行读取所有补充源的开关配置
    config_tasks = [
        metadata_manager._config_service.get(f"{pn}_searchSupplementEnabled", "false")
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
            metadata_manager.logger.warning(f"搜索补充源 '{source.provider_name}' 调用失败: {e}")
            return source.provider_name, [], _dur

    timed_list = await asyncio.gather(*[_call_source(s) for s in supplement_sources])

    # 记录各补充源耗时
    metadata_manager.last_supplement_timing = [
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
