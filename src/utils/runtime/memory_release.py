"""进程内存归还：任务结束后把已释放的堆页交还操作系统。

why：CPython 释放对象后，glibc malloc 通常保留空闲堆页而不归还 OS，
备份、搜索等一次性大分配结束后 RSS 长期停在峰值，看起来像"内存不释放"。
malloc_trim(0) 显式归还顶部及空闲页；非 glibc 平台（musl、macOS、Windows）静默跳过。
"""

import ctypes
import ctypes.util
import gc
import logging
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# 节流间隔：避免高频小任务反复触发全量 gc 阻塞事件循环
_MIN_INTERVAL_SECONDS = 30.0
_last_release = 0.0
_malloc_trim = None
_trim_resolved = False


def _resolve_malloc_trim() -> Optional[Callable[[int], int]]:
    """惰性解析 glibc malloc_trim，仅解析一次；不可用时返回 None。"""
    global _malloc_trim, _trim_resolved
    if _trim_resolved:
        return _malloc_trim
    _trim_resolved = True
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
        _malloc_trim = libc.malloc_trim
        _malloc_trim.argtypes = [ctypes.c_size_t]
        _malloc_trim.restype = ctypes.c_int
    except (OSError, AttributeError):
        _malloc_trim = None
    return _malloc_trim


def release_memory(force: bool = False) -> bool:
    """回收循环引用并归还空闲堆页；节流期内返回 False。"""
    global _last_release
    now = time.monotonic()
    if not force and now - _last_release < _MIN_INTERVAL_SECONDS:
        return False
    _last_release = now
    gc.collect()
    trim = _resolve_malloc_trim()
    if trim is not None:
        try:
            trim(0)
        except Exception:  # noqa: BLE001 - 归还内存属优化，失败不影响业务
            logger.debug("malloc_trim 调用失败", exc_info=True)
    return True
