import collections
import logging
import logging.handlers
from pathlib import Path
import re
from typing import List, Set
import asyncio

from src.core.config import settings

# 这个双端队列将用于在内存中存储最新的日志，以供Web界面展示
_logs_deque = collections.deque(maxlen=200)

# 用于存储所有订阅日志的队列
_log_subscribers: Set[asyncio.Queue] = set()

# 自定义一个日志处理器，它会将日志记录发送到我们的双端队列中
class DequeHandler(logging.Handler):
    def __init__(self, deque):
        super().__init__()
        self.deque = deque

    def emit(self, record):
        # 我们只存储格式化后的消息字符串
        log_message = self.format(record)
        self.deque.appendleft(log_message)

        # 通知所有订阅者
        for queue in _log_subscribers:
            try:
                queue.put_nowait(log_message)
            except asyncio.QueueFull:
                # 如果队列满了,跳过这条日志
                pass

# 新增：一个过滤器，用于从UI日志中排除 httpx 的日志
class NoHttpxLogFilter(logging.Filter):
    def filter(self, record):
        # 不记录来自 'httpx' logger 的日志
        return not record.name.startswith('httpx')

# 新增：一个过滤器，用于隐藏日志中的敏感信息（API密钥、Token等）
class SensitiveInfoFilter(logging.Filter):
    """过滤器，用于隐藏日志中的敏感信息"""

    # 敏感信息的正则表达式模式
    PATTERNS = [
        (re.compile(r'(api_key=)([a-zA-Z0-9]{20,})'), r'\1****'),  # TMDB API key
        (re.compile(r'(apikey=)([a-zA-Z0-9]{20,})'), r'\1****'),  # 其他API key
        (re.compile(r'(token=)([a-zA-Z0-9_-]{20,})'), r'\1****'),  # Token
        (re.compile(r'(Authorization:\s*Bearer\s+)([a-zA-Z0-9_-]{20,})'), r'\1****'),  # Bearer token
        (re.compile(r'(Cookie:\s*[^;]*?)((?:SESSDATA|bili_jct|DedeUserID|buvid3|_m_h5_tk)=[^;]+)'), r'\1****'),  # Cookie中的敏感字段
        (re.compile(r'(_m_h5_tk=)([a-zA-Z0-9_-]+)'), r'\1****'),  # Youku token
    ]

    def filter(self, record):
        # 获取日志消息
        msg = record.getMessage()

        # 应用所有替换模式
        for pattern, replacement in self.PATTERNS:
            msg = pattern.sub(replacement, msg)

        # 更新日志消息
        record.msg = msg
        record.args = ()  # 清空args，因为我们已经格式化了消息

        return True

# 新增：一个过滤器，用于从UI日志中排除B站特定的信息性日志
class BilibiliInfoFilter(logging.Filter):
    def filter(self, record):
        # 检查日志记录是否来自 BilibiliScraper 并且是 INFO 级别
        if record.name == 'BilibiliScraper' and record.levelno == logging.INFO:
            msg = record.getMessage()
            # 过滤掉“无结果”的通知
            if "returned no results." in msg:
                return False
            # 过滤掉 WBI key 获取过程的日志
            if "WBI mixin key" in msg:
                return False
            # 过滤掉搜索成功的日志
            if "API call for type" in msg and "successful" in msg:
                return False
        return True  # 其他所有日志都通过

