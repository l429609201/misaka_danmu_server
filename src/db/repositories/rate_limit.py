"""
RateLimitRepository - 流控状态数据访问

对应 ORM 模型 RateLimitState，按提供方名称（providerName）维护请求计数、
周期重置时间与防篡改校验和。校验和的计算与验证由 src/rate_limiter.py 负责，
本层只做存取。
"""

import logging
from typing import Optional, List
from types import SimpleNamespace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from datetime import datetime

from ..orm_models import RateLimitState
from .base import BaseRepository
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


class RateLimitRepository(BaseRepository[RateLimitState]):
    """流控状态 Repository

    注：本仓储接收外部注入的 session，自身不 commit，
    提交时机由调用方（RateLimiter 的事务块）决定。
    """

    @staticmethod
    def _snapshot(state: RateLimitState) -> SimpleNamespace:
        """复制状态字段，禁止上层通过 ORM 对象隐式写库。"""
        return SimpleNamespace(
            providerName=state.providerName, requestCount=state.requestCount,
            lastResetTime=state.lastResetTime, checksum=state.checksum,
        )

    async def get_snapshot(self, provider_name: str) -> Optional[SimpleNamespace]:
        """只读返回脱离 ORM 的状态，不创建计数行。"""
        state = await self.get_by_id(provider_name)
        return self._snapshot(state) if state is not None else None

    async def get_all_snapshots(self) -> List[SimpleNamespace]:
        """返回同一查询读取的状态快照。"""
        return [self._snapshot(state) for state in await self.get_all()]

    async def get_locked_snapshot(self, provider_name: str) -> SimpleNamespace:
        """锁定或初始化计数行，锁由外层服务事务持有。"""
        return self._snapshot(await self.get_locked(provider_name))

    async def reset_all_snapshots(self) -> List[SimpleNamespace]:
        """在持有全局行锁时重置所有计数并返回待签名快照。"""
        return [self._snapshot(state) for state in await self.reset_all()]

    async def save_snapshots(self, snapshots: List[SimpleNamespace]) -> None:
        """成对写回计数与校验和，只刷新不提交外层事务。"""
        for snapshot in snapshots:
            state = await self.get_by_id(snapshot.providerName)
            if state is None:
                raise RuntimeError("已锁定的流控状态不存在")
            state.requestCount = snapshot.requestCount
            state.lastResetTime = snapshot.lastResetTime
            state.checksum = snapshot.checksum
        await self._session.flush()

    async def get_by_id(self, provider_name: str) -> Optional[RateLimitState]:
        """根据提供方名称获取流控状态"""
        return await self._session.get(RateLimitState, provider_name)

    async def get_all(self, **filters) -> List[RateLimitState]:
        """获取所有状态"""
        result = await self._session.execute(select(RateLimitState))
        return list(result.scalars().all())

    async def create(
        self,
        provider_name: str,
        request_count: int = 0,
        last_reset_time: Optional[datetime] = None,
        checksum: Optional[str] = None,
    ) -> RateLimitState:
        """创建状态

        Args:
            provider_name: 提供方名称，如 bilibili、__global__
            request_count: 初始请求计数
            last_reset_time: 上次重置时间，缺省取当前时间
            checksum: 防篡改校验和，由调用方计算后传入
        """
        reset_time = (last_reset_time or get_now()).replace(microsecond=0)
        state = RateLimitState(
            providerName=provider_name,
            requestCount=request_count,
            lastResetTime=reset_time,
            checksum=checksum,
        )
        self._session.add(state)
        await self._session.flush()
        return state

    async def get_or_create(self, provider_name: str) -> RateLimitState:
        """获取或创建状态；统一使用锁定读避免并发初始化后的旧快照。"""
        return await self.get_locked(provider_name)

    async def get_locked(self, provider_name: str) -> RateLimitState:
        """锁定计数行，首次创建的竞争只回滚保存点，不提交外部事务。"""
        stmt = (
            select(RateLimitState)
            .where(RateLimitState.providerName == provider_name)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        # 首次查询和冲突后的查询均为当前读，不使用 MySQL 的旧一致性快照。
        state = (await self._session.execute(stmt)).scalar_one_or_none()
        if state is not None:
            return state
        try:
            async with self._session.begin_nested():
                return await self.create(provider_name)
        except IntegrityError:
            return (await self._session.execute(stmt)).scalar_one()

    async def update(
        self,
        provider_name: str,
        request_count: Optional[int] = None,
        last_reset_time: Optional[datetime] = None,
        checksum: Optional[str] = None,
    ) -> Optional[RateLimitState]:
        """更新状态，仅覆盖显式传入的字段"""
        state = await self.get_by_id(provider_name)
        if not state:
            return None

        if request_count is not None:
            state.requestCount = request_count
        if last_reset_time is not None:
            state.lastResetTime = last_reset_time.replace(microsecond=0)
        if checksum is not None:
            state.checksum = checksum

        await self._session.flush()
        return state

    async def increment(self, provider_name: str) -> RateLimitState:
        """为指定提供方累加一次请求计数，状态不存在时先创建"""
        state = await self.get_or_create(provider_name)
        state.requestCount += 1
        return state

    async def reset_all(self) -> List[RateLimitState]:
        """重置所有状态的计数与重置时间

        逐个更新已加载的 ORM 对象而非批量 UPDATE，确保 expire_on_commit=False
        时会话内的对象状态不会陈旧。
        """
        states = await self.get_all()
        now_naive = get_now().replace(microsecond=0)
        for state in states:
            state.requestCount = 0
            state.lastResetTime = now_naive
        return states

    async def delete(self, provider_name: str) -> bool:
        """删除状态"""
        state = await self.get_by_id(provider_name)
        if not state:
            return False

        await self._session.delete(state)
        await self._session.flush()
        return True
