"""管理员排错用的只读日志检索工具。

只向站内管理员会话导出（工具名以 code_ 开头，与代码工具同一道门禁）；
逐行剔除流控/配额与凭据相关内容后再返回，剩余行再做一次凭据片段脱敏。
"""

import asyncio
import re
from typing import Any, Dict, List

from src.ai.assistant.security_gateway import ToolPermission
from src.services.assistant_code_service import code_authorized
from src.utils.model_content_policy import contains_forbidden_control_content
from src.workflows.system.logs_flow import search_logs
from .base import Tool, registry

# 含凭据赋值、私钥、带账号密码 URL 的整行直接丢弃，避免半截脱敏仍泄漏
_SECRET_LINE = re.compile(
    r'-----BEGIN [^-]*PRIVATE KEY|https?://[^\s/:]+:[^\s/@]+@|'
    r'(?:password|passwd|secret|cookie|authorization)\s*[=:]', re.I)
# 其余行里的 token/key 查询参数、Bearer 头等只替换值
_SECRET_VALUE = re.compile(
    r'((?:api[_-]?key|apikey|token|access[_-]?token|sessdata|bili_jct|_m_h5_tk|key)=)[^&\s"\']+|'
    r'(Bearer\s+)[A-Za-z0-9._\-]+', re.I)
_MAX_LIMIT = 80


def _redact_line(line: str) -> str:
    """替换行内凭据值，保留键名方便定位问题。"""
    return _SECRET_VALUE.sub(lambda m: (m.group(1) or m.group(2)) + '***', line)


def _filter_matches(matches: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], int]:
    """逐行过滤：命中流控或凭据特征的整行丢弃，返回保留行与丢弃数。"""
    kept: List[Dict[str, Any]] = []
    dropped = 0
    for item in matches:
        line = item.get('line', '')
        if contains_forbidden_control_content(line) or _SECRET_LINE.search(line):
            dropped += 1
            continue
        kept.append({**item, 'line': _redact_line(line)})
    return kept, dropped


async def _search_logs(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """按关键词（空格分隔为 AND）和最低级别检索最新日志，最新优先。"""
    if not code_authorized(context):
        return {'error': '仅站内管理员当前会话可检索日志'}
    keyword = str(arguments.get('keyword') or '')[:200]
    level = str(arguments.get('level') or '')
    # 关键词本身指向流控/配额逻辑时直接拒绝，不靠事后过滤兜底
    if contains_forbidden_control_content(keyword):
        return {'error': '流控与配额相关内容受保护，不提供检索'}
    try:
        limit = max(1, min(int(arguments.get('limit') or 30), _MAX_LIMIT))
    except (TypeError, ValueError):
        limit = 30
    # 多取一些再过滤，避免丢弃敏感行后结果过少
    result = await asyncio.to_thread(search_logs, keyword, level, '', min(limit * 2, 100), 400)
    kept, dropped = _filter_matches(result.get('matches', []))
    return {
        'matches': kept[:limit],
        'total': result.get('total', 0),
        'truncated': result.get('truncated', False) or len(kept) > limit,
        'droppedSensitiveLines': dropped,
        'scannedFiles': result.get('scannedFiles', []),
    }


def register_log_tools() -> None:
    """注册管理员只读日志检索工具；导出受代码模式门禁控制。"""
    registry.register(Tool(
        name='code_search_logs',
        description=('检索服务端最新日志（最新优先），用于排查报错与复现问题。keyword 空格分隔为 AND；'
                     'level 取 DEBUG/INFO/WARNING/ERROR/CRITICAL 表示最低级别。'
                     '含流控或凭据特征的行已被剔除，不要尝试换写法绕过。'),
        parameters={'type': 'object', 'properties': {
            'keyword': {'type': 'string'},
            'level': {'type': 'string', 'enum': ['', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': _MAX_LIMIT},
        }, 'additionalProperties': False},
        permission=ToolPermission.READ_ONLY,
        executor=_search_logs,
        running_label='正在检索服务日志',
    ))
