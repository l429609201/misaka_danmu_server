"""
Control API - 数据源管理相关模型
"""
from datetime import datetime
from pydantic import BaseModel, Field


class SourceCreate(BaseModel):
    """创建数据源"""
    providerName: str = Field(..., description="数据源提供方名称")
    mediaId: str = Field(..., description="在该数据源上的媒体ID")


class SourceInfo(BaseModel):
    """代表一个已关联的数据源的详细信息。"""
    sourceId: int
    providerName: str
    mediaId: str
    isFavorited: bool
    incrementalRefreshEnabled: bool
    isFinished: bool = False
    episodeCount: int
    createdAt: datetime


class ScraperSetting(BaseModel):
    """爬虫源设置"""
    providerName: str
    isEnabled: bool
    useProxy: bool
    displayOrder: int


class MetadataSourceSettingUpdate(BaseModel):
    """元数据源设置更新"""
    providerName: str
    isAuxSearchEnabled: bool
    displayOrder: int


__all__ = [
    "SourceCreate",
    "SourceInfo",
    "ScraperSetting",
    "MetadataSourceSettingUpdate",
]
