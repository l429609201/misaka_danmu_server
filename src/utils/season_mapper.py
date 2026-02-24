"""季度映射模块 - V2.1.6风格重构版本"""
import hashlib
import logging
import re
from typing import Optional, List, Any, Dict
from difflib import SequenceMatcher

from pydantic import Field
from src.db import models, crud

logger = logging.getLogger(__name__)


# ============================================================================
# 外传/衍生作品检测 (V2.1.6新增)
# ============================================================================

# 外传/衍生作品的识别关键词
SPINOFF_KEYWORDS = [
    # 中文
    "外传", "番外", "特别篇", "剧场版", "OVA", "OAD", "SP",
    # 日文
    "外伝", "番外編", "特別編",
    # 英文
    "spin-off", "spinoff", "side story", "gaiden",
    "special", "movie", "film", "ova", "oad",
]

# 预编译的外传检测正则（不区分大小写）
SPINOFF_PATTERN = re.compile(
    r'(?:' + '|'.join(re.escape(kw) for kw in SPINOFF_KEYWORDS) + r')',
    re.IGNORECASE
)


def is_spinoff_title(title: str, base_title: str) -> bool:
    """
    检测标题是否为外传/衍生作品

    Args:
        title: 要检测的标题
        base_title: 原作基础标题

    Returns:
        True 如果是外传/衍生作品
    """
    if not title:
        return False

    title_lower = title.lower()

    # 1. 检查是否包含外传关键词
    if SPINOFF_PATTERN.search(title):
        return True

    # 2. 检查是否为 "XXX外传：YYY" 格式
    if base_title:
        base_lower = base_title.lower()
        # 如果标题包含基础标题，但后面有额外内容且不是季度标识
        if base_lower in title_lower:
            suffix = title_lower.replace(base_lower, "").strip()
            # 排除纯季度标识（如 "第二季"、"II"、"2"）
            if suffix and not re.match(r'^[:\s]*(?:第?\d+季|[ⅰⅱⅲⅳⅴⅵⅶⅷⅸⅹ]+|[ivx]+|season\s*\d+|\d+)$', suffix, re.IGNORECASE):
                # 检查后缀是否看起来像外传标题（有冒号后跟不同名称）
                if re.match(r'^[:\s：]+[^第季\d]+', suffix):
                    return True

    return False


# ============================================================================
# 标题中明确季度信息提取 (V2.1.7新增)
# ============================================================================

# 中文数字和罗马数字映射已迁移至 src.utils.filename_parser
from src.utils.filename_parser import (
    CHINESE_NUM_MAP,
    ROMAN_NUM_MAP,
    extract_season_from_title as _extract_explicit_season_from_title,
)


# ============================================================================
# 核心相似度计算函数 (V2.1.6风格，不依赖thefuzz)
# ============================================================================

def calculate_similarity(str1: str, str2: str) -> float:
    """
    计算两个字符串的相似度 (0-100)
    V2.1.6风格：使用内置difflib，不依赖thefuzz

    Args:
        str1: 第一个字符串
        str2: 第二个字符串

    Returns:
        相似度百分比 (0-100)
    """
    if not str1 or not str2:
        return 0.0

    s1 = str1.lower().strip()
    s2 = str2.lower().strip()

    # 1. 简单相似度
    simple = SequenceMatcher(None, s1, s2).ratio() * 100

    # 2. 部分相似度 - 子串匹配
    partial = 0.0
    shorter, longer = (s1, s2) if len(s1) <= len(s2) else (s2, s1)
    if shorter in longer:
        partial = len(shorter) / len(longer) * 100

    # 3. Token相似度
    s1_tokens = set(s1.split())
    s2_tokens = set(s2.split())
    if s1_tokens and s2_tokens:
        intersection = len(s1_tokens & s2_tokens)
        union = len(s1_tokens | s2_tokens)
        token_sim = (intersection / union) * 100 if union > 0 else 0
    else:
        token_sim = 0

    return float(max(simple, partial, token_sim))


