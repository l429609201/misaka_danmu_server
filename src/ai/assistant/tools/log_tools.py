"""AI 日志工具禁用：自由文本无法可靠剔除流控配置和凭据。"""

from typing import Any, Dict


async def _list_log_files(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """拒绝列举日志文件。"""
    return {"error": "AI 日志访问已禁用"}


async def _search_logs(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """拒绝检索自由文本日志。"""
    return {"error": "AI 日志访问已禁用"}


async def _read_log_file(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """拒绝读取自由文本日志。"""
    return {"error": "AI 日志访问已禁用"}


def register_log_tools() -> None:
    """隔离能力缺失时不注册任何日志工具。"""
    return None
