"""
御坂助手 · 跨会话长期记忆（经验沉淀）
------------------------------------------------------------
参考 MoviePilot app/agent/learning 的思路，只保留最小可用形态：
- 用户偏好、纠正过的做法、成功经验以短条目形式持久化；
- 每轮对话把全部条目注入 system prompt，新会话也能“记得”；
- 写入/删除走工具确认卡，由用户把关，不做自动抽取，避免把错误结论沉淀下来。

存储与技能同目录层级：config/assistant_memory.json（Docker: /app/config/）。
"""

import json
import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .security_gateway import contains_forbidden_control_content
from .skill_model import get_skills_base_dir

logger = logging.getLogger(__name__)

# 条目上限与单条长度：全部注入提示词，必须控制 token 开销
MAX_MEMORIES = 50
MAX_MEMORY_CHARS = 300
MEMORY_CATEGORIES = ("preference", "correction", "experience", "fact")

_lock = threading.Lock()


def _memory_file() -> Optional[Path]:
    """记忆文件路径；技能目录未初始化（启动前/单测）时返回 None。"""
    skills_dir = get_skills_base_dir()
    if skills_dir is None:
        return None
    return Path(skills_dir).parent / "assistant_memory.json"


def _load() -> List[Dict[str, str]]:
    """读取全部记忆；文件缺失或损坏时返回空列表（损坏只记日志，不覆盖原文件）。"""
    path = _memory_file()
    if path is None or not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [m for m in data if isinstance(m, dict) and m.get("id") and m.get("content")]
    except (OSError, ValueError) as e:
        logger.warning(f"御坂记忆文件读取失败，本次按空记忆处理: {e}")
        return []


def _save(memories: List[Dict[str, str]]) -> None:
    """原子写入记忆文件（先写临时文件再替换，避免中途崩溃留下半截 JSON）。"""
    path = _memory_file()
    if path is None:
        raise RuntimeError("记忆存储目录尚未初始化")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(memories, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def list_memories() -> List[Dict[str, str]]:
    """列出全部长期记忆（按创建时间先后）。"""
    with _lock:
        return _load()


def add_memory(content: str, category: str = "preference") -> Dict[str, str]:
    """新增一条记忆；内容重复时返回已有条目，超出上限时淘汰最早的一条。

    Args:
        content: 记忆正文（一句话陈述）。
        category: 分类，取值见 MEMORY_CATEGORIES。

    Returns:
        Dict[str, str]: 新增（或已存在）的记忆条目。

    Raises:
        ValueError: 内容为空、过长、分类非法或包含禁止内容。
    """
    content = (content or "").strip()
    if not content:
        raise ValueError("记忆内容不能为空")
    if len(content) > MAX_MEMORY_CHARS:
        raise ValueError(f"单条记忆不能超过 {MAX_MEMORY_CHARS} 字，请精简为一句话")
    if category not in MEMORY_CATEGORIES:
        raise ValueError(f"分类必须是 {', '.join(MEMORY_CATEGORIES)} 之一")
    if contains_forbidden_control_content(content):
        raise ValueError("记忆内容包含禁止保存的流控/凭据信息")
    with _lock:
        memories = _load()
        for m in memories:
            if m["content"] == content:
                return m
        entry = {
            "id": uuid.uuid4().hex[:8],
            "category": category,
            "content": content,
            "createdAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        memories.append(entry)
        # 超限淘汰最早条目，保证注入提示词的体量有上界
        memories = memories[-MAX_MEMORIES:]
        _save(memories)
        return entry


def delete_memory(memory_id: str) -> bool:
    """按 ID 删除一条记忆；不存在时返回 False。"""
    with _lock:
        memories = _load()
        kept = [m for m in memories if m["id"] != memory_id]
        if len(kept) == len(memories):
            return False
        _save(kept)
        return True


def build_memory_prompt() -> str:
    """把长期记忆渲染为 system prompt 片段；没有记忆时返回空串。"""
    memories = list_memories()
    if not memories:
        return ""
    lines = [f"- [{m['id']}·{m.get('category', 'preference')}] {m['content']}" for m in memories]
    return (
        "以下是用户确认保存的长期记忆（跨会话有效）。回答时遵循其中的偏好与经验；"
        "若与用户本轮明确要求冲突，以本轮要求为准，并可建议用 forget_memory 删除过时条目：\n"
        + "\n".join(lines)
    )
