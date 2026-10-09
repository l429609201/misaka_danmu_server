"""搜索引擎模块（搜索领域核心能力）。

提供全网统一搜索 `unified_search`，供 tasks/ 编排层、API 层、AI 工具层、通知层调用。

分层约定：本模块不得导入 services/task_manager.py，也不得提交任务。
"""

import asyncio
import logging
import re
from enum import Enum
from typing import List, Optional, Any, Callable, Dict, Union, Tuple, Set
import time
from collections import defaultdict
from thefuzz import fuzz
from sqlalchemy.ext.asyncio import AsyncSession
import hashlib
import json


# why: 统一走服务层，不再直连已废弃的 crud 层（架构规则：DB 访问经 Repository/DatabaseService）。
from src.services.service_container import get_database_service, get_metadata_service, get_task_manager
from src.schemas.auth import User
from src.utils.diagnostics.task_exceptions import TaskFailed, TaskSuccess
from src.utils.diagnostics.buffered_logging import BufferedLogHandler, create_buffered_logger, flush_buffered_logs
from src.workflows.search.ui_results import search_supplemental_sources, supplement_empty_search_results
from src.workflows.search.cache_policy import read_search_cache_ttl
# 原始源结果使用完整搜索模型；直接导入接口的同名模型要求 result_index，不能用于缓存恢复。
from src.schemas.ui.search import ProviderSearchInfo
from src.workflows.search.filtering import correct_movie_type_by_title, filter_by_season
from src.workflows.search.result_cache import read_search_results, write_search_results
from src.services.config_service import get_config_service
from src.services.cache_service import get_cache_service
from src.utils import parse_search_keyword

# 源优先级映射：与 compute_score 的 provider_order 契约同源，统一由 scoring 提供
from src.workflows.search.scoring import load_provider_order

from src.services.scraper_manager import ScraperManager
from src.services.metadata_service import MetadataService

logger = logging.getLogger(__name__)


class SearchCaller(str, Enum):
    """搜索调用途径标识。

    why: unified_search 被多处复用（WebUI 首页搜索、控制 API 自动导入、Webhook、AI 工具、
         定时任务），日志混在同一 logger 下无法区分来源。统一前缀便于按途径 grep 与排障。

    取值为 str 子类，故可直接传字符串（如 "webui_search"），也可传枚举成员。
    """

    WEBUI_SEARCH = "webui_search"          # WebUI 首页搜索
    CONTROL_AUTO_IMPORT = "control_auto_import"  # 控制 API 自动导入
    WEBHOOK = "webhook"                    # Webhook 触发导入
    AI_TOOL = "ai_tool"                    # AI 助手工具调用
    SCHEDULED_TASK = "scheduled_task"      # 定时任务
    UNKNOWN = "unknown"                    # 未标注来源

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志格式化
        return self.value


class _CallerLogAdapter(logging.LoggerAdapter):
    """为日志消息统一加上「调用途径」前缀的适配器。

    why: 避免在 unified_search 内部逐条日志手工拼接前缀，新增日志也自动带上途径标识。
    """

    def process(self, msg: str, kwargs: dict) -> tuple:
        return f"[{self.extra['caller']}] {msg}", kwargs


