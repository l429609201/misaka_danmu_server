from datetime import datetime
from typing import Any, Dict, List, Optional, Union, Tuple

from pydantic import BaseModel, Field, model_validator

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
    url: Optional[str] = Field(None, description="平台播放页面URL")
    supportsEpisodeUrls: Optional[bool] = Field(None, description="该源是否支持获取分集URL (用于补充源功能)")


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
    # 新增: 补充源信息
    supplementProvider: Optional[str] = Field(None, description="补充源提供商 (如360), 用于获取分集列表")
    supplementMediaId: Optional[str] = Field(None, description="补充源中的媒体ID")

class TMDBSeasonInfo(BaseModel):
    """TMDB季度信息模型"""
    air_date: Optional[str] = Field(None, alias="airDate")
    episode_count: int = Field(..., alias="episodeCount")
    id: int
    name: str
    season_number: int = Field(..., alias="seasonNumber")
    poster_path: Optional[str] = Field(None, alias="posterPath")
    aliases: Optional[List[str]] = Field(default=[], description="季度别名列表")

    class Config:
        populate_by_name = True

class MetadataDetailsResponse(BaseModel):
    """所有元数据源详情接口的统一响应模型。"""
    id: str
    title: str
    type: Optional[str] = None
    tmdbId: Optional[str] = None
    imdbId: Optional[str] = None
    tvdbId: Optional[str] = None
    doubanId: Optional[str] = None
    bangumiId: Optional[str] = None
    nameEn: Optional[str] = None
    nameJp: Optional[str] = None
    nameRomaji: Optional[str] = None
    aliasesCn: List[str] = []
    aliasesJp: List[str] = []  # 新增：日文别名列表
    imageUrl: Optional[str] = None
    details: Optional[str] = None
    year: Optional[int] = None
    supportsEpisodeUrls: Optional[bool] = Field(None, description="该源是否支持获取分集URL (用于补充源功能)")
    seasons: Optional[List[TMDBSeasonInfo]] = Field(None, description="TMDB TV系列的季度信息列表")
    extra: Optional[Dict[str, Any]] = Field(None, description="额外数据,用于存储原始搜索结果等信息")
    provider: Optional[str] = Field(None, description="数据源提供方名称")

class AnimeCreate(BaseModel):
    """Model for creating a new anime entry manually."""
    title: str = Field(..., description="作品标题")
    type: str = Field("tv_series", description="作品类型 (tv_series, movie, ova, other)")
    season: int = Field(1, description="季度")
    year: Optional[int] = Field(None, description="年份")
    imageUrl: Optional[str] = Field(None, description="海报图片URL")


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
    aliasLocked: Optional[bool] = Field(None, description="别名是否锁定")

class EpisodeInfoUpdate(BaseModel):
    """用于更新分集信息的模型"""
    title: str = Field(..., min_length=1, description="新的分集标题")
    episodeIndex: int = Field(..., ge=1, description="新的集数")
    sourceUrl: Optional[str] = Field(None, description="新的官方链接")
    danmakuFilePath: Optional[str] = Field(None, description="弹幕文件路径")

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
    aliasLocked: Optional[bool] = False

class SourceCreate(BaseModel):
    providerName: str = Field(..., description="数据源提供方名称")
    mediaId: str = Field(..., description="在该数据源上的媒体ID")


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
    displayOrder: int


# --- 媒体库（弹幕情况）模型 ---
class LibrarySourceBrief(BaseModel):
    """媒体库列表中的简化源信息，用于快速操作标记和追更。"""
    sourceId: int
    providerName: str
    isFavorited: bool
    incrementalRefreshEnabled: bool
    isFinished: bool = False


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
    sources: List[LibrarySourceBrief] = []  # 简化的源列表，用于快速操作


class LibraryResponse(BaseModel):
    total: int
    list: List[LibraryAnimeInfo]

# --- 分集管理模型 ---
class EpisodeDetail(BaseModel):
    episodeId: int
    title: str
    episodeIndex: int
    sourceUrl: Optional[str] = None
    fetchedAt: Optional[datetime] = None
    commentCount: int
    danmakuFilePath: Optional[str] = None

class PaginatedEpisodesResponse(BaseModel):
    """用于分集列表分页的响应模型"""
    total: int
    list: List[EpisodeDetail]

