"""
弹弹Play 兼容 API 的番剧详情功能

包含番剧详情获取等功能。
🚀 新架构：API 层只负责路由和参数验证，业务逻辑委托给 workflow 层
"""

import logging

from fastapi import APIRouter, Depends, Path

# 🚀 导入 workflow 层实现
from src.workflows.bangumi.details_flow import get_bangumi_details_flow

# 导入数据模型和路由处理器
from src.schemas.dandan import BangumiDetailsResponse
from .route_handler import get_token_from_path, DandanApiRoute

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 路由定义 - API 层薄层，委托给 workflow 层
# ═══════════════════════════════════════════════════════════════

bangumi_router = APIRouter(route_class=DandanApiRoute)


@bangumi_router.get(
    "/bangumi/{bangumiId}",
    response_model=BangumiDetailsResponse,
    summary="[dandanplay兼容] 获取番剧详情"
)
async def get_bangumi_details(
    bangumiId: str = Path(..., description="作品ID, A开头的备用ID, 或真实的Bangumi ID"),
    token: str = Depends(get_token_from_path),
):
    """
    模拟 dandanplay 的 /api/v2/bangumi/{bangumiId} 接口 - 🚀 委托给 workflow 层处理
    """
    return await get_bangumi_details_flow(bangumiId, token)
