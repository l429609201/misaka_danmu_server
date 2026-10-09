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

鉴权复用 get_current_user；配置复用 get_config_service。
"""

import hashlib
import json
import logging
import secrets
from datetime import timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.utils.auth import security
from src.schemas import ui_models
from src.api.dependencies import get_config_service
from src.workflows.assistant_code import get_assistant_code_workflow
from src.services.ai_service import get_ai_service
from src.services.service_container import get_database_service
from src.core.timezone import get_app_timezone, get_now
from src.ai.assistant import (
    AssistantChatService, AssistantAgent, list_personas, DEFAULT_PERSONA,
)
from src.ai.assistant.mcp import (
    PERMISSION_READONLY, PERMISSION_WRITE, SUPPORTED_TRANSPORTS, TRANSPORT_STDIO,
    McpManager, McpServerConfig,
)
from src.ai.assistant.skill_manager import get_skill_manager
from src.ai.assistant.tools import registry
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
    codeRepair: bool = Field(False, description='本次管理员代码修复任务授权；不授权部署、重启或业务写操作')
    sessionId: Optional[str] = Field(None, description="会话 ID（用于断流恢复标记处理状态）")


class ChoiceSubmitRequest(BaseModel):
    """消费当前用户当前会话的单次选择票据。"""
    sessionId: str = Field(..., min_length=1)
    choiceId: str = Field(..., min_length=1)
    optionId: str = Field(..., min_length=1)


class ToolExecuteRequest(BaseModel):
    """仅凭本用户本会话的单次确认令牌执行服务端保存的动作。"""
    sessionId: str = Field(..., min_length=1)
    confirmationToken: str = Field(..., min_length=1)
    approved: bool = Field(True, description="false 表示取消并作废确认")


@router.get("/assistant/personas", summary="获取御坂助手人设列表", include_in_schema=False)
async def get_personas():
    return {"personas": list_personas(), "default": DEFAULT_PERSONA}


@router.get("/assistant/status", summary="御坂助手对话是否可用", include_in_schema=False)
async def get_status(
    config_service = Depends(get_config_service),
    current_user: ui_models.User = Depends(security.get_current_user),
):
    service = AssistantChatService(config_service)
    ready = await service.is_ready()
    return {"ready": ready}


@router.post("/assistant/chat/stream", summary="御坂助手流式对话", include_in_schema=False)
async def chat_stream(
    payload: ChatStreamRequest,
    request: Request,
    config_service = Depends(get_config_service),
    current_user: ui_models.User = Depends(security.get_current_user_no_db_hold),
):
    """流式对话；管理员可授权本轮代码修复，其余写工具仍需单次确认。"""
    # 从 app.state 取 DB 会话工厂，供工具执行时独立开会话
    st = request.app.state
    session_factory = getattr(st, "db_session_factory", None)
    ai_service = getattr(st, "ai_service", None) or get_ai_service()
    agent = AssistantAgent(
        config_service,
        session_factory=session_factory,
        on_metric_record=ai_service.persist_metric,
    )
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
        "metadata_manager": getattr(st, "metadata_service", None),
        "ai_service": ai_service,
        "title_recognition_manager": getattr(st, "title_recognition_manager", None),
        "config_service": config_service,
        # API 网关仅透传本次请求令牌，由目标路由独立鉴权。
        "app": request.app,
        "current_user": current_user,
        "authorization": request.headers.get("authorization", ""),
        "owner_id": current_user.id,
        "session_id": payload.sessionId,
        "code_repair_authorized": payload.codeRepair,
    }

    sid = payload.sessionId
    run_hash = hashlib.sha256(secrets.token_bytes(32)).hexdigest() if sid else None
    if sid:
        try:
            db = get_database_service()
            async with db.transaction():
                await db.assistant_sessions.begin_stream(sid, current_user.id, run_hash)
        except PermissionError as exc:
            raise HTTPException(404, "会话不存在") from exc

    async def event_generator():
        # 只留安全展示字段，工具输入与返回值不进入历史。
        assistant_reply = ""
        timeline: List[Dict[str, Any]] = []
        terminated = False
        snapshot_persisted = False
        choice_persisted = False
        try:
            async for event in agent.stream(history, persona_key, context_extra):
                if run_hash:
                    db = get_database_service()
                    async with db.transaction():
                        if not await db.assistant_sessions.current_stream(sid, current_user.id, run_hash):
                            return
                if event.get("type") == "delta":
                    piece = event.get("content", "")
                    assistant_reply += piece
                    if piece:
                        if timeline and timeline[-1]["type"] == "text":
                            timeline[-1]["content"] += piece
                        else:
                            timeline.append({"type": "text", "content": piece})
                elif event.get("type") == "done":
                    terminated = True
                    if run_hash:
                        complete = [{"role": m.role, "content": m.content} for m in payload.messages]
                        complete.append({"role": "bot", "content": assistant_reply, "events": timeline})
                        snapshot_persisted = await save_session_snapshot(
                            sid, complete, persona_key, owner_id=current_user.id, run_hash=run_hash,
                        )
                        if not snapshot_persisted:
                            event = {"type": "error", "content": "会话历史保存失败，请刷新后重试。"}
                            assistant_reply = event["content"]
                elif event.get("type") == "error":
                    terminated = True
                    assistant_reply = event.get("content", "") or "对话出错了，请稍后重试。"
                elif event.get("type") == "choice":
                    terminated = True
                    if not sid:
                        event = {"type": "error", "content": "该会话无法发起选择"}
                        assistant_reply = event["content"]
                    else:
                        choice_id = secrets.token_urlsafe(32)
                        expires_at = get_now() + timedelta(minutes=10)
                        event = {**event, "id": choice_id, "expires_at": expires_at.isoformat(),
                                 "expires_at_ms": int(expires_at.replace(tzinfo=get_app_timezone()).timestamp() * 1000)}
                        db = get_database_service()
                        async with db.transaction():
                            if not await db.assistant_sessions.current_stream(sid, current_user.id, run_hash, lock=True):
                                return
                            await db.assistant_sessions.issue(
                                hashlib.sha256(choice_id.encode("utf-8")).hexdigest(),
                                current_user.id, sid, "__choice__",
                                json.dumps({"title": event["title"], "prompt": event["prompt"],
                                            "options": event["options"]}, ensure_ascii=False),
                                expires_at,
                            )
                            original = [{"role": m.role, "content": m.content} for m in payload.messages]
                            title = next((m.content.strip().replace("\n", " ")[:30] for m in payload.messages
                                          if m.role == "user" and m.content.strip()), "新对话")
                            recorded = {key: event[key] for key in ("type", "id", "title", "prompt", "options", "expires_at", "expires_at_ms")}
                            if not await db.assistant_sessions.save_stream_snapshot(
                                sid, title, (original + [{"role": "bot", "content": assistant_reply,
                                                         "events": [*timeline, recorded]}])[-40:],
                                persona_key, current_user.id, run_hash,
                            ):
                                return
                        choice_persisted = True
                elif event.get("type") == "confirm":
                    terminated = True
                    if not sid or registry.get(event.get("name", "")) is None:
                        event = {"type": "error", "content": "该会话无法发起写操作确认"}
                    else:
                        if event.get('name') in ('code_apply_patch', 'code_rollback_patch'):
                            try:
                                event = {**event, 'codePreview': get_assistant_code_workflow().preview(
                                    event['arguments'].get('draft_id', ''), context_extra,
                                )}
                            except (PermissionError, ValueError):
                                assistant_reply = '代码补丁草稿无效或已过期，请重新生成并验证补丁。'
                                timeline.append({'type': 'text', 'content':
                                                 ('\n\n' if any(item['type'] == 'text' for item in timeline) else '')
                                                 + assistant_reply})
                                error = {'type': 'error', 'content': assistant_reply}
                                yield f"data: {json.dumps(error, ensure_ascii=False)}\n\n"
                                return
                        token = secrets.token_urlsafe(32)
                        db = get_database_service()
                        async with db.transaction():
                            if not await db.assistant_sessions.current_stream(sid, current_user.id, run_hash, lock=True):
                                return
                            await db.assistant_sessions.issue(
                                hashlib.sha256(token.encode("utf-8")).hexdigest(),
                                current_user.id, sid, event["name"],
                                json.dumps(event["arguments"], ensure_ascii=False, sort_keys=True),
                                get_now() + timedelta(minutes=5),
                            )
                        preview = {}
                        for key, value in event["arguments"].items():
                            if key in {"animeId", "sourceId", "episodeId", "taskId", "searchId", "resultIndex", "operation_id"} and isinstance(value, (str, int)):
                                preview[key] = str(value)[:64]
                            elif key == "path_params" and isinstance(value, dict):
                                preview[key] = {
                                    field: str(item)[:64] if field.lower().endswith("id") and isinstance(item, (str, int)) else "已隐藏"
                                    for field, item in value.items()
                                }
                            else:
                                preview[key] = "已隐藏"
                        event = {**event, "arguments": preview, "confirmationToken": token, "sessionId": sid}
                        assistant_reply = event.get("description") or event.get("label") or "待确认操作"
                if event.get("type") == "error":
                    timeline.append({"type": "text", "content":
                                     ("\n\n" if any(item["type"] == "text" for item in timeline) else "")
                                     + event.get("content", "对话失败")})
                if event.get("type") == "thinking":
                    timeline.append({key: event[key] for key in ("type", "status", "started_at", "elapsed_ms") if key in event})
                elif event.get("type") == "tool":
                    timeline.append({key: event[key] for key in ("type", "tool_id", "name", "label", "count", "status", "error_code", "error_message") if key in event})
                elif event.get("type") == "choice":
                    timeline.append({key: event[key] for key in ("type", "id", "title", "prompt", "options", "expires_at", "expires_at_ms") if key in event})
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if terminated:
                    return
        except Exception as e:  # noqa: BLE001
            logger.error(f"御坂助手流式对话生成器异常: {e}", exc_info=True)
            assistant_reply = "对话出错了，请稍后重试。"
            timeline.append({"type": "text", "content":
                             ("\n\n" if any(item["type"] == "text" for item in timeline) else "")
                             + assistant_reply})
            err = {"type": "error", "content": assistant_reply}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
        finally:
            # 旧流即使后来结束也不能覆盖新流快照或清除其处理中标记。
            snapshot = [{"role": m.role, "content": m.content} for m in payload.messages]
            if assistant_reply or timeline:
                snapshot.append({"role": "bot", "content": assistant_reply, **({"events": timeline} if timeline else {})})
            if choice_persisted or snapshot_persisted:
                return
            if run_hash is not None:
                await save_session_snapshot(sid, snapshot, persona_key, owner_id=current_user.id, run_hash=run_hash)
            else:
                await save_session_snapshot(sid, snapshot, persona_key, owner_id=current_user.id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/assistant/choice", summary="提交助手选择", include_in_schema=False)
async def submit_choice(
    payload: ChoiceSubmitRequest,
    current_user: ui_models.User = Depends(security.get_current_user),
) -> dict:
    """校验用户、会话、一次性票据和到期时间后返回用户消息。"""
    db = get_database_service()
    async with db.transaction():
        pending = await db.assistant_sessions.claim(
            hashlib.sha256(payload.choiceId.encode("utf-8")).hexdigest(),
            current_user.id, payload.sessionId, expected_tool="__choice__",
        )
        if pending is None:
            raise HTTPException(409, "选择已过期、已使用或不属于当前会话")
        choice = json.loads(pending["arguments_json"])
        option = next((item for item in choice["options"] if item["id"] == payload.optionId), None)
        if option is None or not await db.assistant_sessions.select_choice(
            payload.sessionId, current_user.id, payload.choiceId, payload.optionId,
        ):
            raise HTTPException(409, "无效的选择选项")
        content = f"我选择：{option['label']}"
        await db.assistant_sessions.append_user_message(payload.sessionId, current_user.id, content)
    return {"ok": True, "message": {"role": "user", "content": content}}


@router.post("/assistant/tool/execute", summary="执行已确认的写类工具", include_in_schema=False)
async def execute_confirmed_tool(
    payload: ToolExecuteRequest,
    request: Request,
    config_service = Depends(get_config_service),
    current_user: ui_models.User = Depends(security.get_current_user),
):
    """只执行当前用户会话中尚未消费且未过期的确认动作。"""
    db = get_database_service()
    async with db.transaction():
        pending = await db.assistant_sessions.claim(
            hashlib.sha256(payload.confirmationToken.encode("utf-8")).hexdigest(),
            current_user.id, payload.sessionId,
        )
    if pending is None:
        raise HTTPException(409, "确认已过期、已使用或不属于当前会话")
    if not payload.approved:
        return {"ok": False, "cancelled": True, "message": "已取消操作"}
    name = pending["tool_name"]
    arguments = json.loads(pending["arguments_json"])
    st = request.app.state
    session_factory = getattr(st, "db_session_factory", None)

    # 复刻 stream 接口的 context 组装逻辑
    context = {
        "session_factory": session_factory,
        "task_manager": getattr(st, "task_manager", None),
        "scraper_manager": getattr(st, "scraper_manager", None),
        "rate_limiter": getattr(st, "rate_limiter", None),
        "scheduler_manager": getattr(st, "scheduler_manager", None),
        "metadata_manager": getattr(st, "metadata_service", None),
        "ai_service": getattr(st, "ai_service", None),
        "title_recognition_manager": getattr(st, "title_recognition_manager", None),
        "config_service": config_service,
        "app": request.app,
        "current_user": current_user,
        "authorization": request.headers.get("authorization", ""),
        "owner_id": current_user.id,
        "session_id": payload.sessionId,
        "confirmed_action": True,
    }

    try:
        result = await registry.execute(name, arguments, context)
        if result.get("ok"):
            data = result.get("data") or {}
            msg = data.get("message") if isinstance(data, dict) else None
            return {"ok": True, "data": data, "message": msg or "操作已提交"}
        return {"ok": False, "error": result.get("error", "未知错误")}
    except Exception as exc:  # noqa: BLE001
        logger.error("执行已确认工具 %s 失败", name, exc_info=True)
        return {"ok": False, "error": "工具执行结果未知，请先检查实际状态，避免重复执行"}


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


class McpServerRequest(BaseModel):
    """MCP 服务器配置（新增/更新/测试共用）。

    字段按传输类型二选一：stdio 用 command/args/env，
    http 与 sse 用 url/headers。校验在 _validate_mcp_server 中完成。
    """
    name: str = Field(..., description="服务器名称，作为工具名前缀，须唯一")
    enabled: bool = Field(True, description="是否启用")
    transport: str = Field(TRANSPORT_STDIO, description="传输类型：stdio / http / sse")
    command: str = Field("", description="stdio：可执行命令，如 npx")
    args: List[str] = Field(default_factory=list, description="stdio：命令参数")
    env: Dict[str, str] = Field(default_factory=dict, description="stdio：环境变量")
    url: str = Field("", description="http/sse：服务地址")
    headers: Dict[str, str] = Field(default_factory=dict, description="http/sse：请求头")
    timeout: float = Field(30.0, description="单次请求超时（秒）", ge=1, le=300)
    permission: str = Field(
        PERMISSION_WRITE, description="权限级别：read_only 只读 / write 可写"
    )
    description: str = Field("", description="备注说明")


def _skill_to_dict(skill, content: Optional[str] = None) -> dict:
    """把 Skill 对象转成前端可用的 dict。

    正文按需加载：列表接口不传 content（省内存与传输量），
    详情接口传入由 SkillManager.get_content() 取到的正文。
    """
    return {
        "skillId": skill.skill_id,
        "name": skill.name,
        "version": skill.version,
        "description": skill.description,
        "allowedTools": skill.allowed_tools,
        "enabled": skill.enabled,
        "builtin": skill.builtin,  # 内置技能前端应禁用编辑/删除
        "content": content if content is not None else "",
    }


@router.get("/assistant/skills", summary="列出所有技能", include_in_schema=False)
async def list_skills_api(
    current_user: ui_models.User = Depends(security.get_current_user),
):
    """列出所有技能（含未启用）。"""
    skills = get_skill_manager().list_skills()
    return {"total": len(skills), "skills": [_skill_to_dict(s) for s in skills]}


@router.get("/assistant/skills/{skill_id}", summary="获取技能详情", include_in_schema=False)
async def get_skill_api(
    skill_id: str,
    current_user: ui_models.User = Depends(security.get_current_user),
):
    """获取单个技能的完整内容（正文此刻按需读取）。"""
    manager = get_skill_manager()
    skill = manager.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"技能 {skill_id} 不存在")
    return _skill_to_dict(skill, content=manager.get_content(skill_id) or "")


@router.post("/assistant/skills", status_code=201, summary="创建技能", include_in_schema=False)
async def create_skill_api(
    payload: SkillCreateRequest,
    current_user: ui_models.User = Depends(security.get_current_user),
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
    current_user: ui_models.User = Depends(security.get_current_user),
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
    current_user: ui_models.User = Depends(security.get_current_user),
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
    current_user: ui_models.User = Depends(security.get_current_user),
):
    """启用或停用技能（停用后不再注入 system prompt，文件保留）。"""
    try:
        skill = get_skill_manager().toggle_skill(skill_id, payload.enabled)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _skill_to_dict(skill)


@router.post("/assistant/skills/reload", summary="重载技能目录", include_in_schema=False)
async def reload_skills_api(
    current_user: ui_models.User = Depends(security.get_current_user),
):
    """热重载：重新扫描 config/skills/ 目录（用户手动放入文件后可调此接口生效）。"""
    manager = get_skill_manager()
    manager.reload()
    skills = manager.list_skills()
    return {"message": f"已重载 {len(skills)} 个技能", "total": len(skills)}


# ======================================================================
# MCP 服务器管理（外部工具接入）
# ======================================================================


def _validate_mcp_server(payload: McpServerRequest) -> McpServerConfig:
    """校验并构造 MCP 服务器配置。字段缺失按传输类型给出明确报错。"""
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="服务器名称不能为空")

    transport = (payload.transport or TRANSPORT_STDIO).strip().lower()
    if transport not in SUPPORTED_TRANSPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的传输类型：{transport}（可选：{', '.join(SUPPORTED_TRANSPORTS)}）",
        )
    if transport == TRANSPORT_STDIO and not (payload.command or "").strip():
        raise HTTPException(status_code=400, detail="stdio 传输必须填写启动命令")
    if transport != TRANSPORT_STDIO and not (payload.url or "").strip():
        raise HTTPException(status_code=400, detail=f"{transport} 传输必须填写服务地址")

    permission = (payload.permission or PERMISSION_WRITE).strip().lower()
    if permission not in {PERMISSION_READONLY, PERMISSION_WRITE}:
        raise HTTPException(
            status_code=400,
            detail=f"权限级别只能是 {PERMISSION_READONLY} 或 {PERMISSION_WRITE}",
        )

    return McpServerConfig(
        name=name,
        enabled=payload.enabled,
        transport=transport,
        command=(payload.command or "").strip(),
        args=payload.args or [],
        env=payload.env or {},
        url=(payload.url or "").strip(),
        headers=payload.headers or {},
        timeout=payload.timeout,
        permission=permission,
        description=(payload.description or "").strip(),
    )


@router.get("/assistant/mcp/servers", summary="列出 MCP 服务器", include_in_schema=False)
async def list_mcp_servers_api(
    config_service = Depends(get_config_service),
    current_user: ui_models.User = Depends(security.get_current_user),
):
    """列出所有已配置的 MCP 服务器。"""
    servers = await McpManager(config_service).get_servers()
    return {
        "total": len(servers),
        "servers": [s.model_dump() for s in servers],
        "transports": list(SUPPORTED_TRANSPORTS),
    }


@router.post(
    "/assistant/mcp/servers", status_code=201, summary="新增 MCP 服务器",
    include_in_schema=False,
)
async def create_mcp_server_api(
    payload: McpServerRequest,
    config_service = Depends(get_config_service),
    current_user: ui_models.User = Depends(security.get_current_user),
):
    """新增 MCP 服务器。名称作为工具命名空间，必须唯一。"""
    server = _validate_mcp_server(payload)
    manager = McpManager(config_service)
    servers = await manager.get_servers()
    if any(s.name == server.name for s in servers):
        raise HTTPException(status_code=400, detail=f"服务器名称 {server.name} 已存在")
    servers.append(server)
    await manager.save_servers(servers)
    logger.info(f"用户 '{current_user.username}' 新增 MCP 服务器: {server.name}")
    return server.model_dump()


@router.put(
    "/assistant/mcp/servers/{server_name}", summary="更新 MCP 服务器",
    include_in_schema=False,
)
async def update_mcp_server_api(
    server_name: str,
    payload: McpServerRequest,
    config_service = Depends(get_config_service),
    current_user: ui_models.User = Depends(security.get_current_user),
):
    """更新 MCP 服务器配置。改名时校验新名称不与其他条目冲突。"""
    server = _validate_mcp_server(payload)
    manager = McpManager(config_service)
    servers = await manager.get_servers()
    index = next((i for i, s in enumerate(servers) if s.name == server_name), -1)
    if index < 0:
        raise HTTPException(status_code=404, detail=f"MCP 服务器 {server_name} 不存在")
    if server.name != server_name and any(s.name == server.name for s in servers):
        raise HTTPException(status_code=400, detail=f"服务器名称 {server.name} 已存在")
    servers[index] = server
    await manager.save_servers(servers)
    logger.info(f"用户 '{current_user.username}' 更新 MCP 服务器: {server_name}")
    return server.model_dump()


@router.delete(
    "/assistant/mcp/servers/{server_name}", summary="删除 MCP 服务器",
    include_in_schema=False,
)
async def delete_mcp_server_api(
    server_name: str,
    config_service = Depends(get_config_service),
    current_user: ui_models.User = Depends(security.get_current_user),
):
    """删除 MCP 服务器，其工具随即从助手能力中移除。"""
    manager = McpManager(config_service)
    servers = await manager.get_servers()
    remaining = [s for s in servers if s.name != server_name]
    if len(remaining) == len(servers):
        raise HTTPException(status_code=404, detail=f"MCP 服务器 {server_name} 不存在")
    await manager.save_servers(remaining)
    logger.info(f"用户 '{current_user.username}' 删除 MCP 服务器: {server_name}")
    return {"message": f"MCP 服务器 {server_name} 已删除"}


@router.post(
    "/assistant/mcp/servers/test", summary="测试 MCP 服务器连通性",
    include_in_schema=False,
)
async def test_mcp_server_api(
    payload: McpServerRequest,
    config_service = Depends(get_config_service),
    current_user: ui_models.User = Depends(security.get_current_user),
):
    """连通性测试：直接用传入配置试连并发现工具，无需先保存。"""
    server = _validate_mcp_server(payload)
    return await McpManager(config_service).test_server(server)


@router.get("/assistant/mcp/tools", summary="列出已接入的 MCP 工具", include_in_schema=False)
async def list_mcp_tools_api(
    config_service = Depends(get_config_service),
    current_user: ui_models.User = Depends(security.get_current_user),
):
    """列出所有已启用服务器当前可用的工具（走发现缓存）。"""
    specs = await McpManager(config_service).list_enabled_tool_specs()
    return {
        "total": len(specs),
        "tools": [
            {
                "serverName": s.server_name,
                "originalName": s.original_name,
                "agentToolName": s.agent_tool_name,
                "description": s.description,
                "permission": s.permission.value,
            }
            for s in specs
        ],
    }


@router.post("/assistant/mcp/refresh", summary="刷新 MCP 工具缓存", include_in_schema=False)
async def refresh_mcp_tools_api(
    config_service = Depends(get_config_service),
    current_user: ui_models.User = Depends(security.get_current_user),
):
    """清空工具发现缓存并立即重新发现（服务端工具变更后可调此接口）。"""
    manager = McpManager(config_service)
    await manager.invalidate_cache()
    specs = await manager.list_enabled_tool_specs()
    return {"message": f"已刷新，当前可用 {len(specs)} 个 MCP 工具", "total": len(specs)}