# --- 任务管理器模型 ---
class TaskInfo(BaseModel):
    taskId: str
    title: str
    status: str
    progress: int
    description: str
    createdAt: datetime
    isSystemTask: bool = False
    queueType: str = "download"  # 队列类型: "download"、"management" 或 "fallback"
    taskType: Optional[str] = None  # 任务类型，不为 None 时表示该任务支持重试

class PaginatedTasksResponse(BaseModel):
    """用于任务列表分页的响应模型"""
    total: int
    list: List[TaskInfo]

# --- API Token 管理模型 ---
class ApiTokenInfo(BaseModel):
    id: int
    name: str
    token: str
    isEnabled: bool
    expiresAt: Optional[datetime] = None
    createdAt: datetime
    dailyCallLimit: int
    dailyCallCount: int

class ApiTokenCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50, description="Token的描述性名称")
    validityPeriod: str = Field("permanent", description="有效期: permanent, 1d, 7d, 30d, 180d, 365d")
    dailyCallLimit: int = Field(500, description="每日调用次数限制, -1 表示无限")

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

class PaginatedCommentResponse(BaseModel):
    """用于UI弹幕列表分页的响应模型"""
    total: int
    list: List[Comment]

class BangumiAuthStatus(BaseModel):
    isAuthenticated: bool
    nickname: Optional[str] = None
    username: Optional[str] = None
    sign: Optional[str] = None
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

class ManualImportRequest(BaseModel):
    """用于手动导入单个分集的请求体模型"""
    title: Optional[str] = None
    episodeIndex: int
    # 使用别名 'sourceUrl' 来兼容前端发送的字段
    url: Optional[str] = Field(None, alias='sourceUrl')
    content: Optional[str] = None

    @model_validator(mode='after')
    def check_url_or_content(self) -> "ManualImportRequest":
        if not self.url and not self.content:
            raise ValueError('必须提供 "url" 或 "content" 字段。')
        return self

class DanmakuOutputSettings(BaseModel):
    limit_per_source: int
    aggregation_enabled: bool


class ExternalApiLogInfo(BaseModel):
    accessTime: datetime
    ipAddress: str
    endpoint: str
    statusCode: int
    message: Optional[str] = None
    requestHeaders: Optional[str] = None
    requestBody: Optional[str] = None
    responseHeaders: Optional[str] = None
    responseBody: Optional[str] = None

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
    statusCode: str = "ok"
    useProxy: bool
    isFailoverEnabled: bool
    logRawResponses: bool = Field(False, alias="log_raw_responses")

class ScraperSettingWithConfig(ScraperSetting):
    configurableFields: Optional[Dict[str, Union[str, Tuple[str, str, str], Dict[str, Any]]]] = None
    isLoggable: bool
    version: Optional[str] = None  # 弹幕源版本号

class ProxySettingsResponse(BaseModel):
    proxyMode: str = "none"  # none, http_socks, accelerate
    proxyProtocol: str = "http"
    proxyHost: Optional[str] = None
    proxyPort: Optional[int] = None
    proxyUsername: Optional[str] = None
    proxyPassword: Optional[str] = None
    proxyEnabled: bool = False  # 保留兼容性
    accelerateProxyUrl: Optional[str] = None

class ReassociationRequest(BaseModel):
    targetAnimeId: int

class ConflictEpisode(BaseModel):
    """冲突分集信息"""
    episodeIndex: int
    sourceEpisodeId: int
    targetEpisodeId: int
    sourceDanmakuCount: int
    targetDanmakuCount: int
    sourceLastFetchTime: Optional[datetime]
    targetLastFetchTime: Optional[datetime]

class ProviderConflict(BaseModel):
    """提供商冲突信息"""
    providerName: str
    sourceSourceId: int
    targetSourceId: int
    conflictEpisodes: List[ConflictEpisode]

class ReassociationConflictResponse(BaseModel):
    """关联冲突检测响应"""
    hasConflict: bool
    conflicts: List[ProviderConflict]

class EpisodeResolution(BaseModel):
    """分集冲突解决方案"""
    episodeIndex: int
    keepSource: bool  # True=保留源分集, False=保留目标分集

class ProviderResolution(BaseModel):
    """提供商冲突解决方案"""
    providerName: str
    episodeResolutions: List[EpisodeResolution]
    sourceOffset: int = 0  # 源番剧集数偏移

