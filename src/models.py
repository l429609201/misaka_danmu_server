from datetime import datetime
from typing import Any, Dict, List, Optional

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

class DanmakuUpdateRequest(BaseModel):
    """用于覆盖弹幕的请求体模型"""
    comments: List[Comment]


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
    mediaId: str = Field(..., description="数据源中的媒体ID (e.g., tencent的cid)")
    animeTitle: str = Field(..., description="要存储在数据库中的番剧标题")
    type: str = Field(..., description="媒体类型, e.g., 'tv_series', 'movie'")
    season: Optional[int] = Field(1, description="季度数，默认为1")
    year: Optional[int] = Field(None, description="发行年份")
    tmdbId: Optional[str] = Field(None, description="关联的TMDB ID (可选)")
    imageUrl: Optional[str] = Field(None, description="封面图片URL")
    doubanId: Optional[str] = None
    bangumiId: Optional[str] = None
    currentEpisodeIndex: Optional[int] = Field(None, description="如果搜索时指定了集数，则只导入此分集")

class MetadataDetailsResponse(BaseModel):
    """所有元数据源详情接口的统一响应模型。"""
    id: str
    title: str
    tmdbId: Optional[str] = None
    imdbId: Optional[str] = None
    tvdbId: Optional[str] = None
    doubanId: Optional[str] = None
    bangumiId: Optional[str] = None
    nameEn: Optional[str] = None
    nameJp: Optional[str] = None
    nameRomaji: Optional[str] = None
    aliasesCn: List[str] = []
    imageUrl: Optional[str] = None
    details: Optional[str] = None

class AnimeDetailUpdate(BaseModel):
    """用于更新番剧详细信息的模型"""
    title: str = Field(..., min_length=1, description="新的影视名称")
    type: str
    season: int = Field(..., ge=0, description="新的季度")
    year: Optional[int] = Field(None, description="发行年份")
    episodeCount: Optional[int] = Field(None, ge=1, description="新的集数")
    imageUrl: Optional[str] = None
    tmdbId: Optional[str] = None
    tmdbEpisodeGroupId: Optional[str] = None
    bangumiId: Optional[str] = None
    tvdbId: Optional[str] = None
    doubanId: Optional[str] = None
    imdbId: Optional[str] = None
    nameEn: Optional[str] = None
    nameJp: Optional[str] = None
    nameRomaji: Optional[str] = None
    aliasCn1: Optional[str] = None
    aliasCn2: Optional[str] = None
    aliasCn3: Optional[str] = None

class EpisodeInfoUpdate(BaseModel):
    """用于更新分集信息的模型"""
    title: str = Field(..., min_length=1, description="新的分集标题")
    episodeIndex: int = Field(..., ge=1, description="新的集数")
    sourceUrl: Optional[str] = Field(None, description="新的官方链接")

class AnimeFullDetails(BaseModel):
    """用于返回番剧完整信息的模型"""
    animeId: int
    title: str
    type: str
    season: int
    year: Optional[int] = None
    episodeCount: Optional[int] = None
    localImagePath: Optional[str] = None
    imageUrl: Optional[str] = None
    tmdbId: Optional[str] = None
    tmdbEpisodeGroupId: Optional[str] = None
    bangumiId: Optional[str] = None
    tvdbId: Optional[str] = None
    doubanId: Optional[str] = None
    imdbId: Optional[str] = None
    nameEn: Optional[str] = None
    nameJp: Optional[str] = None
    nameRomaji: Optional[str] = None
    aliasCn1: Optional[str] = None
    aliasCn2: Optional[str] = None
    aliasCn3: Optional[str] = None

class SourceInfo(BaseModel):
    """代表一个已关联的数据源的详细信息。"""
    sourceId: int
    providerName: str
    mediaId: str
    isFavorited: bool
    incrementalRefreshEnabled: bool
    episodeCount: int
    createdAt: datetime

# --- 爬虫源管理模型 ---
class ScraperSetting(BaseModel):
    providerName: str
    isEnabled: bool
    useProxy: bool
    displayOrder: int

class MetadataSourceSettingUpdate(BaseModel):
    providerName: str
    isAuxSearchEnabled: bool
    useProxy: bool
    displayOrder: int


# --- 媒体库（弹幕情况）模型 ---
class LibraryAnimeInfo(BaseModel):
    """代表媒体库中的一个番剧条目。"""
    animeId: int
    localImagePath: Optional[str] = None
    imageUrl: Optional[str] = None
    title: str
    type: str
    season: int
    year: Optional[int] = None
    episodeCount: int
    sourceCount: int
    createdAt: datetime

class LibraryResponse(BaseModel):
    animes: List[LibraryAnimeInfo]

# --- 分集管理模型 ---
class EpisodeDetail(BaseModel):
    episodeId: int
    title: str
    episodeIndex: int
    sourceUrl: Optional[str] = None
    fetchedAt: Optional[datetime] = None
    commentCount: int

# --- 任务管理器模型 ---
class TaskInfo(BaseModel):
    taskId: str
    title: str
    status: str
    progress: int
    description: str
    createdAt: datetime

# --- API Token 管理模型 ---
class ApiTokenInfo(BaseModel):
    id: int
    name: str
    token: str
    isEnabled: bool
    expiresAt: Optional[datetime] = None
    createdAt: datetime

class ApiTokenCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50, description="Token的描述性名称")
    validityPeriod: str = Field("permanent", description="有效期: permanent, 1d, 7d, 30d, 180d, 365d")