def title_contains_season_name(title: str, season_number: int, season_name: str, season_aliases: List[str] = None, threshold: float = 60.0) -> float:
    """
    判断标题是否包含季度名称并计算相似度
    V2.1.6风格：不依赖thefuzz

    Args:
        title: 搜索结果标题
        season_number: 季度编号
        season_name: 季度名称
        season_aliases: 季度别名列表
        threshold: 相似度阈值

    Returns:
        相似度百分比 (0-100)
    """
    if not title or not season_name:
        return 0.0

    title_lower = title.lower().strip()
    season_name_lower = season_name.lower().strip()
    max_similarity = 0.0

    # 策略1: 直接子串包含
    if season_name_lower in title_lower:
        return 95.0

    # 策略2: 移除前缀后包含
    season_cleaned = re.sub(r'^(第\d+季|season\s*\d+|s\d+)\s*', '', season_name_lower, flags=re.IGNORECASE)
    if season_cleaned and season_cleaned in title_lower:
        return 90.0

    # 策略3: 季度号直接匹配
    season_patterns = [
        rf'第{season_number}季', rf'season\s*{season_number}',
        rf's{season_number}\b', rf'第{season_number}部'
    ]
    for pattern in season_patterns:
        if re.search(pattern, title_lower, flags=re.IGNORECASE):
            max_similarity = max(max_similarity, 85.0)
            break

    # 策略4: 相似度计算
    sim = calculate_similarity(season_cleaned or season_name_lower, title_lower)
    max_similarity = max(max_similarity, sim)

    # 策略5: 别名匹配
    if season_aliases:
        for alias in season_aliases:
            alias_sim = calculate_similarity(alias.lower(), title_lower)
            max_similarity = max(max_similarity, alias_sim)

    return max_similarity if max_similarity >= threshold else 0.0


# ============================================================================
# 辅助函数
# ============================================================================

def _build_title_alias_equivalence_map(tv_results: List, seasons_info: List, log) -> Dict[str, Dict]:
    """
    构建标题别名等价映射表
    如果搜索结果标题与TMDB季度别名相同，则建立等价关系
    """
    equivalence_map = {}

    # 收集所有TMDB季度的别名
    tmdb_aliases = {}
    for season in seasons_info:
        aliases = set()
        if season.name:
            aliases.add(season.name.lower().strip())
        if season.aliases:
            for alias in season.aliases:
                aliases.add(alias.lower().strip())
        # 添加季度编号别名
        aliases.add(f"s{season.season_number}")
        aliases.add(f"第{season.season_number}季")

        tmdb_aliases[season.season_number] = {
            'season': season.season_number,
            'name': season.name or f"第{season.season_number}季",
            'aliases': aliases
        }

    # 检查每个搜索结果标题是否与TMDB别名等价
    for item in tv_results:
        title_normalized = item.title.lower().strip()
        for season_num, info in tmdb_aliases.items():
            if title_normalized in info['aliases']:
                equivalence_map[item.title] = {
                    'season': season_num,
                    'name': info['name']
                }
                break

    if equivalence_map:
        log.info(f"📋 别名等价映射: 找到 {len(equivalence_map)} 个直接匹配")

    return equivalence_map


def _calculate_season_similarity(title: str, season_name: str, season_aliases: List[str] = None) -> float:
    """
    计算标题与季度的相似度 (V2.1.6核心算法)
    """
    if not title or not season_name:
        return 0.0

    title_clean = title.lower().strip()
    season_clean = season_name.lower().strip()

    # 直接子串包含
    if season_clean in title_clean:
        return 95.0

    # 移除前缀后包含
    season_no_prefix = re.sub(r'^(第\d+季|season\s*\d+|s\d+)\s*', '', season_clean, flags=re.IGNORECASE)
    if season_no_prefix and season_no_prefix in title_clean:
        return 90.0

    # 相似度计算
    max_sim = calculate_similarity(season_no_prefix or season_clean, title_clean)

    # 别名匹配
    if season_aliases:
        for alias in season_aliases:
            alias_sim = calculate_similarity(alias.lower(), title_clean)
            max_sim = max(max_sim, alias_sim)

    return float(max_sim)


class SeasonInfo(models.BaseModel):
    """通用季度信息模型"""
    season_number: int
    name: Optional[str] = None
    episode_count: int = 0
    air_date: Optional[str] = None
    overview: Optional[str] = None
    aliases: Optional[List[str]] = Field(default=[], description="季度别名列表")


