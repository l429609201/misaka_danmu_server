"""搜索入口编排：主页复用全量缓存，控制 API 保存独立导入会话。"""
import logging
import uuid
from typing import Any, Optional

from src.schemas.ui_models import User
from src.services.task_profiler import TaskProfiler, FLOW_HOME_SEARCH
from src.utils import SearchTimer, SEARCH_TYPE_HOME, SEARCH_TYPE_CONTROL_SEARCH, parse_search_keyword
from src.workflows.search.engine import SearchCaller
from src.workflows.search.provider_search_flow import (
    _apply_recognition_mapping, resolve_search_context, execute_provider_search,
)
from src.workflows.search.result_cache import write_search_results
from src.workflows.search.ui_results import (
    search_cache_keys, read_full_results, write_full_results, write_supplemental_results,
    # 主页命中缓存和分页返回仍依赖这两个入口，必须显式导入。
    read_supplemental_results, build_search_page,
)

logger = logging.getLogger(__name__)


class SearchBusyError(Exception):
    """同一控制凭据已有搜索或自动导入正在执行。"""


class SearchCacheUnavailableError(Exception):
    """控制搜索结果无法保存，不能生成有效的导入会话。"""



async def _cache_context_matches(
    data: dict[str, Any],
    original_keyword: str,
    parsed: dict[str, Any],
    title_recognition_manager: Any,
) -> bool:
    """校验缓存中的搜索上下文仍符合当前识别词规则。"""
    validation = data.get("cache_validation")
    if not isinstance(validation, dict) or not isinstance(validation.get("preprocessing_title"), str):
        logger.info("搜索缓存缺少预处理输入，重新生成")
        return False
    recognition, expected_season, mapped_title = await _apply_recognition_mapping(
        original_keyword, parsed.get("season"), title_recognition_manager,
    )
    if validation.get("recognition_applied") is not recognition.applied:
        logger.info("搜索缓存的识别词反向映射状态已变化，重新生成")
        return False
    expected_episode = parsed.get("episode")
    expected_title = mapped_title if recognition.applied else validation["preprocessing_title"]
    if title_recognition_manager and not recognition.applied:
        processed_title, processed_episode, processed_season, applied = (
            await title_recognition_manager.apply_search_preprocessing(
                expected_title, expected_episode, expected_season,
            )
        )
        if applied:
            expected_title, expected_season, expected_episode = (
                processed_title, processed_season, processed_episode,
            )
    expected = (expected_title, expected_season, expected_episode)
    cached = (data["search_title"], data["search_season"], data["search_episode"])
    if expected != cached:
        logger.info("搜索缓存上下文不一致，重新生成: 缓存=%r，当前=%r", cached, expected)
        return False
    return True


async def search_home(
    keyword: str, *, scraper_manager: Any, metadata_manager: Any,
    ai_service: Any, title_recognition_manager: Any, config_service: Any, user: Any,
    page: int, page_size: int, type_filter: Optional[str] = None,
    year_filter: Optional[int] = None, provider_filter: Optional[str] = None,
    title_filter: Optional[str] = None,
) -> dict[str, Any]:
    """组合缓存、共用搜索和响应分页，命中时不执行外部主搜索及 AI 修正。"""
    timer = SearchTimer(SEARCH_TYPE_HOME, keyword, logger)
    profiler = TaskProfiler(FLOW_HOME_SEARCH)
    timer.start()
    try:
        parsed = parse_search_keyword(keyword)
        original = parsed.get("original_keyword") or keyword.strip()
        cache_key, supplemental_key = search_cache_keys(original, parsed["season"])
        timer.step_start("缓存检查")
        data = await read_full_results(cache_key)
        if data is not None and not await _cache_context_matches(
            data, original, parsed, title_recognition_manager,
        ):
            data = None
        profiler.record_step("缓存检查", timer.step_end(details="命中" if data is not None else "未命中"))
        if data is not None:
            recognition, _, _ = await _apply_recognition_mapping(
                original, parsed["season"], title_recognition_manager,
            )
            supplemental = await read_supplemental_results(
                supplemental_key, data["search_title"], metadata_manager, user,
            )
        else:
            context = await resolve_search_context(
                keyword, session=None, metadata_manager=metadata_manager,
                ai_service=ai_service, title_recognition_manager=title_recognition_manager,
                config_service=config_service, user=user, timer=timer, profiler=profiler,
            )
            outcome = await execute_provider_search(
                context, session=None, scraper_manager=scraper_manager,
                metadata_manager=metadata_manager, ai_service=ai_service, user=user,
                caller=SearchCaller.WEBUI_SEARCH, timer=timer, profiler=profiler,
            )
            recognition = outcome.recognition
            timer.step_start("结果缓存")
            data = await write_full_results(
                cache_key, outcome.results, outcome.search_title, outcome.season, outcome.episode,
                cache_validation=context["cache_validation"],
            )
            supplemental = await write_supplemental_results(supplemental_key, outcome.supplemental_results)
            profiler.record_step("结果缓存", timer.step_end())
        return build_search_page(
            data, supplemental, page, page_size, type_filter, year_filter,
            provider_filter, title_filter, recognition.recognition_title,
            recognition.source_restriction, recognition.rule_source, title_recognition_manager,
        )
    finally:
        timer.finish()
        await profiler.flush()


