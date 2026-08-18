"""
外部控制API - 日志查询路由
包含: /logs, /logs/files, /logs/files/{filename}
"""

import asyncio
import logging
from typing import List

from fastapi import APIRouter, HTTPException, Query

from src.services import get_logs, list_log_files, read_log_file

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/logs", response_model=List[str], summary="获取实时日志")
async def get_realtime_logs():
    """
    获取存储在内存中的最新日志条目（实时日志）。
    返回最近的日志行列表，按时间倒序排列。
    """
    return get_logs()


@router.get("/logs/files", summary="获取历史日志文件列表")
async def get_log_file_list():
    """
    列出所有可用的历史日志文件（包括轮转文件）。

    ### 日志文件说明
    - **app.log**: 主应用日志
    - **bot_raw.log**: Bot原始交互日志
    - **webhook_raw.log**: Webhook原始请求日志
    - **ai_responses.log**: AI响应日志
    - **metadata_responses.log**: 元数据响应日志
    - **scraper_responses.log**: 搜索源响应日志
    """
    return list_log_files()


@router.get("/logs/files/{filename}", summary="读取指定历史日志文件")
async def get_log_file_content(
    filename: str,
    tail: int = Query(200, ge=1, description="每批返回行数，默认200"),
    keyword: str = Query("", description="关键词过滤（大小写不敏感），空字符串不过滤"),
    offset: int = Query(0, ge=0, description="已加载条数，用于加载更多"),
):
    """读取指定日志文件，支持后端关键词过滤和分页加载。

    返回 {"lines": [...], "hasMore": bool, "total": int}
    """
    try:
        return await asyncio.to_thread(read_log_file, filename, tail, keyword, offset)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except IOError as e:
        raise HTTPException(status_code=500, detail=str(e))