class MetadataSearchCandidate(models.BaseModel):
    """元数据搜索候选结果"""
    source: str  # 'tmdb', 'tvdb', 'bangumi', etc.
    id: str  # 源的ID
    title: str
    original_title: Optional[str] = None
    year: Optional[int] = None
    media_type: str  # 'tv' or 'movie'
    overview: Optional[str] = None


class SeasonMapper:
    """季度映射器 - 通过元数据源获取季度名称"""
    
    def __init__(self, metadata_manager, session_factory):
        """
        初始化季度映射器
        
        Args:
            metadata_manager: MetadataSourceManager实例
            session_factory: 数据库会话工厂
        """
        self.metadata_manager = metadata_manager
        self._session_factory = session_factory
        self.logger = logger
    
    async def get_season_name(
        self,
        title: str,
        season_number: int,
        year: Optional[int] = None,
        sources: Optional[List[str]] = None,
        ai_matcher: Optional[Any] = None,
        user: Optional[models.User] = None,
        custom_prompt: Optional[str] = None
    ) -> Optional[str]:
        """
        通过元数据源获取指定季度的名称(通用方法)

        Args:
            title: 剧集标题
            season_number: 季度编号
            year: 年份(可选)
            sources: 要搜索的元数据源列表,None表示使用默认源
            ai_matcher: AI匹配器(可选)
            user: 用户对象
            custom_prompt: 自定义AI提示词(可选)

        Returns:
            季度名称,如果没有找到则返回None
        """
        # 检查缓存
        sources_str = "_".join(sources) if sources else "default"
        cache_key = f"season_name_{title}_{season_number}_{year or 'any'}_{sources_str}"
        async with self._session_factory() as session:
            cached_result = await crud.get_cache(session, cache_key)
            if cached_result:
                self.logger.info(f"季度名称缓存命中: {title} S{season_number:02d}")
                return cached_result.get("season_name")
        
        # 第1步: 搜索所有元数据源
        candidates = await self.search_all_metadata_sources(title, year, "tv", sources, user)
        if not candidates:
            self.logger.info(f"未找到任何元数据: {title}")
            return None
        
        # 第2步: 使用AI选择最佳匹配(如果有AI匹配器)
        selected_candidate = None
        if ai_matcher and len(candidates) > 1:
            try:
                # 转换为字典格式供AI使用
                candidates_dict = [c.model_dump() for c in candidates]
                selected_index = await ai_matcher.select_metadata_result(
                    title,
                    year,
                    candidates_dict,
                    season=season_number,
                    custom_prompt=custom_prompt
                )

                if selected_index is not None and 0 <= selected_index < len(candidates):
                    selected_candidate = candidates[selected_index]
                    self.logger.info(f"AI选择元数据: {selected_candidate.source}:{selected_candidate.id}")
            except Exception as e:
                self.logger.warning(f"AI选择元数据失败: {e}, 使用第一个结果")
        
        # 如果AI未选择或只有一个候选,使用第一个
        if not selected_candidate:
            selected_candidate = candidates[0]
            self.logger.info(f"使用第一个元数据结果: {selected_candidate.source}:{selected_candidate.id}")
        
        # 第3步: 获取季度列表
        seasons = await self.get_seasons_from_source(
            selected_candidate.source,
            selected_candidate.id,
            "tv"
        )
        if not seasons:
            return None
        
        # 第4步: 找到对应季度
        target_season = None
        for season in seasons:
            if season.season_number == season_number:
                target_season = season
                break
        
        if not target_season or not target_season.name:
            return None
        
        # 缓存结果(7天)
        async with self._session_factory() as session:
            await crud.set_cache(
                session,
                cache_key,
                {"season_name": target_season.name, "source": selected_candidate.source},
                ttl_seconds=604800,  # 7天
                provider=selected_candidate.source
            )
        
        self.logger.info(f"获取季度名称成功: {title} S{season_number:02d} → {target_season.name} (来源: {selected_candidate.source})")
        return target_season.name

    async def search_all_metadata_sources(
        self,
        title: str,
        year: Optional[int] = None,
        media_type: str = "tv",
        sources: Optional[List[str]] = None,
        user: Optional[models.User] = None
    ) -> List[MetadataSearchCandidate]:
        """
        搜索所有元数据源,返回候选列表

        Args:
            title: 搜索标题
            year: 年份(可选)
            media_type: 媒体类型 ('tv' or 'movie')
            sources: 要搜索的源列表,None表示搜索所有启用的源
            user: 用户对象

        Returns:
            候选结果列表
        """
        if not user:
            # 创建一个临时用户对象用于API调用
            user = models.User(id=0, username="system", isAdmin=True)

        # 确定要搜索的源
        if sources is None:
            sources = ["tmdb"]  # 默认只搜索TMDB,后续可扩展

        all_candidates = []

        # 并发搜索所有源
        for source_name in sources:
            try:
                source = self.metadata_manager.get_source(source_name)
                if not source:
                    self.logger.warning(f"元数据源 '{source_name}' 未找到")
                    continue

                # 调用源的搜索方法
                search_results = await source.search(title, user, mediaType=media_type)

                if not search_results:
                    self.logger.info(f"{source_name} 搜索无结果: {title}")
                    continue

                # 转换为通用格式
                for result in search_results[:10]:  # 每个源最多取10个结果
                    candidate = MetadataSearchCandidate(
                        source=source_name,
                        id=result.tmdbId or result.id,
                        title=result.title,
                        original_title=getattr(result, 'originalTitle', None),
                        year=result.year,
                        media_type=media_type,
                        overview=getattr(result, 'overview', None)
                    )
                    all_candidates.append(candidate)

                self.logger.info(f"{source_name} 搜索成功: {title}, 找到 {len(search_results)} 个结果")

            except Exception as e:
                self.logger.error(f"{source_name} 搜索失败: {title}, 错误: {e}")
                continue

        return all_candidates

    async def get_seasons_from_source(
        self,
        source: str,
        id: str,
        media_type: str = "tv"
    ) -> List[SeasonInfo]:
        """
        从指定元数据源获取季度信息

        Args:
            source: 元数据源名称 ('tmdb', 'tvdb', etc.)
            id: 源的ID
            media_type: 媒体类型 ('tv' or 'movie')

        Returns:
            季度信息列表
        """
        if media_type != "tv":
            return []

        # 检查缓存
        cache_key = f"{source}_seasons_{id}"
        async with self._session_factory() as session:
            cached_result = await crud.get_cache(session, cache_key)
            if cached_result:
                self.logger.info(f"{source} 季度信息缓存命中: {id}")
                return [SeasonInfo(**s) for s in cached_result]

        # 根据源类型调用不同的API
        try:
            if source == "tmdb":
                seasons = await self._get_tmdb_seasons(id)
            # 后续可扩展其他源
            # elif source == "tvdb":
            #     seasons = await self._get_tvdb_seasons(id)
            else:
                self.logger.warning(f"不支持的元数据源: {source}")
                return []

            # 缓存结果(30天)
            async with self._session_factory() as session:
                await crud.set_cache(
                    session,
                    cache_key,
                    [s.model_dump() for s in seasons],
                    ttl_seconds=2592000,  # 30天
                    provider=source
                )

            self.logger.info(f"获取{source}季度信息成功: {id}, 共{len(seasons)}季")
            return seasons

        except Exception as e:
            self.logger.error(f"获取{source}季度信息失败: {id}, 错误: {e}")
            return []

    async def _get_tmdb_seasons(self, tmdb_id: str) -> List[SeasonInfo]:
        """获取TMDB季度信息(内部方法)"""
        tmdb_source = self.metadata_manager.get_source("tmdb")
        # 直接调用TMDB API获取季度信息
        async with await tmdb_source._create_client() as client:
            response = await client.get(f"/tv/{tmdb_id}")
            response.raise_for_status()
            data = response.json()

            seasons_data = data.get("seasons", [])
            seasons = []

            for season_data in seasons_data:
                # 跳过特别篇(season 0)
                season_number = season_data.get("season_number", 0)
                if season_number == 0:
                    continue

                # 获取季度别名
                season_aliases = await self._get_season_aliases(
                    tmdb_id, season_number, season_data.get("name", "")
                )

                seasons.append(SeasonInfo(
                    season_number=season_number,
                    name=season_data.get("name"),
                    episode_count=season_data.get("episode_count", 0),
                    air_date=season_data.get("air_date"),
                    overview=season_data.get("overview"),
                    aliases=season_aliases
                ))

            return seasons

    async def _get_season_aliases(
        self,
        tmdb_id: str,
        season_number: int,
        season_name: str
    ) -> List[str]:
        """
        获取季度别名，包括常见的中英文表达

        Args:
            tmdb_id: TMDB ID
            season_number: 季度号
            season_name: 季度名称

        Returns:
            季度别名列表
        """
        aliases = set()

        # 添加原始名称
        if season_name:
            aliases.add(season_name)

        # 基于季度号生成常见别名
        season_aliases_map = {
            1: ["第一季", "第1季", "Season 1", "S1", "第一部", "Part 1"],
            2: ["第二季", "第2季", "Season 2", "S2", "第二部", "Part 2", "II", "ⅱ"],
            3: ["第三季", "第3季", "Season 3", "S3", "第三部", "Part 3", "III", "ⅲ"],
            4: ["第四季", "第4季", "Season 4", "S4", "第四部", "Part 4", "IV", "ⅳ"],
            5: ["第五季", "第5季", "Season 5", "S5", "第五部", "Part 5", "V", "ⅴ"],
            6: ["第六季", "第6季", "Season 6", "S6", "第六部", "Part 6", "VI", "ⅵ"],
        }

        # 添加基于季度号的别名
        if season_number in season_aliases_map:
            aliases.update(season_aliases_map[season_number])

        # 基于季度名称生成特殊别名（移除硬编码，让TMDB数据自己说话）
        if season_name:
            # 自动从季度名称中提取关键词作为别名
            import re

            # 提取季度名称中的独特部分（去除通用前缀）
            cleaned_name = re.sub(r'^(第?\d+季|Season\s*\d+|S\d+|[A-Z]+)\s*', '', season_name.strip())
            if cleaned_name:
                # 添加清理后的名称作为别名
                aliases.add(cleaned_name)

                # 添加完整季度名称的变体
                base_name = re.sub(r'^(鬼灭之刃|刀剑神域|进击的巨人|Demon Slayer|Sword Art Online|Attack on Titan)\s*', '', cleaned_name.strip())
                if base_name and base_name != cleaned_name:
                    aliases.add(base_name)

        return list(aliases)


