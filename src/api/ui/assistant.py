"""
御坂助手 · 对话 API（P1：流式对话）
------------------------------------------------------------
- GET  /ui/assistant/personas   人设列表
- GET  /ui/assistant/status     对话是否可用（AI 是否已配置）
- POST /ui/assistant/chat/stream 流式对话（SSE）

鉴权复用 get_current_user；配置复用 get_config_manager。
"""

import json
import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.db import ConfigManager
from src import security
from src.db import models
from src.api.dependencies import get_config_manager
from src.ai.assistant import (
    AssistantChatService, AssistantAgent, list_personas, DEFAULT_PERSONA,
)
from .assistant_sessions import mark_session_processing, save_session_snapshot

logger = logging.getLogger(__name__)
router = APIRouter()


class ChatMessage(BaseModel):
    role: str = Field(..., description="user 或 assistant")
    content: str = Field("", description="消息文本")
    # 图片附件（base64 data URL 列表），仅 user 消息使用；需 vision 模型
    images: Optional[List[str]] = Field(None, description="图片 data URL 列表")


class ChatStreamRequest(BaseModel):
    messages: List[ChatMessage] = Field(default_factory=list, description="最近 N 轮对话")
    persona: Optional[str] = Field(DEFAULT_PERSONA, description="人设 key")
    sessionId: Optional[str] = Field(None, description="会话 ID（用于断流恢复标记处理状态）")


@router.get("/assistant/personas", summary="获取御坂助手人设列表", include_in_schema=False)
async def get_personas():
    return {"personas": list_personas(), "default": DEFAULT_PERSONA}


@router.get("/assistant/status", summary="御坂助手对话是否可用", include_in_schema=False)
async def get_status(
    config_manager: ConfigManager = Depends(get_config_manager),
    current_user: models.User = Depends(security.get_current_user),
):
    service = AssistantChatService(config_manager)
    ready = await service.is_ready()
    return {"ready": ready}


@router.post("/assistant/chat/stream", summary="御坂助手流式对话", include_in_schema=False)
async def chat_stream(
    payload: ChatStreamRequest,
    request: Request,
    config_manager: ConfigManager = Depends(get_config_manager),
    current_user: models.User = Depends(security.get_current_user),
):
    """流式对话（支持只读工具调用），返回 text/event-stream，事件为 {type: delta|tool|done|error}。"""
    # 从 app.state 取 DB 会话工厂，供工具执行时独立开会话
    st = request.app.state
    session_factory = getattr(st, "db_session_factory", None)
    agent = AssistantAgent(config_manager, session_factory=session_factory)
    history = [
        {"role": m.role, "content": m.content, "images": m.images or []}
        for m in payload.messages
    ]
    persona_key = payload.persona or DEFAULT_PERSONA
    # 写类工具执行所需的管理器（P3）
    context_extra = {
        "task_manager": getattr(st, "task_manager", None),
        "scraper_manager": getattr(st, "scraper_manager", None),
        "rate_limiter": getattr(st, "rate_limiter", None),
        "scheduler_manager": getattr(st, "scheduler_manager", None),
        "metadata_manager": getattr(st, "metadata_manager", None),
        "ai_matcher_manager": getattr(st, "ai_matcher_manager", None),
        "title_recognition_manager": getattr(st, "title_recognition_manager", None),
        "config_manager": config_manager,
    }

    sid = payload.sessionId

    async def event_generator():
        # 断流恢复：流开始标记会话处理中；结束/出错时保存快照并清标记
        assistant_reply = ""
        await mark_session_processing(session_factory, sid, True)
        try:
            async for event in agent.stream(history, persona_key, context_extra):
                if event.get("type") == "delta":
                    assistant_reply += event.get("content", "")
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            logger.error(f"御坂助手流式对话生成器异常: {e}", exc_info=True)
            err = {"type": "error", "content": "对话出错了，请稍后重试。"}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
        finally:
            # 无论正常结束还是客户端断开，都保存快照供断流恢复
            snapshot = [{"role": m.role, "content": m.content} for m in payload.messages]
            if assistant_reply:
                snapshot.append({"role": "bot", "content": assistant_reply})
            await save_session_snapshot(session_factory, sid, snapshot, persona_key)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
