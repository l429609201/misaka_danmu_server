"""
清理缓存指令模块
提供 @QLHC 指令，清理系统缓存
"""
import logging
from typing import List

from src.schemas.dandan import DandanSearchAnimeResponse
from src.services.cache_service import get_cache_service
from src.services.service_container import get_database_service
from .base import CommandHandler

logger = logging.getLogger(__name__)


class ClearCacheCommand(CommandHandler):
    """清理缓存指令"""
    
    def __init__(self):
        super().__init__(
            name="QLHC",
            description="清理所有系统缓存（内存和数据库）",
            cooldown_seconds=30,
            usage="@QLHC (支持大小写)",
            examples=["@QLHC", "@qlhc"]
        )
    
    async def execute(self, token: str, args: List[str], session: object,
                     config_service, **kwargs) -> "DandanSearchAnimeResponse":
        """执行清理缓存操作"""
        # 获取图片URL
        image_url = await self.get_image_url(config_service)

        try:
            # 获取cache_manager
            cache_manager = kwargs.get('cache_manager')

            # 清理内存缓存（config_manager的缓存）
            config_service.clear_cache()

            # 清理缓存后端（Redis / Memory / Hybrid）
            backend_msg = ""
            try:
                backend = get_cache_service()
                if backend is not None:
                    backend_count = await backend.clear() or 0
                    backend_msg = f"✓ 缓存后端已清理 ({backend_count} 条)\n"
            except Exception as e:
                logger.warning(f"清除缓存后端失败: {e}")
                backend_msg = f"✗ 缓存后端清理失败: {e}\n"

            # 清理数据库缓存
            db = get_database_service()
            async with db.transaction():
                await db.cache.clear_all()

            # 记录执行时间
            await self.record_execution(token, session)

            logger.info(f"指令 @{self.name} 执行成功，token={token}")

            return self.success_response(
                title="缓存清理成功",
                description=f"✓ 内存缓存已清理\n{backend_msg}✓ 数据库缓存已清理\n\n所有缓存已成功清空",
                image_url=image_url
            )
            
        except Exception as e:
            logger.error(f"指令 @{self.name} 执行失败: {e}", exc_info=True)
            
            return self.error_response(
                title="缓存清理失败",
                description=f"清理过程中发生错误:\n{str(e)}",
                image_url=image_url
            )

