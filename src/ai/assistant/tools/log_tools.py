"""御坂助手 · 日志查询工具
------------------------------------------------------------
均为 READ_ONLY：列出日志文件、跨文件检索、读取指定文件。
用于让助手自助排障（"刚才导入为什么失败"），无需用户去翻日志页面。

日志内容已由 log_manager 的 SensitiveInfoFilter 脱敏（api_key/token/Cookie
均替换为 ****），可安全回灌给模型。

注意：这里直接从 src.services.log_manager 导入，不走 src.services 包入口。
why：包入口会加载 notification_service → llm_menu → src.ai.assistant，
形成循环导入（同 readonly_tools.py 的处理）。
"""

import asyncio
import logging
from typing import Any, Dict

from src.services.log_manager import list_log_files, read_log_file, search_logs
from ..security_gateway import ToolPermission
from .base import Tool, registry

logger = logging.getLogger(__name__)

# 单次回灌给模型的行数上限，避免长堆栈打爆上下文
_MAX_LINES = 30


async def _list_log_files(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """列出可用日志文件及大小、修改时间。"""
    files = await asyncio.to_thread(list_log_files)
    return {
        "total": len(files),
        "files": [
            {
                "name": f["name"],
                "sizeKB": round(f["size"] / 1024, 1),
                "modified": f["modified"],
            }
            for f in files
        ],
    }


async def _search_logs(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """跨日志文件检索关键词/级别。"""
    keyword = (arguments.get("keyword") or "").strip()
    level = (arguments.get("level") or "").strip()
    filename = (arguments.get("filename") or "").strip()
    limit = arguments.get("limit") or _MAX_LINES
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = _MAX_LINES

    if not keyword and not level:
        return {"error": "keyword 和 level 至少提供一个，否则会返回整个日志"}

    try:
        return await asyncio.to_thread(
            search_logs, keyword, level, filename, limit
        )
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"检索日志失败: {e}", exc_info=True)
        return {"error": f"检索日志失败: {e}"}


async def _read_log_file(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """读取指定日志文件的尾部内容。"""
    filename = (arguments.get("filename") or "").strip()
    if not filename:
        return {"error": "缺少 filename，先用 list_log_files 查看可用文件"}

    tail = arguments.get("tail") or _MAX_LINES
    try:
        tail = max(1, min(int(tail), 100))
    except (TypeError, ValueError):
        tail = _MAX_LINES

    keyword = (arguments.get("keyword") or "").strip()
    try:
        result = await asyncio.to_thread(read_log_file, filename, tail, keyword, 0)
    except FileNotFoundError:
        return {"error": f"日志文件不存在: {filename}"}
    except ValueError as e:
        return {"error": str(e)}
    except IOError as e:
        return {"error": str(e)}

    # 单行截断，与 search_logs 保持一致的上下文预算
    lines = [ln if len(ln) <= 300 else ln[:300] + "…" for ln in result.get("lines", [])]
    return {
        "file": filename,
        "lines": lines,
        "total": result.get("total", len(lines)),
        "hasMore": result.get("hasMore", False),
    }


def register_log_tools() -> None:
    """注册日志查询工具（全部 READ_ONLY）。"""
    registry.register(Tool(
        name="list_log_files",
        description=(
            "列出服务器上可用的日志文件及体积、修改时间。app.log 是主日志（各模块运行记录），"
            "scraper_responses.log 是弹幕源原始响应，metadata_responses.log 是元数据源响应，"
            "ai_responses.log 是 AI 调用记录，bot_raw.log 是 Bot 原始交互，"
            "webhook_raw.log 是 Webhook 原始请求。带 .1/.2 后缀的是轮转归档。"
        ),
        parameters={"type": "object", "properties": {}},
        permission=ToolPermission.READ_ONLY,
        executor=_list_log_files,
        running_label="正在列出日志文件",
    ))
    registry.register(Tool(
        name="search_logs",
        description=(
            "跨所有日志文件检索，排查报错的首选工具。不指定 filename 时会扫描全部活跃日志"
            "（不含轮转归档），按最近修改优先，结果按时间倒序。keyword 支持空格分隔多个词"
            "（需全部命中）；level 按级别过滤且包含更高级别（填 WARNING 也会返回 ERROR）。"
            "keyword 和 level 至少给一个。用户报「导入失败/搜不到/报错」时先用这个定位。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "空格分隔的关键词，需全部命中，如 '腾讯 超时'"},
                "level": {"type": "string", "enum": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                          "description": "日志级别，含更高级别"},
                "filename": {"type": "string", "description": "限定单个文件，可选；来自 list_log_files"},
                "limit": {"type": "integer", "description": f"最多返回条数，默认 {_MAX_LINES}，上限 100"},
            },
        },
        permission=ToolPermission.READ_ONLY,
        executor=_search_logs,
        running_label="正在检索日志",
    ))
    registry.register(Tool(
        name="read_log_file",
        description=(
            "读取指定日志文件的最新内容（从末尾取）。适合在 search_logs 定位到某个文件后"
            "查看该文件近期的完整上下文。filename 来自 list_log_files。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "日志文件名，如 app.log"},
                "tail": {"type": "integer", "description": f"返回最新的多少行，默认 {_MAX_LINES}，上限 100"},
                "keyword": {"type": "string", "description": "可选，仅返回含该关键词的行"},
            },
            "required": ["filename"],
        },
        permission=ToolPermission.READ_ONLY,
        executor=_read_log_file,
        running_label="正在读取日志",
    ))