# 新增：过滤 SQLAlchemy 连接池 terminate 连接时的良性噪音
class SQLAlchemyPoolShutdownFilter(logging.Filter):
    """
    压制 SQLAlchemy 连接池 terminate 一条连接时的良性 ERROR 噪音（"Exception terminating connection"）。

    两类触发场景（表象相同，均无害——连接会被池作废重建，不影响功能）：
    1. 运行期：客户端在请求完成前断开，cancel scope 级联取消正被占用的连接
       → 尾部为 asyncio.CancelledError（Cancelled via ... BaseHTTPMiddleware）。
    2. 关闭/重启期：engine.dispose() 批量回收连接，但 uvloop transport 已先关
       → 尾部为 RuntimeError: unable to perform operation on <TCPTransport closed=True>。

    这条 ERROR 由 SQLAlchemy 在 pool/base.py._close_connection 内部 logger.error(exc_info=True)
    直接打出（异常已在池内被 catch），因此**只能在日志层过滤**。

    why 直接丢弃而非降级：filter 早于 handler 的级别判定执行，仅改 record.levelno 不会阻止输出
    （旧实现的 bug）。故这里在非 DEBUG 全局级别下直接 return False 丢弃；DEBUG 模式保留以便排查。
    """
    _MSG_MARKERS = (
        'Exception terminating connection',
        'Exception closing connection',
        'unable to perform operation',
        'TCPTransport closed',
        'the handler is closed',
    )
    _EXC_MARKERS = (
        'TCPTransport closed',
        'the handler is closed',
        'unable to perform operation',
        'CancelledError',
        'Cancelled via cancel scope',
    )

    def filter(self, record):
        if record.levelno < logging.ERROR:
            return True

        msg = record.getMessage()
        is_pool_noise = any(m in msg for m in self._MSG_MARKERS)

        # 主消息未命中时，检查异常堆栈（CancelledError/RuntimeError 详情常只在 exc_info 里）
        if not is_pool_noise and record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            exc_text = f"{type(exc).__name__}: {exc}"
            is_pool_noise = any(m in exc_text for m in self._EXC_MARKERS)

        if is_pool_noise:
            # 全局 DEBUG 级别时保留（降级为 DEBUG 便于排查）；否则直接丢弃，日志彻底干净
            if logging.getLogger().getEffectiveLevel() <= logging.DEBUG:
                record.levelno = logging.DEBUG
                record.levelname = 'DEBUG'
                return True
            return False
        return True

# 新增：一个过滤器，用于翻译 apscheduler 的日志
class ApschedulerLogTranslatorFilter(logging.Filter):
    """一个用于翻译 apscheduler 日志的过滤器。"""
    def filter(self, record):
        if record.name.startswith('apscheduler'):
            # 直接检查原始消息格式字符串，而不是格式化后的消息，这样更可靠
            if record.msg == 'Scheduler started':
                record.msg = '调度器已启动'
                record.args = () # 清空参数，因为新消息是完整的
                return True
            
            # 检查添加任务的日志
            if record.msg == 'Added job "%s" to job store "%s"' and len(record.args) == 2:
                job_id, store = record.args
                record.msg = f'已添加任务 "{job_id}" 到任务存储 "{store}"'
                record.args = () # 清空参数
                return True

        return True


class McpRequestLogDowngradeFilter(logging.Filter):
    """将 MCP SDK 的高频请求日志按 DEBUG 级别处理。

    why：fastapi-mcp 底层依赖的 mcp SDK（logger 名为 'mcp.server.lowlevel.server'）
    会在每次处理请求时用 INFO 级别打印 "Processing request of type XxxRequest"。
    MCP 客户端会定时轮询 ListTools，导致该日志成对刷屏。
    这里把它当作 DEBUG 级别对待：仅当全局日志级别为 DEBUG 时才放行，
    否则（INFO 及以上）直接丢弃，从而消除刷屏噪音。
    """
    def filter(self, record):
        if record.name.startswith("mcp.") and record.levelno == logging.INFO \
                and isinstance(record.msg, str) and record.msg.startswith("Processing request of type"):
            # 等价于把该日志降级为 DEBUG：全局为 DEBUG 时显示，否则过滤掉
            return logging.getLogger().getEffectiveLevel() <= logging.DEBUG
        return True

