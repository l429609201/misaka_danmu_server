"""外部数据源搜索编排（WebUI 首页搜索 / 控制 API 搜索的共用能力）。

why：WebUI 首页搜索（api/ui/search.py）与控制 API 搜索（api/control/import_routes.py）
     此前各自实现了「关键词解析 → 识别词反向映射 → 名称转换 → 季度名称映射 →
     弹幕源校验 → unified_search → AI 类型/季度修正」这一整套前后处理。
     control 版源码中甚至留有 `copied and adapted from ui_api.py` 的注释，
     两份实现已出现行为漂移（类型修正/季度过滤/排序在 ui 侧交给 unified_search，
     在 control 侧又在端点内重做一遍）。本模块将该流程收口为单一实现。

分层约定（与本包 __init__ 一致）：
    本模块不得导入 services/task_manager.py，也不得提交任务。
    API 层只负责参数校验、分页/过滤等表现层逻辑与响应模型组装。
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.utils import parse_search_keyword
from src.utils.data_processing.name_converter import convert_to_chinese_title
from src.utils.parsing.season_ai_mapping import prefetch_metadata_for_correction
from src.workflows.search.ai_correction import correct_search_results

from src.workflows.search.engine import SearchCaller, unified_search

from src.utils.diagnostics.search_timer import SearchTimer, SubStepTiming

logger = logging.getLogger(__name__)


@dataclass
class RecognitionMapping:
    """识别词反向映射的命中结果。

    why：命中后需要把「入库正确名」回填到搜索结果上，且必须同时满足
         源限定与标题精确匹配两个条件才可标记，故将判定所需的三个字段
         打包传回 API 层，由其在响应组装阶段注入。
    """

    #: 识别词指定的入库正确名，注入到结果的 recognitionTitle 字段
    recognition_title: Optional[str] = None
    #: 源限定（规则中的 source=xxx），"all" 表示不限定
    source_restriction: str = "all"
    #: 规则左侧的源站标题，用于标题精确校验
    rule_source: Optional[str] = None
    #: 是否实际命中并应用了反向映射
    applied: bool = False


@dataclass
class ProviderSearchOutcome:
    """外部源搜索编排的产出。

    仅承载业务结果，不含分页/过滤等表现层信息。
    """

    #: 搜索结果（已完成类型判定、季度过滤、排序、AI 修正）
    results: List[Any] = field(default_factory=list)
    #: 辅助源副产物，供 API 层缓存与前端展示
    supplemental_results: List[Any] = field(default_factory=list)
    #: 实际生效的搜索标题（经名称转换与预处理）
    search_title: str = ""
    #: 实际生效的季度过滤值
    season: Optional[int] = None
    #: 实际生效的集数
    episode: Optional[int] = None
    #: 识别词反向映射命中信息
    recognition: RecognitionMapping = field(default_factory=RecognitionMapping)
    #: 全量结果缓存键（不含分页/过滤维度）
    cache_key: str = ""
    #: 辅助源结果缓存键
    supplemental_cache_key: str = ""


class _NullTimer:
    """SearchTimer 的空实现，供调用方不传计时器时使用。

    why：编排流程内部有多个 step_start/step_end 埋点，若允许 timer 为 None
         需要在每个埋点处判空。用空对象替代可保持流程代码整洁。
    """

    def step_start(self, *args, **kwargs) -> None:
        return None

    def step_end(self, *args, **kwargs) -> float:
        return 0.0


class _NullProfiler:
    """TaskProfiler 的空实现，语义同 _NullTimer。"""

    def record_step(self, *args, **kwargs) -> None:
        return None


async def read_search_cache(
    db,
    backend,
    cache_key: str,
    *,
    region: str = "search",
) -> Optional[Any]:
    """读取搜索缓存，缓存后端不可用或失败时回退到数据库。

    why：原 ui/search.py 中「先读 backend、失败或未命中再读 DB」的模式
         重复出现 3 次，且各处的异常日志文案不一致。此处统一收口。

    Args:
        db: DatabaseService 实例（已处于事务中）
        backend: CacheService 实例，可为 None
        cache_key: 不含 "search:" 前缀的缓存键
        region: 缓存后端的 region

    Returns:
        缓存值；未命中时返回 None
    """
    if backend is not None:
        try:
            value = await backend.get(cache_key, region=region)
            if value is not None:
                return value
        except Exception as e:
            logger.warning(f"缓存后端读取失败，回退到数据库: {e}")
    return await db.cache.get_json(f"search:{cache_key}")


async def write_search_cache(
    db,
    backend,
    cache_key: str,
    value: Any,
    ttl_seconds: int,
    *,
    region: str = "search",
) -> None:
    """写入搜索缓存，缓存后端不可用或失败时回退到数据库。

    Args:
        db: DatabaseService 实例（已处于事务中）
        backend: CacheService 实例，可为 None
        cache_key: 不含 "search:" 前缀的缓存键
        value: 待缓存的可 JSON 序列化对象
        ttl_seconds: 相对存活秒数
        region: 缓存后端的 region
    """
    if backend is not None:
        try:
            await backend.set(cache_key, value, ttl=ttl_seconds, region=region)
            return
        except Exception as e:
            logger.warning(f"缓存后端写入失败，回退到数据库: {e}")
    await db.cache.set_json(f"search:{cache_key}", value, ttl_seconds)


async def _apply_recognition_mapping(
    original_keyword: str,
    season_to_filter: Optional[int],
    title_recognition_manager,
) -> tuple[RecognitionMapping, Optional[int], Optional[str]]:
    """应用识别词反向映射：用户用「入库名」搜索时，自动改用源站真实名去搜。

    例：规则 "说唱巅峰对决2026 => {[...title=中国新说唱 第九季...]}"，
        用户搜"中国新说唱 第九季" → 实际用"说唱巅峰对决2026"去搜，
        结果标记 recognitionTitle。

    Args:
        original_keyword: 未经拆解的原始完整关键词
        season_to_filter: 由用户输入解析出的目标季度
        title_recognition_manager: 标题识别管理器，可为 None

    Returns:
        (映射结果, 修正后的季度过滤值, 映射后的搜索标题)
        未命中时搜索标题返回 None，由调用方继续使用原标题。
    """
    mapping_info = RecognitionMapping()
    if not title_recognition_manager:
        return mapping_info, season_to_filter, None

    try:
        mapping = await title_recognition_manager.apply_search_title_mapping(original_keyword)
    except Exception as e:
        logger.warning(f"识别词反向映射失败: {e}")
        return mapping_info, season_to_filter, None

    if not mapping:
        return mapping_info, season_to_filter, None

    mapping_info.recognition_title = mapping["recognition_title"]
    # why：规则形如 source=iqiyi 表示该识别词仅对爱奇艺源生效。命中后记录源限定，
    # 注入 recognitionTitle 时仅打给匹配源的结果，避免 renren 等无关源被误标。
    mapping_info.source_restriction = mapping.get("rule_source_restriction", "all") or "all"
    # 规则左侧源站标题(如"说唱巅峰对决2026")，用于标题精确校验
    mapping_info.rule_source = mapping.get("search_title")
    mapping_info.applied = True

    # why：反向映射把搜索词换成了源站真实名（如"说唱巅峰对决2026"），
    # 但 season_to_filter 仍是用户输入"入库名"解析出的目标季(如第9季)。
    # 源站结果实际是源季(如第1季)，若不修正会被季度过滤全部删光。
    # 这里用规则 season_offset 解析出的"源站季度"覆盖过滤季度，确保能命中源站结果。
    mapped_source_season = mapping.get("search_season")
    if mapped_source_season is not None:
        if season_to_filter != mapped_source_season:
            logger.info(
                f"反向映射季度修正: 过滤季度 {season_to_filter} → "
                f"源站季度 {mapped_source_season}"
            )
        season_to_filter = mapped_source_season
    else:
        # 通配/无法解析源季（如 *+4）：不按季过滤，避免误删源站结果
        season_to_filter = None

    logger.info(
        f"识别词反向映射: 实际搜索 '{mapping['search_title']}'，"
        f"入库名标记为 '{mapping_info.recognition_title}'"
    )
    return mapping_info, season_to_filter, mapping["search_title"]


async def _resolve_season_name(
    search_title: str,
    season: int,
    metadata_manager,
    ai_service,
    user,
) -> Optional[str]:
    """通过元数据源查询指定季度的实际名称。

    例：搜索 "唐朝诡事录 S03" 时，查得第3季实际名称 "唐朝诡事录之西行"，
    该名称会作为额外搜索标题参与本次搜索。

    Args:
        search_title: 基础搜索标题
        season: 目标季度号
        metadata_manager: 元数据源管理器
        ai_service: 共享 AI 服务，可为 None
        user: 发起搜索的用户

    Returns:
        季度实际名称；未找到或查询失败时返回 None
    """
    try:
        # 缺密钥只跳过 AI，普通元数据季度名称查询仍然执行。
        ai_matcher = (
            await ai_service.get_matcher()
            if ai_service and await ai_service.is_available()
            else None
        )
        season_name = await metadata_manager.season_mapper.get_season_name(
            search_title, season, ai_matcher=ai_matcher, user=user
        )
        if season_name:
            logger.info(f"季度名称映射: '{search_title}' S{season:02d} → '{season_name}'")
            return season_name
        logger.info(f"季度名称映射未找到: '{search_title}' S{season:02d}")
    except Exception as e:
        logger.warning(f"季度名称映射失败: {e}")
    return None



async def resolve_search_context(
    keyword: str,
    *,
    # 兼容既有调用参数；上下文解析不持有数据库会话。
    session: Any,
    metadata_manager,
    ai_service,
    title_recognition_manager,
    config_service,
    user,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    enable_recognition_mapping: bool = True,
    enable_preprocessing: bool = True,
    enable_season_name_mapping: bool = True,
    timer: Optional["SearchTimer"] = None,
    profiler=None,
) -> Dict[str, Any]:
    """解析搜索上下文：把用户输入的关键词转换为实际用于搜索的标题与季集。

    执行顺序（顺序有语义依赖，不可调换）：
        1. 关键词解析（拆出标题、季、集）
        2. 识别词反向映射（最高优先级，命中后跳过后续改写）
        3. 名称转换（非中文标题转中文）
        4. 搜索预处理规则
        5. 季度名称映射

    Args:
        keyword: 用户输入的原始关键词
        session: 数据库会话
        metadata_manager: 元数据源管理器
        ai_service: 共享 AI 服务，可为 None
        title_recognition_manager: 标题识别管理器，可为 None
        config_service: 配置服务，供名称转换读取开关
        user: 发起搜索的用户
        season: 显式指定的季度，优先于关键词解析结果
        episode: 显式指定的集数，优先于关键词解析结果
        enable_recognition_mapping: 是否启用识别词反向映射
        enable_preprocessing: 是否启用搜索预处理规则
        enable_season_name_mapping: 是否启用季度名称映射
        timer: 搜索计时器，可为 None
        profiler: 性能统计器，可为 None

    Returns:
        含 search_title/season/episode/recognition/season_mapped_title 的字典
    """
    timer = timer or _NullTimer()
    profiler = profiler or _NullProfiler()

    # ── 步骤 1：关键词解析 ──
    timer.step_start("关键词解析")
    parsed = parse_search_keyword(keyword)
    original_title = parsed["title"]
    # 显式查询参数优先于从关键词中解析出的值
    season_to_filter = season if season is not None else parsed.get("season")
    episode_to_filter = episode if episode is not None else parsed.get("episode")
    # 原始完整关键词（未经拆解），供识别词反向映射使用
    original_keyword = parsed.get("original_keyword") or keyword.strip()
    _dur = timer.step_end()
    profiler.record_step("关键词解析", _dur)

    # ── 步骤 2：识别词反向映射（最高优先级）──
    recognition = RecognitionMapping()
    mapped_title = None
    if enable_recognition_mapping:
        recognition, season_to_filter, mapped_title = await _apply_recognition_mapping(
            original_keyword, season_to_filter, title_recognition_manager
        )

    # ── 步骤 3：名称转换 ──
    # 识别词反向映射命中时跳过，因为用户已显式指定真实搜索词
    timer.step_start("名称转换")
    if recognition.applied and mapped_title:
        search_title = mapped_title
    else:
        search_title, conversion_applied = await convert_to_chinese_title(
            original_title, config_service, metadata_manager, ai_service, user
        )
        if conversion_applied:
            logger.info(f"名称转换: '{original_title}' → '{search_title}'")
    timer.step_end()

    # ── 步骤 4：搜索预处理规则 ──
    # 保存真正的预处理输入；缓存校验不能对最终标题重复应用规则。
    cache_validation = {
        "preprocessing_title": search_title,
        "recognition_applied": recognition.applied,
    }
    # 识别词反向映射命中时跳过，避免对真实搜索词二次改写
    timer.step_start("预处理规则应用")
    if enable_preprocessing and title_recognition_manager and not recognition.applied:
        base_title = search_title
        (
            processed_title,
            processed_episode,
            processed_season,
            preprocessing_applied,
        ) = await title_recognition_manager.apply_search_preprocessing(
            base_title, episode_to_filter, season_to_filter
        )
        if preprocessing_applied:
            search_title = processed_title
            logger.info(f"搜索预处理: '{base_title}' -> '{search_title}'")
            if processed_episode != episode_to_filter:
                episode_to_filter = processed_episode
                logger.info(f"集数预处理: {parsed['episode']} -> {episode_to_filter}")
            if processed_season != season_to_filter:
                season_to_filter = processed_season
                logger.info(f"季度预处理: {parsed['season']} -> {season_to_filter}")
        else:
            logger.info(f"搜索预处理未生效: '{base_title}'")
    timer.step_end()

    # ── 步骤 5：季度名称映射 ──
    # 识别词反向映射命中时跳过（用户已显式指定真实搜索词）
    season_mapped_title = None
    if (
        enable_season_name_mapping
        and season_to_filter is not None
        and season_to_filter > 0
        and not recognition.applied
    ):
        timer.step_start("季度名称映射")
        season_mapped_title = await _resolve_season_name(
            search_title, season_to_filter, metadata_manager, ai_service, user
        )
        timer.step_end()

    return {
        "search_title": search_title,
        "season": season_to_filter,
        "episode": episode_to_filter,
        "recognition": recognition,
        "season_mapped_title": season_mapped_title,
        "cache_validation": cache_validation,
    }



async def _apply_ai_correction(
    search_title: str,
    results: List[Any],
    metadata_manager,
    ai_matcher,
    prefetch_task,
    timer,
    profiler,
    user: Any,
) -> List[Any]:
    """应用 AI 类型与季度映射修正。

    Args:
        search_title: 实际生效的搜索标题
        results: 待修正的搜索结果
        metadata_manager: 元数据源管理器
        ai_matcher: AI 匹配器实例
        prefetch_task: 元数据预热任务，可为 None
        timer: 搜索计时器
        profiler: 性能统计器
        user: 发起搜索的用户

    Returns:
        修正后的结果列表；修正失败时返回原列表
    """
    try:
        timer.step_start("AI映射修正")
        # 取预热结果：metadata_results 用于跳过搜索，seasons_info 用于跳过获取季度
        prefetched_metadata = None
        prefetched_seasons = None
        if prefetch_task:
            try:
                prefetched_full = await prefetch_task
                if isinstance(prefetched_full, dict):
                    prefetched_metadata = prefetched_full.get("metadata_results")
                    prefetched_seasons = prefetched_full.get("seasons_info")
            except Exception as e:
                logger.warning(f"预热数据获取失败: {e}")

        mapping_result = await correct_search_results(
            search_title=search_title,
            search_results=results,
            metadata_manager=metadata_manager,
            ai_matcher=ai_matcher,
            logger=logger,
            similarity_threshold=60.0,
            prefetched_metadata_results=prefetched_metadata,
            prefetched_seasons_info=prefetched_seasons,
            user=user,
        )

        if mapping_result["total_corrections"] > 0:
            logger.info(
                f"统一AI映射成功: 总计修正 {mapping_result['total_corrections']} 个结果"
                f"（类型 {len(mapping_result['type_corrections'])} 个，"
                f"季度 {len(mapping_result['season_corrections'])} 个）"
            )
            _dur = timer.step_end(details=f"修正{mapping_result['total_corrections']}个")
            profiler.record_step("AI映射修正", _dur)
            return mapping_result["corrected_results"]

        logger.info("统一AI映射: 未找到需要修正的信息")
        _dur = timer.step_end(details="无修正")
        profiler.record_step("AI映射修正", _dur)
        return results
    except Exception as e:
        logger.warning(f"统一AI映射任务执行失败: {e}")
        _dur = timer.step_end(details=f"失败: {e}")
        profiler.record_step("AI映射修正", _dur, success=False)
        return results


async def execute_provider_search(
    context: Dict[str, Any],
    *,
    # 只向搜索引擎透传兼容参数，不在外部搜索期间持有事务。
    session: Any,
    scraper_manager,
    metadata_manager,
    ai_service,
    user,
    caller: SearchCaller | str,
    enable_ai_correction: bool = True,
    use_alias_expansion: Optional[bool] = None,
    trust_metadata_aliases: bool = True,
    enable_type_classification: bool = True,
    timer: Optional["SearchTimer"] = None,
    profiler=None,
) -> ProviderSearchOutcome:
    """基于已解析的搜索上下文执行全网搜索并做 AI 修正。

    别名扩展/过滤、类型分级判定、季度过滤、结果排序均由 unified_search 内部完成，
    调用方不应在返回后重复这些处理。

    Args:
        context: resolve_search_context 的返回值
        session: 数据库会话
        scraper_manager: 弹幕源管理器
        metadata_manager: 元数据源管理器
        ai_service: 共享 AI 服务，可为 None
        user: 发起搜索的用户
        caller: 调用途径标识，用于日志前缀
        enable_ai_correction: 是否执行 AI 类型/季度修正
        use_alias_expansion: 是否做别名扩展；None 表示按辅助源启用情况自动判定
        timer: 搜索计时器，可为 None
        profiler: 性能统计器，可为 None

    Returns:
        ProviderSearchOutcome

    Raises:
        ValueError: 没有启用任何弹幕源时抛出，由 API 层转为 HTTP 400
    """
    timer = timer or _NullTimer()
    profiler = profiler or _NullProfiler()

    search_title: str = context["search_title"]
    season_to_filter: Optional[int] = context["season"]
    episode_to_filter: Optional[int] = context["episode"]
    season_mapped_title: Optional[str] = context.get("season_mapped_title")

    # 在所有搜索之前检查是否有可用弹幕源
    if not scraper_manager.has_enabled_scrapers:
        logger.warning("没有启用的弹幕搜索源，终止本次搜索")
        raise ValueError("没有启用的弹幕搜索源，请在“搜索源”页面中启用至少一个。")

    # 关闭修正时不初始化匹配器，也不启动 AI 元数据预热。
    ai_matcher = (
        await ai_service.get_matcher()
        if enable_ai_correction and ai_service and await ai_service.is_available()
        else None
    )
    episode_info = (
        {"season": season_to_filter, "episode": episode_to_filter}
        if episode_to_filter is not None
        else None
    )
    if use_alias_expansion is None:
        use_alias_expansion = bool(metadata_manager) and await metadata_manager.has_any_enabled_aux_source()

    search_titles = [search_title]
    if season_mapped_title and season_mapped_title != search_title:
        search_titles.append(season_mapped_title)
        logger.info(f"搜索将同时使用: {search_titles}")
    supplemental_results: List[Any] = []
    aux_title_type_map: Dict[str, Any] = {}

    prefetch_task = None
    try:
        # 预热与主搜索共用请求生命周期；失败或取消时必须回收，不能遗留后台请求。
        if ai_matcher and metadata_manager:
            prefetch_task = asyncio.create_task(
                prefetch_metadata_for_correction(
                    search_title=search_title, metadata_manager=metadata_manager,
                    ai_matcher=ai_matcher, logger=logger, user=user,
                )
            )
        timer.step_start("统一搜索流程")
        results = await unified_search(
            search_term=search_title, session=session,
            scraper_manager=scraper_manager, metadata_manager=metadata_manager,
            use_alias_expansion=use_alias_expansion, use_alias_filtering=True,
            enable_type_correction=enable_type_classification,
            enable_title_type_correction=True,
            use_source_priority_sorting=True, filter_season=season_to_filter,
            episode_info=episode_info, custom_aliases=set(search_titles),
            # 过滤别名不等于实际搜索词，季度映射标题需要同时传给搜索引擎。
            search_titles=search_titles, supplemental_results_out=supplemental_results,
            aux_title_type_map_out=aux_title_type_map,
            trust_metadata_aliases=trust_metadata_aliases,
            log_excluded=True, search_user=user, caller=caller,
        )
        # 单源计时附在统一搜索步骤上，否则主页报告只显示总耗时。
        source_timings = [
            SubStepTiming(
                name=name[3:] if name.startswith("补充:") else name,
                duration_ms=duration, result_count=count,
                group="补充源" if name.startswith("补充:") else "弹幕源",
            )
            for name, duration, count in getattr(scraper_manager, "last_search_timing", [])
        ]
        if use_alias_expansion:
            source_timings.extend(
                SubStepTiming(
                    name=name, duration_ms=duration, result_count=count,
                    group="辅助源(别名)",
                )
                for name, duration, count in getattr(metadata_manager, "last_aux_search_timing", [])
            )
        profiler.record_step("统一搜索流程", timer.step_end(
            details=f"{len(results)}个结果", sub_steps=source_timings,
        ))
        # 集数属于本次请求，避免复用源缓存时沿用其他请求的集数。
        for item in results:
            item.currentEpisodeIndex = episode_to_filter
        if ai_matcher and metadata_manager:
            results = await _apply_ai_correction(
                search_title, results, metadata_manager, ai_matcher,
                prefetch_task, timer, profiler, user,
            )
    finally:
        if prefetch_task is not None:
            if not prefetch_task.done():
                prefetch_task.cancel()
            # 获取已完成任务的异常，取消异常由外层请求继续传播。
            await asyncio.gather(prefetch_task, return_exceptions=True)

    return ProviderSearchOutcome(
        results=results,
        supplemental_results=supplemental_results,
        search_title=search_title,
        season=season_to_filter,
        episode=episode_to_filter,
        recognition=context["recognition"],
        cache_key=f"provider_search_v2_{search_title}_{season_to_filter or 'all'}",
        supplemental_cache_key=f"supplemental_search_{search_title}",
    )