def _build_shared_search_cache_key(
    search_keywords: List[str], episode_info: Optional[dict],
    max_results_per_source: Optional[int], scraper_manager: "ScraperManager",
) -> str:
    """构造跨搜索入口复用的原始源结果缓存键。"""
    volatile_names = ("token", "password", "secret", "cookie", "api_key", "apikey")

    def stable_settings(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: stable_settings(item) for key, item in sorted(value.items())
                if not any(name in key.lower() for name in volatile_names)
            }
        if isinstance(value, (list, tuple)):
            return [stable_settings(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    payload = {
        "keywords": search_keywords,
        "episode_info": stable_settings(episode_info or {}),
        "max_results_per_source": max_results_per_source,
        "sources": stable_settings(scraper_manager.scraper_settings),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return f"shared_search_raw_v1_{digest}"



def _set_if_has(item: Any, field: str, value: Any) -> None:
    """仅当结果对象确实具备该字段时才赋值。

    why: unified_search 同时服务于 WebUI（ProviderSearchInfo，含 typeSuggestion 等展示字段）
         和 tasks 层（精简结果模型）。直接 setattr 会让 pydantic 精简模型抛错。
    """
    if hasattr(item, field):
        setattr(item, field, value)


def _is_cjk_dominant(text: str) -> bool:
    """检查文本是否主要由 CJK（中日韩）字符组成"""
    if not text:
        return False
    cjk_count = sum(
        1 for c in text
        if '\u4e00' <= c <= '\u9fff'    # CJK统一汉字
        or '\u3040' <= c <= '\u309f'    # 平假名
        or '\u30a0' <= c <= '\u30ff'    # 片假名
        or '\uac00' <= c <= '\ud7af'    # 韩文音节
    )
    return cjk_count > len(text) * 0.3


def _is_cross_language(text1: str, text2: str) -> bool:
    """检查两个文本是否跨语言（一个以CJK为主，另一个以非CJK为主）"""
    return _is_cjk_dominant(text1) != _is_cjk_dominant(text2)


async def unified_search(
    search_term: str,
    # 数据访问已由 DatabaseService 自持短事务，兼容参数允许不提供会话。
    session: Optional[AsyncSession],
    scraper_manager: "ScraperManager",
    metadata_manager: Optional["MetadataService"] = None,
    use_alias_expansion: bool = True,
    use_alias_filtering: bool = True,
    use_title_filtering: bool = True,
    use_source_priority_sorting: bool = True,
    custom_aliases: Optional[set] = None,
    max_results_per_source: Optional[int] = None,
    progress_callback: Optional[Callable] = None,
    episode_info: Optional[dict] = None,
    alias_similarity_threshold: int = 75,
    supplemental_results_out: Optional[list] = None,
    # ── 相似度阈值（原 strict_filtering 布尔双分支已改为参数化）──
    title_similarity_threshold: int = 85,
    title_similarity_strict_threshold: Optional[int] = None,
    strict_length_ratio: float = 0.3,
    # ── 从 API 层整合进来的能力开关 ──
    search_titles: Optional[List[str]] = None,
    enable_type_correction: bool = False,
    filter_season: Optional[int] = None,
    aux_title_type_map: Optional[Dict[str, str]] = None,
    aux_title_type_map_out: Optional[Dict[str, str]] = None,
    trust_metadata_aliases: bool = False,
    log_excluded: bool = False,
    search_user: Optional[Any] = None,
    caller: Union[SearchCaller, str] = SearchCaller.UNKNOWN,
    enable_title_type_correction: bool = False,
    shared_cache_key_out: Optional[Dict[str, str]] = None,
) -> List[Any]:
    """
    统一的搜索函数，供 tasks 编排层、API 层、AI 工具层复用。

    Args:
        search_term: 搜索关键词
        session: 数据库会话（保留以兼容既有调用方；内部 DB 访问统一走 DatabaseService）
        scraper_manager: Scraper管理器
        metadata_manager: 元数据源管理器（用于别名扩展）
        use_alias_expansion: 是否使用别名扩展
        use_alias_filtering: 是否使用别名过滤
        use_title_filtering: 是否使用标题过滤
        use_source_priority_sorting: 是否按源优先级排序
        custom_aliases: 自定义别名集合（如果提供，将与扩展的别名合并）
        max_results_per_source: 每个源最多返回的结果数量（None 时读配置）
        progress_callback: 进度回调函数
        episode_info: 集数信息，透传给各弹幕源
        alias_similarity_threshold: 别名验证相似度阈值
        supplemental_results_out: 补充源结果输出容器
        title_similarity_threshold: 标题过滤主阈值（标准模式为「严格大于」）
        title_similarity_strict_threshold: 传入数值即启用严格模式
            （相似度 >= 该值直接通过，或 >= 主阈值且长度差在 strict_length_ratio 约束内）
        strict_length_ratio: 严格模式下允许的标题长度差占别名长度的比例
        search_titles: 额外搜索标题（如季度映射名），与 search_term 合并去重后一起搜
        enable_type_correction: 是否执行媒体类型分级判定（标题关键词 + 元数据）
        filter_season: 指定季度时过滤结果（仅保留匹配该季度的电视剧）
        aux_title_type_map: 元数据源提供的「标题 → 类型」映射，用于类型判定
        aux_title_type_map_out: 输出容器；辅助源实时返回的「标题 → 类型」映射会
            写入其中，供调用方在搜索后复用（避免二次调用辅助源）
        trust_metadata_aliases: 信任元数据别名，跳过别名相似度验证
        log_excluded: 是否聚合打印被过滤掉的结果标题（便于排查漏召回）
        search_user: 辅助源搜索使用的用户身份；缺省时使用内置 system 用户
    caller: 调用途径标识（如 SearchCaller.WEBUI_SEARCH / "webui_search"），
        用于日志前缀区分来源，不影响搜索行为
        shared_cache_key_out: 可选输出容器，返回本次实际原始缓存键

    Returns:
        搜索结果列表
    """
    # 本函数内所有日志统一带上调用途径前缀，便于按来源排查
    log = _CallerLogAdapter(logger, {"caller": caller})

    # why: 别名验证与「信任元数据别名」互斥，后者优先（API 层依赖此行为）。
    if trust_metadata_aliases:
        use_alias_filtering = False

    db = get_database_service()
    # 与主页结果复用配置读取策略；未初始化时保持缓存可选能力的降级行为。
    try:
        shared_search_cache_ttl = await read_search_cache_ttl(get_config_service())
    except RuntimeError as exc:
        shared_search_cache_ttl = 10800
        log.warning("配置服务不可用，搜索缓存使用 %d 秒: %s", shared_search_cache_ttl, exc)


    if progress_callback is None:
        async def progress_callback(_progress: int, _message: str):
            pass

    # 1. 获取别名（如果启用）- 优化：并行执行别名获取和全网搜索
    filter_aliases = {search_term}  # 确保原始搜索词总是在列表中

    # 如果提供了自定义别名，先添加它们
    if custom_aliases:
        filter_aliases.update(custom_aliases)

    # 优化1: 并行执行别名获取和全网搜索
    alias_task = None
    search_task = None

    if use_alias_expansion and metadata_manager:
        await progress_callback(10, "获取别名...")

        # 提取核心标题（去除季度和集数信息）
        parsed = parse_search_keyword(search_term)
        core_title = parsed["title"]

        # 使用核心标题作为缓存键，这样同一剧的不同集数可以共享别名缓存
        alias_cache_key = f"search_aliases_{core_title}"
        cached_aliases = None
        # why: CacheService 混合模式内部已含 L1 内存 + L2 数据库回填，
        #      无需再手写 crud 数据库兜底（原兜底属重复实现）。
        _backend = get_cache_service()
        if _backend is not None:
            try:
                cached_aliases = await _backend.get(alias_cache_key, region="search")
            except Exception as e:
                log.warning(f"别名缓存读取失败（忽略，走实时获取）: {e}")

        if cached_aliases:
            try:
                _cached_payload = json.loads(cached_aliases)
                # 兼容两种缓存格式：新版 dict（含类型映射）与旧版纯别名 list
                if isinstance(_cached_payload, dict):
                    cached_alias_list = _cached_payload.get("aliases", [])
                    cached_aux_type_map = _cached_payload.get("aux_type_map") or {}
                else:
                    cached_alias_list = _cached_payload
                    cached_aux_type_map = {}

                # 使用 ensure_ascii=False 来正确显示中文
                aliases_display = json.dumps(cached_alias_list, ensure_ascii=False, separators=(',', ':'))
                log.info(f"从缓存中获取'{core_title}'的别名({len(cached_alias_list)}个): {aliases_display}")

                # 缓存命中时同样回传类型映射，避免类型精确修正在缓存期内静默失效
                if aux_title_type_map_out is not None and cached_aux_type_map:
                    aux_title_type_map_out.update(cached_aux_type_map)

                if use_alias_filtering:
                    # 验证缓存的别名相似度（使用核心标题进行比较）
                    for alias in cached_alias_list:
                        # 跨语言别名（如英文搜索词对应的中文别名）免验证直接保留
                        if _is_cross_language(core_title, alias):
                            filter_aliases.add(alias)
                            continue
                        similarity = fuzz.token_set_ratio(core_title, alias)
                        if similarity >= alias_similarity_threshold:
                            filter_aliases.add(alias)
                else:
                    filter_aliases.update(cached_alias_list)
            except Exception as e:
                log.warning(f"解析缓存别名失败: {e}")
        else:
            # 创建别名获取任务（不等待）
            async def get_aliases() -> set[str]:
                """获取并缓存别名，类型映射为空时仍保存缓存。"""
                try:
                    # 优先使用调用方身份，缺省回退内置 system 用户
                    user = search_user or User(id=0, username="system")
                    # 使用核心标题获取别名
                    all_possible_aliases, supp_results, aux_map, _ = await search_supplemental_sources(metadata_manager, core_title, user)

                    # 将补充结果输出到调用方（如果提供了输出参数）
                    if supplemental_results_out is not None and supp_results:
                        supplemental_results_out.extend(supp_results)

                    # why: 辅助源的「标题 → 类型」映射对调用方做类型精确修正有用，
                    #      通过出参回传可避免调用方二次调用辅助源。
                    if aux_title_type_map_out is not None and aux_map:
                        aux_title_type_map_out.update(aux_map)

                    # 类型映射为空时也要缓存别名，避免后续搜索重复访问辅助源。
                    alias_data = json.dumps({
                        "aliases": list(all_possible_aliases),
                        "aux_type_map": aux_map or {},
                    })
                    _backend = get_cache_service()
                    if _backend is not None:
                        try:
                            await _backend.set(alias_cache_key, alias_data, ttl=3600, region="search")
                            log.info(f"已缓存'{core_title}'的别名: {len(all_possible_aliases)}个")
                        except Exception as e:
                            log.warning(f"别名缓存写入失败（忽略）: {e}")

                    return all_possible_aliases
                except Exception as e:
                    log.warning(f"获取别名失败: {e}")
                    return set()

            alias_task = asyncio.create_task(get_aliases())

    # 2. 执行全网搜索（并行）
    await progress_callback(20, "执行全网搜索...")

    # 如果没有指定max_results_per_source，从配置中读取
    if max_results_per_source is None:
        async with db.transaction():
            config_value = await db.config.get_config_value('searchMaxResultsPerSource', '30')
        try:
            max_results_per_source = int(config_value)
        except (TypeError, ValueError):
            max_results_per_source = 30
            log.warning(f"无效的searchMaxResultsPerSource配置值: {config_value}，使用默认值30")

    # ?? bangumi-data 别名增强 - 优化：与配置预加载并行执行
    # 别名增强：把离线库命中的全语言译名（尤其繁中）也加入搜索关键词，解决「官方主名 vs 平台译名」不一致。
    # 注：id 直链补充源已迁移到 bangumi 元数据源的 supplement_search 模板（仅弹幕源空结果时兜底，全入口统一）。

    async def get_bangumi_aliases():
        """并行获取 bangumi-data 别名"""
        try:
            if metadata_manager is not None and await metadata_manager.is_offline_bangumi_enabled():
                core_title = parse_search_keyword(search_term)["title"]
                return await metadata_manager.get_offline_search_aliases(core_title, limit=3)
        except Exception as e:
            log.warning(f"bangumi-data 别名增强失败（忽略）: {type(e).__name__}: {e}")
        return []

    # 并行启动 bangumi 别名获取任务
    bangumi_task = asyncio.create_task(get_bangumi_aliases())

    # 等待 bangumi 别名完成并构建搜索关键词
    bgm_aliases = await bangumi_task
    search_keywords = [search_term]
    seen_kw = {search_term.replace(" ", "")}

    # 调用方额外指定的搜索标题（如 AI 季度映射得到的别名），优先级高于 bangumi 译名
    for extra in (search_titles or []):
        k = (extra or "").replace(" ", "")
        if k and k not in seen_kw:
            seen_kw.add(k)
            search_keywords.append(extra)

    if bgm_aliases:
        for a in bgm_aliases:
            k = a.replace(" ", "")
            if k and k not in seen_kw:
                seen_kw.add(k)
                search_keywords.append(a)
    if len(search_keywords) > 1:
        log.info(f"搜索词增强: '{search_term}' 追加 {len(search_keywords)-1} 个别名/译名搜索词")

    shared_cache_key = _build_shared_search_cache_key(
        search_keywords, episode_info, max_results_per_source, scraper_manager
    )
    if shared_cache_key_out is not None:
        shared_cache_key_out["key"] = shared_cache_key

    async def perform_search():
        """优先读取通用原始结果缓存，未命中时才访问各弹幕源。"""
        try:
            cached = await read_search_results(shared_cache_key, region="search")
            if cached is not None:
                cached_items = cached.get("results", cached) if isinstance(cached, dict) else cached
                if isinstance(cached_items, list):
                    # 本轮未执行源站搜索，避免报告沿用上一轮的源耗时。
                    scraper_manager.last_search_timing = []
                    log.info("命中通用原始搜索缓存: %s（%d 条）", shared_cache_key, len(cached_items))
                    return [
                        item if isinstance(item, ProviderSearchInfo) else ProviderSearchInfo(**item)
                        for item in cached_items
                    ]
        except Exception as exc:
            log.warning("读取通用原始搜索缓存失败，将访问源站: %s", exc)

        results = await search_all(
            scraper_manager, search_keywords,
            episode_info=episode_info,
            max_results_per_source=max_results_per_source,
            metadata_manager=metadata_manager,
        )
        if results:
            try:
                await write_search_results(
                    shared_cache_key,
                    [item.model_dump() for item in results],
                    ttl=shared_search_cache_ttl,
                    region="search",
                )
            except Exception as exc:
                log.warning("写入通用原始搜索缓存失败: %s", exc)
        return results

    search_task = asyncio.create_task(perform_search())

    # 等待别名和搜索任务完成
    if alias_task:
        all_possible_aliases, all_results = await asyncio.gather(alias_task, search_task)

        # 验证别名相似度（使用核心标题进行比较，与获取别名时保持一致）
        if use_alias_filtering:
            validated_aliases = set()
            for alias in all_possible_aliases:
                # 跨语言别名（如英文搜索词对应的中文别名）免验证直接保留
                if _is_cross_language(core_title, alias):
                    validated_aliases.add(alias)
                    continue
                similarity = fuzz.token_set_ratio(core_title, alias)
                if similarity >= alias_similarity_threshold:  # 相似度阈值
                    validated_aliases.add(alias)
                else:
                    log.debug(f"别名验证：已丢弃低相似度的别名 '{alias}' (与 '{core_title}' 相比，相似度={similarity})")
            filter_aliases.update(validated_aliases)
        else:
            filter_aliases.update(all_possible_aliases)

        log.info(f"用于过滤的别名列表: {list(filter_aliases)}")
    else:
        all_results = await search_task

    await progress_callback(40, "搜索完成...")
    log.info(f"直接搜索完成，找到 {len(all_results)} 个原始结果。")

    # 3. 使用标题过滤（如果启用）
    filtered_results = all_results
    if use_title_filtering:  # 移除别名数量限制,即使只有原始搜索词也要过滤
        await progress_callback(60, "过滤搜索结果...")

        def normalize_for_filtering(title: str) -> str:
            if not title: return ""
            # why: 只剥掉括号符号，保留括号内的内容。
            # 原先的 re.sub(r'[\[【(（].*?[\]】)）]', ...) 会把 "【我推的孩子】 第二季"
            # 处理成 "第二季"——这是无意义的通用词，fuzz.partial_ratio 会让所有含"第二季"
            # 的搜索结果（如"蜡笔小新 第二季"）都通过过滤，产生大量误包含。
            # 正确做法是只移除括号符号本身，保留内容：
            #   "【我推的孩子】 第二季" → "我推的孩子 第二季"
            #   "【我推的孩子】"       → "我推的孩子"
            #   "[1080P][剧名]"       → "1080P剧名"（含质量标识也不影响匹配精度）
            cleaned = re.sub(r'[【】\[\]（）()]', '', title).strip()
            return cleaned.lower().replace(" ", "").replace("：", ":").strip()

        normalized_filter_aliases = {normalize_for_filtering(alias) for alias in filter_aliases if alias}
        filtered_results = []

        # 优化：创建相似度缓存字典
        similarity_cache = {}
        cache_hits = 0
        cache_misses = 0
        skipped_by_length = 0
        skipped_by_chars = 0

        excluded_results: List[Any] = []
        # why: 原「严格/标准」双分支约 80 行逻辑几乎完全相同，仅命中判定不同，
        #      已合并为单分支 + 参数化阈值（KISS/YAGNI）：
        #        · title_similarity_strict_threshold 为 None → 标准模式：similarity > 主阈值
        #        · 传入数值 → 严格模式：similarity >= 严格阈值，
        #          或 similarity >= 主阈值且标题长度差在 strict_length_ratio 约束内
        _strict = title_similarity_strict_threshold is not None

        for item in all_results:
            normalized_item_title = normalize_for_filtering(item.title)
            if not normalized_item_title:
                continue

            is_relevant = False
            for alias in normalized_filter_aliases:
                # 优化1: 快速预过滤 - 长度差异过大直接跳过
                length_diff = abs(len(normalized_item_title) - len(alias))
                max_allowed_diff = max(len(alias), 20)
                if length_diff > max_allowed_diff:
                    skipped_by_length += 1
                    continue

                # 优化2: 快速预过滤 - 没有共同字符直接跳过（跨语言时免检）
                if not _is_cross_language(normalized_item_title, alias):
                    if not set(normalized_item_title) & set(alias):
                        skipped_by_chars += 1
                        continue

                # 优化3: 使用缓存避免重复计算
                cache_key = (normalized_item_title, alias)
                if cache_key in similarity_cache:
                    similarity = similarity_cache[cache_key]
                    cache_hits += 1
                else:
                    similarity = fuzz.partial_ratio(normalized_item_title, alias)
                    similarity_cache[cache_key] = similarity
                    cache_misses += 1

                if _strict:
                    # 完全匹配或非常高的相似度直接通过
                    if similarity >= title_similarity_strict_threshold:
                        is_relevant = True
                        break
                    # 高相似度但标题长度差异不大
                    if (similarity >= title_similarity_threshold
                            and length_diff <= max(len(alias) * strict_length_ratio, 10)):
                        is_relevant = True
                        break
                elif similarity > title_similarity_threshold:
                    is_relevant = True
                    break

            if is_relevant:
                filtered_results.append(item)
            elif log_excluded:
                excluded_results.append(item)

        # 输出优化统计信息
        total_comparisons = cache_hits + cache_misses
        if total_comparisons > 0:
            cache_hit_rate = (cache_hits / total_comparisons) * 100
            log.info(f"相似度计算优化统计: 总计算={total_comparisons}, 缓存命中={cache_hits}({cache_hit_rate:.1f}%), "
                     f"长度跳过={skipped_by_length}, 字符跳过={skipped_by_chars}")

        if log_excluded:
            # 聚合打印保留/过滤明细，便于排查漏召回（原 API 层能力）
            filter_log_lines = [f"别名过滤结果 (保留 {len(filtered_results)}/{len(all_results)}):"]
            filter_log_lines.extend(f"  - 已过滤: {item.title}" for item in excluded_results)
            filter_log_lines.extend(f"  - {item.title}" for item in filtered_results)
            log.info("\n".join(filter_log_lines))
        else:
            log.info(f"别名过滤: 从 {len(all_results)} 个原始结果中，保留了 {len(filtered_results)} 个相关结果。")

    # 4. 媒体类型分级判定（可选）
    # why: 保留来源原值，标题强关键词与精确元数据可自动修正，模糊命中只提示确认，
    #      避免剧场版/相似作品被误判。原实现在 API 层，现下沉供所有入口复用。
    if enable_type_correction:
        valid_types = {'tv_series', 'movie'}
        usable_type_map = {
            title: media_type
            for title, media_type in (aux_title_type_map or {}).items()
            if media_type in valid_types
        }
        type_corrected = 0
        type_uncertain = 0

        # 标题强关键词修正：就地把「电视剧类型但标题含电影关键词」改为 movie
        titles_before = {id(item): item.type for item in filtered_results}
        correct_movie_type_by_title(filtered_results, log_prefix="unified_search:")

        for item in filtered_results:
            source_type = 'tv_series' if item.type == 'tv' else item.type
            item.type = source_type
            _set_if_has(item, 'sourceType', source_type)

            # 标题关键词已改写过类型，则记录为已修正
            if titles_before.get(id(item)) != item.type:
                _set_if_has(item, 'typeSuggestion', 'movie')
                _set_if_has(item, 'typeDecision', 'corrected')
                _set_if_has(item, 'typeDecisionReason', 'title_keyword')
                type_corrected += 1
                # why：电影强关键词已给出明确结论，不再被相似标题的低置信元数据降级。
                continue

            exact_type = usable_type_map.get(item.title)
            if exact_type:
                if exact_type != item.type or source_type not in valid_types:
                    item.type = exact_type
                    _set_if_has(item, 'typeSuggestion', exact_type)
                    _set_if_has(item, 'typeDecision', 'corrected')
                    _set_if_has(item, 'typeDecisionReason', 'metadata_exact_title')
                    type_corrected += 1
                continue

            # 非精确标题只提供建议，不自动覆盖，避免相似作品或剧场版误匹配。
            best_title = None
            best_score = 0
            for candidate_title in usable_type_map:
                score = fuzz.token_set_ratio(item.title, candidate_title)
                if score > best_score:
                    best_title, best_score = candidate_title, score
            if best_title and best_score >= 85:
                suggested_type = usable_type_map[best_title]
                if suggested_type != item.type and item.type in valid_types:
                    _set_if_has(item, 'typeSuggestion', suggested_type)
                    _set_if_has(item, 'typeDecision', 'needs_confirmation')
                    _set_if_has(item, 'typeDecisionReason', f'metadata_similar_title:{best_score}')
                    type_uncertain += 1
                elif item.type not in valid_types:
                    item.type = suggested_type
                    _set_if_has(item, 'typeSuggestion', suggested_type)
                    _set_if_has(item, 'typeDecision', 'corrected')
                    _set_if_has(item, 'typeDecisionReason', f'metadata_similar_title:{best_score}')
                    type_corrected += 1

        if type_corrected or type_uncertain:
            log.info(f"媒体类型分级判定: 自动修正 {type_corrected} 个，需确认 {type_uncertain} 个")

    # 控制搜索只需标题强关键词修正；必须在季度过滤前执行，不能误留电影。
    if enable_title_type_correction and not enable_type_correction:
        correct_movie_type_by_title(filtered_results, log_prefix="unified_search:")

    # 5. 季度过滤（可选）
    if filter_season:
        original_count = len(filtered_results)
        # 类型分级模式保留待确认的电视剧候选，避免主页迁移后提前丢失可选项。
        if enable_type_correction:
            filtered_results = [
                item for item in filtered_results
                if item.season == filter_season and (
                    item.type == "tv_series"
                    or (getattr(item, "typeDecision", None) == "needs_confirmation"
                        and getattr(item, "typeSuggestion", None) == "tv_series")
                )
            ]
        else:
            filtered_results, _season_excluded = filter_by_season(filtered_results, filter_season)
        log.info(
            f"根据指定的季度 ({filter_season}) 进行过滤，从 {original_count} 个结果中保留了 {len(filtered_results)} 个。"
        )

    # 6. 排序
    await progress_callback(70, "排序搜索结果...")

    if use_source_priority_sorting:
        # 按源优先级和相似度排序（provider_order 推导收口到 scoring.load_provider_order）
        source_order_map = await load_provider_order()

        def sort_key(item):
            provider_order = source_order_map.get(item.provider, 999)
            similarity_score = fuzz.token_set_ratio(search_term, item.title)
            return (provider_order, -similarity_score)

        sorted_results = sorted(filtered_results, key=sort_key)
    else:
        # 仅按相似度排序
        sorted_results = sorted(
            filtered_results,
            key=lambda x: fuzz.token_set_ratio(search_term, x.title),
            reverse=True
        )

    return sorted_results



async def search_all(scraper_manager: ScraperManager, keywords: List[str], episode_info: Optional[Dict[str, Any]] = None, max_results_per_source: Optional[int] = None, *, metadata_manager: Optional[MetadataService] = None) -> List[ProviderSearchInfo]:
    """
    在所有已启用的搜索源上并发搜索关键词列表。

    Args:
        keywords: 搜索关键词列表
        episode_info: 分集信息
        max_results_per_source: 每个源最多返回的结果数量（None表示不限制）
    """
    if metadata_manager is None:
        try:
            metadata_manager = get_metadata_service()
        except RuntimeError:
            metadata_manager = None
    enabled_scrapers = [
        scraper for name, scraper in scraper_manager.scrapers.items()
        if scraper_manager.scraper_settings.get(name, {}).get('isEnabled')
    ]

    if not enabled_scrapers:
        scraper_manager.last_search_timing = []
        scraper_manager.last_search_errors = []
        return []

    scraper_manager.last_search_errors = []
    # 包装搜索任务，从 @track_performance 装饰器存储的 _task_timings 中读取耗时
    # 使用缓冲 logger 避免并发搜索日志交叉

    # 预加载所有启用源的超时配置并注入到 scraper 实例
    timeout_tasks = {
        scraper.provider_name: scraper_manager.config_service.get(
            f"scraper_{scraper.provider_name}_search_timeout", "30"
        )
        for scraper in enabled_scrapers
    }
    timeout_raw = await asyncio.gather(*timeout_tasks.values())
    for scraper in enabled_scrapers:
        raw_val = timeout_raw[list(timeout_tasks.keys()).index(scraper.provider_name)]
        try:
            scraper._search_timeout = max(5.0, min(100.0, float(raw_val)))
        except (ValueError, TypeError):
            scraper._search_timeout = 30.0

    async def timed_search(scraper, keyword):
        task_id = id(asyncio.current_task())  # 获取当前任务ID

        # 安装缓冲 logger，替换 scraper.logger
        original_logger = scraper.logger
        temp_logger, buffer_handler = create_buffered_logger(scraper.provider_name, task_id)
        scraper.logger = temp_logger

        # 单源总搜索超时熔断：「搜索超时」配置语义为单个源的整体搜索时长上限，
        # 而非单次 HTTP 请求超时。源内部可能并行多请求/降级/限流，任一源卡住
        # 都会拖垮 gather 等待所有源完成，故在此用 wait_for 按配置值强制熔断。
        source_total_timeout = getattr(scraper, "_search_timeout", 30.0) or 30.0
        source_started = time.monotonic()
        source_task = None
        try:
            source_task = asyncio.create_task(scraper.search(keyword, episode_info=episode_info))
            result = await asyncio.wait_for(source_task, timeout=source_total_timeout)
            # 装饰器运行于源搜索子任务，不是 timed_search 外层任务；缺失时用实际耗时兜底。
            duration_ms = scraper._task_timings.pop(id(source_task), 0) if hasattr(scraper, '_task_timings') else 0
            duration_ms = duration_ms or (time.monotonic() - source_started) * 1000
            return (scraper.provider_name, result, duration_ms, None, buffer_handler)
        except asyncio.TimeoutError:
            # 源整体搜索超时：熔断该源，返回空结果，不拖垮其它源
            duration_ms = scraper._task_timings.pop(id(source_task), 0) if hasattr(scraper, '_task_timings') else 0
            duration_ms = duration_ms or (time.monotonic() - source_started) * 1000
            scraper.logger.warning(
                f"{scraper.provider_name}: 搜索超过单源总超时 {source_total_timeout:.0f}s，已熔断跳过"
            )
            return (scraper.provider_name, None, duration_ms,
                    TimeoutError(f"单源搜索超时 ({source_total_timeout:.0f}s)"), buffer_handler)
        except Exception as e:
            duration_ms = scraper._task_timings.pop(id(source_task), 0) if hasattr(scraper, '_task_timings') else 0
            duration_ms = duration_ms or (time.monotonic() - source_started) * 1000
            return (scraper.provider_name, None, duration_ms, e, buffer_handler)
        finally:
            # 恢复原始 logger
            scraper.logger = original_logger

    # 分发策略：每个源自行决定要搜哪些关键词（BaseScraper 默认只用主搜索词 keywords[0]，
    # gamer 等源覆写 select_search_keywords 按语言挑别名），不再「全量别名 × 全部源」笛卡尔积。
    # 各阶段从同一启动时刻扣减预算，单源既有超时配置不变。
    search_started = time.monotonic()
    supplement_deadline = search_started + 30.0
    provider_deadlines = {
        scraper.provider_name: search_started + scraper._search_timeout
        for scraper in enabled_scrapers
    }
    tasks = []
    for scraper in enabled_scrapers:
        try:
            scraper_keywords = scraper.select_search_keywords(keywords)
        except Exception:
            # 挑词异常不影响搜索：回退主搜索词
            scraper_keywords = [keywords[0]] if keywords else []
        for keyword in scraper_keywords:
            tasks.append(timed_search(scraper, keyword))

    # 并行启动补充源搜索（乐观策略：先搜所有可映射平台，后续再过滤）
    supplement_task = None
    if metadata_manager:
        all_possible_empty = {
            name for name in scraper_manager.scrapers if name != 'custom'
        }
        if all_possible_empty:
            primary_keyword = keywords[0] if keywords else ""

            async def _run_supplement() -> Tuple[List[Any], float]:
                """并行启动元数据补充，耗时与总等待预算使用同一单调时钟。"""
                _start = time.monotonic()
                results = await asyncio.wait_for(
                    supplement_empty_search_results(metadata_manager, primary_keyword, all_possible_empty),
                    timeout=max(0.0, supplement_deadline - time.monotonic()),
                )
                _dur = (time.monotonic() - _start) * 1000
                return results, _dur

            supplement_task = asyncio.create_task(_run_supplement())

    # 预加载全局过滤配置（与弹幕源搜索并行，避免搜索完成后串行读取）
    async def _preload_filter_config():
        cn = await scraper_manager.config_service.get("search_result_global_blacklist_cn", "")
        eng = await scraper_manager.config_service.get("search_result_global_blacklist_eng", "")
        return cn, eng

    filter_config_task = asyncio.create_task(_preload_filter_config())

    try:
        timed_results = await asyncio.gather(*tasks)
    except BaseException:
        for pending in (supplement_task, filter_config_task):
            if pending is not None and not pending.done():
                pending.cancel()
        await asyncio.gather(*[task for task in (supplement_task, filter_config_task) if task is not None],
                             return_exceptions=True)
        raise

    # 聚合每个源的耗时和结果数（同一个源可能搜索多个关键词）
    provider_timing: Dict[str, Tuple[float, int]] = {}  # {provider: (max_duration, total_count)}
    # 收集每个源的缓冲日志，按完成顺序记录
    provider_buffers: Dict[str, List[Tuple[BufferedLogHandler, int, float, Exception]]] = {}

    all_results = []
    seen_results = set()

    for provider_name, result, duration_ms, error, buffer_handler in timed_results:
        result_count = 0
        if error:
            scraper_manager.last_search_errors.append(f"{provider_name}: {error}")
            # 记录失败的耗时
            if provider_name not in provider_timing:
                provider_timing[provider_name] = (duration_ms, 0)
            else:
                old_dur, old_cnt = provider_timing[provider_name]
                provider_timing[provider_name] = (max(old_dur, duration_ms), old_cnt)
        elif result:
            # 优化5: 限制每个源的结果数量
            limited_result = result[:max_results_per_source] if max_results_per_source else result
            result_count = len(limited_result)

            # 更新耗时统计
            if provider_name not in provider_timing:
                provider_timing[provider_name] = (duration_ms, result_count)
            else:
                old_dur, old_cnt = provider_timing[provider_name]
                provider_timing[provider_name] = (max(old_dur, duration_ms), old_cnt + result_count)

            for item in limited_result:
                unique_id = (item.provider, item.mediaId)
                if unique_id not in seen_results:
                    all_results.append(item)
                    seen_results.add(unique_id)
        else:
            # 空结果
            if provider_name not in provider_timing:
                provider_timing[provider_name] = (duration_ms, 0)
            else:
                old_dur, old_cnt = provider_timing[provider_name]
                provider_timing[provider_name] = (max(old_dur, duration_ms), old_cnt)

        # 收集缓冲日志
        if provider_name not in provider_buffers:
            provider_buffers[provider_name] = []
        provider_buffers[provider_name].append((buffer_handler, result_count, duration_ms, error))

    # 按源分组输出缓冲的日志（消除交叉）- 使用 create_task 异步执行，不阻塞事件循环
    mgr_logger = logging.getLogger(__name__)

    def _do_flush_logs():
        """在线程池中执行日志 flush，避免占用事件循环。
        why：flush_buffered_logs 只是纯 Python 日志写入（无 I/O 等待），
        用 run_in_executor 推给线程池，主协程可以并行继续处理补充源、过滤等逻辑，
        最后在 return 前 await 确保日志块在计时报告之前全部输出完毕，
        同时不阻塞事件循环。
        """
        for pn, buffers in provider_buffers.items():
            total_count = provider_timing.get(pn, (0, 0))[1]
            total_dur = provider_timing.get(pn, (0, 0))[0]
            first_error = next((e for _, _, _, e in buffers if e), None)
            merged_handler = BufferedLogHandler()
            for bh, _, _, _ in buffers:
                merged_handler._records.extend(bh.records)
                bh.clear()
            flush_buffered_logs(mgr_logger, pn, merged_handler, total_count, total_dur, first_error)

    # 提交到线程池并行执行，主协程继续处理补充源/过滤等逻辑
    flush_task = asyncio.get_event_loop().run_in_executor(None, _do_flush_logs)

    # 保存耗时信息供计时报告使用
    scraper_manager.last_search_timing = [
        (name, dur, cnt) for name, (dur, cnt) in sorted(provider_timing.items(), key=lambda x: -x[1][0])
    ]

    # 收集补充源结果（已在弹幕源搜索开始时并行启动，现在 await 获取结果）
    try:
        if supplement_task:
            # 补充源只负责改善空结果；主搜索已完成时不能因补充源卡住而阻塞 API 响应。
            try:
                if supplement_task.done():
                    supplement_results, _supp_dur = supplement_task.result()
                else:
                    supplement_results, _supp_dur = await asyncio.wait_for(
                        supplement_task, timeout=max(0.0, supplement_deadline - time.monotonic()),
                    )
            except asyncio.TimeoutError:
                supplement_task.cancel()
                await asyncio.gather(supplement_task, return_exceptions=True)
                mgr_logger.warning("元数据补充搜索超过30秒，使用当前已收集的搜索结果")
                scraper_manager.last_search_errors.append("元数据补充搜索超过30秒，已使用当前已收集的结果")
                supplement_results = []

            # 完全无结果的弹幕源（含被禁用的），这些源的补充项无条件合并（零结果兜底）
            empty_providers = {
                name for name, (_, cnt) in provider_timing.items()
                if cnt == 0 and name != 'custom'
            }
            disabled_providers = {
                name for name in scraper_manager.scrapers
                if not scraper_manager.scraper_settings.get(name, {}).get('isEnabled')
                and name != 'custom'
            }
            empty_providers |= disabled_providers

            # why(方案1-结果增补)：不再只对空结果源补充。对"非空"源，也允许把
            # 该源自身没搜到的条目（如综艺往季）合并进来，用「标题归一化+年份」防止
            # 与自身已有结果重复。被禁用/零结果的源仍按原逻辑无条件兜底。
            def _norm_title(t: str) -> str:
                # 归一化标题用于跨源去重：去除空白与常见分隔符，转小写
                if not t:
                    return ""
                return re.sub(r'[\s:：·\-—_、,，.。]+', '', str(t)).lower()

            # 建立"每个 provider 已存在结果"的标题+年份索引，用于识别补充项是否重复
            existing_title_year: set = set()
            for r in all_results:
                existing_title_year.add((r.provider, _norm_title(r.title), r.year))

            # 去重并合并
            added_count = 0
            supplemented_providers = set()
            merged_supp = []  # 实际参与合并的补充项（用于日志）
            for supp_item in supplement_results:
                prov = supp_item.provider
                is_empty_provider = prov in empty_providers
                unique_id = (prov, supp_item.mediaId)
                title_year_key = (prov, _norm_title(supp_item.title), supp_item.year)

                if unique_id in seen_results:
                    continue  # mediaId 完全重复，跳过
                if not is_empty_provider and title_year_key in existing_title_year:
                    continue  # 非空源：该季已被自身结果覆盖，避免重复

                all_results.append(supp_item)
                seen_results.add(unique_id)
                existing_title_year.add(title_year_key)
                added_count += 1
                supplemented_providers.add(prov)
                merged_supp.append(supp_item)
            # 日志只展示实际合并进结果的补充项
            filtered_supp = merged_supp

            # 使用框框格式输出日志
            _lines = ["-", f"┌─── 搜索补充源 ({added_count}个补充, {_supp_dur:.0f}ms) ───"]
            _lines.append(f"  无结果的弹幕源: {sorted(empty_providers)}")
            if filtered_supp:
                for supp_item in filtered_supp:
                    _lines.append(f"  + [{supp_item.provider}] {supp_item.title}")
            else:
                _lines.append(f"  (未获得任何补充结果)")
            _lines.append(f"└─── 搜索补充源 ───")
            mgr_logger.info("\n".join(_lines))

            # 将补充源各项耗时追加到计时报告
            if hasattr(metadata_manager, 'last_supplement_timing') and metadata_manager.last_supplement_timing:
                for s_name, s_dur, s_cnt in metadata_manager.last_supplement_timing:
                    scraper_manager.last_search_timing.append((f"补充:{s_name}", s_dur, s_cnt))
            else:
                scraper_manager.last_search_timing.append(("搜索补充源", _supp_dur, added_count))
    except asyncio.CancelledError:
        pending_tasks = [task for task in (supplement_task, filter_config_task) if task is not None]
        for task in pending_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*pending_tasks, return_exceptions=True)
        raise
    except Exception as e:
        mgr_logger.warning(f"搜索补充源调用失败: {e}", exc_info=True)

    # 使用预加载的全局过滤配置（已与弹幕源并行加载完成）
    cn_pattern_str, eng_pattern_str = await filter_config_task

    # ========== 通用信息增强逻辑（补全缺失字段：年份、集数等）==========
    # 在应用全局过滤前补全，保证过滤时信息完整
    global_enrich_enabled = (await scraper_manager.config_service.get("searchEnrichResults", "false")).lower() == "true"
    if global_enrich_enabled and all_results:
        enrich_limit = int(await scraper_manager.config_service.get("searchEnrichLimit", "10"))
        enrich_timeout = float(await scraper_manager.config_service.get("searchEnrichTimeout", "5"))
        enrich_fields_str = await scraper_manager.config_service.get("searchEnrichFields", "year,episodeCount")
        global_fields = [f.strip() for f in enrich_fields_str.split(",") if f.strip()]

        if not global_fields:
            mgr_logger.debug("[信息增强] 全局字段配置为空，跳过增强")
        else:
            # 按源分组
            results_by_provider = defaultdict(list)
            for item in all_results:
                results_by_provider[item.provider].append(item)

            mgr_logger.info(f"[信息增强] 全局开关已开启，待增强字段: {', '.join(global_fields)}")

            # 并行处理各源（每源独立超时）
            async def enrich_provider(provider: str, results: list) -> None:
                """仅在该源整体搜索预算剩余时间内补齐字段。"""
                deadline = provider_deadlines.get(provider, search_started + 30.0)
                if deadline <= time.monotonic():
                    return
                # 读取该源的开关
                source_enabled = (await scraper_manager.config_service.get(f"scraper_{provider}_enrich_enabled", "false")).lower() == "true"
                if not source_enabled:
                    mgr_logger.debug(f"[信息增强] 源 {provider} 未开启增强，跳过")
                    return

                # 改为读取源类硬编码的 enrich_fields（不再继承全局配置）
                scraper = scraper_manager.scrapers.get(provider)
                fields = getattr(scraper, 'enrich_fields', []) if scraper else []

                if not fields:
                    mgr_logger.debug(f"[信息增强] 源 {provider} 未配置 enrich_fields，跳过")
                    return

                # 只处理前 N 条
                to_enrich = results[:enrich_limit]

                # 筛选出需要增强的结果（至少有一个字段缺失）
                need_enrich = []
                for result in to_enrich:
                    missing = []
                    for f in fields:
                        if f == "episodeCount" and result.episodeCount is None:
                            missing.append(f)
                        elif f == "year" and not result.year:
                            missing.append(f)
                        # 未来扩展：type, season 等
                    if missing:
                        need_enrich.append((result, missing))

                if not need_enrich:
                    mgr_logger.debug(f"[信息增强] 源 {provider} 无需增强的结果，跳过")
                    return

                mgr_logger.info(f"[信息增强] 源 {provider} 开始增强前 {len(need_enrich)} 条结果")

                # 获取 scraper 实例
                scraper = scraper_manager.scrapers.get(provider)
                if not scraper:
                    mgr_logger.warning(f"[信息增强] 源 {provider} 实例未找到，跳过")
                    return

                # 逐条增强（每条独立超时）
                for result, missing_fields in need_enrich:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        mgr_logger.debug("[信息增强] %s 整体预算已耗尽，保留已有结果", provider)
                        break
                    try:
                        await asyncio.wait_for(
                            scraper.enrich_result(result, missing_fields),
                            timeout=min(enrich_timeout, remaining)
                        )
                    except asyncio.TimeoutError:
                        mgr_logger.warning(f"[信息增强] {provider}/{result.mediaId} 超时({enrich_timeout}s)")
                    except Exception as e:
                        mgr_logger.warning(f"[信息增强] {provider}/{result.mediaId} 失败: {e}")

            async def bounded_enrich_provider(provider: str, results: list) -> None:
                """将整段增强约束于同一剩余预算，包括配置与每条请求。"""
                remaining = provider_deadlines.get(provider, search_started + 30.0) - time.monotonic()
                if remaining <= 0:
                    return
                try:
                    await asyncio.wait_for(enrich_provider(provider, results), timeout=remaining)
                except asyncio.TimeoutError:
                    mgr_logger.debug("[信息增强] %s 整体预算耗尽，保留已有结果", provider)

            # 各源仍并行，但不为增强阶段重新启动完整搜索超时。
            await asyncio.gather(*[bounded_enrich_provider(p, items) for p, items in results_by_provider.items()], return_exceptions=True)

            mgr_logger.info(f"[信息增强] 完成，共处理 {len(all_results)} 条结果")
    # ========== 信息增强逻辑结束 ==========

    cn_pattern = re.compile(cn_pattern_str, re.IGNORECASE) if cn_pattern_str else None
    eng_pattern = re.compile(r'(\[|\【|\b)(' + eng_pattern_str + r')(\d{1,2})?(\s|_ALL)?(\]|\】|\b)', re.IGNORECASE) if eng_pattern_str else None

    # 无黑名单时全部保留，但仍完成日志收尾与托管健康统计。
    filtered_results = []
    for item in all_results:
        is_junk = False
        if cn_pattern and cn_pattern.search(item.title):
            is_junk = True
        if not is_junk and eng_pattern and eng_pattern.search(item.title):
            is_junk = True

        if not is_junk:
            filtered_results.append(item)

    logging.getLogger(__name__).info(f"全局标题过滤: 从 {len(all_results)} 个结果中保留了 {len(filtered_results)} 个。")

    # 确保各源日志块在返回结果（进而触发计时报告）之前全部输出完毕
    # why：flush_task 在线程池里并行执行，此处 await 不阻塞事件循环，
    # 仅等待线程写完日志，保证日志顺序：各源日志块 → 补充源日志 → 计时报告
    await flush_task

    # 异步更新弹幕源健康度统计
    try:
        async def update_health(_session: Any, progress_callback: Any) -> None:
            """管理任务只经服务事务写统计，不使用任务附带的原始会话。"""
            await progress_callback(5, "正在更新搜索源健康统计")
            if not await scraper_manager._update_health_stats(timed_results):
                raise TaskFailed("搜索源健康统计更新失败")
            raise TaskSuccess("搜索源健康统计更新完成")

        await get_task_manager().submit_task(
            update_health, f"更新搜索源健康统计 {id(timed_results)}",
            task_type="search_health_update", queue_type="management",
        )
    except Exception as exc:
        mgr_logger.debug("健康统计任务提交失败，不影响搜索结果: %s", exc)


    return filtered_results



async def search_with_failover(
    scraper_manager: ScraperManager, metadata_manager: Optional[MetadataService],
    provider: str, keyword: str, episode_info: Optional[Dict[str, Any]] = None,
) -> List[ProviderSearchInfo]:
    """先查询指定弹幕源，空结果再使用元数据来源的官方链接补充。"""
    results = await scraper_manager.search(provider, keyword, episode_info)
    if results or metadata_manager is None:
        return results
    try:
        return await supplement_search_result(metadata_manager, provider, keyword, episode_info)
    except Exception as exc:
        logger.error("搜索故障转移失败: %s", exc, exc_info=True)
        return results


async def get_failover_comments(metadata_manager: MetadataService, title: str, season: int, episode_index: int, user: User) -> Optional[List[dict]]:
    """
    Iterates through enabled failover sources to find comments for a specific episode.
    """
    db = get_database_service()
    async with db.transaction():
        enabled_sources_settings = await db.metadata_source.get_enabled_failover_sources()

    for source_setting in enabled_sources_settings:
        provider = source_setting['providerName']
        source_instance = metadata_manager.sources.get(provider)
        if not source_instance:
            metadata_manager.logger.warning(f"Enabled failover source '{provider}' was not loaded, skipping.")
            continue

        metadata_manager.logger.info(f"Failover: Trying source '{provider}' for '{title}' S{season}E{episode_index}")
        try:
            comments = await source_instance.get_comments_by_failover(title, season, episode_index, user)
            if comments:
                metadata_manager.logger.info(f"Failover: Source '{provider}' successfully found {len(comments)} comments.")
                return comments
        except Exception as e:
            metadata_manager.logger.error(f"Failover source '{provider}' failed: {e}", exc_info=True)

    metadata_manager.logger.info(f"Failover: No source could find comments for '{title}' S{season}E{episode_index}")
    return None



async def supplement_search_result(metadata_manager: MetadataService, target_provider: str, keyword: str, episode_info: Optional[Dict[str, Any]]) -> List[ProviderSearchInfo]:
    """
    当主搜索源未找到结果时，主动通过故障转移源（如360）查找对应平台的链接，并返回结果。
    """
    metadata_manager.logger.info(f"主搜索源 '{target_provider}' 未找到结果，正在尝试故障转移...")

    db = get_database_service()
    async with db.transaction():
        failover_sources_settings = await db.metadata_source.get_enabled_failover_sources()

    user = User(id=0, username="system")

    for source_setting in failover_sources_settings:
        provider_name = source_setting['providerName']
        source_instance = metadata_manager.sources.get(provider_name)
        if not source_instance or not hasattr(source_instance, "find_url_for_provider"):
            continue

        metadata_manager.logger.info(f"故障转移: 正在使用 '{provider_name}' 查找 '{keyword}' 的 '{target_provider}' 链接...")
        target_url = await source_instance.find_url_for_provider(keyword, target_provider, user)
        if not target_url:
            continue

        metadata_manager.logger.info(f"故障转移成功: 从 '{provider_name}' 找到URL: {target_url}")
        try:
            target_scraper = metadata_manager.scraper_manager.get_scraper(target_provider)
            info = await target_scraper.get_info_from_url(target_url)
            if info:
                return [info]
        except Exception as e:
            metadata_manager.logger.error(f"通过故障转移URL '{target_url}' 获取信息失败: {e}")
            continue

    return []



async def find_new_media_id(metadata_manager: MetadataService, source_info: Dict[str, Any]) -> Optional[str]:
    """
    当获取分集列表失败时，尝试通过故障转移源查找新的 mediaId。
    """
    target_provider = source_info["providerName"]
    title = source_info["title"]
    season = source_info.get("season", 1)
    metadata_manager.logger.info(f"分集获取失败，正在为 '{title}' S{season} ({target_provider}) 尝试故障转移查找新 mediaId...")

    db = get_database_service()
    async with db.transaction():
        failover_sources_settings = await db.metadata_source.get_enabled_failover_sources()

    user = User(id=0, username="system")

    for source_setting in failover_sources_settings:
        provider_name = source_setting['providerName']
        if source_instance := metadata_manager.sources.get(provider_name):
            if hasattr(source_instance, "find_url_for_provider"):
                target_url = await source_instance.find_url_for_provider(title, target_provider, user, season=season)
                if target_url:
                    return await metadata_manager.scraper_manager.get_scraper(target_provider).get_id_from_url(target_url)
    return None