# --- UA Filter Models ---
class UaRule(BaseModel):
    id: int
    uaString: str
    createdAt: datetime

class TokenAccessLog(BaseModel):
    accessTime: datetime
    ipAddress: str
    status: str
    path: Optional[str] = None
    userAgent: Optional[str] = None

# --- 用户和认证模型 ---
class UserBase(BaseModel):
    username: str

class UserCreate(UserBase):
    password: str

class User(UserBase):
    id: int

    class Config:
        from_attributes = True # 允许从ORM对象创建模型

class Token(BaseModel):
    accessToken: str
    tokenType: str

class TokenData(BaseModel):
    username: Optional[str] = None

class PasswordChange(BaseModel):
    oldPassword: str = Field(..., description="当前密码")
    newPassword: str = Field(..., min_length=8, description="新密码 (至少8位)")

class BangumiAuthStatus(BaseModel):
    isAuthenticated: bool
    nickname: Optional[str] = None
    avatarUrl: Optional[str] = None
    bangumiUserId: Optional[int] = None
    authorizedAt: Optional[datetime] = None
    expiresAt: Optional[datetime] = None

class EditedImportRequest(BaseModel):
    """用于编辑后导入的请求体模型"""
    provider: str
    mediaId: str
    animeTitle: str
    mediaType: str
    season: int
    year: Optional[int] = None
    imageUrl: Optional[str] = None
    doubanId: Optional[str] = None
    tmdbId: Optional[str] = None
    imdbId: Optional[str] = None
    tvdbId: Optional[str] = None
    bangumiId: Optional[str] = None
    tmdbEpisodeGroupId: Optional[str] = None
    episodes: List[ProviderEpisodeInfo]

class ControlUrlImportRequest(BaseModel):
    url: str
    provider: str

class DanmakuOutputSettings(BaseModel):
    limit_per_source: int
    aggregation_enabled: bool


class ExternalApiLogInfo(BaseModel):
    accessTime: datetime
    ipAddress: str
    endpoint: str
    statusCode: int
    message: Optional[str] = None

    class Config:
        from_attributes = True

# --- UI API Specific Models ---

class SourceDetailsResponse(BaseModel):
    sourceId: int
    animeId: int
    providerName: str
    mediaId: str
    title: str
    type: str
    season: int
    tmdbId: Optional[str] = None
    bangumiId: Optional[str] = None

class MetadataSourceStatusResponse(BaseModel):
    providerName: str
    isAuxSearchEnabled: bool
    displayOrder: int
    status: str
    useProxy: bool

class ScraperSettingWithConfig(ScraperSetting):
    configurableFields: Optional[Dict[str, str]] = None
    isLoggable: bool
    isVerified: bool

class ProxySettingsResponse(BaseModel):
    proxyProtocol: str
    proxyHost: Optional[str] = None
    proxyPort: Optional[int] = None
    proxyUsername: Optional[str] = None
    proxyPassword: Optional[str] = None
    proxyEnabled: bool

class ReassociationRequest(BaseModel):
    targetAnimeId: int

class BulkDeleteEpisodesRequest(BaseModel):
    episodeIds: List[int]

class BulkDeleteRequest(BaseModel):
    sourceIds: List[int]

class ScheduledTaskCreate(BaseModel):
    name: str
    jobType: str
    cronExpression: str
    isEnabled: bool = True

class ScheduledTaskUpdate(BaseModel):
    name: str
    cronExpression: str
    isEnabled: bool

class ScheduledTaskInfo(ScheduledTaskCreate):
    taskId: str
    lastRunAt: Optional[datetime] = None
    nextRunAt: Optional[datetime] = None

class AvailableJobInfo(BaseModel):
    jobType: str
    name: str

class ProxySettingsUpdate(BaseModel):
    proxyProtocol: str
    proxyHost: Optional[str] = None
    proxyPort: Optional[int] = None
    proxyUsername: Optional[str] = None
    proxyPassword: Optional[str] = None
    proxyEnabled: bool

class UaRuleCreate(BaseModel):
    uaString: str


# --- TMDB API Models ---

class TMDBEpisodeInGroupDetail(BaseModel):
    id: int
    name: str
    episodeNumber: int
    seasonNumber: int
    airDate: Optional[str] = None
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
    episodeCount: int
    groupCount: int
    groups: List[TMDBGroupInGroupDetail]
    network: Optional[Dict[str, Any]] = None
    type: int

class EnrichedTMDBEpisodeInGroupDetail(BaseModel):
    id: int
    name: str # This will be the Chinese name
    episodeNumber: int
    seasonNumber: int
    airDate: Optional[str] = None
    overview: Optional[str] = ""
    order: int
    nameJp: Optional[str] = None
    imageUrl: Optional[str] = None

class EnrichedTMDBGroupInGroupDetail(BaseModel):
    id: str
    name: str
    order: int
    episodes: List[EnrichedTMDBEpisodeInGroupDetail]

class EnrichedTMDBEpisodeGroupDetails(TMDBEpisodeGroupDetails):
    groups: List[EnrichedTMDBGroupInGroupDetail]

# --- Rate Limiter Models ---

class RateLimitStatusItem(BaseModel):
    """单个流控规则的状态"""
    providerName: str
    requestCount: int
    limit: int
    period: str
    periodSeconds: int
    lastResetTime: datetime
    secondsUntilReset: int

    class Config:
        from_attributes = True

class RateLimitStatusResponse(BaseModel):
    """流控状态API的完整响应模型"""
    globalEnabled: bool
    providers: List[RateLimitStatusItem]

# --- Rate Limiter Models ---
