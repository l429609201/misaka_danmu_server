"""
/cache 菜单 Mixin — 清除系统缓存
"""
import logging
from src.notification.base import CommandResult
from src.services.cache_service import get_cache_service
from src.services.service_container import get_database_service

logger = logging.getLogger(__name__)


class CacheMenuMixin:
    """处理 /cache 命令"""

    async def cmd_cache(self, args: str, user_id: str, channel, **kw) -> CommandResult:
        """清除系统缓存（内存 + 数据库）"""
        cleared = []
        errors = []

        # 1. 清除内存配置缓存
        if self.config_service:
            try:
                self.config_service.clear_cache()
                cleared.append("✓ 内存配置缓存")
            except Exception as e:
                errors.append(f"✗ 内存配置缓存: {e}")

        # 2. 清除缓存后端（Redis / Memory / Hybrid）
        try:
            backend = get_cache_service()
            if backend is not None:
                backend_count = await backend.clear() or 0
                cleared.append(f"✓ 缓存后端 ({backend_count} 条)")
        except Exception as e:
            errors.append(f"✗ 缓存后端: {e}")

        # 3. 清除数据库缓存
        try:
            db = get_database_service()
            async with db.transaction():
                count = await db.cache.clear_all()
            cleared.append(f"✓ 数据库缓存 ({count} 条)")
        except Exception as e:
            errors.append(f"✗ 数据库缓存: {e}")

        # 4. 清除 AI 缓存（如果可用）
        if self.ai_service:
            try:
                # 清理只操作已有实例，不能为清缓存创建 AI 客户端。
                matcher = self.ai_service.get_initialized_matcher()
                if matcher and hasattr(matcher, 'cache') and matcher.cache:
                    matcher.cache.clear()
                    cleared.append("✓ AI 响应缓存")
            except Exception as e:
                errors.append(f"✗ AI 缓存: {e}")

        lines = ["🗑️ 缓存清除结果：\n"]
        lines.extend(cleared)
        if errors:
            lines.append("")
            lines.extend(errors)

        if not cleared and not errors:
            return CommandResult(text="⚠️ 没有可清除的缓存。")

        return CommandResult(text="\n".join(lines))

