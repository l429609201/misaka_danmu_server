"""
UI API共享的Pydantic模型
这些模型被多个API端点模块使用
"""

from typing import Optional, List, Dict, Union
from datetime import datetime
from pydantic import BaseModel, Field


class UITaskResponse(BaseModel):
    """后台任务响应"""
    message: str
    taskId: str


class UIProviderSearchResponse(BaseModel):
    """扩展了 ProviderSearchResponse 以包含原始搜索的上下文"""
    results: List[Dict] = Field(default_factory=list, description="主搜索结果列表")
    search_season: Optional[int] = None
    search_episode: Optional[int] = None
    supplemental_results: List[Dict] = Field(default_factory=list, description="来自补充源（如360, Douban）的搜索结果")
    # 分页相关字段
    total: int = Field(0, description="总结果数")
    page: int = Field(1, description="当前页码")
    pageSize: int = Field(10, description="每页数量")
    # 过滤元数据 - 从全量结果中提取，不受分页影响
    available_years: List[int] = Field(default_factory=list, description="所有可用的年份列表")
    available_providers: List[str] = Field(default_factory=list, description="所有可用的来源列表")
    available_types: List[str] = Field(default_factory=list, description="所有可用的类型列表")


class RefreshPosterRequest(BaseModel):
    """刷新海报请求"""
    imageUrl: str


class ReassociationRequest(BaseModel):
    """重新关联请求"""
    targetAnimeId: int


class BulkDeleteEpisodesRequest(BaseModel):
    """批量删除分集请求"""
    episodeIds: List[int] = Field(..., alias="episode_ids")
    deleteFiles: bool = Field(True, description="是否同时删除弹幕XML文件")

    class Config:
        populate_by_name = True


class BulkDeleteRequest(BaseModel):
    """批量删除数据源请求"""
    sourceIds: List[int] = Field(..., alias="source_ids")
    deleteFiles: bool = Field(True, description="是否同时删除弹幕XML文件")

    class Config:
        populate_by_name = True


class ProxyTestResult(BaseModel):
    """代理测试结果"""
    status: str  # 'success' or 'failure'
    latency: Optional[float] = None  # HTTP 连通延迟 (ms)
    error: Optional[str] = None
    # DNS 解析检测结果（新增）
    dns_status: Optional[str] = None  # 'success' | 'failure' | None(未检测)
    dns_latency: Optional[float] = None  # DNS 解析耗时 (ms)
    resolved_ip: Optional[str] = None  # 解析到的首个 IP
    dns_error: Optional[str] = None  # DNS 解析失败原因


class ProxyTestRequest(BaseModel):
    """代理测试请求"""
    proxy_mode: str = "none"  # none, http_socks, accelerate
    proxy_url: Optional[str] = None  # HTTP/SOCKS 代理 URL
    accelerate_proxy_url: Optional[str] = None  # 加速代理地址


class SingleTargetTestRequest(BaseModel):
    """单域名测速 / DNS 解析测试请求"""
    url: str  # 要测试的域名或 URL（如 https://example.com 或 example.com）
    proxy_mode: str = "none"  # none, http_socks, accelerate
    proxy_url: Optional[str] = None
    accelerate_proxy_url: Optional[str] = None
    check_dns: bool = True  # 是否做 DNS 解析检测
    check_http: bool = True  # 是否做 HTTP 连通性检测


class SingleTargetTestResponse(BaseModel):
    """单域名测速 / DNS 解析测试响应"""
    url: str  # 规范化后的测试 URL
    host: str  # 实际解析/请求的主机名
    result: ProxyTestResult  # 复用统一结果结构（含 DNS 与 HTTP）


class FullProxyTestResponse(BaseModel):
    """完整代理测试响应"""
    proxy_connectivity: ProxyTestResult
    target_sites: Dict[str, ProxyTestResult]
    domain_map: Optional[Dict[str, Dict[str, str]]] = None  # domain -> { group, source }


class TitleRecognitionContent(BaseModel):
    """识别词内容模型"""
    content: str = Field(..., description="识别词配置内容")


class TitleRecognitionUpdateResponse(BaseModel):
    """识别词更新响应模型"""
    success: bool = Field(..., description="是否更新成功")
    warnings: List[str] = Field(default_factory=list, description="解析过程中的警告信息")


class TitleRecognitionTestRequest(BaseModel):
    """识别词测试请求"""
    title: str = Field(..., description="要测试的标题")
    season: Optional[int] = Field(1, description="季度")
    episode: Optional[int] = Field(1, description="集数")
    source: Optional[str] = Field(None, description="数据源名称")
    stage: str = Field("all", description="测试阶段: preprocess / postprocess / all")


