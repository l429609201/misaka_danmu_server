"""
弹幕源下载任务管理器

将下载任务与 SSE 连接解耦，实现：
1. 后台任务独立运行，不受前端连接影响
2. 支持任务状态查询
3. 支持断点续传（跳过已下载的文件）
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """任务状态"""
    PENDING = "pending"      # 等待开始
    RUNNING = "running"      # 正在运行
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"        # 失败
    CANCELLED = "cancelled"  # 已取消


@dataclass
class DownloadProgress:
    """下载进度信息"""
    current: int = 0           # 当前已下载数量
    total: int = 0             # 总数量
    current_file: str = ""     # 当前正在下载的文件
    downloaded: List[str] = field(default_factory=list)   # 已下载的文件列表
    skipped: List[str] = field(default_factory=list)      # 跳过的文件列表
    failed: List[str] = field(default_factory=list)       # 失败的文件列表
    messages: List[str] = field(default_factory=list)     # 日志消息列表（最近50条）


@dataclass
class DownloadTask:
    """下载任务"""
    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    progress: DownloadProgress = field(default_factory=DownloadProgress)
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    success_message: Optional[str] = None  # 成功完成时的友好消息
    repo_url: str = ""
    use_full_replace: bool = False
    branch: str = "main"  # 添加分支字段
    need_restart: bool = False  # 是否需要重启容器
    restart_pending: bool = False  # 是否正在等待重启（用于 SSE 发送终止消息）
    # 内部使用
    _cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    _asyncio_task: Optional[asyncio.Task] = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于 API 响应）"""
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "progress": {
                "current": self.progress.current,
                "total": self.progress.total,
                "current_file": self.progress.current_file,
                "downloaded_count": len(self.progress.downloaded),
                "skipped_count": len(self.progress.skipped),
                "failed_count": len(self.progress.failed),
                "failed_files": self.progress.failed,
                "messages": self.progress.messages[-20:],  # 只返回最近20条消息
            },
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error_message": self.error_message,
            "success_message": self.success_message,  # 成功完成时的友好消息
            "repo_url": self.repo_url,
            "use_full_replace": self.use_full_replace,
            "need_restart": self.need_restart,
            "restart_pending": self.restart_pending,
        }

    def add_message(self, message: str):
        """添加日志消息"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.progress.messages.append(f"[{timestamp}] {message}")
        # 只保留最近50条
        if len(self.progress.messages) > 50:
            self.progress.messages = self.progress.messages[-50:]

    def is_cancelled(self) -> bool:
        """检查任务是否被取消"""
        return self._cancel_event.is_set()

    def cancel(self):
        """取消任务"""
        self._cancel_event.set()
        if self._asyncio_task and not self._asyncio_task.done():
            self._asyncio_task.cancel()


class DownloadTaskManager:
    """下载任务管理器（单例）"""
    
    _instance: Optional["DownloadTaskManager"] = None
    _lock = asyncio.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._tasks: Dict[str, DownloadTask] = {}
        self._current_task_id: Optional[str] = None
        self._max_history = 10  # 保留最近10个已完成的任务
        logger.info("下载任务管理器已初始化")

    @property
    def current_task(self) -> Optional[DownloadTask]:
        """获取当前正在运行的任务"""
        if self._current_task_id:
            return self._tasks.get(self._current_task_id)
        return None

    def is_running(self) -> bool:
        """检查是否有任务正在运行"""
        task = self.current_task
        return task is not None and task.status == TaskStatus.RUNNING

    def create_task(self, repo_url: str, use_full_replace: bool = False, branch: str = "main") -> DownloadTask:
        """创建新的下载任务"""
        task_id = str(uuid.uuid4())[:8]
        task = DownloadTask(
            task_id=task_id,
            repo_url=repo_url,
            use_full_replace=use_full_replace,
            branch=branch,  # 添加分支参数
        )
        self._tasks[task_id] = task
        self._cleanup_old_tasks()
        logger.info(f"创建下载任务: {task_id} (分支: {branch})")
        return task

    def get_task(self, task_id: str) -> Optional[DownloadTask]:
        """获取任务"""
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> List[DownloadTask]:
        """获取所有任务"""
        return list(self._tasks.values())

    def set_current_task(self, task_id: str):
        """设置当前任务"""
        self._current_task_id = task_id

    def clear_current_task(self):
        """清除当前任务标记"""
        self._current_task_id = None

    def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        task = self._tasks.get(task_id)
        if task and task.status == TaskStatus.RUNNING:
            task.cancel()
            task.status = TaskStatus.CANCELLED
            task.completed_at = datetime.now()
            task.add_message("任务已被用户取消")
            logger.info(f"任务 {task_id} 已取消")
            if self._current_task_id == task_id:
                self._current_task_id = None
            return True
        return False

    def _cleanup_old_tasks(self):
        """清理旧任务，只保留最近的几个"""
        completed_tasks = [
            (tid, t) for tid, t in self._tasks.items()
            if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
        ]
        # 按完成时间排序
        completed_tasks.sort(key=lambda x: x[1].completed_at or datetime.min, reverse=True)
        # 删除超出限制的旧任务
        for tid, _ in completed_tasks[self._max_history:]:
            del self._tasks[tid]


# 全局单例
_task_manager: Optional[DownloadTaskManager] = None


def get_download_task_manager() -> DownloadTaskManager:
    """获取下载任务管理器单例"""
    global _task_manager
    if _task_manager is None:
        _task_manager = DownloadTaskManager()
    return _task_manager

