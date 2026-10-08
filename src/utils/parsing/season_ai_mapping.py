"""季度映射 AI 编排辅助。

纯算法保留在 season_mapper.py；元数据缓存由调用方传入的管理器负责，
本模块不再导入缓存或数据库服务，避免 utils 与 services 循环依赖。
"""

import logging
import time
from typing import Any, Dict, List, Optional

# 第三方依赖统一顶层导入，不通过局部导入隐藏依赖。
from thefuzz import fuzz

from src.schemas.metadata import MetadataDetailsResponse
from src.schemas.search import ProviderSearchInfo
from src.utils.parsing.season_mapper import (
    _build_title_alias_equivalence_map,
    _calculate_season_similarity,
    is_spinoff_title,
)
from src.utils.parsing.filename_parser import (
    extract_season_from_title as _extract_explicit_season_from_title,
)

logger = logging.getLogger(__name__)


async def _resolve_best_metadata_match(
    search_title: str,
    metadata_results: list,
    ai_matcher,
    logger,
    metadata_source: str,
):
    """
    从多个元数据搜索结果中选出最佳匹配。

    三级策略：唯一结果直取 → 标题高度相似走快速路径 → 交由 AI 选择。
    抽出为独立函数，避免主流程函数过长（KISS）。

    Returns:
        选中的元数据结果对象
    """
    if len(metadata_results) == 1:
        best = metadata_results[0]
        logger.info(f"○ AI季度映射: 唯一[{metadata_source}]匹配: {best.title} (类型: {best.type})")
        return best

    # 快速路径：首个结果标题几乎完全匹配时直接采用，省一次 AI 调用

    first_sim = fuzz.ratio(search_title.lower(), metadata_results[0].title.lower())
    if first_sim >= 90:
        best = metadata_results[0]
        logger.info(
            f"○ AI季度映射: 快速路径 [{metadata_source}]匹配: {best.title} (相似度{first_sim}%)"
        )
        return best

    try:
        provider_results = [
            ProviderSearchInfo(
                provider=metadata_source,
                mediaId=getattr(r, 'tmdbId', None) or r.id,
                title=r.title,
                type=r.type or "unknown",
                season=1,
                year=r.year,
                imageUrl=r.imageUrl,
                episodeCount=None,
            )
            for r in metadata_results
        ]
        query_info = {"title": search_title, "season": None, "episode": None, "year": None, "type": None}

        selected_index = await ai_matcher.select_best_match(query_info, provider_results, {})
        if selected_index is not None and 0 <= selected_index < len(metadata_results):
            best = metadata_results[selected_index]
            logger.info(
                f"✓ AI季度映射: AI选择[{metadata_source}]匹配: {best.title} "
                f"(类型: {best.type}, ID: {best.id})"
            )
            return best

        logger.error("⚠ AI季度映射: AI选择匹配失败，使用第一个结果")
    except Exception as e:
        logger.error(f"⚠ AI季度映射: 匹配选择失败，使用第一个结果: {e}")

    return metadata_results[0]


async def _fetch_seasons_info(
    search_title: str,
    metadata_manager,
    ai_matcher,
    logger,
    metadata_source: str,
    prefetched_metadata_results: Optional[list],
    user: Any,
) -> Optional[list]:
    """
    获取目标作品的季度信息列表。

    Returns:
        季度信息列表；无法获取或季度数不足 2 时返回 None（调用方直接跳过修正）
    """
    if prefetched_metadata_results:
        metadata_results = prefetched_metadata_results
        logger.info(
            f"✓ AI季度映射: 使用预取的[{metadata_source}]结果 ({len(metadata_results)} 个)"
        )
    else:
        metadata_results = await metadata_manager.search_cached(
            search_title, source=metadata_source, log=logger
        )

    if not metadata_results:
        logger.info(f"○ AI季度映射: 未找到 '{search_title}' 的[{metadata_source}]信息")
        return None

    best_match = await _resolve_best_metadata_match(
        search_title, metadata_results, ai_matcher, logger, metadata_source
    )

    # 季度信息只存在于 TV 类型上，若选中的不是 TV 则在结果中另找一个
    if best_match.type != 'tv':
        logger.warning(
            f"⚠ AI季度映射: 选择的结果不是TV类型 ({best_match.type})，无法获取季度信息"
        )
        tv_result = next((r for r in metadata_results if r.type == 'tv'), None)
        if not tv_result:
            logger.error("⚠ AI季度映射: 结果中没有TV类型，无法获取季度信息")
            return None
        logger.info(f"✓ AI季度映射: 找到TV类型结果: {tv_result.title} (ID: {tv_result.id})")
        best_match = tv_result

    try:
        seasons_info = await metadata_manager.get_seasons(metadata_source, best_match.id, user)
    except Exception as e:
        logger.error(f"获取[{metadata_source}]季度信息失败: {best_match.id}, 错误: {e}")
        return None

    if not seasons_info or len(seasons_info) <= 1:
        logger.info(f"○ AI季度映射: '{search_title}' 只有1个季度或无季度信息，跳过")
        return None

    return seasons_info


