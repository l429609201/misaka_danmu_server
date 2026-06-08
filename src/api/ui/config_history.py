"""
配置变更历史 / 回滚 (15)
"""
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db_session, ConfigManager
from src.core import get_now
from src.api.dependencies import get_config_manager

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_HISTORY_PER_KEY = 20


class ConfigChangeRecord(BaseModel):
    key: str = ""
    oldValue: Optional[str] = None
    newValue: Optional[str] = None
    changedAt: str = ""
    source: str = "ui"  # ui/api/system


class ConfigHistoryResponse(BaseModel):
    key: str = ""
    history: List[ConfigChangeRecord] = []


@router.get("/config-history/list", summary="获取配置变更历史")
async def get_config_history(
    key: str = Query(None, description="配置key，不填则返回所有"),
    limit: int = Query(20, ge=1, le=100),
    config_manager: ConfigManager = Depends(get_config_manager),
):
    raw = await config_manager.get("config_change_history", "[]")
    try:
        history = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        history = []
    if key:
        history = [h for h in history if h.get("key") == key]
    return history[-limit:]


@router.post("/config-history/rollback", summary="回滚配置到历史版本")
async def rollback_config(
    body: dict,
    config_manager: ConfigManager = Depends(get_config_manager),
):
    key = body.get("key", "")
    target_value = body.get("value", "")
    if not key:
        return {"message": "key is required", "success": False}

    current = await config_manager.get(key, "")
    await config_manager.setValue(key, target_value)

    # 记录回滚操作本身
    await _record_change(config_manager, key, str(current), target_value, "rollback")
    return {"message": "ok", "success": True}


@router.post("/config-history/clear", summary="清除配置变更历史")
async def clear_config_history(
    config_manager: ConfigManager = Depends(get_config_manager),
):
    await config_manager.setValue("config_change_history", "[]")
    return {"message": "ok"}


async def record_config_change(config_manager: ConfigManager, key: str, old_value: str, new_value: str, source: str = "ui"):
    """供其他模块调用的配置变更记录函数"""
    await _record_change(config_manager, key, old_value, new_value, source)


async def _record_change(config_manager: ConfigManager, key: str, old_value: str, new_value: str, source: str):
    raw = await config_manager.get("config_change_history", "[]")
    try:
        history = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        history = []

    # 截断旧值和新值避免太长
    def _truncate(v, max_len=500):
        s = str(v) if v is not None else ""
        return s[:max_len] + "..." if len(s) > max_len else s

    history.append({
        "key": key,
        "oldValue": _truncate(old_value),
        "newValue": _truncate(new_value),
        "changedAt": get_now().isoformat(),
        "source": source,
    })

    # 只保留最新N条
    if len(history) > 200:
        history = history[-200:]

    await config_manager.setValue("config_change_history", json.dumps(history, ensure_ascii=False))
