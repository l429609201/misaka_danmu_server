"""
搜索流程数据契约

定义搜索、匹配、导入流程中的标准化数据结构，用于：
1. 跨层传递（API → Services → Tasks）
2. 任务参数序列化（重启恢复）
3. 结果持久化（缓存、关联）

设计原则：
- 可序列化（不包含 session/scraper/event 等运行时对象）
- 带版本标记（支持契约演进）
- 最小必要字段（YAGNI）
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """搜索请求契约 - 入口层标准化输入"""
    
    # 入口标识
    flow_id: str = Field(..., description="功能流程标识，如 F01/F02...F18")
    source_entry: str = Field(..., description="入口来源，如 ui_search/webhook/control_api/notification")
    
    # 原始输入
    search_term: str = Field(..., description="用户输入或解析的搜索词")
    search_type: str = Field(default="keyword", description="搜索类型: keyword/tmdb/bangumi 等")
    
    # 媒体信息
    media_type: Optional[str] = Field(None, description="tv_series/movie，keyword 搜索时必填")
    target_season: Optional[int] = Field(None, description="目标季度，电视剧必填")
    target_episode: Optional[int] = Field(None, description="目标集数，单集导入时填写")
    
    # 元数据 ID（多源输入）
    tmdb_id: Optional[str] = None
    tvdb_id: Optional[str] = None
    imdb_id: Optional[str] = None
    bangumi_id: Optional[str] = None
    douban_id: Optional[str] = None
    
    # 控制选项
    enable_ai_match: bool = Field(default=True, description="是否启用 AI 匹配")
    enable_fallback: bool = Field(default=False, description="是否允许顺延验证")
    max_candidates: int = Field(default=10, description="最多保留候选数")
    
    # 关联信息
    parent_task_id: Optional[str] = Field(None, description="父任务 ID，如批量导入的管理任务")
    session_id: Optional[str] = Field(None, description="会话 ID，用于缓存关联")


class PreparedSearch(BaseModel):
    """预处理后的搜索参数 - 服务层内部使用"""
    
    # 标题处理结果
    original_title: str = Field(..., description="用户原始输入标题")
    normalized_title: str = Field(..., description="标准化后标题（去空格/全角转半角）")
    search_title: str = Field(..., description="实际用于搜索的标题（应用识别词）")
    
    # 季集处理
    original_season: Optional[int] = None
    original_episode: Optional[int] = None
    search_season: Optional[int] = Field(None, description="搜索用季度（可能经过映射）")
    search_episode: Optional[int] = Field(None, description="搜索用集数（可能经过映射）")
    
    # 规则应用记录
    applied_preprocessing: bool = Field(default=False, description="是否应用了搜索预处理")
    applied_mapping: Optional[Dict[str, Any]] = Field(None, description="应用的映射规则详情")
    preprocessing_metadata: Optional[Dict[str, Any]] = Field(None, description="预处理元数据")
    
    # 别名与关键词
    search_aliases: List[str] = Field(default_factory=list, description="扩展的搜索别名")
    search_keywords: List[str] = Field(default_factory=list, description="实际搜索关键词列表")


class SearchResult(BaseModel):
    """搜索结果契约 - 服务层返回"""
    
    # 候选结果（ScraperSearchResult 的序列化形式）
    candidates: List[Dict[str, Any]] = Field(default_factory=list, description="排名后的候选列表")
    
    # 补充结果
    supplemental_results: List[Dict[str, Any]] = Field(default_factory=list, description="360/其他补充源结果")
    
    # 结果引用
    result_reference_id: Optional[str] = Field(None, description="结果缓存 key，用于后续导入")
    result_version: str = Field(default="v1", description="结果格式版本")
    
    # 诊断信息
    search_sources_count: int = Field(default=0, description="实际搜索的源数量")
    total_results_count: int = Field(default=0, description="原始结果总数")
    filtered_results_count: int = Field(default=0, description="过滤后结果数")
    search_duration_ms: float = Field(default=0.0, description="搜索耗时（毫秒）")


class ImportPlan(BaseModel):
    """导入计划契约 - 下载任务参数"""
    
    # 源标识（单源或候选列表）
    provider: str = Field(..., description="数据源标识")
    media_id: str = Field(..., description="源站媒体 ID")
    fallback_candidates: Optional[List[Dict[str, str]]] = Field(
        None, 
        description="顺延候选列表 [{provider, mediaId}]，仅自动场景且允许顺延时填写"
    )
    
    # 标题与季度
    source_title: str = Field(..., description="源站原始标题")
    storage_title: str = Field(..., description="入库标题（应用后处理）")
    source_season: Optional[int] = Field(None, description="源站季度")
    storage_season: int = Field(default=1, description="存储季度")
    
    # 集数范围
    source_episode_ids: Optional[List[str]] = Field(None, description="源站分集 ID 列表")
    target_episode_indices: Optional[List[int]] = Field(None, description="目标集号列表")
    explicit_episode_list: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="用户编辑的显式分集列表（优先级最高）"
    )
    
    # 元数据与关联
    tmdb_id: Optional[str] = None
    tvdb_id: Optional[str] = None
    imdb_id: Optional[str] = None
    bangumi_id: Optional[str] = None
    image_url: Optional[str] = None
    year: Optional[int] = None

    # 媒体服务器关联（Webhook 场景）
    media_server_type: Optional[str] = Field(None, description="emby/jellyfin/plex")
    media_server_item_id: Optional[str] = Field(None, description="媒体服务器 itemId")

    # 规则处理记录
    applied_storage_postprocessing: bool = Field(default=False, description="是否应用了入库后处理")
    applied_episode_offset: bool = Field(default=False, description="是否应用了集数偏移")
    postprocessing_metadata: Optional[Dict[str, Any]] = Field(None, description="后处理元数据")

    # 计划版本
    plan_version: str = Field(default="v1", description="计划格式版本")
    frozen_at: Optional[float] = Field(None, description="计划冻结时间戳（用于判断过期）")


class FallbackMapping(BaseModel):
    """
    后备匹配映射契约 - DanDanPlay 协议专用（仅用于缓存序列化）

    **重要：本结构仅用于缓存中的JSON序列化，不对应数据库表。**

    真实使用方式（见 comments.py:354-421, match.py:1115-1147, helpers.py:281-320）：
    - 虚拟animeId（900000-999999）：临时分配，存储在 fallback_anime_{virtual_id} 缓存
    - 真实animeId：数据库 Anime.id，通过持久化计数器 lastAllocatedRealAnimeId 分配
    - episodeId（14位编码）：数据库 Episode.id，格式 25{animeId:06d}{sourceOrder:02d}{episode:04d}
    - 映射关系：存储在 fallback_episode_{episodeId} 缓存，包含 real_anime_id/provider/mediaId 等

    缓存过期行为：
    - 映射缓存过期时，要求用户重新匹配/搜索（不能新分配ID冒充旧对象）
    - 真实animeId永不重用（持久化计数器只增不减）
    - 下载成功后创建的数据库记录永久保留，后续走库内匹配
    """

    # 虚拟 ID（DanDanPlay 协议，仅缓存）
    virtual_anime_id: int = Field(..., description="虚拟作品 ID（6位数，900000-999999），仅DanDanPlay协议返回")
    virtual_episode_id: int = Field(..., description="虚拟分集 ID（episodeId），实际是真实编码的14位ID")

    # 真实作品身份（数据库主键）
    real_anime_id: int = Field(..., description="数据库真实 anime.id（持久化计数器分配）")
    real_source_id: int = Field(..., description="数据库 anime_sources.id（下载成功后才创建）")

    # 源站信息
    provider: str = Field(..., description="数据源标识")
    media_id: str = Field(..., description="源站媒体 ID")
    source_episode_id: Optional[str] = Field(None, description="源站分集 ID")

    # 标题与季集
    source_title: str = Field(..., description="源站标题")
    storage_title: str = Field(..., description="入库标题")
    source_season: Optional[int] = None
    storage_season: int = Field(default=1)
    source_episode: Optional[int] = Field(None, description="源站集号")
    storage_episode: int = Field(..., description="存储集号")

    # 映射元数据（仅用于缓存诊断）
    mapping_version: str = Field(default="v1", description="映射格式版本")
    created_by_task_id: Optional[str] = Field(None, description="创建此映射的搜索任务 ID")
    is_movie: bool = Field(default=False, description="是否为电影")

    # 辅助信息
    image_url: Optional[str] = None
    year: Optional[int] = None

    # 缓存控制（不持久化）
    cache_ttl: Optional[int] = Field(None, description="缓存有效期（秒），None 使用默认")


__all__ = [
    "SearchRequest",
    "PreparedSearch",
    "SearchResult",
    "ImportPlan",
    "FallbackMapping",
]
