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
from typing import Any, Dict, List, Optional

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
    sessionId: Optional[str] = Field(None, description="会话 ID（用于断流恢复标记处理状态）")


class ToolExecuteRequest(BaseModel):
    """执行用户已确认的写类工具（方案 A：前端直接执行，渠道端架构统一）"""
    name: str = Field(..., description="工具名")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="工具参数")


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
        # API 网关工具所需：应用实例用于内部 ASGI 调用，用户身份用于覆盖鉴权依赖
        "app": request.app,
        "current_user": current_user,
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


@router.post("/assistant/tool/execute", summary="执行已确认的写类工具", include_in_schema=False)
async def execute_confirmed_tool(
    payload: ToolExecuteRequest,
    request: Request,
    config_manager: ConfigManager = Depends(get_config_manager),
    current_user: models.User = Depends(security.get_current_user),
):
    """用户点确认卡后，前端直接执行写工具，跳过 LLM 重新决策（断开循环）。

    返回 {ok, data|error, message}，前端把结果作为 tool 消息追加到对话，
    LLM 看到执行结果后生成自然语言回复。
    """
    st = request.app.state
    session_factory = getattr(st, "db_session_factory", None)

    # 复刻 stream 接口的 context 组装逻辑
    context = {
        "session_factory": session_factory,
        "task_manager": getattr(st, "task_manager", None),
        "scraper_manager": getattr(st, "scraper_manager", None),
        "rate_limiter": getattr(st, "rate_limiter", None),
        "scheduler_manager": getattr(st, "scheduler_manager", None),
        "metadata_manager": getattr(st, "metadata_manager", None),
        "ai_matcher_manager": getattr(st, "ai_matcher_manager", None),
        "title_recognition_manager": getattr(st, "title_recognition_manager", None),
        "config_manager": config_manager,
        # API 网关工具所需：应用实例用于内部 ASGI 调用，用户身份用于覆盖鉴权依赖
        "app": request.app,
        "current_user": current_user,
    }

    try:
        result = await registry.execute(payload.name, payload.arguments, context)
        if result.get("ok"):
            data = result.get("data") or {}
            msg = data.get("message") if isinstance(data, dict) else None
            return {"ok": True, "data": data, "message": msg or "操作已提交"}
        return {"ok": False, "error": result.get("error", "未知错误")}
    except Exception as e:  # noqa: BLE001
        logger.error(f"执行工具 {payload.name} 失败: {e}", exc_info=True)
        return {"ok": False, "error": f"工具执行出错：{e}"}


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
    """获取单个技能的完整内容（正文此刻按需读取）。"""
    manager = get_skill_manager()
    skill = manager.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"技能 {skill_id} 不存在")
    return _skill_to_dict(skill, content=manager.get_content(skill_id) or "")


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
    config_manager: ConfigManager = Depends(get_config_manager),
    current_user: models.User = Depends(security.get_current_user),
):
    """列出所有已配置的 MCP 服务器。"""
    servers = await McpManager(config_manager).get_servers()
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
    config_manager: ConfigManager = Depends(get_config_manager),
    current_user: models.User = Depends(security.get_current_user),
):
    """新增 MCP 服务器。名称作为工具命名空间，必须唯一。"""
    server = _validate_mcp_server(payload)
    manager = McpManager(config_manager)
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
    config_manager: ConfigManager = Depends(get_config_manager),
    current_user: models.User = Depends(security.get_current_user),
):
    """更新 MCP 服务器配置。改名时校验新名称不与其他条目冲突。"""
    server = _validate_mcp_server(payload)
    manager = McpManager(config_manager)
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
    config_manager: ConfigManager = Depends(get_config_manager),
    current_user: models.User = Depends(security.get_current_user),
):
    """删除 MCP 服务器，其工具随即从助手能力中移除。"""
    manager = McpManager(config_manager)
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
    config_manager: ConfigManager = Depends(get_config_manager),
    current_user: models.User = Depends(security.get_current_user),
):
    """连通性测试：直接用传入配置试连并发现工具，无需先保存。"""
    server = _validate_mcp_server(payload)
    return await McpManager(config_manager).test_server(server)


@router.get("/assistant/mcp/tools", summary="列出已接入的 MCP 工具", include_in_schema=False)
async def list_mcp_tools_api(
    config_manager: ConfigManager = Depends(get_config_manager),
    current_user: models.User = Depends(security.get_current_user),
):
    """列出所有已启用服务器当前可用的工具（走发现缓存）。"""
    specs = await McpManager(config_manager).list_enabled_tool_specs()
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
    config_manager: ConfigManager = Depends(get_config_manager),
    current_user: models.User = Depends(security.get_current_user),
):
    """清空工具发现缓存并立即重新发现（服务端工具变更后可调此接口）。"""
    manager = McpManager(config_manager)
    await manager.invalidate_cache()
    specs = await manager.list_enabled_tool_specs()
    return {"message": f"已刷新，当前可用 {len(specs)} 个 MCP 工具", "total": len(specs)}
