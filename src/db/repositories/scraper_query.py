"""
ScraperQueryRepository - 爬虫源查询层

职责：处理爬虫源的同步、配置更新和顺序管理
"""

import json
import logging
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, func

from ..orm_models import Scraper
from src.schemas import ScraperSetting
from .config import ConfigRepository  # 同层复用：顺序快照读写 config 表

logger = logging.getLogger(__name__)

# 弹幕源顺序快照的 config 表键
_SCRAPER_ORDER_KEY = "scraperOrder"


class ScraperQueryRepository:
    """爬虫源查询仓储"""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def sync_scrapers_to_db(self, discovered_providers: List[str]):
        """
        同步爬虫源到数据库
        
        Args:
            discovered_providers: 发现的爬虫源列表
        """
        if not discovered_providers:
            logger.warning("发现的搜索源列表为空,跳过同步到数据库的操作。")
            return

        stmt = select(Scraper.providerName)
        result = await self._session.execute(stmt)
        existing_providers = set(result.scalars().all())

        for provider in discovered_providers:
            if provider not in existing_providers:
                max_order_stmt = select(func.max(Scraper.displayOrder))
                max_order_result = await self._session.execute(max_order_stmt)
                max_order = max_order_result.scalar() or -1

                new_scraper = Scraper(
                    providerName=provider,
                    isEnabled=True,
                    displayOrder=max_order + 1,
                    useProxy=False,
                )
                self._session.add(new_scraper)

        await self._session.flush()

    async def remove_stale_scrapers(self, discovered_providers: List[str]):
        """
        删除不再存在的搜索源
        
        Args:
            discovered_providers: 当前发现的爬虫源列表
        """
        if not discovered_providers:
            logger.warning("发现的搜索源列表为空,跳过清理过时源的操作。")
            return
        
        stmt = delete(Scraper).where(Scraper.providerName.notin_(discovered_providers))
        await self._session.execute(stmt)
        await self._session.flush()

    async def get_all_scraper_settings(self) -> List[Dict[str, Any]]:
        """获取所有爬虫源的设置"""
        stmt = select(Scraper).order_by(Scraper.displayOrder)
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        
        return [
            {
                'providerName': row.providerName,
                'isEnabled': row.isEnabled,
                'displayOrder': row.displayOrder,
                'useProxy': row.useProxy,
            }
            for row in rows
        ]

    async def get_scraper_setting_by_name(self, provider_name: str) -> Optional[Dict[str, Any]]:
        """获取单个爬虫源的设置，不存在时返回 None"""
        scraper = await self._session.get(Scraper, provider_name)
        if scraper is None:
            return None
        return {
            'providerName': scraper.providerName,
            'isEnabled': scraper.isEnabled,
            'displayOrder': scraper.displayOrder,
            'useProxy': scraper.useProxy,
        }

    async def update_scraper_proxy(self, provider_name: str, use_proxy: bool) -> bool:
        """更新单个爬虫源的代理开关，返回是否有记录被更新"""
        result = await self._session.execute(
            update(Scraper)
            .where(Scraper.providerName == provider_name)
            .values(useProxy=use_proxy)
        )
        await self._session.flush()
        return int(result.rowcount or 0) > 0

    async def update_scrapers_settings(self, settings: List[ScraperSetting]):
        """
        批量更新爬虫源设置并保存顺序快照
        
        Args:
            settings: 爬虫源设置列表
        """
        try:
            for s in settings:
                await self._session.execute(
                    update(Scraper)
                    .where(Scraper.providerName == s.providerName)
                    .values(isEnabled=s.isEnabled, displayOrder=s.displayOrder, useProxy=s.useProxy)
                )

            ordered_stmt = select(Scraper.providerName).order_by(Scraper.displayOrder)
            ordered = list((await self._session.execute(ordered_stmt)).scalars().all())
            
            # 保存顺序快照（复用同层 ConfigRepository，避免重复实现 upsert SQL）
            await ConfigRepository(self._session).upsert_batch(
                {_SCRAPER_ORDER_KEY: json.dumps(ordered, ensure_ascii=False)}
            )
            await self._session.flush()
            logger.info(f"已保存弹幕源设置与顺序快照: {ordered}")
        except BaseException:
            raise

    async def apply_scraper_order_from_snapshot(self):
        """
        启动/重载后按 config 表的顺序快照重排 Scraper 表的 display_order
        
        规则：快照中出现的源按快照顺序排在前；快照中没有的源追加在后
        """
        raw = await ConfigRepository(self._session).get_value(_SCRAPER_ORDER_KEY, "")
        if not raw:
            return
        
        try:
            ordered_names = json.loads(raw)
            if not isinstance(ordered_names, list):
                return
        except (json.JSONDecodeError, TypeError):
            return

        # 当前库内所有源
        rows = (await self._session.execute(select(Scraper).order_by(Scraper.displayOrder))).scalars().all()
        current_names = [s.providerName for s in rows]
        order_map = {name: idx for idx, name in enumerate(ordered_names)}

        # 排序键：在快照中的按快照索引；不在快照中的排到末尾
        max_snapshot_idx = len(ordered_names)
        def _sort_key(name: str, orig_idx: int):
            return (order_map.get(name, max_snapshot_idx + orig_idx),)

        sorted_names = sorted(
            current_names,
            key=lambda n: _sort_key(n, current_names.index(n))
        )

        # 仅当顺序确有变化时才写库
        for new_order, name in enumerate(sorted_names):
            await self._session.execute(
                update(Scraper).where(Scraper.providerName == name).values(displayOrder=new_order)
            )
        
        await self._session.flush()
        logger.info(f"已按顺序快照重排弹幕源 display_order: {sorted_names}")
