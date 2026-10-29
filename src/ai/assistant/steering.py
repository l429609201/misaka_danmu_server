"""
御坂助手运行中插话（steering）登记表。

参考 MoviePilot agent 的 steering 中间件：助手正在多轮调用工具时，用户可追加
纠偏消息，下一轮请求模型前注入为 user 消息，无需中止重来。

只在进程内存中按会话保存，且严格限额，流结束即清理，避免长期驻留。
"""

import threading
from typing import Dict, List, Optional

# 单会话最多排队的插话条数与单条长度，防止被刷爆内存或撑爆上下文
MAX_PENDING_PER_SESSION = 5
MAX_STEER_CHARS = 2000


class _SteeringRegistry:
    """会话级插话队列；仅登记为“运行中”的会话可接收插话。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # session_id -> (owner_id, 待注入消息)
        self._active: Dict[str, tuple] = {}

    def open(self, session_id: str, owner_id: Optional[int]) -> None:
        """流开始时登记会话（会覆盖同会话的旧队列）。"""
        with self._lock:
            self._active[session_id] = (owner_id, [])

    def close(self, session_id: str) -> None:
        """流结束（含异常/取消）时清理，未消费的插话直接丢弃。"""
        with self._lock:
            self._active.pop(session_id, None)

    def push(self, session_id: str, owner_id: Optional[int], content: str) -> bool:
        """追加一条插话；会话未在运行、属主不符、为空或队列已满时返回 False。"""
        text = (content or "").strip()[:MAX_STEER_CHARS]
        if not text:
            return False
        with self._lock:
            entry = self._active.get(session_id)
            if entry is None or entry[0] != owner_id:
                return False
            pending: List[str] = entry[1]
            if len(pending) >= MAX_PENDING_PER_SESSION:
                return False
            pending.append(text)
            return True

    def drain(self, session_id: str) -> List[str]:
        """取出并清空会话当前的插话。"""
        with self._lock:
            entry = self._active.get(session_id)
            if entry is None or not entry[1]:
                return []
            items = list(entry[1])
            entry[1].clear()
            return items

    def is_active(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._active


steering_registry = _SteeringRegistry()
