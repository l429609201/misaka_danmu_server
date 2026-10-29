"""
媒体类型（anime.type）统一定义 - 纯静态常量，无任何项目内依赖

why：类型枚举扩充（新增综艺 variety、短剧 short_drama）后，原先散落在各处的
`== "tv_series"` 判断会把新类型误当成「非剧集」。所有「是否按剧集/电影处理」的判断
统一走 is_series_type()/is_movie_type()，以后再加类型只需改本文件。

使用方式:
    from src.schemas.media_types import ANIME_TYPES, is_series_type, is_movie_type
"""

from typing import Any, NamedTuple, Optional

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


class StorageTypeSpec(NamedTuple):
    """弹幕存储类型定义。

    Attributes:
        prefix: config 表键前缀（{prefix}DanmakuDirectoryPath / {prefix}DanmakuFilenameTemplate）
        default_dir: 默认根目录；空串 = 留空沿用 fallback 类型
        default_template: 默认命名模板；空串 = 留空沿用 fallback 类型
        fallback: 目录/模板留空时逐项回退到的类型；None 表示基础类型、不回退
    """

    prefix: str
    default_dir: str
    default_template: str
    fallback: Optional[str] = None


# 弹幕存储类型（唯一数据源，与前端 web/src/configs/index.js 的 STORAGE_TYPES 一一对应）
# why：电影/电视与 OVA/综艺/短剧统一为一张表，默认配置、AI 助手配置工具、路径解析都从这里派生，
# 不再特判电影/电视；新增类型只需在此追加一行。OVA/综艺/短剧默认留空回退电视节目，保证升级后路径不变。
STORAGE_TYPES = {
    MEDIA_TYPE_MOVIE: StorageTypeSpec("movie", "/app/config/danmaku/movies", "${title}/${episodeId}"),
    MEDIA_TYPE_TV_SERIES: StorageTypeSpec("tv", "/app/config/danmaku/tv", "${animeId}/${episodeId}"),
    MEDIA_TYPE_OVA: StorageTypeSpec("ova", "", "", MEDIA_TYPE_TV_SERIES),
    MEDIA_TYPE_VARIETY: StorageTypeSpec("variety", "", "", MEDIA_TYPE_TV_SERIES),
    MEDIA_TYPE_SHORT_DRAMA: StorageTypeSpec("shortDrama", "", "", MEDIA_TYPE_TV_SERIES),
}

# 未列出的类型（other/未知/None）使用的存储类型
DEFAULT_STORAGE_TYPE = MEDIA_TYPE_TV_SERIES

# 命名模板可用变量说明（默认配置描述、AI 助手白名单共用）
STORAGE_TEMPLATE_VARIABLES_DESC = (
    "支持变量：${title}, ${titleBase}(标准化标题，去除季度信息), ${season}, ${episode}, ${year}, "
    "${provider}, ${animeId}, ${episodeId}, ${sourceId}, ${tmdbId}(TMDB ID), ${category}(类型名), "
    "${type}(类型值)。支持子目录。.xml后缀会自动添加。"
)


def get_storage_spec(media_type: Any) -> StorageTypeSpec:
    """返回类型对应的弹幕存储定义，未列出的类型返回电视节目定义。"""
    return STORAGE_TYPES.get(media_type, STORAGE_TYPES[DEFAULT_STORAGE_TYPE])


def storage_config_keys(prefix: str) -> tuple[str, str]:
    """返回某配置前缀对应的 (根目录配置键, 命名模板配置键)。"""
    return f"{prefix}DanmakuDirectoryPath", f"{prefix}DanmakuFilenameTemplate"


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
