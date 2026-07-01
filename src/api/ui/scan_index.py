"""
本地扫描增量索引 (34)
"""
import json
import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db_session, ConfigManager
from src.core import get_now
from src.api.dependencies import get_config_manager

logger = logging.getLogger(__name__)
router = APIRouter()


class ScanIndexStats(BaseModel):
    totalFiles: int = 0
    lastScanAt: Optional[str] = None
    newFiles: int = 0
    changedFiles: int = 0
    skippedFiles: int = 0


@router.get("/local-scan/index-stats", summary="本地扫描索引统计")
async def get_scan_index_stats(
    config_manager: ConfigManager = Depends(get_config_manager),
):
    raw = await config_manager.get("local_scan_index", "{}")
    try:
        index = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        index = {}
    return ScanIndexStats(
        totalFiles=len(index.get("files", {})),
        lastScanAt=index.get("lastScanAt"),
        newFiles=index.get("lastNewCount", 0),
        changedFiles=index.get("lastChangedCount", 0),
        skippedFiles=index.get("lastSkippedCount", 0),
    )


@router.post("/local-scan/rebuild-index", summary="重建本地扫描索引")
async def rebuild_scan_index(
    config_manager: ConfigManager = Depends(get_config_manager),
):
    await config_manager.setValue("local_scan_index", json.dumps({
        "files": {},
        "lastScanAt": None,
        "lastNewCount": 0,
        "lastChangedCount": 0,
        "lastSkippedCount": 0,
    }))
    return {"message": "ok", "detail": "索引已清除，下次扫描将全量遍历"}


@router.get("/local-scan/index-detail", summary="本地扫描索引详情")
async def get_scan_index_detail(
    path: str = Query(None, description="筛选路径前缀"),
    limit: int = Query(100, ge=1, le=500),
    config_manager: ConfigManager = Depends(get_config_manager),
):
    raw = await config_manager.get("local_scan_index", "{}")
    try:
        index = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        index = {}
    files = index.get("files", {})
    if path:
        files = {k: v for k, v in files.items() if k.startswith(path)}
    items = list(files.items())[:limit]
    return {
        "total": len(files),
        "items": [{"path": k, "mtime": v.get("mtime"), "size": v.get("size")} for k, v in items],
    }
