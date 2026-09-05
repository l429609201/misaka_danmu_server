"""
御坂助手 · 技能管理工具（Skill Tools）
------------------------------------------------------------
渐进式披露设计（参考 MoviePilot v3）：
- system prompt 里只放技能的 name + description（省 token）
- LLM 判断需要某技能时，调 read_skill 取全文作业指导书

只读工具：list_skills / read_skill
写工具（WRITE 权限，agent 在对话中先说明再执行）：
    create_skill / update_skill / delete_skill / toggle_skill

技能存储在持久化目录 config/skills/<skill_id>/SKILL.md，
用户可手动放文件，也可让御坂代写（"造 skill 的 skill"）。
"""

import logging
from typing import Any, Dict

from ..api_gateway.contracts import ActionEffect
from ..security_gateway import ToolPermission
from ..skill_manager import get_skill_manager
from .base import Tool, registry

logger = logging.getLogger(__name__)


async def _list_skills(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """列出所有可用技能（含启用状态与触发描述）。"""
    enabled_only = bool(arguments.get("enabledOnly", False))
    manager = get_skill_manager()
    skills = manager.list_skills(enabled_only=enabled_only)
    return {
        "total": len(skills),
        "skills": [
            {
                "skillId": s.skill_id,
                "name": s.name,
                "version": s.version,
                "description": s.description,
                "enabled": s.enabled,
                "allowedTools": s.allowed_tools,
                "builtin": s.builtin,  # 内置技能随版本发布，不可改不可删
            }
            for s in skills
        ],
        "hint": (
            "需要某技能的详细步骤时调 read_skill(skillId) 取全文。"
            "builtin=true 的是内置技能，不能 update/delete，只能 toggle 停用；"
            "用户想定制时应用 create_skill 新建。"
        ),
    }


async def _read_skill(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """读取某技能的完整作业指导书（正文按需加载，不常驻内存）。"""
    skill_id = (arguments.get("skillId") or "").strip()
    if not skill_id:
        return {"error": "缺少 skillId"}
    manager = get_skill_manager()
    skill = manager.get_skill(skill_id)
    if not skill:
        return {"error": f"技能 {skill_id} 不存在，可先用 list_skills 查看可用技能"}
    content = manager.get_content(skill_id)
    if content is None:
        return {"error": f"技能 {skill_id} 正文读取失败，请检查日志"}
    return {
        "skillId": skill.skill_id,
        "name": skill.name,
        "version": skill.version,
        "description": skill.description,
        "enabled": skill.enabled,
        "allowedTools": skill.allowed_tools,
        "builtin": skill.builtin,
        "content": content,
    }


async def _create_skill(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """创建新技能（WRITE，需用户确认）。落盘到 config/skills/<skillId>/SKILL.md。"""
    skill_id = (arguments.get("skillId") or "").strip()
    name = (arguments.get("name") or "").strip()
    description = (arguments.get("description") or "").strip()
    content = (arguments.get("content") or "").strip()
    allowed_tools = arguments.get("allowedTools") or []
    if isinstance(allowed_tools, str):
        allowed_tools = allowed_tools.split()

    if not all([skill_id, name, description, content]):
        return {"error": "需要 skillId / name / description / content 四项"}

    try:
        skill = get_skill_manager().create_skill(
            skill_id=skill_id, name=name, description=description,
            content=content, allowed_tools=allowed_tools,
        )
    except (ValueError, RuntimeError) as e:
        return {"error": str(e)}

    return {
        "ok": True,
        "skillId": skill.skill_id,
        "path": str(skill.file_path),
        "message": f"技能「{skill.name}」已创建，下次对话即可自动识别使用",
    }


async def _update_skill(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """更新现有技能（WRITE，需用户确认）。未提供的字段保持不变，版本号自动递增。"""
    skill_id = (arguments.get("skillId") or "").strip()
    if not skill_id:
        return {"error": "缺少 skillId"}
    allowed_tools = arguments.get("allowedTools")
    if isinstance(allowed_tools, str):
        allowed_tools = allowed_tools.split()

    try:
        skill = get_skill_manager().update_skill(
            skill_id=skill_id,
            name=arguments.get("name"),
            description=arguments.get("description"),
            content=arguments.get("content"),
            allowed_tools=allowed_tools,
        )
    except (ValueError, RuntimeError) as e:
        return {"error": str(e)}

    return {
        "ok": True,
        "skillId": skill.skill_id,
        "version": skill.version,
        "message": f"技能「{skill.name}」已更新至 v{skill.version}",
    }


async def _delete_skill(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """删除技能（WRITE，需用户确认）。会删除整个技能目录，不可恢复。"""
    skill_id = (arguments.get("skillId") or "").strip()
    if not skill_id:
        return {"error": "缺少 skillId"}
    try:
        get_skill_manager().delete_skill(skill_id)
    except (ValueError, RuntimeError) as e:
        return {"error": str(e)}
    return {"ok": True, "skillId": skill_id, "message": f"技能 {skill_id} 已删除"}


async def _toggle_skill(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """启用/停用技能（WRITE，需用户确认）。停用后不再注入 system prompt。"""
    skill_id = (arguments.get("skillId") or "").strip()
    enabled = arguments.get("enabled")
    if not skill_id:
        return {"error": "缺少 skillId"}
    if enabled is None:
        return {"error": "缺少 enabled（true 启用 / false 停用）"}
    try:
        skill = get_skill_manager().toggle_skill(skill_id, bool(enabled))
    except (ValueError, RuntimeError) as e:
        return {"error": str(e)}
    return {
        "ok": True,
        "skillId": skill.skill_id,
        "enabled": skill.enabled,
        "message": f"技能「{skill.name}」已{'启用' if skill.enabled else '停用'}",
    }


def register_skill_tools() -> None:
    """注册技能管理工具（2 只读 + 4 写）。"""
    registry.register(Tool(
        name="list_skills",
        description="列出所有可用技能（名称、触发时机描述、启用状态）。system prompt 里已包含摘要，此工具用于按需查看完整列表。",
        parameters={
            "type": "object",
            "properties": {
                "enabledOnly": {"type": "boolean", "description": "是否只返回已启用的技能，默认 false（返回全部）"}
            },
        },
        permission=ToolPermission.READ_ONLY,
        executor=_list_skills,
        running_label="正在列出技能",
    ))
    registry.register(Tool(
        name="read_skill",
        description="读取某技能的完整作业指导书（详细步骤、注意事项）。判断需要某技能时调此工具获取全文。",
        parameters={
            "type": "object",
            "properties": {
                "skillId": {"type": "string", "description": "技能 ID（从 list_skills 或 system prompt 获取）"}
            },
            "required": ["skillId"],
        },
        permission=ToolPermission.READ_ONLY,
        executor=_read_skill,
        running_label="正在读取技能详情",
    ))
    registry.register(Tool(
        name="create_skill",
        description="创建新技能（需确认）。用户要求创建自定义技能时使用。御坂可自己造 skill（'造 skill 的 skill'），但必须先和用户确认内容。",
        parameters={
            "type": "object",
            "properties": {
                "skillId": {"type": "string", "description": "技能 ID（小写短横线，如 import-anime-batch）"},
                "name": {"type": "string", "description": "技能名称（中文，如「批量导入综艺」）"},
                "description": {"type": "string", "description": "触发时机描述（告诉 LLM 何时该用此技能）"},
                "content": {"type": "string", "description": "作业指导书正文（Markdown 格式，详细步骤与注意事项）"},
                "allowedTools": {"type": "array", "items": {"type": "string"},
                                 "description": "该技能推荐使用的工具列表（仅提示用，不强制拦截）"},
            },
            "required": ["skillId", "name", "description", "content"],
        },
        permission=ToolPermission.WRITE,
        executor=_create_skill,
        running_label="正在创建技能",
    ))
    registry.register(Tool(
        name="update_skill",
        description="更新现有技能（需确认）。未提供的字段保持不变，版本号自动递增。",
        parameters={
            "type": "object",
            "properties": {
                "skillId": {"type": "string", "description": "技能 ID"},
                "name": {"type": "string", "description": "新名称（可选）"},
                "description": {"type": "string", "description": "新触发描述（可选）"},
                "content": {"type": "string", "description": "新正文（可选）"},
                "allowedTools": {"type": "array", "items": {"type": "string"},
                                 "description": "新工具列表（可选）"},
            },
            "required": ["skillId"],
        },
        permission=ToolPermission.WRITE,
        executor=_update_skill,
        running_label="正在更新技能",
    ))
    registry.register(Tool(
        name="delete_skill",
        description="删除技能（需确认）。会删除整个技能目录，不可恢复。",
        parameters={
            "type": "object",
            "properties": {
                "skillId": {"type": "string", "description": "技能 ID"}
            },
            "required": ["skillId"],
        },
        permission=ToolPermission.WRITE,
        executor=_delete_skill,
        running_label="正在删除技能",
        # 整个技能目录被移除，文件无法恢复
        effect=ActionEffect.DESTRUCTIVE_WRITE,
    ))
    registry.register(Tool(
        name="toggle_skill",
        description="启用/停用技能（需确认）。停用后不再注入 system prompt，但文件仍保留。",
        parameters={
            "type": "object",
            "properties": {
                "skillId": {"type": "string", "description": "技能 ID"},
                "enabled": {"type": "boolean", "description": "true 启用 / false 停用"}
            },
            "required": ["skillId", "enabled"],
        },
        permission=ToolPermission.WRITE,
        executor=_toggle_skill,
        running_label="正在切换技能状态",
    ))
