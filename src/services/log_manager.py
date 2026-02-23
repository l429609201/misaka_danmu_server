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
    # 为Web界面定义一个更简洁的格式
    ui_formatter = logging.Formatter('[%(asctime)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

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

    # 配置httpx logger,确保其日志也经过敏感信息过滤
    httpx_logger = logging.getLogger("httpx")
    httpx_logger.addFilter(SensitiveInfoFilter())

    # 创建并配置 DequeHandler，以过滤掉不希望在UI上显示的内容
    deque_handler = DequeHandler(_logs_deque)
    deque_handler.addFilter(NoHttpxLogFilter())
    deque_handler.addFilter(BilibiliInfoFilter()) # 添加新的过滤器
    logger.addHandler(deque_handler)

    # 为所有处理器设置格式
    for handler in logger.handlers:
        if isinstance(handler, DequeHandler):
            handler.setFormatter(ui_formatter)
        else:
            handler.setFormatter(verbose_formatter)
    
    logging.info("日志系统已初始化，日志将输出到控制台和 %s", log_file)

    # --- 新增：为爬虫响应设置一个专用的日志记录器 ---
    scraper_log_file = log_dir / "scraper_responses.log"
    
    # 在启动时清空此日志文件，以确保只包含当前会话的调试信息
    if scraper_log_file.exists():
        try:
            # 使用 'w' 模式打开文件会直接截断它
            with open(scraper_log_file, 'w', encoding='utf-8') as f:
                f.truncate(0)
            logging.info(f"已清空旧的搜索源响应日志: {scraper_log_file}")
        except IOError as e:
            logging.error(f"清空搜索源响应日志失败: {e}")

    scraper_logger = logging.getLogger("scraper_responses")
    scraper_logger.setLevel(logging.DEBUG) # 始终记录DEBUG级别的响应
    scraper_logger.propagate = False # 防止日志冒泡到根记录器，避免在控制台和UI上重复显示

    # 为其配置一个独立的文件处理器
    scraper_handler = logging.handlers.RotatingFileHandler(
        scraper_log_file, maxBytes=10*1024*1024, backupCount=3, encoding='utf-8'
    )
    scraper_handler.setFormatter(logging.Formatter(
        '[%(asctime)s] [%(name)s] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    scraper_logger.addHandler(scraper_handler)
    logging.info("专用的搜索源响应日志已初始化，将输出到 %s", scraper_log_file)

    # --- 新增：为元数据响应设置一个专用的日志记录器 ---
    metadata_log_file = log_dir / "metadata_responses.log"

    if metadata_log_file.exists():
        try:
            with open(metadata_log_file, 'w', encoding='utf-8') as f:
                f.truncate(0)
            logging.info(f"已清空旧的元数据响应日志: {metadata_log_file}")
        except IOError as e:
            logging.error(f"清空元数据响应日志失败: {e}")

    metadata_logger = logging.getLogger("metadata_responses")
    metadata_logger.setLevel(logging.DEBUG)
    metadata_logger.propagate = False

    metadata_handler = logging.handlers.RotatingFileHandler(
        metadata_log_file, maxBytes=10*1024*1024, backupCount=3, encoding='utf-8'
    )
    metadata_handler.setFormatter(logging.Formatter(
        '[%(asctime)s] - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'
    ))
    metadata_logger.addHandler(metadata_handler)
    logging.info("专用的元数据响应日志已初始化，将输出到 %s", metadata_log_file)

    # --- 新增：为AI原始响应设置一个专用的日志记录器 ---
    ai_log_file = log_dir / "ai_responses.log"

    if ai_log_file.exists():
        try:
            with open(ai_log_file, 'w', encoding='utf-8') as f:
                f.truncate(0)
            logging.info(f"已清空旧的AI响应日志: {ai_log_file}")
        except IOError as e:
            logging.error(f"清空AI响应日志失败: {e}")

    ai_logger = logging.getLogger("ai_responses")
    ai_logger.setLevel(logging.DEBUG)
    ai_logger.propagate = False

    ai_handler = logging.handlers.RotatingFileHandler(
        ai_log_file, maxBytes=10*1024*1024, backupCount=3, encoding='utf-8'
    )
    ai_handler.setFormatter(logging.Formatter(
        '[%(asctime)s] - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'
    ))
    ai_logger.addHandler(ai_handler)
    logging.info("专用的AI响应日志已初始化，将输出到 %s", ai_log_file)

    # --- 新增：为 Webhook 原始请求设置一个专用的日志记录器 ---
    webhook_log_file = log_dir / "webhook_raw.log"

    if webhook_log_file.exists():
        try:
            with open(webhook_log_file, 'w', encoding='utf-8') as f:
                f.truncate(0)
            logging.info(f"已清空旧的 Webhook 原始请求日志: {webhook_log_file}")
        except IOError as e:
            logging.error(f"清空 Webhook 原始请求日志失败: {e}")

    webhook_logger = logging.getLogger("webhook_raw")
    webhook_logger.setLevel(logging.INFO) # 只记录 INFO 级别及以上的日志
    webhook_logger.propagate = False # 防止日志冒泡到根记录器

    webhook_handler = logging.handlers.RotatingFileHandler(
        webhook_log_file, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8'
    )
    # 为这个日志使用一个非常简洁的格式，只包含时间和消息
    webhook_handler.setFormatter(logging.Formatter('[%(asctime)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
    webhook_logger.addHandler(webhook_handler)
    logging.info("专用的 Webhook 原始请求日志已初始化，将输出到 %s", webhook_log_file)

    # --- 新增：为 Bot 原始交互设置一个专用的日志记录器 ---
    bot_log_file = log_dir / "bot_raw.log"

    if bot_log_file.exists():
        try:
            with open(bot_log_file, 'w', encoding='utf-8') as f:
                f.truncate(0)
            logging.info(f"已清空旧的 Bot 原始交互日志: {bot_log_file}")
        except IOError as e:
            logging.error(f"清空 Bot 原始交互日志失败: {e}")

    bot_raw_logger = logging.getLogger("bot_raw")
    bot_raw_logger.setLevel(logging.DEBUG)
    bot_raw_logger.propagate = False

    bot_raw_handler = logging.handlers.RotatingFileHandler(
        bot_log_file, maxBytes=10*1024*1024, backupCount=3, encoding='utf-8'
    )
    bot_raw_handler.setFormatter(logging.Formatter('[%(asctime)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
    bot_raw_logger.addHandler(bot_raw_handler)
    logging.info("专用的 Bot 原始交互日志已初始化，将输出到 %s", bot_log_file)

def get_logs() -> List[str]:
    """返回为API存储的所有日志条目列表。"""
    return list(_logs_deque)


def subscribe_to_logs(queue: asyncio.Queue) -> None:
    """订阅日志更新。"""
    _log_subscribers.add(queue)


def unsubscribe_from_logs(queue: asyncio.Queue) -> None:
    """取消订阅日志更新。"""
    _log_subscribers.discard(queue)