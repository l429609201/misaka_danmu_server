"""
外部控制API - 日志查询路由
包含: /logs, /logs/files, /logs/files/{filename}
"""

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


@router.get("/logs/files/{filename}", response_model=List[str], summary="读取指定历史日志文件")
async def get_log_file_content(
    filename: str,
    tail: int = Query(500, ge=1, le=5000, description="读取最后N行，默认500，最大5000"),
):
    """
    读取指定日志文件的最后 N 行内容。

    ### 参数
    - **filename**: 日志文件名（从 `/logs/files` 接口获取）
    - **tail**: 读取最后多少行，默认500行
    """
    try:
        return read_log_file(filename, tail=tail)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except IOError as e:
        raise HTTPException(status_code=500, detail=str(e))
