"""
搜索源(Scraper)相关的CRUD操作
"""

import json
import logging
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, func

from ..orm_models import Scraper
from .. import models

logger = logging.getLogger(__name__)

# 弹幕源顺序快照的 config 表键。
# why：弹幕源更新/重启时源可能因加载时序/版本回退短暂缺失，remove_stale_scrapers 会删掉
# 缺失源的记录，回归后被当新源追加到末尾导致用户调好的顺序丢失。用户每次保存顺序时
# 单独存一份 providerName 顺序到 config，启动 sync 后按此顺序重排 display_order 即可恢复。
_SCRAPER_ORDER_KEY = "scraperOrder"


async def sync_scrapers_to_db(session: AsyncSession, provider_names: List[str]):
    """同步搜索源到数据库(仅添加新的,不删除旧的)"""
    if not provider_names:
        return

    existing_stmt = select(Scraper.providerName)
    existing_providers = set((await session.execute(existing_stmt)).scalars().all())

    new_providers = [name for name in provider_names if name not in existing_providers]
    if not new_providers:
        return

    max_order_stmt = select(func.max(Scraper.displayOrder))
    max_order = (await session.execute(max_order_stmt)).scalar_one_or_none() or 0

    session.add_all([
        Scraper(providerName=name, displayOrder=max_order + i + 1, useProxy=False)
        for i, name in enumerate(new_providers)
    ])
    await session.commit()


async def get_scraper_setting_by_name(session: AsyncSession, provider_name: str) -> Optional[Dict[str, Any]]:
    """获取单个搜索源的设置"""
    scraper = await session.get(Scraper, provider_name)
    if scraper:
        return {
            "providerName": scraper.providerName,
            "isEnabled": scraper.isEnabled,
            "displayOrder": scraper.displayOrder,
            "useProxy": scraper.useProxy
        }
    return None


async def get_all_scraper_settings(session: AsyncSession) -> List[Dict[str, Any]]:
    """获取所有搜索源的设置"""
    stmt = select(Scraper).order_by(Scraper.displayOrder)
    result = await session.execute(stmt)
    return [
        {
            "providerName": s.providerName,
            "isEnabled": s.isEnabled,
            "displayOrder": s.displayOrder,
            "useProxy": s.useProxy
        }
        for s in result.scalars()
    ]


async def update_scraper_proxy(session: AsyncSession, provider_name: str, use_proxy: bool) -> bool:
    """更新单个搜索源的代理设置"""
    stmt = update(Scraper).where(Scraper.providerName == provider_name).values(useProxy=use_proxy)
    result = await session.execute(stmt)
    return result.rowcount > 0


async def update_scrapers_settings(session: AsyncSession, settings: List[models.ScraperSetting]):
    """批量更新搜索源设置"""
    for s in settings:
        await session.execute(
            update(Scraper)
            .where(Scraper.providerName == s.providerName)
            .values(isEnabled=s.isEnabled, displayOrder=s.displayOrder, useProxy=s.useProxy)
        )
    await session.commit()
    # 用户保存后单独存一份顺序到 config 表，供启动/更新导致源缺失后按此顺序重载 display_order。
    try:
        await save_scraper_order_snapshot(session)
    except Exception as e:
        logger.warning(f"保存弹幕源顺序快照失败（不影响本次设置保存）: {e}")


async def save_scraper_order_snapshot(session: AsyncSession):
    """把当前 Scraper 表按 display_order 排好的 providerName 列表存到 config 表。"""
    from .config import update_config_value
    stmt = select(Scraper.providerName).order_by(Scraper.displayOrder)
    ordered = [row for row in (await session.execute(stmt)).scalars().all()]
    await update_config_value(session, _SCRAPER_ORDER_KEY, json.dumps(ordered, ensure_ascii=False))
    logger.info(f"已保存弹幕源顺序快照: {ordered}")


async def apply_scraper_order_from_snapshot(session: AsyncSession):
    """启动/重载后按 config 表的顺序快照重排 Scraper 表的 display_order。

    规则：快照中出现的源按快照顺序排在前（display_order=0,1,2...）；
    快照中没有的源（全新源）按原 display_order 追加在后，保持相对次序。
    why：弹幕源更新导致部分源被删又回归、被当新源追加到末尾时，用此快照恢复用户调好的顺序。
    """
    from .config import get_config_value
    raw = await get_config_value(session, _SCRAPER_ORDER_KEY, "")
    if not raw:
        return
    try:
        ordered_names = json.loads(raw)
        if not isinstance(ordered_names, list):
            return
    except (json.JSONDecodeError, TypeError):
        return

    # 当前库内所有源
    rows = (await session.execute(select(Scraper).order_by(Scraper.displayOrder))).scalars().all()
    current_names = [s.providerName for s in rows]
    order_map = {name: idx for idx, name in enumerate(ordered_names)}

    # 排序键：在快照中的按快照索引；不在快照中的排到末尾并保持原有相对顺序
    max_snapshot_idx = len(ordered_names)
    def _sort_key(name: str, orig_idx: int):
        return (order_map.get(name, max_snapshot_idx + orig_idx),)

    sorted_names = sorted(
        current_names,
        key=lambda n: _sort_key(n, current_names.index(n))
    )

    # 仅当顺序确有变化时才写库，避免无谓 UPDATE
    changed = False
    for new_order, name in enumerate(sorted_names):
        await session.execute(
            update(Scraper).where(Scraper.providerName == name).values(displayOrder=new_order)
        )
        changed = True
    if changed:
        await session.commit()
        logger.info(f"已按顺序快照重排弹幕源 display_order: {sorted_names}")


async def remove_stale_scrapers(session: AsyncSession, discovered_providers: List[str]):
    """删除不再存在的搜索源"""
    if not discovered_providers:
        logger.warning("发现的搜索源列表为空,跳过清理过时源的操作。")
        return
    stmt = delete(Scraper).where(Scraper.providerName.notin_(discovered_providers))
    await session.execute(stmt)
    await session.commit()

