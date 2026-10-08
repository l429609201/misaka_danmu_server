"""
UaRuleRepository - UA 规则数据访问层

why：UA 规则（ua_rules 表）此前只有 crud/token_log.py 承接，但该表与
TokenAccessLog 无任何关联——前者是请求过滤规则，后者是访问日志。
合在一个 Repository 会让其同时管理两张不相干的表，违背单一职责，
因此独立成 UaRuleRepository。

替代 crud/token_log.py 中的:
- get_ua_rules
- add_ua_rule
- delete_ua_rule
"""

import logging
from typing import Optional, List
from sqlalchemy import select

from ..orm_models import UaRule
from .base import BaseRepository
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


class UaRuleRepository(BaseRepository[UaRule]):
    """UA 规则 Repository"""

    async def get_by_id(self, rule_id: int) -> Optional[UaRule]:
        """
        根据 ID 获取 UA 规则

        Args:
            rule_id: 规则 ID

        Returns:
            UaRule 对象，不存在时返回 None
        """
        return await self._session.get(UaRule, rule_id)

    async def get_all(self, **filters) -> List[UaRule]:
        """
        获取所有 UA 规则（按创建时间倒序）

        替代 crud.get_ua_rules

        Returns:
            UaRule 对象列表
        """
        stmt = select(UaRule).order_by(UaRule.createdAt.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_ua_string(self, ua_string: str) -> Optional[UaRule]:
        """
        根据 UA 字符串精确查找规则

        why：ua_string 字段带 unique 约束，创建前可用此方法预检，
        避免直接触发 IntegrityError。

        Args:
            ua_string: UA 字符串

        Returns:
            UaRule 对象，不存在时返回 None
        """
        stmt = select(UaRule).where(UaRule.uaString == ua_string)
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def create(self, **data) -> UaRule:
        """
        创建 UA 规则

        替代 crud.add_ua_rule

        注意：ua_string 带 unique 约束，重复插入会抛 IntegrityError。
        此处保持与原 crud 一致的行为（不做隐式去重），避免改变上层的
        异常处理语义。需要幂等时请先调用 get_by_ua_string 预检。

        Args:
            **data: 规则数据，需含 uaString；createdAt 未传时自动填充

        Returns:
            创建的 UaRule 对象（已 flush，id 可用）
        """
        data.setdefault("createdAt", get_now())
        rule = UaRule(**data)
        self._session.add(rule)
        # 仅 flush 以获取自增 id，事务提交由调用方控制
        await self._session.flush()
        return rule

    async def update(self, rule_id: int, **data) -> Optional[UaRule]:
        """
        更新 UA 规则

        Args:
            rule_id: 规则 ID
            **data: 待更新字段

        Returns:
            更新后的 UaRule 对象，不存在时返回 None
        """
        rule = await self._session.get(UaRule, rule_id)
        if not rule:
            return None

        for key, value in data.items():
            if hasattr(rule, key):
                setattr(rule, key, value)

        await self._session.flush()
        return rule

    async def delete(self, rule_id: int) -> bool:
        """
        删除 UA 规则

        替代 crud.delete_ua_rule

        Args:
            rule_id: 规则 ID

        Returns:
            删除成功返回 True，规则不存在返回 False
        """
        rule = await self._session.get(UaRule, rule_id)
        if not rule:
            return False

        await self._session.delete(rule)
        await self._session.flush()
        return True
