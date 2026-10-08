"""别名纯数据处理：只负责匹配和字段提取，不访问服务、网络或数据库。"""
from typing import Any, Dict, Iterable, Optional

from thefuzz import fuzz


def pick_best_match(results: Iterable[Any], title: str, year: Optional[int]) -> Optional[Any]:
    """按标题相似度和年份选择至少达到 70 分的结果。"""
    best_item = None
    best_score = 0
    for item in results:
        title_score = fuzz.token_set_ratio(title, item.title)
        year_bonus = 0
        if year and getattr(item, "year", None):
            if item.year == year:
                year_bonus = 20
            elif abs(item.year - year) == 1:
                year_bonus = 5
            elif abs(item.year - year) > 3:
                year_bonus = -20
        total_score = title_score + year_bonus
        if total_score > best_score:
            best_score = total_score
            best_item = item
    return best_item if best_score >= 70 else None


def extract_aliases_from_details(details: Any) -> Optional[Dict[str, Any]]:
    """提取已有详情的别名字段，避免调用方重复请求元数据。"""
    if not details:
        return None
    aliases = {
        "name_en": details.nameEn,
        "name_jp": details.nameJp,
        "name_romaji": details.nameRomaji,
        "aliases_cn": details.aliasesCn or [],
    }
    return aliases if any(aliases.values()) else None
