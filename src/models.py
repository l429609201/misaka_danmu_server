from datetime import datetime
from typing import Any, Dict, List, Optional
from datetime import datetime
from pydantic import BaseModel, Field

# Search 模块模型
class AnimeInfo(BaseModel):
    animeId: int = Field(..., description="Anime ID")
    animeTitle: str = Field(..., description="节目名称")
    type: str = Field(..., description="节目类型, e.g., 'tv_series', 'movie'")
    rating: int = Field(0, description="评分 (暂未实现，默认为0)")
    imageUrl: Optional[str] = Field(None, description="封面图片URL (暂未实现)")


class AnimeSearchResponse(BaseModel):
    hasMore: bool = Field(False, description="是否还有更多结果")
    animes: List[AnimeInfo] = Field([], description="番剧列表")


# Match 模块模型
class MatchInfo(BaseModel):
    animeId: int = Field(..., description="Anime ID")
    animeTitle: str = Field(..., description="节目名称")
    episodeId: int = Field(..., description="Episode ID")
    episodeTitle: str = Field(..., description="分集标题")
    type: str = Field(..., description="节目类型")
    shift: float = Field(0.0, description="时间轴偏移(秒)")


class MatchResponse(BaseModel):
    isMatched: bool = Field(False, description="是否成功匹配")
    matches: List[MatchInfo] = Field([], description="匹配结果列表")


# Comment 模块模型
class Comment(BaseModel):
    cid: int = Field(..., description="弹幕ID (数据库主键)")
    p: str = Field(..., description="弹幕参数: time,mode,color,source")
    m: str = Field(..., description="弹幕内容")


class CommentResponse(BaseModel):
    count: int = Field(..., description="弹幕总数")
    comments: List[Comment] = Field([], description="弹幕列表")


# --- 通用 Provider 和 Import 模型 ---
class ProviderSearchInfo(BaseModel):
    """代表来自外部数据源的单个搜索结果。"""
    provider: str = Field(..., description="数据源提供方, e.g., 'tencent', 'bilibili'")
    mediaId: str = Field(..., description="该数据源中的媒体ID (e.g., tencent的cid)")
    title: str = Field(..., description="节目名称")
    type: str = Field(..., description="节目类型, e.g., 'tv_series', 'movie'")
    season: int = Field(1, description="季度, 默认为1")
    year: Optional[int] = Field(None, description="发行年份")
    imageUrl: Optional[str] = Field(None, description="封面图片URL")
    douban_id: Optional[str] = Field(None, description="豆瓣ID (如果可用)")
    episodeCount: Optional[int] = Field(None, description="总集数")
    currentEpisodeIndex: Optional[int] = Field(None, description="如果搜索词指定了集数，则为当前集数")


class ProviderSearchResponse(BaseModel):
    """跨外部数据源搜索的响应模型。"""
    results: List[ProviderSearchInfo] = Field([], description="来自所有数据源的搜索结果列表")


class ProviderEpisodeInfo(BaseModel):
    """代表来自外部数据源的单个分集。"""
    provider: str = Field(..., description="数据源提供方")
    episodeId: str = Field(..., description="该数据源中的分集ID (e.g., tencent的vid)")
    title: str = Field(..., description="分集标题")
    episodeIndex: int = Field(..., description="分集序号")
    url: Optional[str] = Field(None, description="分集原始URL")

class ImportRequest(BaseModel):
    provider: str = Field(..., description="要导入的数据源, e.g., 'tencent'")
    media_id: str = Field(..., description="数据源中的媒体ID (e.g., tencent的cid)")
    anime_title: str = Field(..., description="要存储在数据库中的番剧标题")
    type: str = Field(..., description="媒体类型, e.g., 'tv_series', 'movie'")
    season: Optional[int] = Field(1, description="季度数，默认为1")
    tmdb_id: Optional[str] = Field(None, description="关联的TMDB ID (可选)")
    image_url: Optional[str] = Field(None, description="封面图片URL")
    douban_id: Optional[str] = None
    current_episode_index: Optional[int] = Field(None, description="如果搜索时指定了集数，则只导入此分集")

class AnimeDetailUpdate(BaseModel):
    """用于更新番剧详细信息的模型"""
    title: str = Field(..., min_length=1, description="新的影视名称")
    type: str
    season: int = Field(..., ge=1, description="新的季度")
    episode_count: Optional[int] = Field(None, ge=1, description="新的集数")
    tmdb_id: Optional[str] = None
    tmdb_episode_group_id: Optional[str] = None
    bangumi_id: Optional[str] = None
    tvdb_id: Optional[str] = None
    douban_id: Optional[str] = None
    imdb_id: Optional[str] = None
    name_en: Optional[str] = None
    name_jp: Optional[str] = None
    name_romaji: Optional[str] = None
    alias_cn_1: Optional[str] = None
    alias_cn_2: Optional[str] = None
    alias_cn_3: Optional[str] = None

