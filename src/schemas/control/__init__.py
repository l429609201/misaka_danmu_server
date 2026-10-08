"""
Control API 模型
"""
# 使用包内静态导入，消除同名文件冲突及对当前工作目录的依赖。
from .common import (
    ControlActionResponse,
    ControlTaskResponse,
    ExecutionTaskResponse,
    ControlSearchResultItem,
    ControlSearchResponse,
    ControlDirectImportRequest,
    ControlAnimeCreateRequest,
    ControlEditedImportRequest,
    ControlXmlImportRequest,
    ControlAnimeDetailsResponse,
    ControlMetadataSearchResponse,
    ConfigItem,
    ConfigUpdateRequest,
    ConfigResponse,
    HelpResponse,
    EpisodeGroupSummary,
    EpisodeInGroupRequest,
    GroupInGroupRequest,
    EpisodeGroupCreateRequest,
    EpisodeGroupUpdateRequest,
    EpisodeGroupAssociateRequest,
    EpisodesWithFilteredResponse,
    AutoImportSearchType,
    AutoImportMediaType,
    ControlAutoImportRequest,
    ScraperConfigItem,
    ScraperConfigUpdate,
    ControlApiTokenUpdate,
)

from .anime import AnimeCreate, AnimeDetailUpdate, AnimeFullDetails
from .source import SourceCreate, SourceInfo, ScraperSetting, MetadataSourceSettingUpdate
from .library import LibrarySourceBrief, LibraryAnimeInfo, LibraryResponse
from .episode import (
    EpisodeDetail,
    EpisodeInfoUpdate,
    PaginatedEpisodesResponse,
    EpisodeOffsetRequest,
    BulkDeleteEpisodesRequest,
    BulkDeleteRequest,
)
from .task import TaskInfo, PaginatedTasksResponse
from .token import ApiTokenInfo, ApiTokenCreate, TokenAccessLog, UaRule, UaRuleCreate
from .scheduler import ScheduledTaskCreate, ScheduledTaskUpdate, AvailableJobInfo
from .reassociation import (
    ReassociationRequest,
    ConflictEpisode,
    ProviderConflict,
    ReassociationConflictResponse,
    EpisodeResolution,
    ProviderResolution,
    ReassociationResolveRequest,
)
from .settings import (
    DanmakuOutputSettings,
    ProxySettingsResponse,
    ProxySettingsUpdate,
    MetadataSourceStatusResponse,
    ScraperSettingWithConfig,
    SourceDetailsResponse,
)
from .import_flow import (
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

__all__ = [
    # 从 control.py 导入的通用模型
    "ControlActionResponse",
    "ControlTaskResponse",
    "ExecutionTaskResponse",
    "ControlSearchResultItem",
    "ControlSearchResponse",
    "ControlDirectImportRequest",
    "ControlAnimeCreateRequest",
    "ControlEditedImportRequest",
    "ControlXmlImportRequest",
    "ControlAnimeDetailsResponse",
    "ControlMetadataSearchResponse",
    "ConfigItem",
    "ConfigUpdateRequest",
    "ConfigResponse",
    "HelpResponse",
    "EpisodeGroupSummary",
    "EpisodeInGroupRequest",
    "GroupInGroupRequest",
    "EpisodeGroupCreateRequest",
    "EpisodeGroupUpdateRequest",
    "EpisodeGroupAssociateRequest",
    "EpisodesWithFilteredResponse",
    "AutoImportSearchType",
    "AutoImportMediaType",
    "ControlAutoImportRequest",
    "ScraperConfigItem",
    "ScraperConfigUpdate",
    "ControlApiTokenUpdate",
    # anime
    "AnimeCreate",
    "AnimeDetailUpdate",
    "AnimeFullDetails",
    # source
    "SourceCreate",
    "SourceInfo",
    "ScraperSetting",
    "MetadataSourceSettingUpdate",
    # library
    "LibrarySourceBrief",
    "LibraryAnimeInfo",
    "LibraryResponse",
    # episode
    "EpisodeDetail",
    "EpisodeInfoUpdate",
    "PaginatedEpisodesResponse",
    "EpisodeOffsetRequest",
    "BulkDeleteEpisodesRequest",
    "BulkDeleteRequest",
    # task
    "TaskInfo",
    "PaginatedTasksResponse",
    # token
    "ApiTokenInfo",
    "ApiTokenCreate",
    "TokenAccessLog",
    "UaRule",
    "UaRuleCreate",
    # scheduler
    "ScheduledTaskCreate",
    "ScheduledTaskUpdate",
    "AvailableJobInfo",
    # reassociation
    "ReassociationRequest",
    "ConflictEpisode",
    "ProviderConflict",
    "ReassociationConflictResponse",
    "EpisodeResolution",
    "ProviderResolution",
    "ReassociationResolveRequest",
    # settings
    "DanmakuOutputSettings",
    "ProxySettingsResponse",
    "ProxySettingsUpdate",
    "MetadataSourceStatusResponse",
    "ScraperSettingWithConfig",
    "SourceDetailsResponse",
    # import_flow
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
]
