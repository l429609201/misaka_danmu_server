"""Control API 通用模型；使用包内普通导入，避免依赖命令执行目录。"""

from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from src.schemas.search import ProviderSearchInfo, ProviderEpisodeInfo
from src.schemas.metadata import MetadataDetailsResponse
from src.schemas.import_schemas import AutoImportMediaType, AutoImportSearchType, ControlAutoImportRequest


class ControlActionResponse(BaseModel):
    """通用操作成功响应模型"""
    status: str = "success"
    message: str
    animeId: Optional[int] = None
    sourceId: Optional[int] = None


class ControlTaskResponse(BaseModel):
    """任务提交成功响应模型"""
    status: str = "success"
    message: str
    taskId: str


class ExecutionTaskResponse(BaseModel):
    """用于返回执行任务ID的响应模型"""
    schedulerTaskId: str
    executionTaskId: Optional[str] = None
    status: Optional[str] = Field(None, description="执行任务状态: 运行中/已完成/失败/已取消/等待中/已暂停")


class ControlSearchResultItem(ProviderSearchInfo):
    """搜索结果项，包含结果索引"""
    resultIndex: int = Field(..., alias="result_index", description="结果在列表中的顺序索引，从0开始")

    class Config:
        populate_by_name = True


class ControlSearchResponse(BaseModel):
    """搜索响应模型"""
    searchId: str = Field(..., description="本次搜索操作的唯一ID，用于后续操作")
    results: List[ControlSearchResultItem] = Field(..., description="搜索结果列表")


class ControlDirectImportRequest(BaseModel):
    """直接导入请求模型"""
    searchId: str = Field(..., description="来自搜索响应的searchId")
    resultIndex: int = Field(..., alias="result_index", ge=0, description="要导入的结果的索引 (从0开始)")
    tmdbId: Optional[str] = None
    tvdbId: Optional[str] = None
    bangumiId: Optional[str] = None
    imdbId: Optional[str] = None
    doubanId: Optional[str] = None

    class Config:
        populate_by_name = True


class ControlAnimeCreateRequest(BaseModel):
    """用于外部API自定义创建影视条目的请求模型"""
    title: str = Field(..., description="作品主标题")
    type: AutoImportMediaType = Field(..., description="媒体类型")
    season: Optional[int] = Field(None, description="季度号 (tv_series 类型必需)")
    year: Optional[int] = Field(None, description="年份")
    nameEn: Optional[str] = Field(None, description="英文标题")
    nameJp: Optional[str] = Field(None, description="日文标题")
    nameRomaji: Optional[str] = Field(None, description="罗马音标题")
    aliasCn1: Optional[str] = Field(None, description="中文别名1")
    aliasCn2: Optional[str] = Field(None, description="中文别名2")
    aliasCn3: Optional[str] = Field(None, description="中文别名3")
    tmdbId: Optional[str] = None
    tvdbId: Optional[str] = None
    bangumiId: Optional[str] = None
    imdbId: Optional[str] = None
    doubanId: Optional[str] = None

    @model_validator(mode='after')
    def check_season_for_tv_series(self) -> "ControlAnimeCreateRequest":
        """保持电视剧创建时必须提供季度的校验规则。"""
        if self.type == 'tv_series' and self.season is None:
            raise ValueError('对于电视节目 (tv_series)，季度 (season) 是必需的。')
        return self


class ControlEditedImportRequest(BaseModel):
    """编辑后导入请求模型"""
    searchId: str = Field(..., description="来自搜索响应的searchId")
    resultIndex: int = Field(..., alias="result_index", ge=0, description="要编辑的结果的索引 (从0开始)")
    title: Optional[str] = Field(None, description="覆盖原始标题")
    episodes: List[ProviderEpisodeInfo] = Field(..., description="编辑后的分集列表")
    tmdbId: Optional[str] = None
    tvdbId: Optional[str] = None
    bangumiId: Optional[str] = None
    imdbId: Optional[str] = None
    doubanId: Optional[str] = None
    tmdbEpisodeGroupId: Optional[str] = Field(None, description="强制指定TMDB剧集组ID")

    class Config:
        populate_by_name = True


class ControlUrlImportRequest(BaseModel):
    """用于外部API通过URL导入到指定源的请求模型"""
    sourceId: int = Field(..., description="要导入到的目标数据源ID")
    episodeIndex: int = Field(..., alias="episode_index", description="要导入的特定集数", gt=0)
    url: str = Field(..., description="包含弹幕的视频页面的URL")
    title: Optional[str] = Field(None, description="（可选）强制指定分集标题")

    class Config:
        populate_by_name = True


class ControlXmlImportRequest(BaseModel):
    """用于外部API通过XML/文本导入到指定源的请求模型"""
    sourceId: int = Field(..., description="要导入到的目标数据源ID")
    episodeIndex: int = Field(..., alias="episode_index", description="要导入的特定集数", gt=0)
    content: str = Field(..., description="XML或纯文本格式的弹幕内容")
    title: Optional[str] = Field(None, description="（可选）强制指定分集标题")

    class Config:
        populate_by_name = True


class DanmakuOutputSettings(BaseModel):
    """弹幕输出设置模型"""
    limitPerSource: int = Field(..., alias="limit_per_source")
    mergeOutputEnabled: bool = Field(..., alias="merge_output_enabled")

    class Config:
        populate_by_name = True


class ControlAnimeDetailsResponse(BaseModel):
    """用于外部API的番剧详情响应模型"""
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

