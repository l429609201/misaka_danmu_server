"""
日志管理相关的业务编排层
"""
import asyncio
import logging
import re
from typing import List, Dict, Any

from src.services.log_manager import get_logs, list_log_files, read_log_file, read_log_lines, subscribe_to_logs, unsubscribe_from_logs

logger = logging.getLogger(__name__)


_LEVEL_TAG_RE = re.compile(r'\[(DEBUG|INFO|WARNING|ERROR|CRITICAL)\]')
_ACTIVE_LOG_RE = re.compile(r'^.+\.log$')


def search_logs(keyword: str = "", level: str = "", filename: str = "",
                limit: int = 30, line_max_chars: int = 300) -> dict:
    """协调单文件读取并跨活跃日志检索，保留最新优先与 AND 关键词语义。"""
    limit = max(1, min(limit, 100))
    words = [word.lower() for word in keyword.split() if word.strip()]
    order = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    wanted = set(order[order.index(level.strip().upper()):]) if level.strip().upper() in order else set()
    files = [filename] if filename else [item["name"] for item in list_log_files()
                                        if _ACTIVE_LOG_RE.match(item["name"])]
    matches = []
    scanned = []
    total = 0
    for name in files:
        scanned.append(name)
        try:
            lines = read_log_lines(name)
        except OSError as exc:
            logger.warning("检索日志跳过不可读文件 %s: %s", name, exc)
            continue
        for line in reversed(lines):
            if not line.strip() or (words and not all(word in line.lower() for word in words)):
                continue
            tag = _LEVEL_TAG_RE.search(line)
            line_level = tag.group(1) if tag else ""
            if wanted and line_level not in wanted:
                continue
            total += 1
            if len(matches) < limit:
                text = line if len(line) <= line_max_chars else line[:line_max_chars] + "…"
                matches.append({"file": name, "level": line_level, "line": text})
    return {"matches": matches, "total": total, "truncated": total > len(matches), "scannedFiles": scanned}


async def workflow_get_logs() -> List[str]:
    """获取存储在内存中的最新日志条目"""
    return get_logs()


async def workflow_list_log_files() -> List[Dict[str, Any]]:
    """列出所有日志文件（包括轮转文件）"""
    return list_log_files()


async def workflow_read_log_file(
    filename: str,
    tail: int = 200,
    keyword: str = "",
    offset: int = 0
) -> Dict[str, Any]:
    """
    读取指定日志文件，支持后端关键词过滤和分页加载
    
    返回 {"lines": [...], "hasMore": bool, "total": int}
    """
    result = await asyncio.to_thread(read_log_file, filename, tail, keyword, offset)
    return result


async def workflow_stream_logs():
    """
    生成 SSE 实时日志流
    
    这是一个异步生成器，用于 StreamingResponse
    """
    async def event_generator():
        # 创建一个队列用于接收新日志
        log_queue = asyncio.Queue()

        # 订阅日志更新
        subscribe_to_logs(log_queue)

        try:
            # 首先发送当前所有日志
            current_logs = get_logs()
            for log in reversed(current_logs):  # 反转以保持时间顺序
                # SSE支持多行: 每行前加 "data: " 前缀,最后加 "\n\n" 表示消息结束
                if '\n' in log:
                    # 多行日志,每行都加 data: 前缀
                    lines = log.split('\n')
                    for line in lines:
                        yield f"data: {line}\n"
                    yield "\n"  # 消息结束标记
                else:
                    # 单行日志
                    yield f"data: {log}\n\n"

            # 然后持续推送新日志
            while True:
                try:
                    # 等待新日志,设置超时以便定期发送心跳
                    log = await asyncio.wait_for(log_queue.get(), timeout=30.0)
                    # SSE支持多行: 每行前加 "data: " 前缀,最后加 "\n\n" 表示消息结束
                    if '\n' in log:
                        # 多行日志,每行都加 data: 前缀
                        lines = log.split('\n')
                        for line in lines:
                            yield f"data: {line}\n"
                        yield "\n"  # 消息结束标记
                    else:
                        # 单行日志
                        yield f"data: {log}\n\n"
                except asyncio.TimeoutError:
                    # 发送心跳注释以保持连接
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            logger.debug("SSE日志流连接被客户端关闭")
        finally:
            # 取消订阅
            unsubscribe_from_logs(log_queue)

    return event_generator()
