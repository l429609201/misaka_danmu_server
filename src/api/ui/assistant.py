"""
御坂助手 · 对话 API（P1：流式对话 + P2：技能管理）
------------------------------------------------------------
- GET  /ui/assistant/personas       人设列表
- GET  /ui/assistant/status         对话是否可用（AI 是否已配置）
- POST /ui/assistant/chat/stream    流式对话（SSE）

技能管理（P2 新增）：
- GET  /ui/assistant/skills         列出所有技能
- GET  /ui/assistant/skills/{id}    获取技能详情
- POST /ui/assistant/skills         创建技能
- PUT  /ui/assistant/skills/{id}    更新技能
- DELETE /ui/assistant/skills/{id}  删除技能
- PUT  /ui/assistant/skills/{id}/toggle 启用/停用技能

鉴权复用 get_current_user；配置复用 get_config_manager。
"""

import json
import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.db import ConfigManager
from src import security
from src.db import models
from src.api.dependencies import get_config_manager
from src.ai.assistant import (
    AssistantChatService, AssistantAgent, list_personas, DEFAULT_PERSONA,
)
from src.ai.assistant.skill_manager import get_skill_manager
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


# ────────────────────────────────────────────────────────────
# 技能管理 API（用户可自制 skill 到持久化目录 config/skills/）
# ────────────────────────────────────────────────────────────

class SkillCreateRequest(BaseModel):
    skillId: str = Field(..., description="技能 ID（小写字母/数字/短横线）")
    name: str = Field(..., description="技能名称")
    description: str = Field(..., description="触发时机描述（供 LLM 判断何时使用）")
    content: str = Field(..., description="作业指导书正文（Markdown）")
    allowedTools: List[str] = Field(default_factory=list, description="推荐工具列表（仅提示用）")


class SkillUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, description="新名称")
    description: Optional[str] = Field(None, description="新触发描述")
    content: Optional[str] = Field(None, description="新正文")
    allowedTools: Optional[List[str]] = Field(None, description="新工具列表")


class SkillToggleRequest(BaseModel):
    enabled: bool = Field(..., description="true 启用 / false 停用")


def _skill_to_dict(skill) -> dict:
    """把 Skill 对象转成前端可用的 dict。"""
    return {
        "skillId": skill.skill_id,
        "name": skill.name,
        "version": skill.version,
        "description": skill.description,
        "allowedTools": skill.allowed_tools,
        "enabled": skill.enabled,
        "content": skill.content,
    }


@router.get("/assistant/skills", summary="列出所有技能", include_in_schema=False)
async def list_skills_api(
    current_user: models.User = Depends(security.get_current_user),
):
    """列出所有技能（含未启用）。"""
    skills = get_skill_manager().list_skills()
    return {"total": len(skills), "skills": [_skill_to_dict(s) for s in skills]}


@router.get("/assistant/skills/{skill_id}", summary="获取技能详情", include_in_schema=False)
async def get_skill_api(
    skill_id: str,
    current_user: models.User = Depends(security.get_current_user),
):
    """获取单个技能的完整内容。"""
    skill = get_skill_manager().get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"技能 {skill_id} 不存在")
    return _skill_to_dict(skill)


@router.post("/assistant/skills", status_code=201, summary="创建技能", include_in_schema=False)
async def create_skill_api(
    payload: SkillCreateRequest,
    current_user: models.User = Depends(security.get_current_user),
):
    """创建新技能并落盘到 config/skills/<skillId>/SKILL.md。"""
    try:
        skill = get_skill_manager().create_skill(
            skill_id=payload.skillId,
            name=payload.name,
            description=payload.description,
            content=payload.content,
            allowed_tools=payload.allowedTools,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    logger.info(f"用户 '{current_user.username}' 创建技能: {payload.skillId}")
    return _skill_to_dict(skill)


@router.put("/assistant/skills/{skill_id}", summary="更新技能", include_in_schema=False)
async def update_skill_api(
    skill_id: str,
    payload: SkillUpdateRequest,
    current_user: models.User = Depends(security.get_current_user),
):
    """更新技能（未提供字段保持不变，版本号自动递增）。"""
    try:
        skill = get_skill_manager().update_skill(
            skill_id=skill_id,
            name=payload.name,
            description=payload.description,
            content=payload.content,
            allowed_tools=payload.allowedTools,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    logger.info(f"用户 '{current_user.username}' 更新技能: {skill_id} → v{skill.version}")
    return _skill_to_dict(skill)


@router.delete("/assistant/skills/{skill_id}", summary="删除技能", include_in_schema=False)
async def delete_skill_api(
    skill_id: str,
    current_user: models.User = Depends(security.get_current_user),
):
    """删除技能（连同整个目录，不可恢复）。"""
    try:
        get_skill_manager().delete_skill(skill_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    logger.info(f"用户 '{current_user.username}' 删除技能: {skill_id}")
    return {"message": f"技能 {skill_id} 已删除"}


@router.put("/assistant/skills/{skill_id}/toggle", summary="启用/停用技能", include_in_schema=False)
async def toggle_skill_api(
    skill_id: str,
    payload: SkillToggleRequest,
    current_user: models.User = Depends(security.get_current_user),
):
    """启用或停用技能（停用后不再注入 system prompt，文件保留）。"""
    try:
        skill = get_skill_manager().toggle_skill(skill_id, payload.enabled)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _skill_to_dict(skill)


@router.post("/assistant/skills/reload", summary="重载技能目录", include_in_schema=False)
async def reload_skills_api(
    current_user: models.User = Depends(security.get_current_user),
):
    """热重载：重新扫描 config/skills/ 目录（用户手动放入文件后可调此接口生效）。"""
    manager = get_skill_manager()
    manager.reload()
    skills = manager.list_skills()
    return {"message": f"已重载 {len(skills)} 个技能", "total": len(skills)}