class ControlMetadataSearchResponse(BaseModel):
    """用于外部API的元数据搜索响应模型"""
    results: List[MetadataDetailsResponse]

class ConfigItem(BaseModel):
    """配置项模型"""
    key: str
    value: str
    type: str
    description: str

class ConfigUpdateRequest(BaseModel):
    """配置更新请求模型"""
    key: str
    value: str

class ConfigResponse(BaseModel):
    """配置响应模型"""
    configs: List[ConfigItem]

class HelpResponse(BaseModel):
    """帮助响应模型"""
    available_keys: List[str]
    description: str

class EpisodeGroupSummary(BaseModel):
    """剧集组摘要"""
    groupId: str
    tmdbTvId: int
    episodeCount: int
    groupCount: int
    isLocal: bool
    associatedAnimeIds: List[int] = Field(default_factory=list, description="关联了此剧集组的条目ID列表")

class EpisodeInGroupRequest(BaseModel):
    """创建/更新剧集组时的单集信息"""
    id: int = Field(..., description="分集ID（TMDB episodeId 或合成ID）")
    name: str = Field("", description="分集名称")
    episodeNumber: int = Field(..., description="TMDB 原始集数")
    seasonNumber: int = Field(..., description="TMDB 原始季数")
    order: int = Field(0, description="在组内的排序")

class GroupInGroupRequest(BaseModel):
    """创建/更新剧集组时的分组信息"""
    name: str = Field("", description="分组名称")
    order: int = Field(..., description="分组排序")
    episodes: List[EpisodeInGroupRequest] = Field(..., description="该组下的分集列表")

class EpisodeGroupCreateRequest(BaseModel):
    """创建剧集组请求"""
    tmdbTvId: int = Field(..., description="TMDB TV ID")
    groupId: Optional[str] = Field(None, description="TMDB 原生剧集组ID。不传则自动生成本地剧集组 local-{tmdbTvId}")
    name: str = Field("", description="剧集组名称")
    groups: List[GroupInGroupRequest] = Field(..., description="分组列表")
    animeId: Optional[int] = Field(None, description="关联的条目ID，传入则创建后自动关联")

class EpisodeGroupUpdateRequest(BaseModel):
    """更新剧集组请求"""
    tmdbTvId: int = Field(..., description="TMDB TV ID")
    name: str = Field("", description="剧集组名称")
    groups: List[GroupInGroupRequest] = Field(..., description="分组列表")

class EpisodeGroupAssociateRequest(BaseModel):
    """关联/解关联剧集组与条目"""
    animeId: int = Field(..., description="要关联的条目ID")

class EpisodesWithFilteredResponse(BaseModel):
    """返回保留分集与被黑名单过滤的分集，供编辑导入使用。"""
    episodes: List[ProviderEpisodeInfo] = Field(..., description="保留的分集列表")
    filteredEpisodes: List[ProviderEpisodeInfo] = Field(
        default_factory=list, description="被过滤掉的分集（预告/花絮等）"
    )

class ControlRateLimitProviderStatus(BaseModel):
    """单个源的限流状态"""
    provider: str
    quota_per_minute: int
    quota_per_hour: int
    quota_per_day: int
    used_per_minute: int
    used_per_hour: int
    used_per_day: int
    remaining_per_minute: int
    remaining_per_hour: int
    remaining_per_day: int

class ControlRateLimitStatusResponse(BaseModel):
    """全局限流状态响应"""
    global_quota_per_minute: int
    global_quota_per_hour: int
    global_quota_per_day: int
    global_used_per_minute: int
    global_used_per_hour: int
    global_used_per_day: int
    global_remaining_per_minute: int
    global_remaining_per_hour: int
    global_remaining_per_day: int
    providers: List[ControlRateLimitProviderStatus]

class ScraperConfigItem(BaseModel):
    """单个弹幕源的配置信息"""
    providerName: str = Field(..., description="弹幕源名称")
    isEnabled: bool = Field(..., description="是否启用")
    useProxy: bool = Field(..., description="是否启用代理")
    displayOrder: int = Field(..., description="显示顺序")
    episodeBlacklistRegex: str = Field("", description="分集标题正则黑名单")
    logRawResponses: bool = Field(False, description="是否记录原始响应")
    searchTimeout: int = Field(30, description="搜索超时时间(秒)")
    enrichEnabled: bool = Field(False, description="搜索时是否拉取详情补全缺失字段（年份、集数等）")
    enrichFields: str = Field("", description="待补全字段列表（逗号分隔，为空则继承全局配置）")

class ScraperConfigUpdate(BaseModel):
    """更新单个弹幕源配置的请求体"""
    useProxy: Optional[bool] = Field(None, description="是否启用代理")
    episodeBlacklistRegex: Optional[str] = Field(None, description="分集标题正则黑名单")
    logRawResponses: Optional[bool] = Field(None, description="是否记录原始响应")
    searchTimeout: Optional[int] = Field(None, ge=1, le=120, description="搜索超时时间(秒), 1-120")
    enrichEnabled: Optional[bool] = Field(None, description="搜索时是否拉取详情补全缺失字段")
    enrichFields: Optional[str] = Field(None, description="待补全字段列表（逗号分隔）")

class ControlApiTokenUpdate(BaseModel):
    """更新 API Token 的请求体"""
    name: str = Field(..., min_length=1, max_length=50, description="Token的描述性名称")
    dailyCallLimit: int = Field(..., description="每日调用次数限制, -1 表示无限")
    validityPeriod: str = Field("custom", description="新的有效期: 'permanent', 'custom', '30d' 等。'custom' 表示不改变当前有效期。")