def _match_single_title_to_season(
    item,
    seasons_info: list,
    title_alias_mapping: Dict[str, Dict],
    similarity_threshold: float,
    logger,
) -> tuple:
    """
    为单个搜索结果匹配最佳季度（不含 AI 辅助，纯同步）。

    Returns:
        (best_season, best_confidence, best_season_name, best_method)
    """
    best_season = item.season or 1
    best_confidence = 0.0
    best_season_name = ""
    best_method = "原始"

    # 策略1: 别名等价匹配（最快，置信度最高）
    equivalent_info = title_alias_mapping.get(item.title)
    if equivalent_info:
        logger.debug(
            f"    🎯 别名等价匹配: S{equivalent_info['season']} ({equivalent_info['name']})"
        )
        return equivalent_info['season'], 98.0, equivalent_info['name'], "别名等价"

    # 策略2: 算法相似度匹配
    for season in seasons_info:
        season_num = season.season_number
        season_name = season.name or f"第{season_num}季"
        confidence = _calculate_season_similarity(
            item.title, season_name, season.aliases or []
        )
        logger.debug(f"    - S{season_num} ({season_name}): 相似度 {confidence:.1f}%")

        if confidence > best_confidence and confidence >= similarity_threshold:
            best_season = season_num
            best_confidence = confidence
            best_season_name = season_name
            best_method = "算法相似度"

    return best_season, best_confidence, best_season_name, best_method


