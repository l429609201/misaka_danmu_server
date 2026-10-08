"""
季度映射模块 - V2.1.6 完整功能版本

提供季度映射、外传识别、TMDB剧集组支持等功能
"""

import hashlib
import logging
import re
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple, Any

from src.schemas.search import ProviderEpisodeInfo, ProviderSearchInfo
from src.schemas.metadata import MetadataDetailsResponse, TMDBSeasonInfo
# 中文/罗马数字映射与显式季度提取统一由 filename_parser 提供，避免重复实现
from src.utils.parsing.filename_parser import (
    extract_season_from_title as _extract_explicit_season_from_title,
)

# 延迟导入避免循环依赖
# 循环路径：utils/season_mapper → services.cache_service → services.__init__ → task_manager → db → crud → utils

logger = logging.getLogger(__name__)


# ============================================================================
# 外传/衍生作品相关 (V2.1.6新增)
# ============================================================================

# 外传/衍生作品识别关键词
SPINOFF_KEYWORDS = [
    # 中文
    "外传", "番外", "特别篇", "剧场版", "OVA", "OAD", "SP",
    # 日文
    "外伝", "番外編", "特別編",
    # 英文
    "spin-off", "spinoff", "side story", "gaiden",
    "special", "movie", "film", "ova", "oad",
]

# 预编译外传关键词匹配模式，避免每次调用重复编译
SPINOFF_PATTERN = re.compile(
    "|".join(re.escape(kw) for kw in SPINOFF_KEYWORDS),
    re.IGNORECASE,
)


def is_spinoff_title(title: str, base_title: str) -> bool:
    """
    检测标题是否为外传/衍生作品。

    外传作品不应参与正片的季度映射，否则会把"某作品外传"误判成某一季。

    :param title: 要检测的标题
    :param base_title: 原作基础标题
    :return: True 表示是外传/衍生作品
    """
    if not title:
        return False

    title_lower = title.lower()

    # 1. 命中外传关键词
    if SPINOFF_PATTERN.search(title):
        return True

    # 2. "原作标题：副标题" 形式，且副标题不是季度标识
    if base_title:
        base_lower = base_title.lower()
        if base_lower in title_lower:
            suffix = title_lower.replace(base_lower, "").strip()
            # 排除纯季度标识（如 "第二季"、"II"、"2"、"season 2"）
            season_only = re.match(
                r'^[:\s]*(?:第?\d+季|[ⅰⅱⅲⅳⅴⅵⅶⅷⅸⅹ]+|[ivx]+|season\s*\d+|\d+)$',
                suffix,
                re.IGNORECASE,
            )
            if suffix and not season_only:
                # 冒号后跟非季度内容，判定为外传副标题
                if re.match(r'^[:\s：]+[^第季\d]+', suffix):
                    return True

    return False


def calculate_similarity(str1: str, str2: str) -> float:
    """
    计算两个字符串的相似度 (0-100)

    使用标准库 difflib，不引入 thefuzz 依赖。综合三种策略取最大值：
    简单序列相似度、子串包含度、词元 Jaccard 相似度。

    :param str1: 第一个字符串
    :param str2: 第二个字符串
    :return: 相似度百分比 (0-100)
    """
    if not str1 or not str2:
        return 0.0

    s1 = str1.lower().strip()
    s2 = str2.lower().strip()

    # 策略1：整体序列相似度
    simple = SequenceMatcher(None, s1, s2).ratio() * 100

    # 策略2：子串包含度（短串完整出现在长串中时按长度占比计分）
    partial = 0.0
    shorter, longer = (s1, s2) if len(s1) <= len(s2) else (s2, s1)
    if shorter in longer:
        partial = len(shorter) / len(longer) * 100

    # 策略3：词元集合 Jaccard 相似度
    s1_tokens = set(s1.split())
    s2_tokens = set(s2.split())
    if s1_tokens and s2_tokens:
        union = len(s1_tokens | s2_tokens)
        token_sim = (len(s1_tokens & s2_tokens) / union) * 100 if union > 0 else 0.0
    else:
        token_sim = 0.0

    return float(max(simple, partial, token_sim))