class ReassociationResolveRequest(BaseModel):
    """关联冲突解决请求"""
    targetAnimeId: int
    resolutions: List[ProviderResolution]

class EpisodeOffsetRequest(BaseModel):
    episodeIds: List[int]
    offset: int

class BulkDeleteEpisodesRequest(BaseModel):
    episodeIds: List[int]

class BulkDeleteRequest(BaseModel):
    sourceIds: List[int]

class ScheduledTaskCreate(BaseModel):
    name: str
    jobType: str
    cronExpression: str
    isEnabled: bool = True
    taskConfig: dict = {}

class ScheduledTaskUpdate(BaseModel):
    name: str
    cronExpression: str
    isEnabled: bool
    taskConfig: dict = {}

class ScheduledTaskInfo(ScheduledTaskCreate):
    taskId: str
    lastRunAt: Optional[datetime] = None
    nextRunAt: Optional[datetime] = None
    isSystemTask: bool = False

class AvailableJobInfo(BaseModel):
    jobType: str
    name: str
    description: str = ""
    isSystemTask: bool = False
    configSchema: list = []

class ProxySettingsUpdate(BaseModel):
    proxyMode: str = "none"  # none, http_socks, accelerate
    proxyProtocol: str = "http"
    proxyHost: Optional[str] = None
    proxyPort: Optional[Union[int, str]] = None
    proxyUsername: Optional[str] = None
    proxyPassword: Optional[str] = None
    proxyEnabled: bool = False  # 保留兼容性
    proxySslVerify: bool = Field(True, description="是否验证代理服务器的SSL证书")
    accelerateProxyUrl: Optional[str] = None

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


class BatchManualImportItem(BaseModel):
    title: Optional[str] = Field(None, description="分集标题 (可选)")
    episodeIndex: int = Field(..., gt=0, description="集数")
    content: str = Field(..., description="URL或XML文件内容")

class BatchManualImportRequest(BaseModel):
    items: List[BatchManualImportItem]


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


class ControlRateLimitProviderStatus(BaseModel):
    """用于外部API的单个流控规则状态"""
    providerName: str
    directCount: int = Field(0, description="直接下载计数")
    fallbackCount: int = Field(0, description="后备调用计数")
    requestCount: int = Field(0, description="总计数 (directCount + fallbackCount)")
    quota: Union[int, str]

class ControlRateLimitStatusResponse(BaseModel):
    """用于外部API的流控状态响应模型"""
    globalEnabled: bool
    globalRequestCount: int
    globalLimit: int
    globalPeriod: str
    secondsUntilReset: int
    fallbackTotalCount: int = Field(0, description="后备调用总计数")
    fallbackTotalLimit: int = Field(50, description="后备调用总限制")
    fallbackMatchCount: int = Field(0, description="匹配后备计数")
    fallbackSearchCount: int = Field(0, description="后备搜索计数")
    providers: List[ControlRateLimitProviderStatus]


# --- 拆分数据源相关模型 ---

class SplitSourceNewMediaInfo(BaseModel):
    """创建新条目时的媒体信息"""
    title: str = Field(..., description="新条目标题")
    season: int = Field(1, description="季度")
    year: Optional[int] = Field(None, description="年份")
    imageUrl: Optional[str] = Field(None, description="封面图URL")


class SplitSourceRequest(BaseModel):
    """拆分数据源请求模型"""
    sourceId: int = Field(..., description="源数据源ID")
    episodeIds: List[int] = Field(..., description="要拆分的分集ID列表")
    targetType: str = Field(..., description="目标类型: 'new' 创建新条目, 'existing' 合并到已有条目")
    newMediaInfo: Optional[SplitSourceNewMediaInfo] = Field(None, description="新条目信息 (targetType='new' 时必填)")
    existingMediaId: Optional[int] = Field(None, description="已有条目ID (targetType='existing' 时必填)")
    reindexEpisodes: bool = Field(True, description="是否重新编号分集（从1开始）")


class SplitSourceResponse(BaseModel):
    """拆分数据源响应模型"""
    success: bool
    targetMediaId: int = Field(..., description="目标媒体ID（新创建或已有）")
    movedEpisodeCount: int = Field(..., description="移动的分集数量")
    message: str
