"""
媒体服务器相关的 Pydantic Schema 定义
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


class MediaServerCreate(BaseModel):
    name: str
    providerName: str
    url: str
    apiToken: str
    isEnabled: bool = True
    selectedLibraries: List[str] = []
    filterRules: Dict[str, Any] = {}


class MediaServerUpdate(BaseModel):
    name: Optional[str] = None
    providerName: Optional[str] = None
    url: Optional[str] = None
    apiToken: Optional[str] = None
    isEnabled: Optional[bool] = None
    selectedLibraries: Optional[List[str]] = None
    filterRules: Optional[Dict[str, Any]] = None


class MediaServerResponse(BaseModel):
    id: int
    name: str
    providerName: str
    url: str
    apiToken: str
    isEnabled: bool
    selectedLibraries: List[str]
    filterRules: Dict[str, Any]
    createdAt: datetime
    updatedAt: datetime


class MediaServerTestResponse(BaseModel):
    success: bool
    message: str
    serverInfo: Optional[Dict[str, Any]] = None


class MediaLibraryInfo(BaseModel):
    id: str
    name: str
    type: str


class MediaItemResponse(BaseModel):
    id: int
    serverId: int
    mediaId: str
    libraryId: Optional[str]
    seriesId: Optional[str]
    seasonId: Optional[str]
    episodeId: Optional[str]
    title: str
    mediaType: str
    season: Optional[int]
    episode: Optional[int]
    year: Optional[int]
    tmdbId: Optional[str]
    tvdbId: Optional[str]
    imdbId: Optional[str]
    posterUrl: Optional[str]
    isImported: bool
    createdAt: datetime


class MediaItemUpdate(BaseModel):
    title: Optional[str] = None
    mediaType: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    year: Optional[int] = None
    tmdbId: Optional[str] = None
    tvdbId: Optional[str] = None
    imdbId: Optional[str] = None
    posterUrl: Optional[str] = None


class MediaItemsImportRequest(BaseModel):
    itemIds: List[int]


class MediaServerScanRequest(BaseModel):
    """媒体服务器扫描请求"""
    library_ids: Optional[List[str]] = None


class MediaServerLookupRequest(BaseModel):
    """反查请求：按标题在指定媒体服务器中搜索候选条目"""
    keyword: str = Field(..., min_length=1, description="搜索关键词（作品标题）")
    mediaType: Optional[str] = Field(None, description="限定类型：movie / tv_series，留空则全部")


class MediaServerLookupItem(BaseModel):
    """反查结果中的单个候选条目（顶层 Series / Movie）"""
    itemId: str = Field(..., description="媒体服务器中的条目 ID")
    title: str
    mediaType: Optional[str] = None
    year: Optional[int] = None
    seriesId: Optional[str] = Field(None, description="Series 级 ID（剧集才有）")
    seasonId: Optional[str] = Field(None, description="Season 级 ID（分季才有）")
    season: Optional[int] = None
    tmdbId: Optional[str] = None
    imdbId: Optional[str] = None
    posterUrl: Optional[str] = None


class MediaServerBindRequest(BaseModel):
    """绑定请求：把选中的媒体服务器条目写入作品的绑定字段"""
    serverId: int = Field(..., description="媒体服务器配置 ID")
    seriesId: str = Field(..., min_length=1, description="Series/Movie 级 ID")
    seasonId: Optional[str] = Field(None, description="Season 级 ID，剧集分季时提供")
