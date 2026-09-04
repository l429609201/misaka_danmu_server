"""
御坂助手 · UI 文档检索工具（Doc Tools）
------------------------------------------------------------
解决的问题：助手原先只有「后端概念 + API 工具」的知识，用户问
「XX 页面那个按钮是干什么的」「为什么这个开关点不了」时无从下手，
只能凭印象硬答，导致答非所问。

设计：knowledge/ui_guide.md 按 `## 章节` 拆成检索单元，
LLM 用 search_docs(query) 按需取回 1~3 段原文再回答，
token 可控（不常驻 system prompt），且文档更新只改 md 不动代码。

只读工具：search_docs / list_doc_sections
"""

import logging
from typing import Any, Dict

from ..knowledge_base import get_knowledge_base
from ..security_gateway import ToolPermission
from .base import Tool, registry

logger = logging.getLogger(__name__)

# 单次返回的正文上限：避免超长章节挤爆上下文
_MAX_BODY_CHARS = 4000


async def _search_docs(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """检索 UI 功能手册，返回最相关的章节原文。"""
    query = (arguments.get("query") or "").strip()
    if not query:
        return {"error": "缺少 query，请传入用户想了解的功能名称或问题关键词"}

    try:
        limit = int(arguments.get("limit") or 3)
    except (TypeError, ValueError):
        limit = 3
    limit = max(1, min(limit, 5))

    kb = get_knowledge_base()
    hits = kb.search(query, limit=limit)
    if not hits:
        return {
            "query": query,
            "total": 0,
            "sections": [],
            "hint": (
                "手册中没有匹配章节。可换用更口语化或更具体的关键词重试（如把"
                "「怎么让弹幕对齐」换成「偏移」），或调 list_doc_sections 查看全部可查章节。"
                "确认手册确实没有相关内容时，如实告诉用户你不确定，不要凭印象编造界面操作。"
            ),
        }

    return {
        "query": query,
        "total": len(hits),
        "sections": [
            {
                "title": section.title,
                "aliases": section.aliases,
                "content": section.body[:_MAX_BODY_CHARS],
                "truncated": len(section.body) > _MAX_BODY_CHARS,
                "score": round(score, 2),
            }
            for section, score in hits
        ],
        "hint": (
            "以上是官方文档原文。请基于这些内容回答，"
            "涉及按钮名称、入口路径、参数取值时必须与原文一致，不要改写成自己的猜测。"
        ),
    }


async def _list_doc_sections(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """列出手册全部章节标题与别名，用于判断某个话题是否有文档覆盖。"""
    kb = get_knowledge_base()
    titles = kb.list_titles()
    return {
        "total": len(titles),
        "sections": titles,
        "hint": "确定目标章节后，用 search_docs 传该章节标题或其别名取回正文。",
    }


def register_doc_tools() -> None:
    """注册 UI 文档检索工具（全部只读，可随时调用）。"""
    registry.register(Tool(
        name="search_docs",
        description=(
            "检索本系统的《界面功能手册》（官方文档原文）。"
            "用户询问界面功能、页面按钮作用、操作步骤、配置项含义、"
            "「为什么某个开关点不了」「某个按钮点了会发生什么」这类问题时，"
            "必须先调此工具拿到原文再回答，不要凭印象描述界面。"
            "传入用户的原话关键词即可，支持口语化表达。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索关键词，建议直接用用户原话中的功能名（如「拆分数据源」「不导入」「预下载」「偏移」）",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回章节数，默认 3，最大 5。问题笼统时可适当加大",
                },
            },
            "required": ["query"],
        },
        permission=ToolPermission.READ_ONLY,
        executor=_search_docs,
        running_label="正在查阅功能手册",
    ))
    registry.register(Tool(
        name="list_doc_sections",
        description=(
            "列出《界面功能手册》的全部章节标题与别名。"
            "当 search_docs 未命中、或需要判断某话题是否有文档覆盖时使用。"
        ),
        parameters={"type": "object", "properties": {}},
        permission=ToolPermission.READ_ONLY,
        executor=_list_doc_sections,
        running_label="正在列出手册章节",
    ))