def title_contains_season_name(
    title: str,
    season_number: int,
    season_name: str,
    season_aliases: Optional[List[str]] = None,
    threshold: float = 60.0,
) -> float:
    """
    判断标题是否包含季度名称并计算相似度。

    按 5 种策略逐级判定，命中越早置信度越高；低于阈值返回 0。

    :param title: 搜索结果标题
    :param season_number: 季度编号
    :param season_name: 季度名称
    :param season_aliases: 季度别名列表
    :param threshold: 相似度阈值，低于该值视为不匹配
    :return: 相似度百分比 (0-100)
    """
    if not title or not season_name:
        return 0.0

    title_lower = title.lower().strip()
    season_name_lower = season_name.lower().strip()
    max_similarity = 0.0

    # 策略1：季度名直接作为子串出现
    if season_name_lower in title_lower:
        return 95.0

    # 策略2：剥离"第N季/season N/sN"前缀后再匹配
    season_cleaned = re.sub(
        r'^(第\d+季|season\s*\d+|s\d+)\s*', '', season_name_lower, flags=re.IGNORECASE
    )
    if season_cleaned and season_cleaned in title_lower:
        return 90.0

    # 策略3：标题中直接含季度号
    season_patterns = [
        rf'第{season_number}季',
        rf'season\s*{season_number}',
        rf's{season_number}\b',
        rf'第{season_number}部',
    ]
    for pattern in season_patterns:
        if re.search(pattern, title_lower, flags=re.IGNORECASE):
            max_similarity = max(max_similarity, 85.0)
            break

    # 策略4：模糊相似度兜底
    max_similarity = max(
        max_similarity,
        calculate_similarity(season_cleaned or season_name_lower, title_lower),
    )

    # 策略5：别名逐个比对
    if season_aliases:
        for alias in season_aliases:
            max_similarity = max(max_similarity, calculate_similarity(alias.lower(), title_lower))

    return max_similarity if max_similarity >= threshold else 0.0




class SeasonMapper:
    """
    季度映射器

    功能：
    1. 处理多季度映射
    2. 识别外传/衍生作品
    3. TMDB剧集组支持
    4. 缓存管理
    """

    def __init__(
        self,
        metadata_manager=None,
        session_factory: Any = None
    ):
        self.metadata_manager = metadata_manager
        self.session_factory = session_factory
        self._cache = {}

    async def get_seasons_from_source(
        self,
        provider: str,
        media_id: str,
        user,
        force_refresh: bool = False
    ) -> List[TMDBSeasonInfo]:
        """
        从指定源获取所有可用季度

        :param provider: 提供商名称
        :param media_id: 媒体ID
        :param user: 用户对象
        :param force_refresh: 是否强制刷新
        :return: 季度列表
        """
        try:
            if not self.metadata_manager:
                return []

            # 检查缓存
            cache_key = f"{provider}:{media_id}"
            if not force_refresh and cache_key in self._cache:
                return self._cache[cache_key]

            # 从元数据源获取详情
            details = await self.metadata_manager.get_details(
                provider, media_id, user, mediaType="tv"
            )

            if not details:
                return []

            # 提取季度信息
            seasons = self._extract_seasons_from_details(details)

            # 缓存结果
            self._cache[cache_key] = seasons

            return seasons

        except Exception as e:
            logger.error(f"获取季度信息失败: {e}", exc_info=True)
            return []

    def _extract_seasons_from_details(
        self,
        details: MetadataDetailsResponse
    ) -> List[TMDBSeasonInfo]:
        """读取结构化季度信息，保留名称、别名并按季度编号排序。"""
        seasons = getattr(details, 'tmdbSeasons', None) or getattr(details, 'seasons', None) or []
        if not seasons and getattr(details, 'season', None):
            seasons = [details.season]

        # 模型不可哈希，按季度编号去重；兼容旧源仅返回季度编号的情况。
        by_number = {}
        for season in seasons:
            if isinstance(season, int):
                season = TMDBSeasonInfo(
                    id=0, name=f"第{season}季", seasonNumber=season, episodeCount=0,
                )
            by_number.setdefault(season.season_number, season)
        return [by_number[number] for number in sorted(by_number)]

    def is_spinoff(self, title: str) -> bool:
        """
        判断是否为外传/衍生作品

        :param title: 标题
        :return: 是否为外传
        """
        title_lower = title.lower()
        return any(keyword in title_lower for keyword in SPINOFF_KEYWORDS)

    async def map_episode_to_season(
        self,
        episodes: List[ProviderEpisodeInfo],
        target_season: int
    ) -> List[ProviderEpisodeInfo]:
        """
        将剧集映射到指定季度

        :param episodes: 剧集列表
        :param target_season: 目标季度
        :return: 映射后的剧集列表
        """
        mapped = []
        for ep in episodes:
            mapped_ep = ep.copy()
            mapped_ep.season = target_season
            mapped.append(mapped_ep)
        return mapped

    def clear_cache(self):
        """清空缓存"""
        self._cache.clear()
        logger.info("季度映射缓存已清空")



