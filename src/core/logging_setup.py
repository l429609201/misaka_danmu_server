"""应用日志启动配置与处理器，不承担日志查询流程。"""

import logging
import logging.handlers
import re
from pathlib import Path

from src.core.config import settings
from src.services.file_storage_service import get_file_storage_service
from src.services.log_manager import get_log_dir, publish_log


class DequeHandler(logging.Handler):
    """将格式化记录交给日志服务的内存订阅入口。"""

    def emit(self, record: logging.LogRecord) -> None:
        publish_log(self.format(record))


class NoHttpxLogFilter(logging.Filter):
    """UI 不展示 httpx 高频请求日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith("httpx")


class SensitiveInfoFilter(logging.Filter):
    """对各输出处理器的凭据字符串统一脱敏。"""

    PATTERNS = [
        (re.compile(r'(api_key=)([a-zA-Z0-9]{20,})'), r'\1****'),
        (re.compile(r'(apikey=)([a-zA-Z0-9]{20,})'), r'\1****'),
        (re.compile(r'(token=)([a-zA-Z0-9_-]{20,})'), r'\1****'),
        (re.compile(r'(Authorization:\s*Bearer\s+)([a-zA-Z0-9_-]{20,})'), r'\1****'),
        (re.compile(r'(Cookie:\s*[^;]*?)((?:SESSDATA|bili_jct|DedeUserID|buvid3|_m_h5_tk)=[^;]+)'), r'\1****'),
        (re.compile(r'(_m_h5_tk=)([a-zA-Z0-9_-]+)'), r'\1****'),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pattern, replacement in self.PATTERNS:
            msg = pattern.sub(replacement, msg)
        record.msg = msg
        record.args = ()
        return True


class BilibiliInfoFilter(logging.Filter):
    """过滤 UI 中无诊断价值的来源信息日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "BilibiliScraper" and record.levelno == logging.INFO:
            msg = record.getMessage()
            if "returned no results." in msg or "WBI mixin key" in msg:
                return False
            if "API call for type" in msg and "successful" in msg:
                return False
        return True


class SQLAlchemyPoolShutdownFilter(logging.Filter):
    """非 DEBUG 模式压制已被连接池处理的关闭噪音。"""

    _MSG_MARKERS = ('Exception terminating connection', 'Exception closing connection',
                    'unable to perform operation', 'TCPTransport closed', 'the handler is closed')
    _EXC_MARKERS = ('TCPTransport closed', 'the handler is closed', 'unable to perform operation',
                    'CancelledError', 'Cancelled via cancel scope')

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.ERROR:
            return True
        is_noise = any(marker in record.getMessage() for marker in self._MSG_MARKERS)
        if not is_noise and record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            is_noise = any(marker in f"{type(exc).__name__}: {exc}" for marker in self._EXC_MARKERS)
        if is_noise:
            if logging.getLogger().getEffectiveLevel() <= logging.DEBUG:
                record.levelno = logging.DEBUG
                record.levelname = "DEBUG"
                return True
            return False
        return True


class ApschedulerLogTranslatorFilter(logging.Filter):
    """翻译调度器启动及注册日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name.startswith("apscheduler"):
            if record.msg == "Scheduler started":
                record.msg = "调度器已启动"
                record.args = ()
            elif record.msg == 'Added job "%s" to job store "%s"' and len(record.args) == 2:
                job_id, store = record.args
                record.msg = f'已添加任务 "{job_id}" 到任务存储 "{store}"'
                record.args = ()
        return True


class McpRequestLogDowngradeFilter(logging.Filter):
    """仅 DEBUG 模式展示 MCP 高频请求日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name.startswith("mcp.") and record.levelno == logging.INFO and \
                isinstance(record.msg, str) and record.msg.startswith("Processing request of type"):
            return logging.getLogger().getEffectiveLevel() <= logging.DEBUG
        return True


def setup_logging() -> None:
    """启动时安装控制台、轮转文件及 UI 处理器，文件准备交给 FS。"""
    storage = get_file_storage_service()
    log_dir = get_log_dir()
    try:
        storage.resource_mkdir(log_dir, parents=True, exist_ok=True)
    except (OSError, PermissionError) as exc:
        print(f"警告: 无法创建日志目录 {log_dir}: {exc}，将使用当前目录")
        log_dir = Path(".")
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log.level.upper(), logging.INFO))
    # 热重载先关闭旧文件句柄，防止重复输出和句柄泄漏。
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()
    root.filters.clear()
    verbose = logging.Formatter('[%(asctime)s] [%(name)s:%(lineno)d] [%(levelname)s] - %(message)s',
                                datefmt='%Y-%m-%d %H:%M:%S')
    ui = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    handlers = [logging.StreamHandler(), logging.handlers.RotatingFileHandler(
        log_dir / "app.log", maxBytes=5*1024*1024, backupCount=5, encoding="utf-8"), DequeHandler()]
    for handler in handlers:
        # 子 logger 的传播记录只执行 handler 过滤器，不能仅挂在 root 上。
        handler.addFilter(ApschedulerLogTranslatorFilter())
        handler.addFilter(SensitiveInfoFilter())
        handler.addFilter(McpRequestLogDowngradeFilter())
        handler.addFilter(SQLAlchemyPoolShutdownFilter())
        if isinstance(handler, DequeHandler):
            handler.addFilter(NoHttpxLogFilter())
            handler.addFilter(BilibiliInfoFilter())
            handler.setFormatter(ui)
        else:
            handler.setFormatter(verbose)
        root.addHandler(handler)
    logging.getLogger("httpx").addFilter(SensitiveInfoFilter())
    specialized = [
        ("scraper_responses", "scraper_responses.log", "搜索源响应", logging.DEBUG,
         '[%(asctime)s] [%(name)s] - %(message)s', 10*1024*1024),
        ("metadata_responses", "metadata_responses.log", "元数据响应", logging.DEBUG,
         '[%(asctime)s] - %(message)s', 10*1024*1024),
        ("ai_responses", "ai_responses.log", "AI响应", logging.DEBUG,
         '[%(asctime)s] - %(message)s', 10*1024*1024),
        ("webhook_raw", "webhook_raw.log", "Webhook原始请求", logging.INFO,
         '[%(asctime)s] %(message)s', 5*1024*1024),
        ("bot_raw", "bot_raw.log", "Bot原始交互", logging.DEBUG,
         '[%(asctime)s] %(message)s', 10*1024*1024),
    ]
    for name, filename, _, level, fmt, max_bytes in specialized:
        path = log_dir / filename
        if storage.resource_exists(path):
            try:
                storage.resource_write_text(path, "", encoding="utf-8")
            except OSError as exc:
                logging.error("清空 %s 失败: %s", filename, exc)
        spec_logger = logging.getLogger(name)
        for old_handler in spec_logger.handlers[:]:
            spec_logger.removeHandler(old_handler)
            old_handler.close()
        spec_logger.setLevel(level)
        spec_logger.propagate = False
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=max_bytes, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter(fmt, datefmt='%Y-%m-%d %H:%M:%S'))
        handler.addFilter(SensitiveInfoFilter())
        spec_logger.addHandler(handler)
    logging.info("\n".join([f"日志系统已初始化 (目录: {log_dir})", "  - app.log (主日志)"] +
                           [f"  - {filename} ({desc})" for _, filename, desc, *_ in specialized]))