async def ai_season_mapping_and_correction(
    search_title: str,
    search_results: list,
    metadata_manager,
    ai_matcher,
    logger,
    similarity_threshold: float = 60.0,
    prefetched_metadata_results: list = None,  # 🚀 V2.1.6: 预取的元数据结果
    metadata_source: str = "tmdb"  # 元数据源
) -> list:
    """
    AI季度映射与修正函数 - V2.1.6优化版本

    Args:
        search_title: 标准化的搜索标题
        search_results: 搜索结果列表
        metadata_manager: 元数据管理器
        ai_matcher: AI匹配器
        logger: 日志记录器
        similarity_threshold: 相似度阈值
        prefetched_metadata_results: 预取的元数据搜索结果（用于并行优化）
        metadata_source: 元数据源名称 (默认: tmdb)

    Returns:
        list: 修正结果列表
    """
    try:
        # 1. 使用预取的元数据结果或重新查询
        if prefetched_metadata_results:
            metadata_results = prefetched_metadata_results
            logger.info(f"✓ AI季度映射: 使用预取的[{metadata_source}]结果 ({len(metadata_results)} 个)")
        else:
            metadata_results = await _get_cached_metadata_search(search_title, metadata_manager, logger, metadata_source)

        if not metadata_results:
            logger.info(f"○ AI季度映射: 未找到 '{search_title}' 的[{metadata_source}]信息")
            return []

        # 2. 如果返回多个结果，使用AI选择最佳匹配
        if len(metadata_results) == 1:
            best_match = metadata_results[0]
            logger.info(f"○ AI季度映射: 唯一[{metadata_source}]匹配: {best_match.title} (类型: {best_match.type})")
        else:
            # 多个结果时，AI选择最佳匹配
            try:
                provider_results = []
                for r in metadata_results:
                    provider_results.append(models.ProviderSearchInfo(
                        provider=metadata_source,
                        mediaId=r.tmdbId or r.id,
                        title=r.title,
                        type=r.type or "unknown",
                        season=1,
                        year=r.year,
                        imageUrl=r.imageUrl,
                        episodeCount=None
                    ))

                query_info = {
                    "title": search_title,
                    "season": None,
                    "episode": None,
                    "year": None,
                    "type": None
                }

                selected_index = await ai_matcher.select_best_match(
                    query_info, provider_results, {}
                )

                if selected_index is not None and 0 <= selected_index < len(metadata_results):
                    best_match = metadata_results[selected_index]
                    logger.info(f"✓ AI季度映射: AI选择[{metadata_source}]匹配: {best_match.title} (类型: {best_match.type}, ID: {best_match.id})")
                else:
                    logger.error(f"⚠ AI季度映射: AI选择匹配失败，使用第一个结果")
                    best_match = metadata_results[0]

            except Exception as e:
                logger.error(f"⚠ AI季度映射: 匹配选择失败，使用第一个结果: {e}")
                best_match = metadata_results[0]

        # 3. 获取季度信息用于后续修正
        # 确保选择的是TV类型，否则无法获取季度信息
        if best_match.type != 'tv':
            logger.warning(f"⚠ AI季度映射: 选择的结果不是TV类型 ({best_match.type})，无法获取季度信息")
            # 尝试找到TV类型的结果
            tv_result = None
            for result in metadata_results:
                if result.type == 'tv':
                    tv_result = result
                    logger.info(f"✓ AI季度映射: 找到TV类型结果: {tv_result.title} (ID: {tv_result.id})")
                    break

            if not tv_result:
                logger.error(f"⚠ AI季度映射: 结果中没有TV类型，无法获取季度信息")
                return []

            best_match = tv_result

        try:
            seasons_info = await metadata_manager.get_seasons(metadata_source, best_match.id)
        except Exception as e:
            logger.error(f"获取[{metadata_source}]季度信息失败: {best_match.id}, 错误: {e}")
            return []

        if not seasons_info or len(seasons_info) <= 1:
            logger.info(f"○ AI季度映射: '{search_title}' 只有1个季度或无季度信息，跳过")
            return []

        # 聚合日志收集
        log_lines = []
        log_lines.append(f"✓ AI季度映射: 获取到 '{search_title}' 的[{metadata_source}]季度信息，共 {len(seasons_info)} 个季度")
        for season in seasons_info:
            season_name = season.name or f"第{season.season_number}季"
            log_lines.append(f"  - 第{season.season_number}季: {season_name}")

        # 4. 对所有搜索结果进行季度修正
        tv_results = [item for item in search_results if item.type == 'tv_series']
        if not tv_results:
            log_lines.append(f"○ AI季度映射: 没有TV结果需要修正")
            logger.info("\n".join(log_lines))
            return []

        log_lines.append(f"○ 开始季度修正，检查 {len(tv_results)} 个TV结果...")

        # 调试：打印季度信息详情
        log_lines.append(f"🔍 [{metadata_source}]季度信息详情:")
        for season in seasons_info:
            aliases_str = ', '.join(season.aliases[:5]) if season.aliases else '无'
            log_lines.append(f"  S{season.season_number}: {season.name} (别名: {aliases_str})")

        # 5. V2.1.6增强方案：算法优先 + 别名等价匹配
        corrected_results = []

        # 构建标题别名等价映射
        title_alias_mapping = _build_title_alias_equivalence_map(tv_results, seasons_info, logger)

        # 获取基础标题（用于外传检测）
        base_title = search_title

        for item in tv_results:
            item_title = item.title
            best_season = item.season or 1
            best_confidence = 0.0
            best_season_name = ""
            best_method = "原始"

            # V2.1.6新增: 外传/衍生作品检测 - 跳过外传作品的季度映射
            if is_spinoff_title(item_title, base_title):
                logger.debug(f"  ○ 跳过外传作品: '{item_title}' (保持原季度 S{best_season})")
                continue

            logger.debug(f"  ○ 检查 '{item_title}' 的季度匹配...")

            # V2.1.7新增: 标题中明确季度信息保护
            # 如果标题已明确包含"第N季"等信息，且与当前season一致，则跳过修正
            explicit_season = _extract_explicit_season_from_title(item_title)
            if explicit_season is not None and explicit_season == item.season:
                logger.debug(f"  ○ 标题已明确包含季度信息: '{item_title}' → S{explicit_season}，跳过修正")
                continue

            # 策略1: 别名等价匹配 (最快)
            equivalent_info = title_alias_mapping.get(item_title)
            if equivalent_info:
                best_season = equivalent_info['season']
                best_confidence = 98.0  # 别名等价给予高置信度
                best_season_name = equivalent_info['name']
                best_method = "别名等价"
                logger.debug(f"    🎯 别名等价匹配: S{best_season} ({best_season_name})")
            else:
                # 策略2: V2.1.6算法相似度匹配
                for season in seasons_info:
                    season_num = season.season_number
                    season_name = season.name or f"第{season_num}季"
                    season_aliases = season.aliases or []

                    # V2.1.6：使用相似度计算
                    confidence = _calculate_season_similarity(
                        item_title,
                        season_name,
                        season_aliases
                    )

                    logger.debug(f"    - S{season_num} ({season_name}): 相似度 {confidence:.1f}%")

                    # 更新最佳匹配
                    if confidence > best_confidence and confidence >= similarity_threshold:
                        best_season = season_num
                        best_confidence = confidence
                        best_season_name = season_name
                        best_method = "算法相似度"

                # 策略3: AI辅助 (仅当算法置信度在模糊区间 60-75% 时)
                if similarity_threshold <= best_confidence < 75 and ai_matcher:
                    try:
                        # 构建候选列表供AI选择
                        candidates = [
                            {"season": s.season_number, "name": s.name or f"第{s.season_number}季"}
                            for s in seasons_info
                        ]
                        ai_result = await ai_matcher.select_best_season_for_title(
                            item_title, candidates
                        )
                        if ai_result and ai_result.get('confidence', 0) > best_confidence:
                            best_season = ai_result['season']
                            best_confidence = ai_result['confidence']
                            best_season_name = ai_result.get('name', f"第{best_season}季")
                            best_method = "AI辅助"
                            logger.debug(f"    🤖 AI辅助确认: S{best_season} ({best_season_name})")
                    except Exception as e:
                        logger.debug(f"    AI辅助跳过: {e}")

            # 记录最终选择
            if best_confidence >= similarity_threshold and item.season != best_season:
                correction = {
                    'item': item,
                    'original_season': item.season,
                    'corrected_season': best_season,
                    'confidence': best_confidence,
                    'tmdb_season_name': best_season_name,
                    'method': best_method
                }
                corrected_results.append(correction)
                log_lines.append(f"  ✓ {best_method}修正: '{item_title}' S{item.season or '?'} → S{best_season} ({best_season_name}) (置信度: {best_confidence:.1f}%)")
            elif best_confidence >= similarity_threshold:
                logger.debug(f"  ○ 无需修正: '{item_title}' 已是正确季度 S{best_season} ({best_method}, 置信度: {best_confidence:.1f}%)")
            else:
                logger.debug(f"  ○ 相似度不足: '{item_title}' 保持原季度 S{item.season or '?'} (最高相似度: {best_confidence:.1f}% < {similarity_threshold}%)")

        log_lines.append(f"✓ 季度映射完成: 修正了 {len(corrected_results)} 个结果的季度信息")
        # 聚合式打印所有日志
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
    prefetched_metadata_results: list = None,  # 🚀 V2.1.6: 预取的元数据结果
    metadata_source: str = "tmdb"  # 元数据源
) -> dict:
    """
    统一的AI类型和季度映射与修正函数

    Args:
        search_title: 标准化的搜索标题
        search_results: 搜索结果列表
        metadata_manager: 元数据管理器
        ai_matcher: AI匹配器
        logger: 日志记录器
        similarity_threshold: 相似度阈值
        prefetched_metadata_results: 预取的元数据搜索结果（可选，用于并行优化）
        metadata_source: 元数据源名称 (默认: tmdb)

    Returns:
        dict: 包含类型修正和季度修正的结果
    """
    try:
        # 聚合日志收集
        unified_log_lines = []
        unified_log_lines.append(f"○ 开始统一AI映射修正: '{search_title}' ({len(search_results)} 个结果)")

        # 初始化结果
        type_corrections = []
        season_corrections = []

        # 1. 类型修正（目前保持原类型）
        unified_log_lines.append(f"○ 开始类型修正...")
        for item in search_results:
            original_type = item.type
            corrected_type = original_type
            if original_type != corrected_type:
                type_corrections.append({
                    'item': item,
                    'original_type': original_type,
                    'corrected_type': corrected_type
                })
                item.type = corrected_type
                unified_log_lines.append(f"  ✓ 类型修正: '{item.title}' {original_type} → {corrected_type}")

        unified_log_lines.append(f"✓ 类型修正完成: 修正了 {len(type_corrections)} 个结果的类型信息")

        # 2. 季度修正（只对电视剧进行）
        tv_results = [item for item in search_results if item.type == 'tv_series']
        if tv_results:
            season_corrections = await ai_season_mapping_and_correction(
                search_title=search_title,
                search_results=search_results,
                metadata_manager=metadata_manager,
                ai_matcher=ai_matcher,
                logger=logger,
                similarity_threshold=similarity_threshold,
                prefetched_metadata_results=prefetched_metadata_results,
                metadata_source=metadata_source
            )

            # 应用季度修正到原始搜索结果
            for correction in season_corrections:
                item = correction['item']
                item.season = correction['corrected_season']
                unified_log_lines.append(f"  ✓ 季度修正应用: '{item.title}' → S{item.season}")

        # 3. 构建修正后的结果列表
        corrected_results = search_results.copy()

        total_corrections = len(type_corrections) + len(season_corrections)
        unified_log_lines.append(f"✓ 统一AI映射修正完成: 类型修正 {len(type_corrections)} 个, 季度修正 {len(season_corrections)} 个, 总计 {total_corrections} 个")

        # 聚合式打印所有日志
        logger.info("\n".join(unified_log_lines))

        return {
            'type_corrections': type_corrections,
            'season_corrections': season_corrections,
            'total_corrections': total_corrections,
            'corrected_results': corrected_results
        }

    except Exception as e:
        logger.warning(f"统一AI映射修正失败: {e}")
        return {
            'type_corrections': [],
            'season_corrections': [],
            'total_corrections': 0,
            'corrected_results': search_results
        }


