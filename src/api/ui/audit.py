"""
用户会话与安全审计 (20) - 扩展API
"""
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.api.dependencies import get_config_service
from src.core import get_now
from src.services.config_service import ConfigService
from src.services.service_container import get_database_service
from src.utils.auth import security

logger = logging.getLogger(__name__)
# 统一保护审计查询和清空操作，沿用现有 JWT 与 IP 白名单鉴权策略。
router = APIRouter(dependencies=[Depends(security.get_current_user)])


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
    config_service: ConfigService = Depends(get_config_service),
) -> List[Dict[str, Any]]:
    """通过实际配置服务读取安全审计日志。"""
    raw = await config_service.get("security_audit_log", "[]")
    try:
        logs = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        logs = []
    if event_type:
        logs = [l for l in logs if l.get("eventType") == event_type]
    return logs[-limit:]


@router.get("/audit/session-stats", summary="会话统计")
async def get_session_stats() -> Dict[str, int]:
    """通过数据库服务获取会话统计，避免 API 层直接操作数据库。"""
    db = get_database_service()
    async with db.transaction():
        return await db.session_store.get_stats()


@router.post("/audit/clear", summary="清除审计日志")
async def clear_audit_logs(
    config_service: ConfigService = Depends(get_config_service),
) -> Dict[str, str]:
    """通过配置服务清除日志，同时更新配置缓存。"""
    await config_service.set("security_audit_log", "[]")
    return {"message": "ok"}


async def record_audit_event(
    config_service: ConfigService,
    event_type: str,
    ip_address: str = "",
    user_agent: str = "",
    detail: str = "",
    success: bool = True,
) -> None:
    """通过配置服务记录安全审计事件并同步缓存，供其他模块调用。"""
    raw = await config_service.get("security_audit_log", "[]")
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
    await config_service.set("security_audit_log", json.dumps(logs, ensure_ascii=False))
