"""
媒体类型（anime.type）统一定义 - 纯静态常量，无任何项目内依赖

why：类型枚举扩充（新增综艺 variety、短剧 short_drama）后，原先散落在各处的
`== "tv_series"` 判断会把新类型误当成「非剧集」。所有「是否按剧集/电影处理」的判断
统一走 is_series_type()/is_movie_type()，以后再加类型只需改本文件。

使用方式:
    from src.schemas.media_types import ANIME_TYPES, is_series_type, is_movie_type
"""

from typing import Any, Optional

# ---- 具体类型值（与数据库 ENUM anime_type 一一对应）----
MEDIA_TYPE_TV_SERIES = "tv_series"      # 电视节目（电视剧/动漫/纪录片等连续剧集）
MEDIA_TYPE_MOVIE = "movie"              # 电影
MEDIA_TYPE_OVA = "ova"                  # OVA
MEDIA_TYPE_OTHER = "other"              # 其他
MEDIA_TYPE_VARIETY = "variety"          # 综艺
MEDIA_TYPE_SHORT_DRAMA = "short_drama"  # 短剧

# anime.type 允许的全部枚举值（顺序即数据库 ENUM 定义顺序，新值只能追加在末尾）
ANIME_TYPES = (
    MEDIA_TYPE_TV_SERIES,
    MEDIA_TYPE_MOVIE,
    MEDIA_TYPE_OVA,
    MEDIA_TYPE_OTHER,
    MEDIA_TYPE_VARIETY,
    MEDIA_TYPE_SHORT_DRAMA,
)

# 类型中文名（路径模板 ${category}、界面展示用；未知类型回退「其他」）
ANIME_TYPE_LABELS = {
    MEDIA_TYPE_TV_SERIES: "电视节目",
    MEDIA_TYPE_MOVIE: "电影",
    MEDIA_TYPE_OVA: "OVA",
    MEDIA_TYPE_OTHER: "其他",
    MEDIA_TYPE_VARIETY: "综艺",
    MEDIA_TYPE_SHORT_DRAMA: "短剧",
}


def get_type_label(media_type: Any) -> str:
    """返回类型的中文名，未知或空类型返回「其他」。"""
    return ANIME_TYPE_LABELS.get(media_type, ANIME_TYPE_LABELS[MEDIA_TYPE_OTHER])


# 按「剧集形态」处理的类型：分季、多集、弹幕路径走 tv 模板
SERIES_TYPES = frozenset({MEDIA_TYPE_TV_SERIES, MEDIA_TYPE_VARIETY, MEDIA_TYPE_SHORT_DRAMA})


def is_series_type(media_type: Any) -> bool:
    """判断类型是否按剧集形态处理（tv_series / variety / short_drama）。

    Args:
        media_type: 类型值，可为 None。

    Returns:
        属于剧集形态返回 True；None 返回 False（未知类型由调用方自行决定是否兜底）。
    """
    return media_type in SERIES_TYPES


def is_movie_type(media_type: Any) -> bool:
    """判断类型是否为电影。

    Args:
        media_type: 类型值，可为 None。

    Returns:
        为 movie 时返回 True。
    """
    return media_type == MEDIA_TYPE_MOVIE


def to_form_type(media_type: Optional[str]) -> Optional[str]:
    """把具体类型折叠为「形态」：剧集类统一返回 tv_series，其余原样返回。

    why：外部系统（弹弹play API、TMDB/Emby 等搜索类型、AI 匹配提示词）只认
    tv_series/movie，传给它们之前用本函数降级，保证对外兼容。

    Args:
        media_type: 具体类型值。

    Returns:
        剧集形态返回 tv_series，否则原值（含 None）。
    """
    if media_type in SERIES_TYPES:
        return MEDIA_TYPE_TV_SERIES
    return media_type