def setup_logging():
    """
    配置根日志记录器，使其能够将日志输出到控制台、一个可轮转的文件，
    以及一个用于API的内存双端队列。
    此函数应在应用启动时被调用一次。
    """
    def _is_docker_environment():
        """检测是否在Docker容器中运行"""
        import os
        # 方法1: 检查 /.dockerenv 文件（Docker标准做法）
        if Path("/.dockerenv").exists():
            return True
        # 方法2: 检查环境变量
        if os.getenv("DOCKER_CONTAINER") == "true" or os.getenv("IN_DOCKER") == "true":
            return True
        # 方法3: 检查当前工作目录是否为 /app
        if Path.cwd() == Path("/app"):
            return True
        return False

    if _is_docker_environment():
        log_dir = Path("/app/config/logs")
    else:
        log_dir = Path("config/logs")

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as e:
        # 如果无法创建日志目录，使用当前目录
        print(f"警告: 无法创建日志目录 {log_dir}: {e}，将使用当前目录")
        log_dir = Path(".")
        log_file = log_dir / "app.log"
    else:
        log_file = log_dir / "app.log"
    log_file = log_dir / "app.log"

    # 为控制台和文件日志定义详细的格式
    verbose_formatter = logging.Formatter(
        '[%(asctime)s] [%(name)s:%(lineno)d] [%(levelname)s] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    # 为Web界面定义一个更简洁的格式（包含级别标签，方便前端按级别过滤）
    ui_formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

    # 从配置中获取日志级别，如果无效则默认为 INFO
    log_level = getattr(logging, settings.log.level.upper(), logging.INFO)
    logger = logging.getLogger()
    logger.setLevel(log_level)

    # 清理已存在的处理器，以避免在热重载时重复添加
    if logger.hasHandlers():
        logger.handlers.clear()

    # 添加新的过滤器到根日志记录器，以便翻译所有输出
    logger.addFilter(ApschedulerLogTranslatorFilter())
    logger.addFilter(SensitiveInfoFilter())  # 添加敏感信息过滤器到所有处理器

    logger.addHandler(logging.StreamHandler()) # 控制台处理器
    logger.addHandler(logging.handlers.RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=5, encoding='utf-8')) # 文件处理器

    # 把 MCP 高频请求日志降级 + SQLAlchemy 连接池关闭噪音过滤器挂到所有处理器上。
    # why：filter 必须加在 handler 上才对子 logger 传播来的记录生效；加在 root logger
    # 上只对 root 直接产生的记录有效。连接池报错来自子 logger(sqlalchemy.pool.*)，
    # 原先把 SQLAlchemyPoolShutdownFilter 加在 root 上（addFilter）对其不生效，故改挂 handler。
    mcp_filter = McpRequestLogDowngradeFilter()
    pool_filter = SQLAlchemyPoolShutdownFilter()  # 压制连接池 terminate 连接的良性噪音
    for handler in logger.handlers:
        handler.addFilter(mcp_filter)
        handler.addFilter(pool_filter)

    # 配置httpx logger,确保其日志也经过敏感信息过滤
    httpx_logger = logging.getLogger("httpx")
    httpx_logger.addFilter(SensitiveInfoFilter())

    # 创建并配置 DequeHandler，以过滤掉不希望在UI上显示的内容
    deque_handler = DequeHandler(_logs_deque)
    deque_handler.addFilter(NoHttpxLogFilter())
    deque_handler.addFilter(BilibiliInfoFilter()) # 添加新的过滤器
    deque_handler.addFilter(mcp_filter)  # UI 日志同样降级 MCP 高频请求噪音
    deque_handler.addFilter(pool_filter)  # UI 日志同样压制连接池关闭噪音（deque_handler 在上面 for 循环后创建，需单独补挂）
    logger.addHandler(deque_handler)

    # 为所有处理器设置格式
    for handler in logger.handlers:
        if isinstance(handler, DequeHandler):
            handler.setFormatter(ui_formatter)
        else:
            handler.setFormatter(verbose_formatter)
    
    # --- 专用日志记录器配置 ---
    # 定义所有专用日志: (logger名称, 文件名, 描述, 日志级别, 格式, maxBytes)
    _P = "  - "  # 子项缩进前缀
    _specialized_loggers = [
        ("scraper_responses", "scraper_responses.log", "搜索源响应",
         logging.DEBUG, '[%(asctime)s] [%(name)s] - %(message)s', 10*1024*1024),
        ("metadata_responses", "metadata_responses.log", "元数据响应",
         logging.DEBUG, '[%(asctime)s] - %(message)s', 10*1024*1024),
        ("ai_responses", "ai_responses.log", "AI响应",
         logging.DEBUG, '[%(asctime)s] - %(message)s', 10*1024*1024),
        ("webhook_raw", "webhook_raw.log", "Webhook原始请求",
         logging.INFO, '[%(asctime)s] %(message)s', 5*1024*1024),
        ("bot_raw", "bot_raw.log", "Bot原始交互",
         logging.DEBUG, '[%(asctime)s] %(message)s', 10*1024*1024),
    ]

    for logger_name, filename, desc, level, fmt, max_bytes in _specialized_loggers:
        filepath = log_dir / filename
        # 启动时清空，确保只包含当前会话的调试信息
        if filepath.exists():
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.truncate(0)
            except IOError as e:
                logging.error(f"清空 {filename} 失败: {e}")

        spec_logger = logging.getLogger(logger_name)
        spec_logger.setLevel(level)
        spec_logger.propagate = False
        handler = logging.handlers.RotatingFileHandler(
            filepath, maxBytes=max_bytes, backupCount=3, encoding='utf-8'
        )
        handler.setFormatter(logging.Formatter(fmt, datefmt='%Y-%m-%d %H:%M:%S'))
        spec_logger.addHandler(handler)

    # 汇总输出日志系统初始化信息
    log_lines = [f"日志系统已初始化 (目录: {log_dir})"]
    log_lines.append(f"{_P}app.log (主日志)")
    for _, filename, desc, *_ in _specialized_loggers:
        log_lines.append(f"{_P}{filename} ({desc})")
    logging.info("\n".join(log_lines))

