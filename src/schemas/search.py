"""
搜索相关的 Pydantic 模型

从 src/db/models.py 迁移而来
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class AnimeInfo(BaseModel):
    """搜索结果中的动漫信息"""
    animeId: int = Field(..., description="Anime ID")
    animeTitle: str = Field(..., description="节目名称")
    type: str = Field(..., description="节目类型, e.g., 'tv_series', 'movie'")
    rating: int = Field(0, description="评分 (暂未实现，默认为0)")
    imageUrl: Optional[str] = Field(None, description="封面图片URL (暂未实现)")


class AnimeSearchResponse(BaseModel):
    """搜索响应模型"""
    hasMore: bool = Field(False, description="是否还有更多结果")
    animes: List[AnimeInfo] = Field([], description="番剧列表")


class ProviderEpisodeInfo(BaseModel):
    """数据源分集信息"""
    provider: str = Field(..., description="数据源名称")
    episodeId: str = Field(..., description="该数据源中的分集ID")
    title: str = Field(..., description="分集标题")
    episodeIndex: int = Field(..., description="分集序号")
    url: Optional[str] = Field(None, description="分集原始URL")


class ProviderSearchInfo(BaseModel):
    """单个搜索源的作品信息"""
    provider: str = Field(..., description="数据源名称")
    mediaId: str = Field(..., description="该数据源中的媒体ID")
    title: str = Field(..., description="作品标题")
    type: str = Field(..., description="作品类型: tv_series, movie")
    season: Optional[int] = Field(None, description="季度号")
    result_index: int = Field(..., description="结果索引（用于 /import/direct）")


class ProviderSearchResponse(BaseModel):
    """搜索响应模型"""
    searchId: str = Field(..., description="搜索会话ID，用于后续导入")
    results: List[ProviderSearchInfo] = Field([], description="搜索结果列表")
