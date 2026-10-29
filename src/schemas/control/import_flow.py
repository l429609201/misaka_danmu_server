"""
Control API - 导入和其他相关模型
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator

from ..ui.search import ProviderEpisodeInfo


class ControlUrlImportRequest(BaseModel):
    """URL 导入请求"""
    url: str
    provider: str


class ManualImportRequest(BaseModel):
    """用于手动导入单个分集的请求体模型"""
    title: Optional[str] = None
    episodeIndex: int
    # 使用别名 'sourceUrl' 来兼容前端发送的字段
    url: Optional[str] = Field(None, alias='sourceUrl')
    content: Optional[str] = None
    # 自定义源 URL 导入时，前端解析出的真实平台名（如 'bilibili'），由后端用于 scraper 调用
    urlProvider: Optional[str] = None

    @model_validator(mode='after')
    def check_url_or_content(self):
        if not self.url and not self.content:
            raise ValueError('必须提供 url 或 content 之一')
        return self


class BatchManualImportItem(BaseModel):
    """批量导入的单个分集项"""
    episodeIndex: int
    title: Optional[str] = None
    url: Optional[str] = Field(None, alias='sourceUrl')
    content: Optional[str] = None
    urlProvider: Optional[str] = None


class BatchManualImportRequest(BaseModel):
    """批量手动导入请求"""
    items: List[BatchManualImportItem]


class ExternalApiLogInfo(BaseModel):
    """外部 API 日志信息"""
    id: int
    providerName: str
    requestUrl: str
    requestMethod: str
    responseStatus: int
    responseBody: str
    createdAt: str


class EditImportRequest(BaseModel):
    """编辑后导入请求"""
    title: Optional[str] = None
    doubanId: Optional[str] = None
    tmdbId: Optional[str] = None
    imdbId: Optional[str] = None
    tvdbId: Optional[str] = None
    bangumiId: Optional[str] = None
    tmdbEpisodeGroupId: Optional[str] = None
    episodes: List[ProviderEpisodeInfo]


class RateLimitStatusItem(BaseModel):
    """限流状态项"""
    provider: str
    limit: int
    remaining: int
    resetAt: Optional[str] = None


class RateLimitStatusResponse(BaseModel):
    """限流状态响应"""
    global_status: RateLimitStatusItem
    provider_status: List[RateLimitStatusItem]


class ControlRateLimitProviderStatus(BaseModel):
    """Control API 的限流提供商状态"""
    providerName: str
    qpm: int
    currentUsage: int
    windowStart: str
    windowEnd: str


class ControlRateLimitStatusResponse(BaseModel):
    """Control API 的限流状态响应"""
    providers: List[ControlRateLimitProviderStatus]


class SplitSourceNewMediaInfo(BaseModel):
    """拆分数据源的新媒体信息"""
    title: str
    type: str
    season: int
    year: Optional[int] = None
    tmdbId: Optional[str] = None
    imdbId: Optional[str] = None


class SplitSourceRequest(BaseModel):
    """拆分数据源请求"""
    sourceId: int
    splitPoint: int
    newMediaInfo: SplitSourceNewMediaInfo


class SplitSourceResponse(BaseModel):
    """拆分数据源响应"""
    originalAnimeId: int
    newAnimeId: int
    message: str


__all__ = [
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
