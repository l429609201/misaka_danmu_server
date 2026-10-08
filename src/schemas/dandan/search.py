"""
DanDan API - 搜索相关模型
"""
from typing import List, Optional
from pydantic import BaseModel, Field


class AnimeInfo(BaseModel):
    """番剧信息"""
    animeId: int = Field(..., description="Anime ID")
    animeTitle: str = Field(..., description="节目名称")
    type: str = Field(..., description="节目类型, e.g., 'tv_series', 'movie'")
    rating: int = Field(0, description="评分 (暂未实现，默认为0)")
    imageUrl: Optional[str] = Field(None, description="封面图片URL (暂未实现)")


class AnimeSearchResponse(BaseModel):
    """番剧搜索响应"""
    hasMore: bool = Field(False, description="是否还有更多结果")
    animes: List[AnimeInfo] = Field([], description="番剧列表")


__all__ = [
    "AnimeInfo",
    "AnimeSearchResponse",
]
