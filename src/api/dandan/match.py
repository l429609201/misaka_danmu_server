"""
弹弹Play 兼容 API 的匹配功能

通过文件名匹配弹幕库，支持库内直接匹配和后备匹配。
🚀 新架构：API 层只负责路由和参数验证，业务逻辑委托给 workflow 层
"""

import asyncio
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

# 导入 workflow 层实现
from src.workflows.match.match_flow import get_match_for_item as workflow_get_match_for_item

# 导入数据模型和路由处理器
from src.schemas.dandan import (
    DandanMatchResponse,
    DandanBatchMatchRequestItem,
    DandanBatchMatchRequest
)
from .route_handler import get_token_from_path, DandanApiRoute

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 路由定义 - API 层薄层，委托给 workflow 层
# ═══════════════════════════════════════════════════════════════

match_router = APIRouter(route_class=DandanApiRoute)


@match_router.post(
    "/match",
    response_model=DandanMatchResponse,
    summary="[dandanplay兼容] 匹配单个文件"
)
async def match_single_file(
    request: DandanBatchMatchRequestItem,
    token: str = Depends(get_token_from_path)
):
    """
    通过文件名匹配弹幕库。此接口不使用文件Hash。
    优先进行库内直接匹配，失败后回退到TMDB剧集组映射。

    🚀 新架构：委托给 workflow 层处理
    """
    return await workflow_get_match_for_item(request, token)


@match_router.post(
    "/match/batch",
    response_model=List[DandanMatchResponse],
    summary="[dandanplay兼容] 批量匹配文件"
)
async def match_batch_files(
    request: DandanBatchMatchRequest,
    token: str = Depends(get_token_from_path)
):
    """
    批量匹配文件。

    🚀 新架构：委托给 workflow 层处理
    """
    if len(request.requests) > 32:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="批量匹配请求不能超过32个文件。"
        )

    tasks = [
        workflow_get_match_for_item(item, token)
        for item in request.requests
    ]
    results = await asyncio.gather(*tasks)
    return results
