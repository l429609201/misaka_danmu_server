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
    latency: Optional[float] = None  # in ms
    error: Optional[str] = None


class ProxyTestRequest(BaseModel):
    """代理测试请求"""
    proxy_mode: str = "none"  # none, http_socks, accelerate
    proxy_url: Optional[str] = None  # HTTP/SOCKS 代理 URL
    accelerate_proxy_url: Optional[str] = None  # 加速代理地址


class FullProxyTestResponse(BaseModel):
    """完整代理测试响应"""
    proxy_connectivity: ProxyTestResult
    target_sites: Dict[str, ProxyTestResult]


class TitleRecognitionContent(BaseModel):
    """识别词内容模型"""
    content: str = Field(..., description="识别词配置内容")


class TitleRecognitionUpdateResponse(BaseModel):
    """识别词更新响应模型"""
    success: bool = Field(..., description="是否更新成功")
    warnings: List[str] = Field(default_factory=list, description="解析过程中的警告信息")


class ApiTokenUpdate(BaseModel):
    """API Token更新请求"""
    name: str = Field(..., min_length=1, max_length=50, description="Token的描述性名称")
    dailyCallLimit: int = Field(..., description="每日调用次数限制, -1 表示无限")
    validityPeriod: str = Field(..., description="新的有效期: 'permanent', 'custom', '30d' 等")


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


class ImportFromUrlRequest(BaseModel):
    """从URL导入请求 - 重构后支持动态解析"""
    url: str  # 必填：要导入的URL
    # 以下字段可选，如果不提供则从URL自动解析
    provider: Optional[str] = None  # 可选：指定平台，不指定则自动检测
    title: Optional[str] = None  # 可选：指定标题，不指定则从源获取
    media_type: Optional[str] = None  # 可选：媒体类型
    season: Optional[int] = None  # 可选：季度


class ValidateUrlRequest(BaseModel):
    """URL校验请求"""
    url: str  # 要校验的URL


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


class GlobalFilterSettings(BaseModel):
    """全局过滤设置"""
    cn: str
    eng: str


class RateLimitProviderStatus(BaseModel):
    """流控提供商状态"""
    providerName: str
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
