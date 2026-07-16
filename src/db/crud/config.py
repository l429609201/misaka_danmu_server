"""
配置相关的CRUD操作
"""

import logging
from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from ..orm_models import Config

logger = logging.getLogger(__name__)


async def get_config_value(session: AsyncSession, key: str, default: str = "") -> str:
    """
    从数据库获取配置值

    Args:
        session: 数据库会话
        key: 配置键
        default: 默认值,当数据库中不存在该键时返回此值(默认为空字符串)

    Returns:
        配置值,如果数据库中不存在则返回default

    注意:
        - 如果数据库中存在该键但值为空字符串,会返回空字符串(不会返回default)
        - 只有当数据库中不存在该键时,才会返回default
        - 对于AI提示词等配置,应该先调用initialize_configs确保键存在,再调用此函数读取
    """
    stmt = select(Config.configValue).where(Config.configKey == key)
    result = await session.execute(stmt)
    value = result.scalar_one_or_none()

    if value is None:
        return default
    return value


async def update_config_value(session: AsyncSession, key: str, value: str):
    """
    更新配置值(如果不存在则插入)

    Args:
        session: 数据库会话
        key: 配置键
        value: 配置值
    """
    dialect = session.bind.dialect.name
    values_to_insert = {"configKey": key, "configValue": value}

    if dialect == 'mysql':
        stmt = mysql_insert(Config).values(values_to_insert)
        stmt = stmt.on_duplicate_key_update(config_value=stmt.inserted.config_value)
    elif dialect == 'postgresql':
        stmt = postgresql_insert(Config).values(values_to_insert)
        # 修正：使用 on_conflict_do_update 并通过 index_elements 指定主键列，以提高兼容性
        stmt = stmt.on_conflict_do_update(
            index_elements=['config_key'],
            set_={'config_value': stmt.excluded.config_value}
        )
    else:
        raise NotImplementedError(f"配置更新功能尚未为数据库类型 '{dialect}' 实现。")

    await session.execute(stmt)
    await session.commit()



async def upsert_config_values(session: AsyncSession, values: Dict[str, Any]) -> None:
    """批量写入配置但不提交，由调用方控制事务边界。"""
    if not values:
        return

    dialect = session.bind.dialect.name
    for key, value in values.items():
        values_to_insert = {"configKey": key, "configValue": str(value)}
        if dialect == "mysql":
            stmt = mysql_insert(Config).values(values_to_insert)
            stmt = stmt.on_duplicate_key_update(config_value=stmt.inserted.config_value)
        elif dialect == "postgresql":
            stmt = postgresql_insert(Config).values(values_to_insert)
            stmt = stmt.on_conflict_do_update(
                index_elements=["config_key"],
                set_={"config_value": stmt.excluded.config_value},
            )
        else:
            raise NotImplementedError(f"配置批量更新尚未支持数据库类型 '{dialect}'。")
        await session.execute(stmt)


async def update_config_values_atomic(session: AsyncSession, values: Dict[str, Any]) -> None:
    """在单个事务中批量更新配置值。

    why: 同一业务配置通常由多个键组成；逐键调用 ``update_config_value`` 会逐次提交，
    中途失败后数据库会留下半套配置。此函数只在所有 upsert 成功后提交一次。
    """
    if not values:
        return

    try:
        await upsert_config_values(session, values)
        await session.commit()
    except BaseException:
        # why: 显式回滚可立即释放当前失败事务，避免依赖清理阶段才处理。
        await session.rollback()
        raise

# 后备搜索已分配过的最大真实 animeId 计数器（config key）。
# why: 见 allocate_next_counter_value / get_next_real_anime_id 的说明——防止删除后 id 被重用导致 episodeId 串台。
LAST_ALLOCATED_ANIME_ID_KEY = "lastAllocatedRealAnimeId"


async def allocate_next_counter_value(session: AsyncSession, key: str, floor: int, description: str = "") -> int:
    """分配下一个自增计数值：返回 max(已存计数值, floor) + 1，并把结果回写。
    分配成功后立即提交，保证号段租约持久化并及时释放行锁。

    why: 用于 animeId 分配。简单的 MAX(id)+1 在删除最大 id 的作品后 MAX 会回退，
    导致新作品重用已删 id，其 episodeId 与旧作品完全重叠 → 命中旧作品残留的后备缓存 →
    新剧被错误写成旧剧（串台）。改用持久化计数器（只增不减）记住"曾分配过的最大 id"，
    每次返回 max(计数器, 当前DB最大值) + 1，删除作品不影响计数器，id 永不重用。

    :param floor: 下限基准（传数据库当前 MAX(id)），确保即使计数器异常落后也不会分配出已存在的 id。
    Returns: 本次分配的计数值（严格大于历史所有分配值与 floor）。
    """
    dialect = session.bind.dialect.name
    initial_values = {
        "configKey": key,
        "configValue": "0",
        "description": description or None,
    }

    # why: 单纯“先查再写”会让并发事务读到相同计数值。先原子确保行存在，
    # 再用 FOR UPDATE 串行化同一个 key 的分配。计数值是独立的持久化号段租约，
    # 必须在此处提交；部分调用方只写外部缓存，不会再提交当前数据库会话。
    if dialect == "mysql":
        insert_stmt = mysql_insert(Config).values(initial_values)
        ensure_stmt = insert_stmt.on_duplicate_key_update(config_value=Config.configValue)
    elif dialect == "postgresql":
        ensure_stmt = postgresql_insert(Config).values(initial_values).on_conflict_do_nothing(
            index_elements=["config_key"]
        )
    else:
        raise NotImplementedError(f"计数器分配尚未支持数据库类型 '{dialect}'。")

    await session.execute(ensure_stmt)
    lock_stmt = (
        select(Config)
        .where(Config.configKey == key)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    config_row = (await session.execute(lock_stmt)).scalar_one()
    try:
        current = int(config_row.configValue or "0")
    except (ValueError, TypeError):
        current = 0

    next_value = max(current, floor) + 1
    config_row.configValue = str(next_value)
    if description and not config_row.description:
        config_row.description = description
    await session.commit()
    return next_value


async def initialize_configs(session: AsyncSession, defaults: Dict[str, tuple[Any, str]]):
    """
    初始化默认配置(仅插入数据库中不存在的配置项)

    Args:
        session: 数据库会话
        defaults: 默认配置字典,格式为 {key: (value, description)}
    """
    if not defaults:
        return

    existing_stmt = select(Config.configKey)
    existing_keys = set((await session.execute(existing_stmt)).scalars().all())

    new_configs = [
        Config(configKey=key, configValue=str(value), description=description)
        for key, (value, description) in defaults.items()
        if key not in existing_keys
    ]
    if new_configs:
        session.add_all(new_configs)
        await session.commit()
        logger.info(f"成功初始化 {len(new_configs)} 个新配置项。")
    logger.info("默认配置检查完成。")