class TitleRecognitionTestResponse(BaseModel):
    """识别词测试响应"""
    originalTitle: str
    processedTitle: str
    originalSeason: Optional[int] = None
    processedSeason: Optional[int] = None
    originalEpisode: Optional[int] = None
    processedEpisode: Optional[int] = None
    matched: bool = False
    matchedRules: List[str] = Field(default_factory=list, description="命中的规则描述")


class ApiTokenUpdate(BaseModel):
    """API Token更新请求"""
    name: str = Field(..., min_length=1, max_length=50, description="Token的描述性名称")
    dailyCallLimit: int = Field(..., description="每日调用次数限制, -1 表示无限")
    validityPeriod: str = Field(..., description="新的有效期: 'permanent', 'custom', '30d' 等")
    customToken: Optional[str] = Field(None, min_length=5, max_length=100, description="自定义Token字符串，留空则保持不变")


class CustomDanmakuPathRequest(BaseModel):
    """自定义弹幕路径请求"""
    enabled: str
    template: str


class CustomDanmakuPathResponse(BaseModel):
    """自定义弹幕路径响应"""
    enabled: str
    template: str


class MatchFallbackTokensResponse(BaseModel):
    """匹配后备Token响应"""
    value: str


class ConfigValueResponse(BaseModel):
    """配置值响应"""
    value: str


class ConfigValueRequest(BaseModel):
    """配置值请求"""
    value: str


class TmdbReverseLookupConfig(BaseModel):
    """TMDB反查配置"""
    enabled: bool
    sources: List[str]  # 启用反查的源列表，如 ['imdb', 'tvdb', 'douban', 'bangumi']


class TmdbReverseLookupConfigRequest(BaseModel):
    """TMDB反查配置请求"""
    enabled: bool
    sources: List[str]


class EpisodeOffsetPreviewRequest(BaseModel):
    """集数偏移预览请求"""
    animeTitle: str = Field(..., description="番剧标题（用于匹配识别词规则）")
    episodeIndices: List[int] = Field(..., description="要预览偏移的集数列表")

class EpisodeOffsetPreviewResponse(BaseModel):
    """集数偏移预览响应"""
    offsetMap: Dict[int, int] = Field(default_factory=dict, description="偏移映射 {原始集数: 偏移后集数}，只包含有变化的集数")
    hasOffset: bool = Field(False, description="是否存在偏移规则")

class ImportFromUrlRequest(BaseModel):
    """从URL导入请求 - 重构后支持动态解析"""
    url: str  # 必填：要导入的URL
    # 以下字段可选，如果不提供则从URL自动解析
    provider: Optional[str] = None  # 可选：指定平台，不指定则自动检测
    title: Optional[str] = None  # 可选：指定标题，不指定则从源获取
    media_type: Optional[str] = None  # 可选：媒体类型
    season: Optional[int] = None  # 可选：季度
    # B站合集导入：import_mode='collection' 时按合集展开为多集
    import_mode: Optional[str] = None  # 'single'(默认) | 'collection'
    collection_season_id: Optional[str] = None  # 合集 season_id（import_mode=collection 时使用）
    collection_mid: Optional[str] = None  # 合集所属 UP 的 mid


class UrlCollectionInfo(BaseModel):
    """URL 所属合集信息（目前仅 B站 ugc_season）"""
    seasonId: str  # 合集 season_id
    mid: str  # 合集所属 UP 的 mid
    title: Optional[str] = None  # 合集标题
    total: Optional[int] = None  # 合集视频总数（用于前端提示"共 N 个"）


class ValidateUrlRequest(BaseModel):
    """URL校验请求"""
    url: str  # 要校验的URL


class ImportCollectionRequest(BaseModel):
    """自定义源「整个合集」导入请求（目前仅 B站 ugc_season）。

    将合集内全部视频作为「当前自定义源」的分集批量导入：后端拉取合集视频列表，
    构造批量手动导入项（每项一个视频 URL），逐个抓取弹幕写入当前 sourceId。
    """
    url: str  # 合集内任一视频的 URL（后端据此解析合集）
    title: Optional[str] = None  # 合集标题（可选，仅用于任务名展示）
    startEpisodeIndex: Optional[int] = None  # 起始集号（可选，默认 1）


class ValidateUrlResponse(BaseModel):
    """URL校验响应"""
    isValid: bool  # URL是否有效
    provider: Optional[str] = None  # 识别出的平台
    mediaId: Optional[str] = None  # 媒体ID
    title: Optional[str] = None  # 作品标题
    imageUrl: Optional[str] = None  # 封面图URL
    mediaType: Optional[str] = None  # 媒体类型 (movie/tv_series)
    year: Optional[int] = None  # 年份
    episodeIndex: Optional[int] = None  # 集数（如果能从URL解析出来）
    errorMessage: Optional[str] = None  # 错误信息
    collection: Optional[UrlCollectionInfo] = None  # 该视频所属合集信息（仅 B站且属于合集时返回）


