"""
弹弹Play 兼容 API 的弹幕评论功能

包含弹幕获取、外部弹幕获取等功能。
🚀 新架构：API 层只负责路由和参数验证，业务逻辑委托给 workflow 层
"""

import logging

from fastapi import APIRouter, Depends, Path, Query, Request

# 薄路由只依赖响应 Schema，不再导入已删除的数据库模型兼容层。
from src.schemas.dandan import comment as models
from src.workflows.comments.danmaku_flow import get_comments_for_dandan as workflow_get_comments_for_dandan
from src.workflows.comments.external_flow import get_external_comments_from_url as workflow_get_external_comments
from .route_handler import get_token_from_path, DandanApiRoute

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 路由定义 - API 层薄层，委托给 workflow 层
# ═══════════════════════════════════════════════════════════════

comments_router = APIRouter(route_class=DandanApiRoute)

# ═══════════════════════════════════════════════════════════════
# 外部弹幕获取路由 - 🚀 委托给 workflow 层
# ═══════════════════════════════════════════════════════════════

@comments_router.get(
    "/extcomment",
    response_model=models.CommentResponse,
    summary="[dandanplay兼容] 获取外部弹幕"
)
async def get_external_comments_from_url(
    url: str = Query(..., description="外部视频链接 (支持 Bilibili, 腾讯, 爱奇艺, 优酷, 芒果TV)"),
    chConvert: int = Query(0, description="中文简繁转换。0-不转换，1-转换为简体，2-转换为繁体。"),
    token: str = Depends(get_token_from_path),
):
    """
    从外部URL获取弹幕 - 🚀 新架构：委托给 workflow 层处理
    """
    return await workflow_get_external_comments(url, token, chConvert)

# === get_comments_for_dandan ===
@comments_router.get(
    "/comment/{episodeId}",
    response_model=models.CommentResponse,
    response_model_exclude_none=True,
    summary="[dandanplay兼容] 获取弹幕"
)
async def get_comments_for_dandan(
    request: Request,
    episodeId: int = Path(..., description="分集ID (来自 /search/episodes 响应中的 episodeId)"),
    chConvert: int = Query(0, description="中文简繁转换。0-不转换，1-转换为简体，2-转换为繁体。"),
    fromTime: int = Query(0, alias="from", description="弹幕开始时间(秒)"),
    withRelated: bool = Query(True, description="是否包含关联弹幕"),
    async_mode: bool = Query(False, alias="async", description="异步模式：传入1的时候，在超时响应的情况下返回taskid"),
    token: str = Depends(get_token_from_path),
):
    """
    获取弹幕 - 🚀 新架构：委托给 workflow 层处理
    """
    return await workflow_get_comments_for_dandan(
        episodeId=episodeId,
        token=token,
        chConvert=chConvert,
        fromTime=fromTime,
        withRelated=withRelated,
        # 后备流程需要请求上下文来获取会话工厂。
        request=request,
        async_mode=async_mode
    )
