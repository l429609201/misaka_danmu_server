"""
ScraperRepository - 爬虫配置数据访问层
"""

import logging
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..orm_models import Scraper
from .base import BaseRepository

logger = logging.getLogger(__name__)


class ScraperRepository(BaseRepository[Scraper]):
    """爬虫配置 Repository"""

    async def get_by_id(self, provider_name: str) -> Optional[Scraper]:
        """根据 provider 名称获取爬虫配置"""
        return await self._session.get(Scraper, provider_name)

    async def get_all(self, **filters) -> List[Scraper]:
        """获取所有爬虫配置"""
        stmt = select(Scraper).order_by(Scraper.displayOrder)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_enabled(self) -> List[Scraper]:
        """获取所有已启用的爬虫"""
        stmt = select(Scraper).where(Scraper.isEnabled == True).order_by(Scraper.displayOrder)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, provider_name: str, **kwargs) -> Scraper:
        """创建爬虫配置"""
        scraper = Scraper(providerName=provider_name, **kwargs)
        self._session.add(scraper)
        await self._session.flush()
        return scraper

    async def update(self, provider_name: str, **kwargs) -> Optional[Scraper]:
        """更新爬虫配置"""
        scraper = await self.get_by_id(provider_name)
        if not scraper:
            return None

        for key, value in kwargs.items():
            if hasattr(scraper, key):
                setattr(scraper, key, value)

        await self._session.flush()
        return scraper

    async def delete(self, provider_name: str) -> bool:
        """删除爬虫配置"""
        scraper = await self.get_by_id(provider_name)
        if not scraper:
            return False

        await self._session.delete(scraper)
        await self._session.flush()
        return True

    async def toggle_enable(self, provider_name: str) -> Optional[Scraper]:
        """切换爬虫启用状态"""
        scraper = await self.get_by_id(provider_name)
        if not scraper:
            return None

        scraper.isEnabled = not scraper.isEnabled
        await self._session.flush()
        return scraper

    async def get_by_name(self, provider_name: str) -> Optional[Scraper]:
        """根据名称获取爬虫配置（别名方法）"""
        return await self.get_by_id(provider_name)

    async def get_setting_by_name(self, provider_name: str) -> Optional[Dict[str, Any]]:
        """获取单个爬虫的设置（返回字典格式）"""
        scraper = await self.get_by_id(provider_name)
        if not scraper:
            return None

        return {
            "providerName": scraper.providerName,
            "isEnabled": scraper.isEnabled,
            "displayOrder": scraper.displayOrder,
            "useProxy": scraper.useProxy
        }

    async def get_all_settings(self) -> List[Dict[str, Any]]:
        """获取所有爬虫的设置（返回字典列表）"""
        scrapers = await self.get_all()
        return [
            {
                "providerName": s.providerName,
                "isEnabled": s.isEnabled,
                "displayOrder": s.displayOrder,
                "useProxy": s.useProxy
            }
            for s in scrapers
        ]

    async def update_proxy(self, provider_name: str, use_proxy: bool) -> bool:
        """更新单个爬虫的代理设置"""
        scraper = await self.get_by_id(provider_name)
        if not scraper:
            return False

        scraper.useProxy = use_proxy
        await self._session.flush()
        return True

    async def record_search_health(
        self,
        timed_results: List[Tuple[str, Any, float, Optional[BaseException], Any]],
        now: datetime,
    ) -> None:
        """记录搜索健康统计，事务提交由调用方负责。"""
        changed = False
        for provider_name, result, duration_ms, error, _ in timed_results:
            scraper_row = await self._session.get(Scraper, provider_name)
            if scraper_row is None:
                continue

            changed = True
            scraper_row.totalSearches = (scraper_row.totalSearches or 0) + 1
            scraper_row.totalDurationMs = (scraper_row.totalDurationMs or 0) + duration_ms
            if error:
                scraper_row.failCount = (scraper_row.failCount or 0) + 1
                error_text = str(error)[:500]
                scraper_row.lastError = error_text
                if isinstance(error, TimeoutError) or "timeout" in error_text.lower() or "timed out" in error_text.lower():
                    scraper_row.timeoutCount = (scraper_row.timeoutCount or 0) + 1
            elif result:
                scraper_row.successCount = (scraper_row.successCount or 0) + 1
                scraper_row.totalResultCount = (scraper_row.totalResultCount or 0) + len(result)
            else:
                scraper_row.emptyCount = (scraper_row.emptyCount or 0) + 1
            scraper_row.lastSearchAt = now

        if changed:
            await self._session.flush()

    async def reset_health_stats(self) -> None:
        """只清空健康统计字段，保留用户配置，由服务层负责提交。"""
        await self._session.execute(Scraper.__table__.update().values(
            total_searches=0, success_count=0, fail_count=0, timeout_count=0,
            empty_count=0, total_duration_ms=0, total_result_count=0,
            last_search_at=None, last_error=None,
        ))
        await self._session.flush()
