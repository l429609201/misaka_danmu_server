"""日志记录服务及单文件查询能力；启动配置和跨文件检索各归所属层。"""

import asyncio
import collections
from pathlib import Path
import re
from typing import List, Set

from src.core.env import is_docker_environment
from src.services.file_storage_service import get_file_storage_service

_logs_deque = collections.deque(maxlen=200)
_log_subscribers: Set[asyncio.Queue] = set()
_LOG_FILE_RE = re.compile(r'^.+\.log(\.\d+)?$')


def publish_log(message: str) -> None:
    """接收已格式化日志并通知内存订阅者。"""
    _logs_deque.appendleft(message)
    for queue in tuple(_log_subscribers):
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            pass


def get_logs() -> List[str]:
    """返回为 API 存储的最新日志。"""
    return list(_logs_deque)


def get_log_dir() -> Path:
    """返回当前环境的日志目录。"""
    return Path("/app/config/logs") if is_docker_environment() else Path("config/logs")


def list_log_files() -> List[dict]:
    """通过文件服务枚举日志及轮转文件元数据。"""
    storage = get_file_storage_service()
    log_dir = get_log_dir()
    if not storage.resource_exists(log_dir):
        return []
    files = []
    for path in storage.resource_iterdir(log_dir):
        if storage.resource_is_file(path) and _LOG_FILE_RE.match(path.name):
            stat = storage.resource_stat(path)
            files.append(dict(name=path.name, size=stat.st_size, modified=stat.st_mtime))
    files.sort(key=lambda item: item["modified"], reverse=True)
    return files


def read_log_lines(filename: str) -> List[str]:
    """读取日志目录中的单个文件，拒绝解析后的路径穿越。"""
    storage = get_file_storage_service()
    root = storage.resource_resolve(get_log_dir())
    path = storage.resource_resolve(root / filename)
    if path.parent != root:
        raise ValueError("非法的文件路径")
    if not storage.resource_is_file(path):
        raise FileNotFoundError(f"日志文件不存在: {filename}")
    try:
        return storage.resource_read_bytes(path).decode("utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise IOError(f"读取日志文件失败: {exc}") from exc


def read_log_file(filename: str, tail: int = 200, keyword: str = "", offset: int = 0) -> dict:
    """查询单文件尾部日志，支持关键词与分页偏移。"""
    lines = [line for line in read_log_lines(filename) if line.strip()]
    if keyword:
        lines = [line for line in lines if keyword.lower() in line.lower()]
    total = len(lines)
    end = max(0, total - max(0, offset))
    start = max(0, end - max(0, tail))
    return {"lines": lines[start:end], "hasMore": start > 0, "total": total}


def subscribe_to_logs(queue: asyncio.Queue) -> None:
    """订阅日志更新。"""
    _log_subscribers.add(queue)


def unsubscribe_from_logs(queue: asyncio.Queue) -> None:
    """取消订阅日志更新。"""
    _log_subscribers.discard(queue)