async def _get_cached_metadata_search(
    search_title: str,
    metadata_manager,
    logger,
    source: str = "tmdb"  # 支持其他元数据源
) -> List[models.MetadataDetailsResponse]:
    """
    获取元数据搜索结果，带6小时缓存

    Args:
        search_title: 搜索标题
        metadata_manager: 元数据管理器
        logger: 日志记录器
        source: 元数据源名称 (默认: tmdb，可选: bangumi 等)

    Returns:
        List[models.MetadataDetailsResponse]: 元数据搜索结果
    """

    # 生成缓存键（包含源名称）
    cache_key = f"{source}_search_{hashlib.md5(search_title.encode('utf-8')).hexdigest()}"

    # 检查缓存
    async with metadata_manager._session_factory() as session:
        cached_result = await crud.get_cache(session, cache_key)
        if cached_result:
            logger.info(f"[{source}] 搜索缓存命中: {search_title}")
            return [models.MetadataDetailsResponse(**r) for r in cached_result]

    # 缓存未命中，执行搜索
    logger.debug(f"[{source}] 搜索缓存未命中，执行搜索: {search_title}")
    try:
        results = await metadata_manager.search(source, search_title, None, mediaType='multi')

        # 缓存结果（6小时 = 21600秒）
        async with metadata_manager._session_factory() as session:
            await crud.set_cache(
                session,
                cache_key,
                [r.model_dump() for r in results],
                ttl_seconds=21600,  # 6小时
                provider=source
            )
            logger.info(f"[{source}] 搜索结果已缓存: {search_title} (6小时)")

        return results
    except Exception as e:
        logger.error(f"[{source}] 搜索失败: {search_title}, 错误: {e}")
        return []


# 保持向后兼容的别名
_get_cached_tmdb_search = _get_cached_metadata_search




