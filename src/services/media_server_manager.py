"""
媒体服务器管理器
负责管理所有媒体服务器实例
"""

import logging
from typing import Dict, List, Optional, Callable
from sqlalchemy.ext.asyncio import AsyncSession

from src.media_servers import EmbyMediaServer, JellyfinMediaServer, PlexMediaServer
from src.media_servers.base import BaseMediaServer
from src.db import crud

logger = logging.getLogger(__name__)


class MediaServerManager:
    """媒体服务器管理器"""
    
    # 支持的服务器类型
    SERVER_CLASSES = {
        'emby': EmbyMediaServer,
        'jellyfin': JellyfinMediaServer,
        'plex': PlexMediaServer,
    }
    
    def __init__(self, session_factory: Callable):
        self.session_factory = session_factory
        self.servers: Dict[int, BaseMediaServer] = {}  # server_id -> instance
        self.logger = logging.getLogger(__name__)
    
    async def initialize(self):
        """初始化管理器,加载所有启用的服务器"""
        async with self.session_factory() as session:
            servers = await crud.get_all_media_servers(session)
        
        _server_display = {}  # server_id -> "name (provider)"
        for server_config in servers:
            if server_config.get('isEnabled'):
                await self._load_server(server_config)
                sid = server_config['id']
                if sid in self.servers:
                    _server_display[sid] = f"{server_config['name']} ({server_config['providerName']})"

        # 汇总输出
        _P = "  - "
        log_lines = [f"已加载 {len(self.servers)} 个媒体服务器"]
        for sid, desc in _server_display.items():
            log_lines.append(f"{_P}{desc}")
        self.logger.info("\n".join(log_lines))
    
    async def _load_server(self, config: Dict) -> Optional[BaseMediaServer]:
        """加载单个服务器实例"""
        server_id = config['id']
        provider_name = config['providerName']
        
        if provider_name not in self.SERVER_CLASSES:
            self.logger.warning(f"不支持的服务器类型: {provider_name}")
            return None
        
        try:
            server_class = self.SERVER_CLASSES[provider_name]
            instance = server_class(
                url=config['url'],
                api_token=config['apiToken']
            )
            
            self.servers[server_id] = instance
            return instance
        except Exception as e:
            self.logger.error(f"加载媒体服务器失败: {e}", exc_info=True)
            return None
    
    async def reload_server(self, server_id: int):
        """重新加载指定服务器"""
        # 先关闭旧实例
        if server_id in self.servers:
            await self.servers[server_id].close()
            del self.servers[server_id]

        # 加载新配置
        async with self.session_factory() as session:
            config = await crud.get_media_server_by_id(session, server_id)

        if config and config.get('isEnabled'):
            await self._load_server(config)

    async def remove_server(self, server_id: int):
        """移除服务器实例"""
        if server_id in self.servers:
            await self.servers[server_id].close()
            del self.servers[server_id]
            self.logger.info(f"已移除媒体服务器: {server_id}")

    def get_server(self, server_id: int) -> Optional[BaseMediaServer]:
        """获取服务器实例"""
        return self.servers.get(server_id)

    async def test_connection(self, server_id: int) -> Dict:
        """测试服务器连接(通过server_id)"""
        async with self.session_factory() as session:
            config = await crud.get_media_server_by_id(session, server_id)

        if not config:
            raise ValueError(f"服务器 {server_id} 不存在")

        provider_name = config['providerName']
        if provider_name not in self.SERVER_CLASSES:
            raise ValueError(f"不支持的服务器类型: {provider_name}")

        try:
            server_class = self.SERVER_CLASSES[provider_name]
            instance = server_class(url=config['url'], api_token=config['apiToken'])
            is_connected = await instance.test_connection()
            await instance.close()

            if is_connected:
                return {
                    "serverName": config['name'],
                    "providerName": provider_name,
                    "url": config['url']
                }
            else:
                raise Exception("连接测试失败")
        except Exception as e:
            self.logger.error(f"测试连接失败: {e}", exc_info=True)
            raise
    
    async def close_all(self):
        """关闭所有服务器连接"""
        for server in self.servers.values():
            await server.close()
        self.servers.clear()
        self.logger.info("所有媒体服务器连接已关闭")


# 全局实例
_media_server_manager: Optional[MediaServerManager] = None


def get_media_server_manager() -> MediaServerManager:
    """获取全局媒体服务器管理器实例"""
    from fastapi import Request
    from starlette.requests import Request as StarletteRequest

    # 尝试从FastAPI应用状态获取
    try:
        from src.main import app
        if hasattr(app.state, 'media_server_manager'):
            return app.state.media_server_manager
    except:
        pass

    # 如果没有找到,抛出异常
    raise RuntimeError("媒体服务器管理器未初始化")

