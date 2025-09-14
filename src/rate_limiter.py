import asyncio
import logging
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import crud
from .scraper_manager import ScraperManager
from .timezone import get_now

logger = logging.getLogger(__name__)

class RateLimitExceededError(Exception):
    def __init__(self, message, retry_after_seconds):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds

XOR_KEY = b'mb2%bFSu$D9x3K9xG%W8N&$h0@&I$y7n#@#y9gU&#PGv891NA!RPs@3tDJ46M03v'

class RateLimiter:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], scraper_manager: ScraperManager):
        self._session_factory = session_factory
        self._scraper_manager = scraper_manager
        self._period_map = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}
        self.logger = logging.getLogger(self.__class__.__name__)

        self.enabled: bool = True
        self.global_limit: int = 500
        self.global_period: str = "hour"

        try:
            config_path = Path(__file__).parent / "rate_limit.bin"
            if config_path.exists():
                with open(config_path, 'rb') as f:
                    obfuscated_bytes = f.read()

                json_bytes = bytearray()
                for i, byte in enumerate(obfuscated_bytes):
                    json_bytes.append(byte ^ XOR_KEY[i % len(XOR_KEY)])

                config_data = json.loads(json_bytes.decode('utf-8'))
                
                self.enabled = config_data.get("enabled", self.enabled)
                self.global_limit = config_data.get("global_limit", self.global_limit)
                self.global_period = config_data.get("global_period", self.global_period)
                self.logger.info(f"成功加载速率限制参数。")
            else:
                self.logger.warning("未找到配置，将使用默认的速率限制参数。")
        except Exception as e:
            self.logger.error(f"加载配置失败，将使用默认值。错误: {e}", exc_info=True)

    async def _get_provider_quota(self, provider_name: str) -> Optional[int]:
        try:
            scraper = self._scraper_manager.get_scraper(provider_name)
            quota = getattr(scraper, 'rate_limit_quota', None)
            if quota is not None and quota > 0:
                return quota
        except (ValueError, AttributeError):
            pass
        return None

    def _get_global_limit(self) -> tuple[int, str]:
        if not self.enabled:
            return 0, "hour"
        return self.global_limit, self.global_period

    async def check(self, provider_name: str):
        global_limit, period_str = self._get_global_limit()
        if global_limit <= 0:
            return
        
        period_seconds = self._period_map.get(period_str, 3600)

        async with self._session_factory() as session:
            global_state = await crud.get_or_create_rate_limit_state(session, "__global__")
            provider_state = await crud.get_or_create_rate_limit_state(session, provider_name)

            now = get_now()
            time_since_reset = now - global_state.lastResetTime
            
            if time_since_reset.total_seconds() >= period_seconds:
                self.logger.info(f"全局速率限制周期已过，正在重置所有计数器。")
                await crud.reset_all_rate_limit_states(session)
                await session.commit()
                
                await session.refresh(global_state)
                await session.refresh(provider_state)
                
                time_since_reset = now - global_state.lastResetTime # Re-calculate with the new reset time

            if global_state.requestCount >= global_limit:
                retry_after = period_seconds - time_since_reset.total_seconds()
                msg = f"已达到全局速率限制 ({global_state.requestCount}/{global_limit})。"
                self.logger.warning(msg)
                raise RateLimitExceededError(msg, retry_after_seconds=max(0, retry_after))

            provider_quota = await self._get_provider_quota(provider_name)
            if provider_quota is not None and provider_state.requestCount >= provider_quota:
                retry_after = period_seconds - time_since_reset.total_seconds()
                msg = f"已达到源 '{provider_name}' 的特定配额 ({provider_state.requestCount}/{provider_quota})。"
                self.logger.warning(msg)
                raise RateLimitExceededError(msg, retry_after_seconds=max(0, retry_after))

    async def increment(self, provider_name: str):
        global_limit, _ = self._get_global_limit()
        if global_limit <= 0:
            return

        async with self._session_factory() as session:
            await crud.increment_rate_limit_count(session, "__global__")
            await crud.increment_rate_limit_count(session, provider_name)
            await session.commit()
            self.logger.debug(f"已为 '__global__' 和 '{provider_name}' 增加下载流控计数。")