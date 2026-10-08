"""媒体服务器服务：统一维护 Emby/Jellyfin/Plex 的已配置实例。"""

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from src.media_servers import EmbyMediaServer, JellyfinMediaServer, PlexMediaServer
from src.media_servers.base import BaseMediaServer, MediaLibrary
from src.services.database_service import DatabaseService

logger = logging.getLogger(__name__)


class MediaServerService:
    """管理媒体服务器配置和已启用的连接实例。"""
    
    # 支持的服务器类型
    SERVER_CLASSES = {
        'emby': EmbyMediaServer,
        'jellyfin': JellyfinMediaServer,
        'plex': PlexMediaServer,
    }
    
    def __init__(self, session_factory: Callable):
        self.session_factory = session_factory
        self._db = DatabaseService(session_factory)  # ✅ 新架构：使用 DatabaseService
        self.servers: Dict[int, BaseMediaServer] = {}  # server_id -> instance
        self.logger = logging.getLogger(__name__)
    
    async def initialize(self):
        """初始化管理器,加载所有启用的服务器"""
        async with self._db.transaction():
            servers = await self._db.media_server.get_all_media_servers()
        
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
    
    def _create_server(self, config: Dict[str, Any]) -> BaseMediaServer:
        """按配置创建受支持的媒体服务器适配器。"""
        provider_name = config["providerName"]
        server_class = self.SERVER_CLASSES.get(provider_name)
        if server_class is None:
            raise ValueError(f"不支持的服务器类型: {provider_name}")
        return server_class(url=config["url"], api_token=config["apiToken"])

    async def _load_server(self, config: Dict) -> Optional[BaseMediaServer]:
        """为已启用配置创建持久连接。"""
        try:
            instance = self._create_server(config)
            self.servers[config["id"]] = instance
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
        async with self._db.transaction():
            config = await self._db.media_server.get_media_server_by_id(server_id)

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

    async def resolve_server(self, server_id: int) -> tuple[BaseMediaServer, bool]:
        """获取启用的实例，或从配置创建需要由调用方关闭的临时实例。"""
        server = self.get_server(server_id)
        if server is not None:
            return server, False
        async with self._db.transaction():
            config = await self._db.media_server.get_media_server_by_id(server_id)
        if config is None:
            raise LookupError(f"媒体服务器 {server_id} 不存在")
        return self._create_server(config), True

    @asynccontextmanager
    async def open_server(self, server_id: int) -> AsyncIterator[BaseMediaServer]:
        """借用持久实例或安全关闭临时实例。"""
        server, temporary = await self.resolve_server(server_id)
        try:
            yield server
        finally:
            if temporary:
                await server.close()

    async def test_connection(self, server_id: int) -> Dict[str, Any]:
        """通过统一适配器入口测试媒体服务器连接。"""
        async with self.open_server(server_id) as server:
            return await server.test_connection()

    async def get_libraries(self, server_id: int) -> List[MediaLibrary]:
        """读取媒体库，禁用服务器的临时连接会被正常关闭。"""
        async with self.open_server(server_id) as server:
            return await server.get_libraries()
    
    async def close_all(self):
        """关闭所有服务器连接"""
        for server in self.servers.values():
            await server.close()
        self.servers.clear()
        self.logger.info("所有媒体服务器连接已关闭")

