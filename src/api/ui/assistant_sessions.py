"""
御坂助手 · 会话历史 API（P4）
------------------------------------------------------------
- GET    /ui/assistant/sessions            会话列表（摘要）
- GET    /ui/assistant/sessions/{sid}      会话详情（含消息）
- PUT    /ui/assistant/sessions/{sid}      保存/更新会话展示快照
- DELETE /ui/assistant/sessions/{sid}      删除会话
- PUT    /ui/assistant/sessions/{sid}/processing  标记处理中（断流恢复用）

会话数据只属于当前登录用户视角（本项目单管理员，暂不做多用户隔离）。
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, delete as sa_delete
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from src import security
from src.db import models, get_db_session, orm_models
from src.core.timezone import get_now

logger = logging.getLogger(__name__)
router = APIRouter()

_MAX_PERSIST_MESSAGES = 40  # 每会话最多持久化的消息数


class SessionMessage(BaseModel):
    role: str = Field(..., description="user / bot")
    content: str = Field("", description="消息文本")


class SessionSaveRequest(BaseModel):
    title: Optional[str] = Field(None, description="会话标题")
    persona: Optional[str] = Field(None, description="人设 key")
    messages: List[SessionMessage] = Field(default_factory=list)


def _title_from_messages(messages: List[SessionMessage]) -> str:
    """取第一条用户消息前 30 字作标题。"""
    for m in messages:
        if m.role == "user" and m.content.strip():
            t = m.content.strip().replace("\n", " ")
            return t[:30] + ("…" if len(t) > 30 else "")
    return "新对话"


async def mark_session_processing(session_factory, sid: str, processing: bool):
    """标记会话处理状态（断流恢复用）。会话不存在时按需创建占位。"""
    if not sid or not session_factory:
        return
    try:
        async with session_factory() as session:
            row = (await session.execute(
                select(orm_models.AssistantSession).where(
                    orm_models.AssistantSession.sessionId == sid)
            )).scalar_one_or_none()
            if not row:
                row = orm_models.AssistantSession(sessionId=sid, title="新对话")
                session.add(row)
            row.isProcessing = processing
            row.updatedAt = get_now()
            await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"标记会话处理状态失败: {e}")


async def save_session_snapshot(session_factory, sid: str, messages: List[dict], persona: str = None):
    """流式结束后保存会话展示快照（整体覆盖，供断流恢复拉取）。"""
    if not sid or not session_factory:
        return
    real = [m for m in messages if m.get("content")]
    if len(real) <= 1:
        return
    try:
        async with session_factory() as session:
            row = (await session.execute(
                select(orm_models.AssistantSession).where(
                    orm_models.AssistantSession.sessionId == sid)
            )).scalar_one_or_none()
            title = _title_from_messages([SessionMessage(**m) for m in real])
            if not row:
                row = orm_models.AssistantSession(sessionId=sid, title=title,
                                                  persona=persona or "misaka_20001")
                session.add(row)
                await session.flush()
            else:
                row.title = title
                if persona:
                    row.persona = persona
                row.updatedAt = get_now()
                await session.execute(sa_delete(orm_models.AssistantMessage).where(
                    orm_models.AssistantMessage.sessionDbId == row.id))
            for m in real[-_MAX_PERSIST_MESSAGES:]:
                session.add(orm_models.AssistantMessage(
                    sessionDbId=row.id, role=m["role"], content=m["content"]))
            row.isProcessing = False
            await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"保存会话快照失败: {e}")


@router.get("/assistant/sessions", summary="御坂助手会话列表", include_in_schema=False)
async def list_sessions(
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user),
):
    stmt = (
        select(orm_models.AssistantSession)
        .order_by(orm_models.AssistantSession.updatedAt.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "sessionId": r.sessionId,
            "title": r.title or "新对话",
            "persona": r.persona,
            "isProcessing": r.isProcessing,
            "updatedAt": r.updatedAt.isoformat() if r.updatedAt else "",
        }
        for r in rows
    ]


@router.get("/assistant/sessions/{sid}", summary="御坂助手会话详情", include_in_schema=False)
async def get_session(
    sid: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user),
):
    stmt = (
        select(orm_models.AssistantSession)
        .where(orm_models.AssistantSession.sessionId == sid)
        .options(selectinload(orm_models.AssistantSession.messages))
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "会话不存在")
    msgs = sorted(row.messages, key=lambda m: m.id)
    return {
        "sessionId": row.sessionId,
        "title": row.title,
        "persona": row.persona,
        "isProcessing": row.isProcessing,
        "messages": [{"role": m.role, "content": m.content} for m in msgs],
    }


@router.put("/assistant/sessions/{sid}", summary="保存御坂助手会话", include_in_schema=False)
async def save_session(
    sid: str,
    payload: SessionSaveRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user),
):
    stmt = select(orm_models.AssistantSession).where(orm_models.AssistantSession.sessionId == sid)
    row = (await session.execute(stmt)).scalar_one_or_none()
    title = payload.title or _title_from_messages(payload.messages)

    if not row:
        row = orm_models.AssistantSession(
            sessionId=sid, title=title, persona=payload.persona or "misaka_20001",
        )
        session.add(row)
        await session.flush()
    else:
        row.title = title
        if payload.persona:
            row.persona = payload.persona
        row.updatedAt = get_now()
        # 先清旧消息再写新快照（整体覆盖，简单可靠）
        await session.execute(
            sa_delete(orm_models.AssistantMessage).where(
                orm_models.AssistantMessage.sessionDbId == row.id
            )
        )

    kept = payload.messages[-_MAX_PERSIST_MESSAGES:]
    for m in kept:
        if m.content:
            session.add(orm_models.AssistantMessage(
                sessionDbId=row.id, role=m.role, content=m.content
            ))
    await session.commit()
    return {"status": "ok", "sessionId": sid}


@router.delete("/assistant/sessions/{sid}", summary="删除御坂助手会话", include_in_schema=False)
async def delete_session(
    sid: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: models.User = Depends(security.get_current_user),
):
    stmt = select(orm_models.AssistantSession).where(orm_models.AssistantSession.sessionId == sid)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row:
        await session.delete(row)
        await session.commit()
    return {"status": "ok"}
