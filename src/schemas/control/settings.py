"""
Control API - 设置相关模型
"""
from typing import Any, Dict, Optional, Tuple, Union
from pydantic import BaseModel, ConfigDict, Field

from .source import ScraperSetting


class DanmakuOutputSettings(BaseModel):
    """弹幕输出设置"""
    limit_per_source: int
    aggregation_enabled: bool


class ProxySettingsResponse(BaseModel):
    """代理设置响应"""
    proxyMode: str = "none"  # none, http_socks, accelerate
    proxyProtocol: str = "http"
    proxyHost: Optional[str] = None
    proxyPort: Optional[int] = None
    proxyUsername: Optional[str] = None
    proxyPassword: Optional[str] = None
    proxyEnabled: bool = False  # 保留兼容性
    accelerateProxyUrl: Optional[str] = None


class ProxySettingsUpdate(BaseModel):
    """代理设置更新"""
    proxyMode: str
    proxyProtocol: Optional[str] = "http"
    proxyHost: Optional[str] = None
    proxyPort: Optional[int] = None
    proxyUsername: Optional[str] = None
    proxyPassword: Optional[str] = None
    accelerateProxyUrl: Optional[str] = None


class MetadataSourceStatusResponse(BaseModel):
    """元数据源状态响应"""
    model_config = ConfigDict(extra="allow")

    providerName: str
    isAuxSearchEnabled: bool
    isFailoverEnabled: bool
    displayOrder: int
    status: str
    statusCode: str = "ok"
    useProxy: bool
    logRawResponses: bool = Field(False, alias="log_raw_responses")


class ScraperSettingWithConfig(ScraperSetting):
    """带配置的爬虫设置"""
    configurableFields: Optional[Dict[str, Union[str, Tuple[str, str, str], Dict[str, Any]]]] = None
    isLoggable: bool
    logRawResponses: bool = False
    version: Optional[str] = None  # 弹幕源版本号
    displayName: Optional[str] = None  # UI 友好显示名称，优先于 providerName


class SourceDetailsResponse(BaseModel):
    """数据源详情响应"""
    sourceId: int
    animeId: int
    providerName: str
    mediaId: str
    title: str
    type: str
    season: int
    tmdbId: Optional[str] = None
    bangumiId: Optional[str] = None


__all__ = [
    "DanmakuOutputSettings",
    "ProxySettingsResponse",
    "ProxySettingsUpdate",
    "MetadataSourceStatusResponse",
    "ScraperSettingWithConfig",
    "SourceDetailsResponse",
]
