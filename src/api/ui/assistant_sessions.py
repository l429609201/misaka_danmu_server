"""御坂助手会话历史 API；流式回调使用独立短事务。"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.schemas import ui_models as models
from src.services.database_service import DatabaseService
from src.services.service_container import get_database_service
from src.utils.auth import security

logger = logging.getLogger(__name__)
router = APIRouter()

_MAX_PERSIST_MESSAGES = 40


class SessionMessage(BaseModel):
    role: str = Field(..., description="user / bot")
    content: str = Field("", description="消息文本")


class SessionSaveRequest(BaseModel):
    title: Optional[str] = Field(None, description="会话标题")
    persona: Optional[str] = Field(None, description="人设 key")
    messages: List[SessionMessage] = Field(default_factory=list)


def _title_from_messages(messages: List[SessionMessage]) -> str:
    """取第一条用户消息前 30 字作标题。"""
    for message in messages:
        if message.role == "user" and message.content.strip():
            title = message.content.strip().replace("\n", " ")
            return title[:30] + ("…" if len(title) > 30 else "")
    return "新对话"


async def mark_session_processing(sid: str, processing: bool, owner_id: int) -> None:
    """按用户标记会话处理状态；写入失败不打断流式响应。"""
    if not sid:
        return
    try:
        db = get_database_service()
        async with db.transaction():
            await db.assistant_sessions.mark_processing(sid, processing, owner_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("标记会话处理状态失败: %s", exc)


async def save_session_snapshot(
    sid: str, messages: List[dict], persona: Optional[str] = None, *, owner_id: int,
    run_hash: Optional[str] = None,
) -> bool:
    """流式结束后保存会话快照，供断流恢复拉取。"""
    if not sid:
        return False
    real = [message for message in messages if message.get("content") or message.get("events")]
    if len(real) <= 1:
        return False
    try:
        title = _title_from_messages([SessionMessage(**message) for message in real])
        db = get_database_service()
        async with db.transaction():
            if run_hash is not None:
                saved = await db.assistant_sessions.save_stream_snapshot(
                    sid, title, real[-_MAX_PERSIST_MESSAGES:], persona,
                    owner_id, run_hash,
                )
            else:
                await db.assistant_sessions.save_snapshot(
                    sid, title, real[-_MAX_PERSIST_MESSAGES:], persona, processing=False,
                    owner_id=owner_id,
                )
                saved = True
        return saved
    except Exception as exc:  # noqa: BLE001
        logger.warning("保存会话快照失败: %s", exc)
        return False


@router.get("/assistant/sessions", summary="御坂助手会话列表", include_in_schema=False)
async def list_sessions(
    limit: int = Query(50, ge=1, le=200),
    db_service: DatabaseService = Depends(get_database_service),
    current_user: models.User = Depends(security.get_current_user),
) -> List[dict]:
    """列出最近更新的会话摘要。"""
    async with db_service.transaction():
        return await db_service.assistant_sessions.list_summaries(limit, current_user.id)


@router.get("/assistant/sessions/{sid}", summary="御坂助手会话详情", include_in_schema=False)
async def get_session(
    sid: str,
    db_service: DatabaseService = Depends(get_database_service),
    current_user: models.User = Depends(security.get_current_user),
) -> dict:
    """读取指定会话及其消息。"""
    async with db_service.transaction():
        detail = await db_service.assistant_sessions.get_detail(sid, current_user.id)
    if detail is None:
        raise HTTPException(404, "会话不存在")
    return detail


@router.put("/assistant/sessions/{sid}", summary="保存御坂助手会话", include_in_schema=False)
async def save_session(
    sid: str,
    payload: SessionSaveRequest,
    db_service: DatabaseService = Depends(get_database_service),
    current_user: models.User = Depends(security.get_current_user),
) -> dict:
    """保存或整体更新会话展示快照。"""
    title = payload.title or _title_from_messages(payload.messages)
    kept = [
        {"role": message.role, "content": message.content}
        for message in payload.messages[-_MAX_PERSIST_MESSAGES:]
    ]
    try:
        async with db_service.transaction():
            await db_service.assistant_sessions.save_snapshot(
                sid, title, kept, payload.persona, owner_id=current_user.id,
            )
    except PermissionError as exc:
        raise HTTPException(404, "会话不存在") from exc
    return {"status": "ok", "sessionId": sid}


@router.delete("/assistant/sessions/{sid}", summary="删除御坂助手会话", include_in_schema=False)
async def delete_session(
    sid: str,
    db_service: DatabaseService = Depends(get_database_service),
    current_user: models.User = Depends(security.get_current_user),
) -> dict:
    """删除会话，不存在也返回成功。"""
    async with db_service.transaction():
        await db_service.assistant_sessions.delete_by_sid(sid, current_user.id)
    return {"status": "ok"}
