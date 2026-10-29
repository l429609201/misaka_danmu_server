"""
匹配相关的 Pydantic 模型

从 src/db/models.py 迁移而来
"""

from typing import List
from pydantic import BaseModel, Field


class MatchInfo(BaseModel):
    """匹配结果信息"""
    animeId: int = Field(..., description="Anime ID")
    animeTitle: str = Field(..., description="节目名称")
    episodeId: int = Field(..., description="Episode ID")
    episodeTitle: str = Field(..., description="分集标题")
    type: str = Field(..., description="节目类型")
    shift: float = Field(0.0, description="时间轴偏移(秒)")


class MatchResponse(BaseModel):
    """匹配响应模型"""
    isMatched: bool = Field(False, description="是否成功匹配")
    matches: List[MatchInfo] = Field([], description="匹配结果列表")
