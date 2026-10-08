"""
UI API - 搜索和导入相关模型
"""
from typing import List, Optional
from pydantic import BaseModel, Field


class ProviderSearchInfo(BaseModel):
    """代表来自外部数据源的单个搜索结果。"""
    provider: str = Field(..., description="数据源提供方, e.g., 'tencent', 'bilibili'")
    mediaId: str = Field(..., description="该数据源中的媒体ID (e.g., tencent的cid)")
    title: str = Field(..., description="节目名称")
    type: str = Field(..., description="节目类型, e.g., 'tv_series', 'movie'")
    season: Optional[int] = Field(1, description="季度, 默认为1；电影可为空")
    year: Optional[int] = Field(None, description="发行年份")
    imageUrl: Optional[str] = Field(None, description="封面图片URL")
    episodeCount: Optional[int] = Field(None, description="总集数")
    currentEpisodeIndex: Optional[int] = Field(None, description="如果搜索词指定了集数，则为当前集数")
    url: Optional[str] = Field(None, description="平台播放页面URL")
    supportsEpisodeUrls: Optional[bool] = Field(None, description="该源是否支持获取分集URL (用于补充源功能)")
    supplementSource: Optional[str] = Field(None, description="搜索补充源名称 (如 '360')，非补充结果时为null")
    recognitionTitle: Optional[str] = Field(None, description="识别词指定的入库正确名，前端用于展示识别词标签")
    sourceType: Optional[str] = Field(None, description="弹幕源原始媒体类型")
    typeSuggestion: Optional[str] = Field(None, description="元数据建议的媒体类型")
    typeDecision: Optional[str] = Field(None, description="类型判定状态: corrected/needs_confirmation")
    typeDecisionReason: Optional[str] = Field(None, description="类型判定依据")


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
    """导入请求"""
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
    season: Optional[int] = None
    seasons: Optional[List[TMDBSeasonInfo]] = None


__all__ = [
    "ProviderSearchInfo",
    "ProviderSearchResponse",
    "ProviderEpisodeInfo",
    "ImportRequest",
    "TMDBSeasonInfo",
    "MetadataDetailsResponse",
]
