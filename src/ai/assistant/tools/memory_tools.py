"""
御坂助手 · 长期记忆工具
------------------------------------------------------------
只读：list_memories
写（WRITE，需 Web 确认卡）：remember / forget_memory

记忆全文已在 system prompt 中注入，list_memories 用于需要查看 ID 再删除等场景。
"""

import logging
from typing import Any, Dict

from ..memory_store import MEMORY_CATEGORIES, add_memory, delete_memory, list_memories
from ..security_gateway import ToolPermission
from .base import Tool, registry

logger = logging.getLogger(__name__)


async def _list_memories(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """列出全部长期记忆。"""
    memories = list_memories()
    return {"total": len(memories), "memories": memories}


async def _remember(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """保存一条长期记忆（需用户确认）。"""
    try:
        entry = add_memory(arguments.get("content") or "", arguments.get("category") or "preference")
    except ValueError as e:
        return {"error": str(e)}
    except (OSError, RuntimeError) as e:
        logger.error(f"保存御坂记忆失败: {e}")
        return {"error": "记忆保存失败，请查看服务端日志"}
    return {"saved": entry}


async def _forget_memory(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """删除一条长期记忆（需用户确认）。"""
    memory_id = (arguments.get("memoryId") or "").strip()
    if not memory_id:
        return {"error": "缺少 memoryId"}
    try:
        removed = delete_memory(memory_id)
    except (OSError, RuntimeError) as e:
        logger.error(f"删除御坂记忆失败: {e}")
        return {"error": "记忆删除失败，请查看服务端日志"}
    if not removed:
        return {"error": f"记忆 {memory_id} 不存在，可先用 list_memories 查看"}
    return {"deleted": memory_id}


def register_memory_tools() -> None:
    """注册长期记忆相关工具。"""
    registry.register(Tool(
        name="list_memories",
        description="列出用户确认保存的长期记忆（含 ID）。system prompt 已包含记忆内容，仅在需要 ID（如删除）时调用。",
        parameters={"type": "object", "properties": {}},
        permission=ToolPermission.READ_ONLY,
        executor=_list_memories,
        running_label="正在读取长期记忆",
    ))
    registry.register(Tool(
        name="remember",
        description=(
            "保存一条跨会话长期记忆（需确认）。用于用户明确说“记住…”、纠正了你的做法、"
            "或某个操作经验值得下次复用时。内容写成一句独立可读的陈述，不要保存密钥、Token 等凭据。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "记忆正文（一句话，≤300 字）"},
                "category": {"type": "string", "enum": list(MEMORY_CATEGORIES),
                             "description": "preference=偏好，correction=纠正，experience=经验，fact=事实"},
            },
            "required": ["content"],
        },
        permission=ToolPermission.WRITE,
        executor=_remember,
        running_label="正在保存长期记忆",
    ))
    registry.register(Tool(
        name="forget_memory",
        description="删除一条长期记忆（需确认）。记忆过时或用户要求忘掉时使用，memoryId 见 system prompt 或 list_memories。",
        parameters={
            "type": "object",
            "properties": {"memoryId": {"type": "string", "description": "记忆 ID"}},
            "required": ["memoryId"],
        },
        permission=ToolPermission.WRITE,
        executor=_forget_memory,
        running_label="正在删除长期记忆",
    ))