class GlobalFilterSettings(BaseModel):
    """全局过滤设置"""
    cn: str
    eng: str



class SingleEpisodeFilterSettings(BaseModel):
    """单剧分集过滤配置"""
    content: str = ""



class GlobalEpisodeTitleFilterSettings(BaseModel):
    """兜底全局分集标题过滤配置"""
    enabled: bool = False
    regex: str = ""


class RegexTestPattern(BaseModel):
    """正则测试条目"""
    label: str = ""
    pattern: str = ""


class RegexTestRequest(BaseModel):
    """正则测试请求"""
    text: str = ""
    patterns: List[RegexTestPattern] = Field(default_factory=list)


class RegexTestMatch(BaseModel):
    """正则测试命中项"""
    label: str = ""
    pattern: str = ""
    matchedText: str = ""


class RegexTestInvalid(BaseModel):
    """无效正则项"""
    label: str = ""
    pattern: str = ""
    error: str = ""


class RegexTestResponse(BaseModel):
    """正则测试响应"""
    matched: bool = False
    matches: List[RegexTestMatch] = Field(default_factory=list)
    invalids: List[RegexTestInvalid] = Field(default_factory=list)

class RateLimitProviderStatus(BaseModel):
    """流控提供商状态"""
    providerName: str
    displayName: Optional[str] = None  # UI 友好显示名称
    requestCount: int
    quota: Union[int, str]  # Can be a number or "∞"


class FallbackRateLimitStatus(BaseModel):
    """后备流控状态"""
    totalCount: int
    totalLimit: int
    matchCount: int
    searchCount: int


class RateLimitStatusResponse(BaseModel):
    """流控状态响应"""
    enabled: bool  # 改为enabled以匹配前端
    verificationFailed: bool = Field(False, description="配置文件验证是否失败")
    globalRequestCount: int
    globalLimit: int
    globalPeriod: str
    secondsUntilReset: int
    providers: List[RateLimitProviderStatus]
    fallback: Optional[FallbackRateLimitStatus] = None


class WebhookSettings(BaseModel):
    """Webhook设置"""
    webhookEnabled: bool
    webhookDelayedImportEnabled: bool
    webhookDelayedImportHours: int
    webhookCustomDomain: str
    webhookFilterMode: str
    webhookFilterRegex: str
    webhookLogRawRequest: bool
    webhookFallbackEnabled: bool
    webhookEnableTmdbSeasonMapping: bool
    webhookDeleteSyncEnabled: bool = False


class WebhookTaskItem(BaseModel):
    """Webhook任务项"""
    id: int
    receptionTime: datetime
    executeTime: datetime
    webhookSource: str
    status: str
    taskTitle: str

    class Config:
        from_attributes = True


class PaginatedWebhookTasksResponse(BaseModel):
    """分页Webhook任务响应"""
    total: int
    list: List[WebhookTaskItem]


class AITestRequest(BaseModel):
    """AI测试请求"""
    provider: str
    apiKey: str
    baseUrl: Optional[str] = None
    model: str


class AITestResponse(BaseModel):
    """AI测试响应"""
    success: bool
    message: str
    latency: Optional[float] = None  # 响应时间(毫秒)
    error: Optional[str] = None


class FileItem(BaseModel):
    """文件/目录项"""
    storage: str = "local"  # 存储类型
    type: str  # 文件类型: dir/file
    path: str  # 完整路径
    name: str  # 文件/目录名
    basename: Optional[str] = None  # 基础名称(不含扩展名)
    extension: Optional[str] = None  # 扩展名
    size: Optional[int] = 0  # 文件大小(字节)
    modify_time: Optional[datetime] = None  # 修改时间

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }




class DuplicateAnimeItem(BaseModel):
    """重复组中的单个条目"""
    animeId: int
    title: str
    season: int
    year: Optional[int] = None
    sourceCount: int = 0
    imageUrl: Optional[str] = None
    localImagePath: Optional[str] = None


class DuplicateGroup(BaseModel):
    """一组重复的条目"""
    tmdbId: str
    season: Optional[int] = None  # 严格模式下有值
    items: List[DuplicateAnimeItem]


class ScanDuplicatesResponse(BaseModel):
    """扫描重复项响应"""
    groups: List[DuplicateGroup]
    totalGroups: int
    totalItems: int


class MergeOperation(BaseModel):
    """单个合并操作"""
    targetAnimeId: int
    sourceAnimeIds: List[int]


class BatchMergeRequest(BaseModel):
    """批量合并请求"""
    operations: List[MergeOperation]


class MergeResultItem(BaseModel):
    """单个合并结果"""
    targetAnimeId: int
    success: bool
    error: Optional[str] = None


class BatchMergeResponse(BaseModel):
    """批量合并响应"""
    results: List[MergeResultItem]
    successCount: int
    failCount: int
