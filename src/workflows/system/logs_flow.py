"""
日志管理相关的业务编排层
"""
import asyncio
import logging
from typing import List, Dict, Any

from src.services.log_manager import get_logs, list_log_files, read_log_file, subscribe_to_logs, unsubscribe_from_logs

logger = logging.getLogger(__name__)


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
