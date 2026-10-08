"""后备详情与正式入库共用的持久化源序号租约。"""

import json

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.orm_models import AnimeSource, Config


async def reserve_source_order(
    session: AsyncSession, anime_id: int, provider: str, media_id: str
) -> int:
    """为同一作品的源保留稳定序号，锁及写入由调用方事务统一提交。"""
    key = f"fallbackSourceOrders:{anime_id}"
    values = {
        "configKey": key, "configValue": "[]",
        "description": "后备源序号租约，防止未入库源切换时整季缓存串台",
    }
    dialect = session.bind.dialect.name
    if dialect == "mysql":
        stmt = mysql_insert(Config).values(values)
        stmt = stmt.on_duplicate_key_update(config_key=stmt.inserted.config_key)
    elif dialect == "postgresql":
        stmt = postgresql_insert(Config).values(values)
        stmt = stmt.on_conflict_do_nothing(index_elements=["config_key"])
    else:
        raise NotImplementedError(f"源序号租约尚未支持数据库类型 '{dialect}'")
    await session.execute(stmt)
    record = (await session.execute(
        select(Config).where(Config.configKey == key).with_for_update()
        .execution_options(populate_existing=True)
    )).scalar_one()
    reservations = json.loads(record.configValue)
    # 使用锁定读获取最新入库状态，避免 MySQL 可重复读快照遗漏刚提交的源。
    rows = (await session.execute(
        select(AnimeSource.providerName, AnimeSource.mediaId, AnimeSource.sourceOrder)
        .where(AnimeSource.animeId == anime_id).with_for_update()
    )).all()
    for name, source_media_id, order in rows:
        if name == provider and source_media_id == media_id:
            return order
    for item in reservations:
        if item["provider"] == provider and item["media_id"] == media_id:
            # 显式重排或旧入口可能占用租约，拒绝静默覆盖另一源的整季缓存。
            if any(row[2] == item["order"] for row in rows):
                raise ValueError("预留源序号已被其他源占用，拒绝覆盖整季映射")
            return item["order"]
    # 租约不随缓存过期释放，否则旧 episodeId 可能被重新分配给另一源。
    order = max([0] + [row[2] for row in rows] + [item["order"] for item in reservations]) + 1
    if order > 99:
        raise ValueError("源序号超过 episodeId 两位编码上限，拒绝生成冲突编号")
    reservations.append({"provider": provider, "media_id": media_id, "order": order})
    record.configValue = json.dumps(reservations, ensure_ascii=False)
    await session.flush()
    return order