async def search_control(
    keyword: str, *, season: Optional[int], episode: Optional[int], session: Any,
    scraper_manager: Any, metadata_manager: Any, config_service: Any,
    ai_service: Any, title_recognition_manager: Any, api_key: str,
) -> dict[str, Any]:
    """共用搜索核心，保留控制 API 的互斥锁与十分钟完整导入索引。"""
    if not await scraper_manager.acquire_search_lock(api_key):
        raise SearchBusyError("已有搜索或自动导入任务正在进行中，请稍后再试。")
    timer = SearchTimer(SEARCH_TYPE_CONTROL_SEARCH, keyword, logger)
    try:
        timer.start()
        user = User(id=0, username="control_api")
        # 每次控制搜索独立收集错误，避免缓存命中时串用上一轮状态。
        if hasattr(scraper_manager, "last_search_errors"):
            scraper_manager.last_search_errors = []
        parsed = parse_search_keyword(keyword)
        original = parsed.get("original_keyword") or keyword.strip()
        # 显式参数先覆盖解析值，再按首次搜索顺序应用识别规则。
        parsed["season"] = season if season is not None else parsed.get("season")
        parsed["episode"] = episode if episode is not None else parsed.get("episode")
        home_key, supplemental_key = search_cache_keys(original, parsed["season"])
        data = await read_full_results(home_key)
        if data is not None and not await _cache_context_matches(
            data, original, parsed, title_recognition_manager,
        ):
            data = None
        if data is not None:
            recognition, _, _ = await _apply_recognition_mapping(
                original, parsed["season"], title_recognition_manager,
            )
        else:
            context = await resolve_search_context(
                keyword, session=session, metadata_manager=metadata_manager,
                ai_service=ai_service, title_recognition_manager=title_recognition_manager,
                config_service=config_service, user=user, season=season, episode=episode, timer=timer,
            )
            outcome = await execute_provider_search(
                context, session=session, scraper_manager=scraper_manager,
                metadata_manager=metadata_manager, ai_service=ai_service, user=user,
                caller=SearchCaller.CONTROL_AUTO_IMPORT, timer=timer,
            )
            recognition = outcome.recognition
            data = await write_full_results(
                home_key, outcome.results, outcome.search_title, outcome.season, outcome.episode,
                cache_validation=context["cache_validation"],
            )
            await write_supplemental_results(supplemental_key, outcome.supplemental_results)
        # 冷热路径统一注入最终集号及受源、标题限定的识别标记，不修改共享缓存。
        results = build_search_page(
            data, [], 1, max(1, len(data["results"])), None, None, None, None,
            recognition.recognition_title, recognition.source_restriction,
            recognition.rule_source, title_recognition_manager,
        )["results"]
        search_id = str(uuid.uuid4())
        saved = await write_search_results(f"control_search_{search_id}", results, ttl=600, region="default")
        if not saved:
            raise SearchCacheUnavailableError("搜索结果缓存不可用，请稍后重试。")
        response = {
            "searchId": search_id,
            "results": [{**item, "resultIndex": index} for index, item in enumerate(results)],
        }
        errors = list(getattr(scraper_manager, "last_search_errors", []))
        if not results and not errors:
            errors.append("未找到匹配结果")
        if errors:
            response["errors"] = errors
        return response
    finally:
        # 即使计时报告失败，控制锁也必须释放。
        try:
            timer.finish()
        finally:
            await scraper_manager.release_search_lock(api_key)