def get_logs() -> List[str]:
    """返回为API存储的所有日志条目列表。"""
    return list(_logs_deque)


def get_log_dir() -> Path:
    """返回日志目录路径。"""
    import os
    if Path("/.dockerenv").exists() or os.getenv("DOCKER_CONTAINER") == "true" or os.getenv("IN_DOCKER") == "true" or Path.cwd() == Path("/app"):
        return Path("/app/config/logs")
    return Path("config/logs")


def list_log_files() -> List[dict]:
    """列出日志目录中的所有日志文件（包括轮转文件）。"""
    log_dir = get_log_dir()
    if not log_dir.exists():
        return []

    # 匹配 xxx.log 和 xxx.log.1, xxx.log.2 等轮转文件
    log_pattern = re.compile(r'^.+\.log(\.\d+)?$')

    files = []
    for f in sorted(log_dir.iterdir()):
        if f.is_file() and log_pattern.match(f.name):
            stat = f.stat()
            files.append({
                "name": f.name,
                "size": stat.st_size,
                "modified": stat.st_mtime,
            })

    # 按修改时间倒序
    files.sort(key=lambda x: x["modified"], reverse=True)
    return files


def read_log_file(filename: str, tail: int = 500) -> List[str]:
    """读取指定日志文件的最后 N 行。"""
    log_dir = get_log_dir()
    file_path = (log_dir / filename).resolve()

    # 安全检查：防止路径穿越
    if not str(file_path).startswith(str(log_dir.resolve())):
        raise ValueError("非法的文件路径")

    if not file_path.exists() or not file_path.is_file():
        raise FileNotFoundError(f"日志文件不存在: {filename}")

    # 读取最后 tail 行
    lines = []
    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            from collections import deque
            lines = list(deque(f, maxlen=tail))
    except Exception as e:
        raise IOError(f"读取日志文件失败: {e}")

    return [line.rstrip('\n').rstrip('\r') for line in lines]


def subscribe_to_logs(queue: asyncio.Queue) -> None:
    """订阅日志更新。"""
    _log_subscribers.add(queue)


def unsubscribe_from_logs(queue: asyncio.Queue) -> None:
    """取消订阅日志更新。"""
    _log_subscribers.discard(queue)