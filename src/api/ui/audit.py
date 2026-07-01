"""
用户会话与安全审计 (20) - 扩展API
"""
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db_session, orm_models, ConfigManager
from src.core import get_now
from src.api.dependencies import get_config_manager
from src import security

logger = logging.getLogger(__name__)
router = APIRouter()


class AuditLogItem(BaseModel):
    eventType: str = ""
    ipAddress: str = ""
    userAgent: str = ""
    detail: str = ""
    timestamp: str = ""
    success: bool = True


@router.get("/audit/logs", summary="安全审计日志")
async def get_audit_logs(
    limit: int = Query(50, ge=1, le=200),
    event_type: Optional[str] = Query(None),
    config_manager: ConfigManager = Depends(get_config_manager),
):
    raw = await config_manager.get("security_audit_log", "[]")
    try:
        logs = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        logs = []
    if event_type:
        logs = [l for l in logs if l.get("eventType") == event_type]
    return logs[-limit:]


@router.get("/audit/session-stats", summary="会话统计")
async def get_session_stats(
    session: AsyncSession = Depends(get_db_session),
):
    total_q = select(func.count(orm_models.UserSession.id))
    total = (await session.execute(total_q)).scalar() or 0
    active_q = select(func.count(orm_models.UserSession.id)).where(
        orm_models.UserSession.isRevoked == False
    )
    active = (await session.execute(active_q)).scalar() or 0
    return {"totalSessions": total, "activeSessions": active}


@router.post("/audit/clear", summary="清除审计日志")
async def clear_audit_logs(
    config_manager: ConfigManager = Depends(get_config_manager),
):
    await config_manager.setValue("security_audit_log", "[]")
    return {"message": "ok"}


async def record_audit_event(
    config_manager: ConfigManager,
    event_type: str,
    ip_address: str = "",
    user_agent: str = "",
    detail: str = "",
    success: bool = True,
):
    """记录安全审计事件（供其他模块调用）"""
    raw = await config_manager.get("security_audit_log", "[]")
    try:
        logs = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        logs = []
    logs.append({
        "eventType": event_type,
        "ipAddress": ip_address,
        "userAgent": user_agent[:200] if user_agent else "",
        "detail": detail[:500] if detail else "",
        "timestamp": get_now().isoformat(),
        "success": success,
    })
    if len(logs) > 500:
        logs = logs[-500:]
    await config_manager.setValue("security_audit_log", json.dumps(logs, ensure_ascii=False))
