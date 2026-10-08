"""统一搜索结果评分。

设计约束（本文件是全系统唯一评分实现）：
    · 只提供一个 compute_score() 函数，**不设基类/子类**。
      只有一种算法却建继承层次属于过度设计，且基类+子类仍是两个可被独立改写的入口，
      无法真正消除「双真相源」——这正是重构前 auto_import 与 webhook 各写一套评分的成因。
    · 调用方之间的唯一差异（webhook 需为库内已有源加分）降级为可选入参，
      通过 existing_source_keys + library_source_bonus 表达，不派生新算法。

历史背景：
    重构前 auto_import.py 使用 7 元组字典序排序，webhook.py 使用 12 项加权总分，
    权重值互不一致（如「标题完全匹配」为 1000 vs 10000），相同输入可能得到不同排序。
    本实现以加权总分为基准：元组字典序会让排在后面的因素成为死代码
    （前一项分出高下后，后续项永不参与比较），加权总分则保证所有因素都真实生效。
"""

import logging
from typing import Any, Dict, Optional, Set
from src.services.service_container import get_database_service
from thefuzz import fuzz

__all__ = ["compute_score", "SCORE_WEIGHTS", "load_provider_order"]

logger = logging.getLogger(__name__)


# 权重表集中定义，便于调参与单元测试断言
SCORE_WEIGHTS: Dict[str, int] = {
    "title_exact": 10000,        # 标题完全匹配
    "title_normalized": 5000,    # 去标点/空格后完全匹配
    "similarity_high": 2000,     # token_sort_ratio > 98 且长度差 <= 10
    "similarity_mid": 1000,      # token_sort_ratio > 95 且长度差 <= 20
    "long_running": 800,         # 长期连载作品（标题完全匹配且年份早 3 年以上）
    "year_match": 200,           # 年份匹配（元数据年份常不准确，故权重较低）
    "year_mismatch": -200,       # 年份不匹配惩罚
    "season_match": 500,         # 季度匹配
    "season_mismatch": -500,     # 季度不匹配惩罚（同名不同季必须拉开差距）
    "length_penalty_factor": -2, # 标题长度差异惩罚系数
    "provider_base": 1000,       # 源优先级基准分
    "provider_step": 60,         # 源优先级每级递减分
}

# 相似度计入实际分数的下限：低于此值不计入，避免弱相关结果靠相似度堆分
_SIMILARITY_FLOOR = 85


def compute_score(
    item: Any,
    *,
    query_title: str,
    query_season: Optional[int] = None,
    query_year: Optional[int] = None,
    query_type: Optional[str] = None,
    provider_order: Optional[Dict[str, int]] = None,
    existing_source_keys: Optional[Set[str]] = None,
    library_source_bonus: int = 0,
) -> int:
    """计算单个搜索结果的加权总分，分数越高越优先。

    Args:
        item: 搜索结果对象，需具备 title/provider/mediaId/season/year/type 属性。
        query_title: 目标标题（应为名称转换、识别词预处理后的标题）。
        query_season: 目标季度，None 或 0 表示不参与季度比较。
        query_year: 目标年份（数据库年份优先），None 表示不参与年份比较。
        query_type: 目标媒体类型，仅 'tv_series' 时启用季度加权。
        provider_order: {provider 名称: displayOrder}，值越小优先级越高。
        existing_source_keys: 库内已有源集合，元素格式 "provider:mediaId"。
        library_source_bonus: 命中 existing_source_keys 时的加分。
            仅 webhook 场景传 3000（优先复用库内源以减少重复拉取），其余场景保持 0。

    Returns:
        加权总分。
    """
    weights = SCORE_WEIGHTS
    provider_order = provider_order or {}

    score = 0
    item_title = item.title or ""
    item_title_stripped = item_title.strip()
    query_title_stripped = (query_title or "").strip()

    item_season = getattr(item, "season", None)
    item_year = getattr(item, "year", None)

    # 1. 标题完全匹配
    title_exact = item_title_stripped == query_title_stripped
    if title_exact:
        score += weights["title_exact"]

    # 2. 去标点/空格后完全匹配（兼容全角冒号与空格差异）
    if _normalize(item_title) == _normalize(query_title_stripped):
        score += weights["title_normalized"]

    # 3/4. 相似度分档加分：长度差约束用于排除「原名 + 长后缀」的特别篇
    token_sort = fuzz.token_sort_ratio(query_title_stripped, item_title)
    len_diff = abs(len(item_title) - len(query_title_stripped))
    if token_sort > 98 and len_diff <= 10:
        score += weights["similarity_high"]
    if token_sort > 95 and len_diff <= 20:
        score += weights["similarity_mid"]

    # 5. 长期连载作品：标题完全匹配但首播年份显著早于目标年份
    if (
        title_exact
        and query_year is not None
        and item_year is not None
        and query_year - item_year >= 3
    ):
        score += weights["long_running"]

    # 6/10. 年份匹配与惩罚
    if query_year is not None and item_year is not None:
        if item_year == query_year:
            score += weights["year_match"]
        else:
            score += weights["year_mismatch"]

    # 7. 季度匹配与惩罚：季度过滤可能因 season 解析不准漏掉，此处再加权作为双保险
    if (
        query_season is not None
        and query_season > 0
        and item_season is not None
        and (query_type is None or query_type == "tv_series")
    ):
        score += weights["season_match"] if item_season == query_season else weights["season_mismatch"]

    # 8. 一般相似度：仅在达到下限后计入实际分值（0~100）
    token_set = fuzz.token_set_ratio(query_title_stripped, item_title)
    if token_set >= _SIMILARITY_FLOOR:
        score += token_set

    # 9. 标题长度差异惩罚
    score += len_diff * weights["length_penalty_factor"]

    # 11. 源优先级：displayOrder 越小得分越高，相邻级差 60
    order = provider_order.get(item.provider, 999)
    score += max(0, weights["provider_base"] - order * weights["provider_step"])

    # 12. 库内已有源加分（仅 webhook 场景生效）
    if library_source_bonus and existing_source_keys:
        if f"{item.provider}:{getattr(item, 'mediaId', '')}" in existing_source_keys:
            score += library_source_bonus

    return score


def _normalize(title: str) -> str:
    """归一化标题：统一全角冒号并移除空格，用于去标点比较。"""
    return (title or "").replace("：", ":").replace(" ", "").strip()


async def load_provider_order() -> Dict[str, int]:
    """读取弹幕源设置并推导 compute_score 所需的 provider_order 映射。

    why: {s['providerName']: s['displayOrder'] for s in settings} 此前在
    engine / auto_import(两处) / webhook 共 4 处各写一遍。provider_order 是
    compute_score 的入参契约，故与其同文件内聚，避免调用方重复推导。

    不接收 session：数据访问统一由 DatabaseService 管理事务，调用方无需也不应
    再传递会话（旧的 crud(session, ...) 形式正在全面移除）。

    Returns:
        {provider 名称: displayOrder}，值越小优先级越高。查询失败返回空字典
        （compute_score 对缺失 provider 回退 999，不影响排序可用性）。
    """


    try:
        db = get_database_service()
        async with db.transaction():
            settings = await db.scraper.get_all_scraper_settings()
    except Exception as e:
        logger.warning(f"读取弹幕源优先级失败，退化为无优先级排序: {type(e).__name__}: {e}")
        return {}

    return {s["providerName"]: s["displayOrder"] for s in settings}