# ============================================================================
# 季度映射内部辅助函数
# ============================================================================

def _build_title_alias_equivalence_map(
    tv_results: List[Any],
    seasons_info: List[Any],
    log: logging.Logger,
) -> Dict[str, Dict[str, Any]]:
    """
    构建标题别名等价映射表。

    若搜索结果标题与元数据源的某季度名称/别名完全一致，直接建立等价关系，
    这是最快且最可靠的匹配路径，可跳过后续相似度计算。

    :param tv_results: 待映射的电视剧类搜索结果
    :param seasons_info: 元数据源返回的季度信息
    :param log: 日志记录器
    :return: {结果标题: {'season': 季度号, 'name': 季度名}}
    """
    equivalence_map: Dict[str, Dict[str, Any]] = {}

    # 汇总每一季的全部可识别别名
    season_aliases: Dict[int, Dict[str, Any]] = {}
    for season in seasons_info:
        aliases = set()
        if season.name:
            aliases.add(season.name.lower().strip())
        if season.aliases:
            for alias in season.aliases:
                aliases.add(alias.lower().strip())
        # 补充季度编号形式的别名
        aliases.add(f"s{season.season_number}")
        aliases.add(f"第{season.season_number}季")

        season_aliases[season.season_number] = {
            'season': season.season_number,
            'name': season.name or f"第{season.season_number}季",
            'aliases': aliases,
        }

    for item in tv_results:
        title_normalized = item.title.lower().strip()
        for season_num, info in season_aliases.items():
            if title_normalized in info['aliases']:
                equivalence_map[item.title] = {
                    'season': season_num,
                    'name': info['name'],
                }
                break

    if equivalence_map:
        log.info(f"📋 别名等价映射: 找到 {len(equivalence_map)} 个直接匹配")

    return equivalence_map


def _calculate_season_similarity(
    title: str,
    season_name: str,
    season_aliases: Optional[List[str]] = None,
) -> float:
    """
    计算标题与某一季度的相似度。

    与 title_contains_season_name 的区别：本函数不做阈值截断，
    返回原始相似度供调用方统一比较取最优。

    :param title: 搜索结果标题
    :param season_name: 季度名称
    :param season_aliases: 季度别名列表
    :return: 相似度百分比 (0-100)
    """
    if not title or not season_name:
        return 0.0

    title_clean = title.lower().strip()
    season_clean = season_name.lower().strip()

    # 季度名直接作为子串出现
    if season_clean in title_clean:
        return 95.0

    # 剥离季度前缀后再匹配
    season_no_prefix = re.sub(
        r'^(第\d+季|season\s*\d+|s\d+)\s*', '', season_clean, flags=re.IGNORECASE
    )
    if season_no_prefix and season_no_prefix in title_clean:
        return 90.0

    max_sim = calculate_similarity(season_no_prefix or season_clean, title_clean)

    # 别名逐个比对取最大值
    if season_aliases:
        for alias in season_aliases:
            max_sim = max(max_sim, calculate_similarity(alias.lower(), title_clean))

    return float(max_sim)
