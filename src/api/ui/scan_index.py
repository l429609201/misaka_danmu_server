"""
本地扫描增量索引 (34)
"""
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.api.dependencies import get_config_service
from src.services.config_service import ConfigService

router = APIRouter()


class ScanIndexStats(BaseModel):
    totalFiles: int = 0
    lastScanAt: Optional[str] = None
    newFiles: int = 0
    changedFiles: int = 0
    skippedFiles: int = 0


@router.get("/local-scan/index-stats", summary="本地扫描索引统计")
async def get_scan_index_stats(
    config_service: ConfigService = Depends(get_config_service),
) -> ScanIndexStats:
    """通过配置服务读取扫描索引统计。"""
    raw = await config_service.get("local_scan_index", "{}")
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
    config_service: ConfigService = Depends(get_config_service),
) -> Dict[str, str]:
    """使用配置服务的写入接口清空索引，触发下次全量扫描。"""
    await config_service.set("local_scan_index", json.dumps({
        "files": {},
        "lastScanAt": None,
        "lastNewCount": 0,
        "lastChangedCount": 0,
        "lastSkippedCount": 0,
    }))
    return {"message": "ok", "detail": "索引已清除，下次扫描将全量遍历"}


@router.get("/local-scan/index-detail", summary="本地扫描索引详情")
async def get_scan_index_detail(
    path: Optional[str] = Query(None, description="筛选路径前缀"),
    limit: int = Query(100, ge=1, le=500),
    config_service: ConfigService = Depends(get_config_service),
) -> Dict[str, Any]:
    """读取索引详情并保留路径前缀筛选和数量限制。"""
    raw = await config_service.get("local_scan_index", "{}")
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