async def ai_season_mapping_and_correction(
    search_title: str,
    search_results: list,
    metadata_manager,
    ai_matcher,
    logger,
    similarity_threshold: float = 60.0,
    prefetched_metadata_results: Optional[list] = None,
    metadata_source: str = "tmdb",
    prefetched_seasons_info: Optional[list] = None,
    user: Any = None,
) -> list:
    """
    AI 季度映射与修正。

    对搜索结果中的电视剧条目，依据元数据源的真实季度信息修正其季度号。
    匹配策略按成本递增：别名等价 → 算法相似度 → AI 辅助（仅模糊区间）。

    Args:
        search_title: 标准化的搜索标题
        search_results: 搜索结果列表
        metadata_manager: 元数据管理器
        ai_matcher: AI 匹配器
        logger: 日志记录器
        similarity_threshold: 相似度阈值
        prefetched_metadata_results: 预取的元数据搜索结果（并行优化用）
        metadata_source: 元数据源名称
        prefetched_seasons_info: 预取的季度信息（命中则跳过元数据查询）
        user: 发起搜索的用户；无用户的后台入口传入 None

    Returns:
        修正记录列表，每项含 item / original_season / corrected_season / confidence /
        tmdb_season_name / method
    """
    try:
        # 快速路径：季度信息已预热，直接进入修正阶段
        if prefetched_seasons_info is not None:
            seasons_info = prefetched_seasons_info
            if not seasons_info or len(seasons_info) <= 1:
                count = len(seasons_info) if seasons_info else 0
                logger.info(f"○ AI季度映射: '{search_title}' 只有{count}个季度（预热），跳过")
                return []
            logger.info(f"✓ AI季度映射: 使用预热的季度信息 ({len(seasons_info)} 个季度)")
        else:
            seasons_info = await _fetch_seasons_info(
                search_title, metadata_manager, ai_matcher, logger,
                metadata_source, prefetched_metadata_results, user,
            )
            if seasons_info is None:
                return []

        # 日志聚合：避免逐条 info 刷屏
        log_lines = [
            f"✓ AI季度映射: 获取到 '{search_title}' 的[{metadata_source}]季度信息，"
            f"共 {len(seasons_info)} 个季度"
        ]
        for season in seasons_info:
            log_lines.append(
                f"  - 第{season.season_number}季: {season.name or f'第{season.season_number}季'}"
            )

        tv_results = [item for item in search_results if item.type == 'tv_series']
        if not tv_results:
            log_lines.append("○ AI季度映射: 没有TV结果需要修正")
            logger.info("\n".join(log_lines))
            return []

        log_lines.append(f"○ 开始季度修正，检查 {len(tv_results)} 个TV结果...")
        log_lines.append(f"🔍 [{metadata_source}]季度信息详情:")
        for season in seasons_info:
            aliases_str = ', '.join(season.aliases[:5]) if season.aliases else '无'
            log_lines.append(f"  S{season.season_number}: {season.name} (别名: {aliases_str})")

        title_alias_mapping = _build_title_alias_equivalence_map(tv_results, seasons_info, logger)
        corrected_results = []

        for item in tv_results:
            item_title = item.title

            # 外传/衍生作品不参与季度映射，否则会被误并入正传季度
            if is_spinoff_title(item_title, search_title):
                logger.debug(f"  ○ 跳过外传作品: '{item_title}' (保持原季度 S{item.season or 1})")
                continue

            logger.debug(f"  ○ 检查 '{item_title}' 的季度匹配...")

            # 标题已明确标注季度且与当前值一致时，无需再修正
            explicit_season = _extract_explicit_season_from_title(item_title)
            if explicit_season is not None and explicit_season == item.season:
                logger.debug(
                    f"  ○ 标题已明确包含季度信息: '{item_title}' → S{explicit_season}，跳过修正"
                )
                continue

            best_season, best_confidence, best_season_name, best_method = (
                _match_single_title_to_season(
                    item, seasons_info, title_alias_mapping, similarity_threshold, logger
                )
            )

            # 策略3: AI 辅助，仅在算法落入模糊区间(阈值~75%)时才付出调用成本
            if best_method != "别名等价" and similarity_threshold <= best_confidence < 75 and ai_matcher:
                try:
                    candidates = [
                        {"season": s.season_number, "name": s.name or f"第{s.season_number}季"}
                        for s in seasons_info
                    ]
                    ai_result = await ai_matcher.select_best_season_for_title(item_title, candidates)
                    if ai_result and ai_result.get('confidence', 0) > best_confidence:
                        best_season = ai_result['season']
                        best_confidence = ai_result['confidence']
                        best_season_name = ai_result.get('name', f"第{best_season}季")
                        best_method = "AI辅助"
                        logger.debug(f"    🤖 AI辅助确认: S{best_season} ({best_season_name})")
                except Exception as e:
                    logger.debug(f"    AI辅助跳过: {e}")

            if best_confidence >= similarity_threshold and item.season != best_season:
                corrected_results.append({
                    'item': item,
                    'original_season': item.season,
                    'corrected_season': best_season,
                    'confidence': best_confidence,
                    'tmdb_season_name': best_season_name,
                    'method': best_method,
                })
                log_lines.append(
                    f"  ✓ {best_method}修正: '{item_title}' S{item.season or '?'} → "
                    f"S{best_season} ({best_season_name}) (置信度: {best_confidence:.1f}%)"
                )
            elif best_confidence >= similarity_threshold:
                logger.debug(
                    f"  ○ 无需修正: '{item_title}' 已是正确季度 S{best_season} "
                    f"({best_method}, 置信度: {best_confidence:.1f}%)"
                )
            else:
                logger.debug(
                    f"  ○ 相似度不足: '{item_title}' 保持原季度 S{item.season or '?'} "
                    f"(最高相似度: {best_confidence:.1f}% < {similarity_threshold}%)"
                )

        log_lines.append(f"✓ 季度映射完成: 修正了 {len(corrected_results)} 个结果的季度信息")
        logger.info("\n".join(log_lines))
        return corrected_results

    except Exception as e:
        logger.warning(f"AI季度映射失败: {e}")
        return []


