"""纯任务计时状态，不访问数据库或业务服务。"""

import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Optional
from uuid import uuid4


@dataclass
class PerfStep:
    """单个步骤的计时结果。"""
    step_name: str
    duration_ms: float
    success: bool
    details: Optional[str] = None


class TaskProfiler:
    """持有流程计时状态，步骤异常保持向调用方传播。"""

    def __init__(self, flow_type: str, correlation_id: Optional[str] = None) -> None:
        self.flow_type = flow_type
        self.correlation_id = correlation_id or str(uuid4())
        self._steps: list[PerfStep] = []
        self._total_start = time.perf_counter()

    @asynccontextmanager
    async def step(self, step_name: str) -> AsyncIterator[None]:
        """记录异步步骤耗时及失败详情，不干扰原有异常。"""
        start = time.perf_counter()
        try:
            yield
        except Exception as exc:
            self.record_step(step_name, (time.perf_counter() - start) * 1000, False, str(exc)[:500])
            raise
        else:
            self.record_step(step_name, (time.perf_counter() - start) * 1000)

    def record_step(self, step_name: str, duration_ms: float, success: bool = True,
                    details: Optional[str] = None) -> None:
        """添加调用方已测量的步骤。"""
        self._steps.append(PerfStep(step_name, duration_ms, success, details))

    @property
    def steps(self) -> tuple[PerfStep, ...]:
        """返回计时记录快照，持久化服务不直接操作内部列表。"""
        return tuple(self._steps)

    @property
    def total_duration_ms(self) -> float:
        """返回创建以来的总耗时，单位毫秒。"""
        return (time.perf_counter() - self._total_start) * 1000
