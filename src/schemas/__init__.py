"""
API Schema 定义模块

Schema 将在后续从现有文件中拆分整理
目前为占位模块

使用方式:
    from src.schemas import CommonResponse, PaginatedResponse
"""

# 占位 - Schema 将在后续整理时添加

"""
API Schema 定义模块

所有 API 请求/响应的 Pydantic 模型定义
按 API 模块分类组织
"""

# DanDan API
from .dandan import (
    AnimeInfo,
    AnimeSearchResponse,
    MatchInfo,
    MatchResponse,
    Comment,
    CommentResponse,
    TaskCommentResponse,
    DanmakuUpdateRequest,
)

# UI API
from .ui import (
    ProviderSearchInfo,
    ProviderSearchResponse,
    ProviderEpisodeInfo,
    ImportRequest,
    TMDBSeasonInfo,
    MetadataDetailsResponse,
)

# Control API
from .control import (
    # anime
    AnimeCreate,
    AnimeDetailUpdate,
    AnimeFullDetails,
    # source
    SourceCreate,
    SourceInfo,
    ScraperSetting,
    MetadataSourceSettingUpdate,
    # library
    LibrarySourceBrief,
    LibraryAnimeInfo,
    LibraryResponse,
    # episode
    EpisodeDetail,
    EpisodeInfoUpdate,
    PaginatedEpisodesResponse,
    EpisodeOffsetRequest,
    BulkDeleteEpisodesRequest,
    BulkDeleteRequest,
    # task
    TaskInfo,
    PaginatedTasksResponse,
    # token
    ApiTokenInfo,
    ApiTokenCreate,
    TokenAccessLog,
    UaRule,
    UaRuleCreate,
    # scheduler
    ScheduledTaskCreate,
    ScheduledTaskUpdate,
    AvailableJobInfo,
    # reassociation
    ReassociationRequest,
    ConflictEpisode,
    ProviderConflict,
    ReassociationConflictResponse,
    EpisodeResolution,
    ProviderResolution,
    ReassociationResolveRequest,
    # settings
    DanmakuOutputSettings,
    ProxySettingsResponse,
    ProxySettingsUpdate,
    MetadataSourceStatusResponse,
    ScraperSettingWithConfig,
    SourceDetailsResponse,
    # import_flow
    ControlUrlImportRequest,
    ManualImportRequest,
    BatchManualImportItem,
    BatchManualImportRequest,
    ExternalApiLogInfo,
    EditImportRequest,
    RateLimitStatusItem,
    RateLimitStatusResponse,
    ControlRateLimitProviderStatus,
    ControlRateLimitStatusResponse,
    SplitSourceNewMediaInfo,
    SplitSourceRequest,
    SplitSourceResponse,
)

# 认证
from .auth import (
    UserBase,
    UserCreate,
    User,
    Token,
    TokenData,
    PasswordChange,
    MfaRequiredResponse,
    TotpSetupResponse,
    TotpVerifyRequest,
    TotpDisableRequest,
    PassKeyInfo,
    PassKeyRegisterRequest,
    PassKeyAuthenticateRequest,
    PassKeyRenameRequest,
    MfaStatusResponse,
    BangumiAuthStatus,
)

# 通用模型
from .common import (
    PaginatedCommentResponse,
    ScheduledTaskInfo,
    TMDBEpisodeInGroupDetail,
    TMDBGroupInGroupDetail,
    TMDBEpisodeGroupDetails,
    EnrichedTMDBEpisodeInGroupDetail,
    EnrichedTMDBGroupInGroupDetail,
    EnrichedTMDBEpisodeGroupDetails,
)

__all__ = [
    # dandan
    "AnimeInfo",
    "AnimeSearchResponse",
    "MatchInfo",
    "MatchResponse",
    "Comment",
    "CommentResponse",
    "TaskCommentResponse",
    "DanmakuUpdateRequest",
    # ui
    "ProviderSearchInfo",
    "ProviderSearchResponse",
    "ProviderEpisodeInfo",
    "ImportRequest",
    "TMDBSeasonInfo",
    "MetadataDetailsResponse",
    # control
    "AnimeCreate",
    "AnimeDetailUpdate",
    "AnimeFullDetails",
    "SourceCreate",
    "SourceInfo",
    "ScraperSetting",
    "MetadataSourceSettingUpdate",
    "LibrarySourceBrief",
    "LibraryAnimeInfo",
    "LibraryResponse",
    "EpisodeDetail",
    "EpisodeInfoUpdate",
    "PaginatedEpisodesResponse",
    "EpisodeOffsetRequest",
    "BulkDeleteEpisodesRequest",
    "BulkDeleteRequest",
    "TaskInfo",
    "PaginatedTasksResponse",
    "ApiTokenInfo",
    "ApiTokenCreate",
    "TokenAccessLog",
    "UaRule",
    "UaRuleCreate",
    "ScheduledTaskCreate",
    "ScheduledTaskUpdate",
    "AvailableJobInfo",
    "ReassociationRequest",
    "ConflictEpisode",
    "ProviderConflict",
    "ReassociationConflictResponse",
    "EpisodeResolution",
    "ProviderResolution",
    "ReassociationResolveRequest",
    "DanmakuOutputSettings",
    "ProxySettingsResponse",
    "ProxySettingsUpdate",
    "MetadataSourceStatusResponse",
    "ScraperSettingWithConfig",
    "SourceDetailsResponse",
    "ControlUrlImportRequest",
    "ManualImportRequest",
    "BatchManualImportItem",
    "BatchManualImportRequest",
    "ExternalApiLogInfo",
    "EditImportRequest",
    "RateLimitStatusItem",
    "RateLimitStatusResponse",
    "ControlRateLimitProviderStatus",
    "ControlRateLimitStatusResponse",
    "SplitSourceNewMediaInfo",
    "SplitSourceRequest",
    "SplitSourceResponse",
    # auth
    "UserBase",
    "UserCreate",
    "User",
    "Token",
    "TokenData",
    "PasswordChange",
    "MfaRequiredResponse",
    "TotpSetupResponse",
    "TotpVerifyRequest",
    "TotpDisableRequest",
    "PassKeyInfo",
    "PassKeyRegisterRequest",
    "PassKeyAuthenticateRequest",
    "PassKeyRenameRequest",
    "MfaStatusResponse",
    "BangumiAuthStatus",
    # common
    "PaginatedCommentResponse",
    "ScheduledTaskInfo",
    "TMDBEpisodeInGroupDetail",
    "TMDBGroupInGroupDetail",
    "TMDBEpisodeGroupDetails",
    "EnrichedTMDBEpisodeInGroupDetail",
    "EnrichedTMDBGroupInGroupDetail",
    "EnrichedTMDBEpisodeGroupDetails",
]


