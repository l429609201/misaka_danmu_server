"""主页结果编排：组合通用搜索缓存、辅助刷新及识别词响应标记。"""
import asyncio
import logging
from copy import deepcopy
from typing import Any, Callable, Optional

from fastapi import HTTPException

from src.services.config_service import get_config_service
from src.services.service_container import get_task_manager
from src.utils.diagnostics.task_exceptions import TaskFailed, TaskSuccess
from src.workflows.search.result_cache import (
    read_search_results, write_search_results, select_search_results,
)

logger = logging.getLogger(__name__)


async def _search_cache_ttl() -> int:
    """复用搜索缓存寿命配置，服务尚未初始化时回退三小时。"""
    try:
        return await get_config_service().get_search_cache_ttl()
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
            _, results, _, _ = await metadata_manager.search_supplemental_sources(title, user)
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
            matches = matches and recognition_manager is not None and recognition_manager._exact_match(
                item.get("title") or "", recognition_rule)
        item["recognitionTitle"] = recognition_title if matches else None
        item["currentEpisodeIndex"] = data["search_episode"]
    return {**selection, "supplemental_results": deepcopy(supplemental),
            "search_season": data["search_season"], "search_episode": data["search_episode"],
            "page": page, "pageSize": page_size,
            "available_years": sorted({i["year"] for i in results if i.get("year")}, reverse=True),
            "available_providers": sorted({i["provider"] for i in results if i.get("provider")}),
            "available_types": sorted({i["type"] for i in results if i.get("type")})}
