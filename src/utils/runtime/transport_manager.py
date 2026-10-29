"""
HTTP Transport 管理器
提供线程安全的共享 HTTP transport 管理，避免全局状态导致的连接失效问题。
"""
import asyncio
import httpx
from typing import Optional, Dict
import logging

logger = logging.getLogger(__name__)


class TransportManager:
    """
    管理共享的 HTTP transport 实例。

    特性：
    - 线程安全：使用异步锁保护创建过程
    - 懒加载：按需创建 transport
    - 生命周期管理：仅在应用关闭时清理
    - 代理支持：为不同代理 URL 维护独立的 transport
    """

    def __init__(self):
        self._shared_transport: Optional[httpx.AsyncHTTPTransport] = None
        self._proxy_transports: Dict[str, httpx.AsyncHTTPTransport] = {}
        self._lock = asyncio.Lock()
        self._proxy_lock = asyncio.Lock()

    async def get_shared_transport(self) -> httpx.AsyncHTTPTransport:
        """
        获取共享的无代理 transport。

        如果不存在则创建，支持并发调用。
        """
        if self._shared_transport is None:
            async with self._lock:
                if self._shared_transport is None:
                    self._shared_transport = httpx.AsyncHTTPTransport(
                        retries=2,
                        limits=httpx.Limits(
                            max_keepalive_connections=50,
                            max_connections=100,
                            keepalive_expiry=30.0  # 30秒过期，避免僵尸连接
                        )
                    )
                    logger.debug(f"Created shared transport (id={id(self._shared_transport)})")
        return self._shared_transport

    async def get_proxy_transport(self, proxy_url: str) -> httpx.AsyncHTTPTransport:
        """
        获取指定代理的共享 transport。

        为每个代理 URL 维护独立的 transport 实例。
        """
        if proxy_url not in self._proxy_transports:
            async with self._proxy_lock:
                if proxy_url not in self._proxy_transports:
                    self._proxy_transports[proxy_url] = httpx.AsyncHTTPTransport(
                        proxy=proxy_url,
                        retries=2,
                        limits=httpx.Limits(
                            max_keepalive_connections=10,  # 代理连接更保守
                            max_connections=50,
                            keepalive_expiry=15.0  # 代理连接过期时间更短
                        )
                    )
                    logger.debug(f"Created proxy transport for {proxy_url} (id={id(self._proxy_transports[proxy_url])})")
        return self._proxy_transports[proxy_url]

    async def close_all(self):
        """
        关闭所有管理的 transport。

        仅在应用关闭时调用，避免在业务逻辑中调用导致全局失效。
        """
        logger.info("Closing all managed transports...")

        if self._shared_transport:
            try:
                await self._shared_transport.aclose()
            except Exception as e:
                logger.warning(f"Error closing shared transport: {e}")
            self._shared_transport = None

        close_tasks = []
        for proxy_url, transport in self._proxy_transports.items():
            close_tasks.append(self._safe_close_transport(transport, proxy_url))

        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)

        self._proxy_transports.clear()
        logger.info("All transports closed successfully.")

    async def _safe_close_transport(self, transport: httpx.AsyncHTTPTransport, description: str):
        """安全关闭单个 transport，记录错误但不抛出异常。"""
        try:
            await transport.aclose()
            logger.debug(f"Closed transport: {description}")
        except Exception as e:
            logger.warning(f"Error closing transport {description}: {e}")