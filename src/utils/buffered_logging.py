"""
搜索日志缓冲工具

解决问题：asyncio.gather() 并发搜索时，各源的日志交叉输出难以阅读。
解决方案：为每个并发任务创建临时缓冲 logger，搜索完成后按源分组输出。
"""
import logging
from typing import List, Tuple


class BufferedLogHandler(logging.Handler):
    """缓冲 LogRecord 的 Handler，搜索完成后统一输出"""

    def __init__(self):
        super().__init__()
        self._records: List[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord):
        self._records.append(record)

    @property
    def records(self) -> List[logging.LogRecord]:
        return self._records

    def clear(self):
        self._records.clear()


def create_buffered_logger(provider_name: str, task_id: int) -> Tuple[logging.Logger, BufferedLogHandler]:
    """
    创建一个临时的缓冲 logger，用于替换 scraper.logger。

    Args:
        provider_name: 源名称
        task_id: asyncio task id（确保唯一性）

    Returns:
        (临时 logger, BufferedLogHandler 实例)
    """
    handler = BufferedLogHandler()
    handler.setLevel(logging.DEBUG)

    temp_logger = logging.getLogger(f"_buf_.{provider_name}.{task_id}")
    temp_logger.handlers.clear()
    temp_logger.addHandler(handler)
    temp_logger.propagate = False
    temp_logger.setLevel(logging.DEBUG)

    return temp_logger, handler


def flush_buffered_logs(
    output_logger: logging.Logger,
    provider_name: str,
    handler: BufferedLogHandler,
    result_count: int,
    duration_ms: float,
    error: Exception = None,
):
    """
    将缓冲的日志按源分组输出。

    Args:
        output_logger: 用于输出的目标 logger
        provider_name: 源名称
        handler: 缓冲 handler
        result_count: 搜索结果数量
        duration_ms: 搜索耗时(ms)
        error: 搜索异常（如有）
    """
    records = handler.records

    # 构建分组标题
    dur_str = f"{duration_ms:.0f}ms" if duration_ms else "N/A"
    if error:
        header = f"┌─── {provider_name} (错误, {dur_str}) ───"
    else:
        header = f"┌─── {provider_name} ({result_count}个结果, {dur_str}) ───"
    footer = f"└─── {provider_name} ───"

    # 寻找 root handler 的 formatter 来格式化缓冲的 records
    root_handlers = logging.getLogger().handlers
    formatter = root_handlers[0].formatter if root_handlers and root_handlers[0].formatter else logging.Formatter(
        '%(asctime)s [%(name)s] [%(levelname)s] - %(message)s'
    )

    lines = [header]
    for record in records:
        try:
            formatted = formatter.format(record)
            # 缩进每行（多行日志如搜索结果列表）
            for line in formatted.split('\n'):
                lines.append(f"  {line}")
        except Exception:
            lines.append(f"  [{record.levelname}] {record.getMessage()}")

    if error:
        lines.append(f"  ❌ 异常: {error}")

    if not records and not error:
        lines.append(f"  (无日志输出)")

    lines.append(footer)

    # 一次性输出，避免被其他源的日志打断
    output_logger.info("\n".join(lines))

    # 清理临时 logger
    handler.clear()