async def ai_type_and_season_mapping_and_correction(
    search_title: str,
    search_results: list,
    metadata_manager,
    ai_matcher,
    logger,
    similarity_threshold: float = 60.0,
    prefetched_metadata_results: Optional[list] = None,
    metadata_source: str = "tmdb",
    prefetched_seasons_info: Optional[list] = None,
    user: Any = None,
) -> Dict[str, Any]:
    """
    统一的 AI 类型与季度映射修正入口。

    这是搜索链路对外的唯一调用点：先做类型修正，再对电视剧条目做季度修正，
    并把修正结果就地应用到 search_results 上。

    Args:
        search_title: 标准化的搜索标题
        search_results: 搜索结果列表（会被就地修改）
        metadata_manager: 元数据管理器
        ai_matcher: AI 匹配器
        logger: 日志记录器
        similarity_threshold: 相似度阈值
        prefetched_metadata_results: 预取的元数据搜索结果（并行优化用）
        metadata_source: 元数据源名称
        prefetched_seasons_info: 预取的季度信息
        user: 发起搜索的用户；无用户的后台入口传入 None

    Returns:
        dict: type_corrections / season_corrections / total_corrections / corrected_results
    """
    try:
        log_lines = [
            f"○ 开始统一AI映射修正: '{search_title}' ({len(search_results)} 个结果)"
        ]

        type_corrections: List[Dict[str, Any]] = []
        season_corrections: List[Dict[str, Any]] = []

        # 1. 类型修正：当前保持原类型，保留结构以便后续接入类型判定逻辑
        log_lines.append("○ 开始类型修正...")
        log_lines.append(f"✓ 类型修正完成: 修正了 {len(type_corrections)} 个结果的类型信息")

        # 2. 季度修正：仅对电视剧生效
        if any(item.type == 'tv_series' for item in search_results):
            season_corrections = await ai_season_mapping_and_correction(
                search_title=search_title,
                search_results=search_results,
                metadata_manager=metadata_manager,
                ai_matcher=ai_matcher,
                logger=logger,
                similarity_threshold=similarity_threshold,
                prefetched_metadata_results=prefetched_metadata_results,
                metadata_source=metadata_source,
                prefetched_seasons_info=prefetched_seasons_info,
                user=user,
            )

            # 将修正值写回原始结果对象
            for correction in season_corrections:
                item = correction['item']
                item.season = correction['corrected_season']
                log_lines.append(f"  ✓ 季度修正应用: '{item.title}' → S{item.season}")

        total_corrections = len(type_corrections) + len(season_corrections)
        log_lines.append(
            f"✓ 统一AI映射修正完成: 类型修正 {len(type_corrections)} 个, "
            f"季度修正 {len(season_corrections)} 个, 总计 {total_corrections} 个"
        )
        logger.info("\n".join(log_lines))

        return {
            'type_corrections': type_corrections,
            'season_corrections': season_corrections,
            'total_corrections': total_corrections,
            'corrected_results': search_results.copy(),
        }

    except Exception as e:
        logger.warning(f"统一AI映射修正失败: {e}")
        return {
            'type_corrections': [],
            'season_corrections': [],
            'total_corrections': 0,
            'corrected_results': search_results,
        }



