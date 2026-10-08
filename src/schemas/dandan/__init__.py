"""
DanDan API 模型
"""
from .search import AnimeInfo, AnimeSearchResponse
from .match import MatchInfo, MatchResponse
from .comment import Comment, CommentResponse, TaskCommentResponse, DanmakuUpdateRequest

__all__ = [
    # search
    "AnimeInfo",
    "AnimeSearchResponse",
    # match
    "MatchInfo",
    "MatchResponse",
    # comment
    "Comment",
    "CommentResponse",
    "TaskCommentResponse",
    "DanmakuUpdateRequest",
]

# 显式导出迁入包内的协议模型，保留调用方原有导入路径。
from .protocol import (
    DandanResponseBase, DandanEpisodeInfo, DandanAnimeInfo,
    DandanSearchEpisodesResponse, DandanSearchAnimeItem, DandanSearchAnimeResponse,
    BangumiTitle, BangumiEpisodeSeason, BangumiEpisode, BangumiIntro,
    BangumiTag, BangumiOnlineDatabase, BangumiTrailer, BangumiDetails,
    BangumiDetailsResponse, DandanMatchInfo, DandanMatchResponse,
    DandanBatchMatchRequestItem, DandanBatchMatchRequest,
)

__all__ += [
    "DandanResponseBase", "DandanEpisodeInfo", "DandanAnimeInfo",
    "DandanSearchEpisodesResponse", "DandanSearchAnimeItem", "DandanSearchAnimeResponse",
    "BangumiTitle", "BangumiEpisodeSeason", "BangumiEpisode", "BangumiIntro",
    "BangumiTag", "BangumiOnlineDatabase", "BangumiTrailer", "BangumiDetails",
    "BangumiDetailsResponse", "DandanMatchInfo", "DandanMatchResponse",
    "DandanBatchMatchRequestItem", "DandanBatchMatchRequest",
]
