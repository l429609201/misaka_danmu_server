"""
ConfigRepository - 配置数据访问层

负责配置的数据库操作，返回 ORM 对象或原始值。
"""

import logging
from typing import Optional, List, Dict, Any
from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..orm_models import Config
from .base import BaseRepository

logger = logging.getLogger(__name__)


class ConfigRepository(BaseRepository[Config]):
    """
    配置 Repository

    职责：
    - 封装配置的数据库访问
    - 返回 Config ORM 对象或简单值（字符串）
    - 处理 MySQL/PostgreSQL 的 upsert 兼容性
    """

    async def get_by_id(self, key: str) -> Optional[Config]:
        """
        根据配置键获取配置对象

        Args:
            key: 配置键

        Returns:
            Config ORM 对象，不存在时返回 None
        """
        stmt = select(Config).where(Config.configKey == key)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_value(self, key: str, default: str = "") -> str:
        """
        获取配置值（字符串）

        Args:
            key: 配置键
            default: 默认值，数据库中不存在时返回

        Returns:
            配置值字符串

        注意：
        - 如果数据库中存在该键但值为空字符串，会返回空字符串（不返回 default）
        - 只有当数据库中不存在该键时，才返回 default
        """
        stmt = select(Config.configValue).where(Config.configKey == key)
        result = await self._session.execute(stmt)
        value = result.scalar_one_or_none()

        if value is None:
            return default
        return value

    async def get_all(self, **filters) -> List[Config]:
        """
        获取所有配置

        Returns:
            Config ORM 对象列表
        """
        stmt = select(Config).order_by(Config.configKey)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, key: str, value: str) -> Config:
        """
        创建新配置

        Args:
            key: 配置键
            value: 配置值

        Returns:
            创建的 Config ORM 对象
        """
        new_config = Config(configKey=key, configValue=value)
        self._session.add(new_config)
        await self._session.flush()
        return new_config

    async def update(self, key: str, value: str) -> Optional[Config]:
        """
        更新配置值

        Args:
            key: 配置键
            value: 新值

        Returns:
            更新后的 Config ORM 对象，不存在时返回 None
        """
        config = await self.get_by_id(key)
        if not config:
            return None

        config.configValue = value
        await self._session.flush()
        return config

    async def upsert(self, key: str, value: str) -> Config:
        """
        插入或更新配置（Upsert）

        使用数据库原生的 ON CONFLICT 语法，支持 MySQL 和 PostgreSQL

        Args:
            key: 配置键
            value: 配置值

        Returns:
            Config ORM 对象（可能需要重新查询获取）
        """
        dialect = self._session.bind.dialect.name
        values_to_insert = {"configKey": key, "configValue": value}

        if dialect == 'mysql':
            stmt = mysql_insert(Config).values(values_to_insert)
            # 冲突更新及 inserted 集合使用数据库列名，而非 ORM 属性名。
            stmt = stmt.on_duplicate_key_update(config_value=stmt.inserted.config_value)
        elif dialect == 'postgresql':
            stmt = postgresql_insert(Config).values(values_to_insert)
            stmt = stmt.on_conflict_do_update(
                index_elements=['config_key'],
                set_={'config_value': stmt.excluded.config_value}
            )
        else:
            raise NotImplementedError(f"配置 upsert 尚未支持数据库类型 '{dialect}'")

        await self._session.execute(stmt)
        await self._session.flush()

        # 重新查询获取最新的对象
        return await self.get_by_id(key)

    async def upsert_batch(self, values: Dict[str, Any]) -> None:
        """
        批量 upsert 配置

        Args:
            values: 配置键值对字典
        """
        if not values:
            return

        dialect = self._session.bind.dialect.name

        for key, value in values.items():
            values_to_insert = {"configKey": key, "configValue": str(value)}

            if dialect == "mysql":
                stmt = mysql_insert(Config).values(values_to_insert)
                # 与单项更新保持一致，使用数据库列名定位冲突更新字段。
                stmt = stmt.on_duplicate_key_update(config_value=stmt.inserted.config_value)
            elif dialect == "postgresql":
                stmt = postgresql_insert(Config).values(values_to_insert)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["config_key"],
                    set_={"config_value": stmt.excluded.config_value},
                )
            else:
                raise NotImplementedError(f"配置批量更新尚未支持数据库类型 '{dialect}'")

            await self._session.execute(stmt)

        await self._session.flush()

    async def allocate_next_counter_value(
        self, key: str, floor: int = 0, description: str = ""
    ) -> int:
        """加锁递增持久化计数器，首次创建也通过冲突处理避免并发重复分配。"""
        dialect = self._session.bind.dialect.name
        values = {"configKey": key, "configValue": str(floor), "description": description}
        if dialect == "mysql":
            stmt = mysql_insert(Config).values(values)
            stmt = stmt.on_duplicate_key_update(config_key=stmt.inserted.config_key)
        elif dialect == "postgresql":
            stmt = postgresql_insert(Config).values(values)
            stmt = stmt.on_conflict_do_nothing(index_elements=["config_key"])
        else:
            raise NotImplementedError(f"计数器分配尚未支持数据库类型 '{dialect}'")
        await self._session.execute(stmt)
        # 锁保持到调用方提交；禁止在仓储提交，避免破坏 ID 分配事务。
        result = await self._session.execute(
            select(Config).where(Config.configKey == key).with_for_update()
            .execution_options(populate_existing=True)
        )
        counter = result.scalar_one()
        next_value = max(int(counter.configValue), floor) + 1
        counter.configValue = str(next_value)
        await self._session.flush()
        return next_value


    async def delete(self, key: str) -> bool:
        """
        删除配置

        Args:
            key: 配置键

        Returns:
            是否删除成功
        """
        config = await self.get_by_id(key)
        if not config:
            return False

        await self._session.delete(config)
        await self._session.flush()
        return True

    async def get_all_as_dict(self) -> Dict[str, str]:
        """
        获取所有配置并转换为字典

        Returns:
            配置字典 {key: value}
        """
        configs = await self.get_all()
        return {c.configKey: c.configValue for c in configs}

    async def initialize_configs(self, defaults: Dict[str, tuple]) -> None:
        """
        初始化默认配置（仅插入数据库中不存在的配置项）

        替代 crud.initialize_configs

        Args:
            defaults: 默认配置字典，格式为 {key: (value, description)}
        """
        if not defaults:
            return

        # 查询已存在的配置键
        existing_stmt = select(Config.configKey)
        existing_keys = set((await self._session.execute(existing_stmt)).scalars().all())

        # 过滤出需要插入的配置项
        to_insert = []
        for key, value_tuple in defaults.items():
            if key in existing_keys:
                continue

            # 容错处理：支持 (value, description) 或单独的 value
            if isinstance(value_tuple, tuple) and len(value_tuple) == 2:
                value, description = value_tuple
            else:
                value = value_tuple
                description = ""

            to_insert.append({
                # Core Table.insert() 使用数据库列名；ORM 属性名会被静默忽略，
                # 当新增默认配置时会退化成 INSERT INTO config () VALUES ()。
                "config_key": key,
                "config_value": str(value),
                "description": description,
            })

        # 批量插入
        if to_insert:
            await self._session.execute(
                Config.__table__.insert(),
                to_insert
            )
            await self._session.flush()

    async def update_config_values_atomic(self, values: Dict[str, Any]) -> None:
        """
        在单个事务中批量更新配置值

        替代 crud.update_config_values_atomic

        Args:
            values: 配置键值对字典

        Note:
            事务由外层 DatabaseService.transaction() 管理，无需手动 commit/rollback
        """
        if not values:
            return

        # 批量 upsert
        for key, value in values.items():
            await self.upsert(key, str(value))