async def prefetch_metadata_for_correction(
    search_title: str,
    metadata_manager,
    ai_matcher,
    logger=None,
    metadata_source: str = "tmdb",
    user: Any = None,
) -> Optional[Dict[str, Any]]:
    """
    为 AI 类型/季度修正预取元数据（可与弹幕源搜索并行执行）。

    与 ai_type_and_season_mapping_and_correction 放在同一模块，因为本函数产出的
    三项数据正是该函数的 prefetched_* 入参——属于「参数预计算」，不属于搜索阶段。

    执行三步：
    1. 搜元数据源（带 6 小时缓存）
    2. 多结果时用 AI 选最佳匹配（首个结果高度相似则走快速路径，跳过 AI）
    3. 对电视剧类型获取季度信息

    Args:
        search_title: 标准化的搜索标题
        metadata_manager: 元数据管理器
        ai_matcher: AI 匹配器（为 None 时步骤 2 退化为取首个结果）
        logger: 日志记录器，缺省使用模块级 logger
        metadata_source: 元数据源名称（默认 tmdb）
        user: 发起搜索的用户；无用户的后台入口传入 None

    Returns:
        {'metadata_results': list, 'seasons_info': list | None, 'best_match': obj | None}
        任一环节异常时返回 None，调用方按「无预热数据」处理即可
    """
    log = logger or logging.getLogger(__name__)
    _start = time.perf_counter()

    try:
        # ── 步骤 1：搜元数据源 ──
        # 缓存查询统一交给元数据服务，不再调用已移除的工具层入口。
        metadata_results = await metadata_manager.search_cached(
            search_title, source=metadata_source, log=log
        )
        _step1_ms = (time.perf_counter() - _start) * 1000
        log.info(
            f"预热步骤1 搜{metadata_source}: "
            f"{len(metadata_results) if metadata_results else 0}个结果 ({_step1_ms:.0f}ms)"
        )
        if not metadata_results:
            return {"metadata_results": [], "seasons_info": None, "best_match": None}

        # ── 步骤 2：AI 选最佳匹配 ──
        best_match = metadata_results[0]
        if len(metadata_results) > 1 and ai_matcher:
            # 快速路径：首个结果标题已高度相似，无需付出 AI 调用成本
            first_similarity = fuzz.ratio(
                search_title.lower(), metadata_results[0].title.lower()
            )
            if first_similarity >= 90:
                log.info(
                    f"预热步骤2 快速路径: 首个结果'{metadata_results[0].title}'"
                    f"相似度{first_similarity}%，跳过AI选择"
                )
            else:
                try:
                    provider_results = [
                        ProviderSearchInfo(
                            provider=metadata_source,
                            mediaId=getattr(r, 'tmdbId', None) or r.id,
                            title=r.title,
                            type=r.type or "unknown",
                            season=1,
                            year=r.year,
                            imageUrl=r.imageUrl,
                            episodeCount=None,
                        )
                        for r in metadata_results
                    ]
                    query_info = {
                        "title": search_title, "season": None,
                        "episode": None, "year": None, "type": None,
                    }
                    selected_index = await ai_matcher.select_best_match(
                        query_info, provider_results, {}
                    )
                    if selected_index is not None and 0 <= selected_index < len(metadata_results):
                        best_match = metadata_results[selected_index]
                except Exception as e:
                    # AI 选择失败不影响整体预热，退化为使用首个结果
                    log.warning(f"预热步骤2 AI选匹配失败（退化为首个结果）: {e}")
        _step2_ms = (time.perf_counter() - _start) * 1000
        log.info(f"预热步骤2 AI选匹配: best='{best_match.title}' ({_step2_ms:.0f}ms)")

        # ── 步骤 3：获取季度信息（仅电视剧）──
        seasons_info = None
        tv_match = best_match if best_match.type == 'tv' else None
        if not tv_match:
            tv_match = next((r for r in metadata_results if r.type == 'tv'), None)
        if tv_match:
            try:
                seasons_info = await metadata_manager.get_seasons(metadata_source, tv_match.id, user)
            except Exception as e:
                log.warning(f"预热步骤3 获取季度失败（忽略）: {e}")
        _step3_ms = (time.perf_counter() - _start) * 1000
        log.info(
            f"预热步骤3 获取季度: {len(seasons_info) if seasons_info else 0}季 ({_step3_ms:.0f}ms)"
        )

        return {
            "metadata_results": metadata_results,
            "seasons_info": seasons_info,
            "best_match": tv_match or best_match,
        }
    except Exception as e:
        log.warning(f"元数据预热失败: {e}")
        return None