class EpisodeInfoUpdate(BaseModel):
    """用于更新分集信息的模型"""
    title: str = Field(..., min_length=1, description="新的分集标题")
    episode_index: int = Field(..., ge=1, description="新的集数")
    source_url: Optional[str] = Field(None, description="新的官方链接")

class AnimeFullDetails(BaseModel):
    """用于返回番剧完整信息的模型"""
    anime_id: int
    title: str
    type: str
    season: int
    episode_count: Optional[int] = None
    image_url: Optional[str] = None
    tmdb_id: Optional[str] = None
    tmdb_episode_group_id: Optional[str] = None
    bangumi_id: Optional[str] = None
    tvdb_id: Optional[str] = None
    douban_id: Optional[str] = None
    imdb_id: Optional[str] = None
    name_en: Optional[str] = None
    name_jp: Optional[str] = None
    name_romaji: Optional[str] = None
    alias_cn_1: Optional[str] = None
    alias_cn_2: Optional[str] = None
    alias_cn_3: Optional[str] = None

# --- 爬虫源管理模型 ---
class ScraperSetting(BaseModel):
    provider_name: str
    is_enabled: bool
    display_order: int

class MetadataSourceSettingUpdate(BaseModel):
    provider_name: str
    is_enabled: bool
    is_aux_search_enabled: bool
    display_order: int


# --- 媒体库（弹幕情况）模型 ---
class LibraryAnimeInfo(BaseModel):
    """代表媒体库中的一个番剧条目。"""
    animeId: int
    imageUrl: Optional[str] = None
    title: str
    type: str
    season: int
    episodeCount: int
    sourceCount: int
    createdAt: datetime

class LibraryResponse(BaseModel):
    animes: List[LibraryAnimeInfo]

# --- 分集管理模型 ---
class EpisodeDetail(BaseModel):
    id: int
    title: str
    episode_index: int
    source_url: Optional[str] = None
    fetched_at: Optional[datetime] = None
    comment_count: int

# --- 任务管理器模型 ---
class TaskInfo(BaseModel):
    task_id: str
    title: str
    status: str
    progress: int
    description: str

# --- API Token 管理模型 ---
class ApiTokenInfo(BaseModel):
    id: int
    name: str
    token: str
    is_enabled: bool
    expires_at: Optional[datetime] = None
    created_at: datetime

class ApiTokenCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50, description="Token的描述性名称")
    validity_period: str = Field("permanent", description="有效期: permanent, 1d, 7d, 30d, 180d, 365d")

# --- UA Filter Models ---
class UaRule(BaseModel):
    id: int
    ua_string: str
    created_at: datetime

class TokenAccessLog(BaseModel):
    access_time: datetime
    ip_address: str
    status: str
    path: Optional[str] = None
    user_agent: Optional[str] = None

# --- 用户和认证模型 ---
class UserBase(BaseModel):
    username: str

class UserCreate(UserBase):
    password: str

class User(UserBase):
    id: int

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None

class PasswordChange(BaseModel):
    old_password: str = Field(..., description="当前密码")
    new_password: str = Field(..., min_length=8, description="新密码 (至少8位)")


# --- TMDB API Models ---

class TMDBEpisodeInGroupDetail(BaseModel):
    id: int
    name: str
    episode_number: int
    season_number: int
    air_date: Optional[str] = None
    overview: Optional[str] = ""
    order: int

class TMDBGroupInGroupDetail(BaseModel):
    id: str
    name: str
    order: int
    episodes: List[TMDBEpisodeInGroupDetail]

class TMDBEpisodeGroupDetails(BaseModel):
    id: str
    name: str
    description: Optional[str] = ""
    episode_count: int
    group_count: int
    groups: List[TMDBGroupInGroupDetail]
    network: Optional[Dict[str, Any]] = None
    type: int

class EnrichedTMDBEpisodeInGroupDetail(BaseModel):
    id: int
    name: str # This will be the Chinese name
    episode_number: int
    season_number: int
    air_date: Optional[str] = None
    overview: Optional[str] = ""
    order: int
    name_jp: Optional[str] = None
    image_url: Optional[str] = None

class EnrichedTMDBGroupInGroupDetail(BaseModel):
    id: str
    name: str
    order: int
    episodes: List[EnrichedTMDBEpisodeInGroupDetail]

class EnrichedTMDBEpisodeGroupDetails(TMDBEpisodeGroupDetails):
    groups: List[EnrichedTMDBGroupInGroupDetail]
