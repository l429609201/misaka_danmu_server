"""
弹弹Play 兼容 API 的版本信息接口

GET /{token}/api/v2/version
返回服务端名称、版本号和当前时间，格式与 dandanplay API v2 保持一致。
"""

import logging
from fastapi import APIRouter, Depends

from src._version import APP_VERSION
from src.core import get_now
from .route_handler import get_token_from_path

logger = logging.getLogger(__name__)

version_router = APIRouter()


@version_router.get(
    "/version",
    summary="获取服务端版本信息",
    tags=["Version"],
)
async def get_version(
    token: str = Depends(get_token_from_path),
):
    """
    返回服务端版本信息，格式兼容 dandanplay API v2。

    响应字段：
    - **success**: 是否成功
    - **errorCode**: 错误码，0 表示无错误
    - **errorMessage**: 错误信息
    - **serverName**: 服务端名称
    - **version**: 服务端版本号
    - **serverTime**: 服务端当前时间（ISO 8601 格式）
    """
    now = get_now()

    return {
        "success": True,
        "errorCode": 0,
        "errorMessage": "",
        "serverName": "Misaka_Danmaku_Server",
        "version": APP_VERSION,
        "serverTime": now.isoformat(),
    }
