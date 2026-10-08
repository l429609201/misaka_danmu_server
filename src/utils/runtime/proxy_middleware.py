"""代理中间件 - 统一处理 HTTP/SOCKS 代理和加速代理"""

import logging
from enum import Enum
from typing import Optional, TYPE_CHECKING
import httpx

if TYPE_CHECKING:
    # 仅用于类型注解，运行时不导入以规避循环依赖
    from src.services.config_service import ConfigService

logger = logging.getLogger(__name__)


class ProxyMode(str, Enum):
    """代理模式"""
    NONE = "none"              # 不使用代理
    HTTP_SOCKS = "http_socks"  # HTTP/SOCKS 代理
    ACCELERATE = "accelerate"  # 加速代理


class ProxyMiddleware:
    """
    统一代理中间件
    
    支持两种代理模式：
    - HTTP/SOCKS 代理：通过代理服务器转发请求
    - 加速代理：URL 重写，通过中间服务器转发（如 Vercel/Cloudflare）
    """
    
    def __init__(self, config_service: "ConfigService"):
        self.config_service = config_service
        self.logger = logging.getLogger(self.__class__.__name__)
    
    async def get_proxy_mode(self) -> ProxyMode:
        """获取当前代理模式"""
        mode = await self.config_service.get("proxyMode", "none")
        try:
            return ProxyMode(mode)
        except ValueError:
            # 兼容旧配置：如果 proxyEnabled 为 true，则使用 http_socks 模式
            proxy_enabled = await self.config_service.get("proxyEnabled", "false")
            if proxy_enabled.lower() == "true":
                return ProxyMode.HTTP_SOCKS
            return ProxyMode.NONE
    
    async def get_http_proxy_url(self) -> Optional[str]:
        """获取 HTTP/SOCKS 代理 URL"""
        proxy_url = await self.config_service.get("proxyUrl", "")
        return proxy_url if proxy_url else None
    
    async def get_accelerate_proxy_url(self) -> str:
        """获取加速代理地址"""
        return await self.config_service.get("accelerateProxyUrl", "")
    
    async def get_ssl_verify(self) -> bool:
        """获取是否验证 SSL 证书"""
        ssl_verify = await self.config_service.get("proxySslVerify", "true")
        return ssl_verify.lower() == "true"
    
    def transform_url_for_accelerate(self, original_url: str, proxy_base: str) -> str:
        """
        转换 URL 为加速代理格式
        
        原始: https://api.example.com/path
        转换: https://proxy.vercel.app/https/api.example.com/path
        """
        if not proxy_base:
            return original_url
        
        proxy_base = proxy_base.rstrip('/')
        protocol = "https" if original_url.startswith("https://") else "http"
        target = original_url.replace(f"{protocol}://", "")
        
        return f"{proxy_base}/{protocol}/{target}"
    
    async def transform_url(self, url: str) -> str:
        """
        根据代理模式转换 URL
        
        - NONE/HTTP_SOCKS: 返回原始 URL
        - ACCELERATE: 返回加速代理格式的 URL
        """
        mode = await self.get_proxy_mode()
        
        if mode == ProxyMode.ACCELERATE:
            proxy_base = await self.get_accelerate_proxy_url()
            if proxy_base:
                return self.transform_url_for_accelerate(url, proxy_base)
        
        return url
    
    async def get_client_config(self) -> dict:
        """
        获取 httpx 客户端配置
        
        Returns:
            dict: 包含 proxy 和 verify 配置
        """
        mode = await self.get_proxy_mode()
        config = {
            "proxy": None,
            "verify": True
        }
        
        if mode == ProxyMode.HTTP_SOCKS:
            config["proxy"] = await self.get_http_proxy_url()
            config["verify"] = await self.get_ssl_verify()
        
        return config
    
    async def create_client(self, timeout: float = 30.0, **kwargs) -> httpx.AsyncClient:
        """
        创建配置好代理的 httpx 客户端
        
        Args:
            timeout: 超时时间（秒）
            **kwargs: 传递给 httpx.AsyncClient 的其他参数
        
        Returns:
            httpx.AsyncClient: 配置好代理的客户端
        """
        client_config = await self.get_client_config()
        
        return httpx.AsyncClient(
            proxy=client_config["proxy"],
            verify=client_config["verify"],
            timeout=timeout,
            follow_redirects=kwargs.pop("follow_redirects", True),
            **kwargs
        )


# 全局单例
_proxy_middleware: Optional[ProxyMiddleware] = None


def get_proxy_middleware() -> ProxyMiddleware:
    """获取代理中间件单例"""
    global _proxy_middleware
    if _proxy_middleware is None:
        raise RuntimeError("ProxyMiddleware not initialized. Call init_proxy_middleware first.")
    return _proxy_middleware


def init_proxy_middleware(config_service: "ConfigService") -> ProxyMiddleware:
    """初始化代理中间件"""
    global _proxy_middleware
    _proxy_middleware = ProxyMiddleware(config_service)
    logger.info("ProxyMiddleware initialized")
    return _proxy_middleware

