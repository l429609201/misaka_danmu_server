"""
/status 菜单 Mixin — 系统状态概览
"""
import time
import logging

from src.notification.base import CommandResult
from src._version import APP_VERSION

logger = logging.getLogger(__name__)

# 模块加载时间作为启动时间
_START_TIME = time.time()


class StatusMenuMixin:
    """处理 /status 命令"""

    async def cmd_status(self, args: str, user_id: str, channel, **kw) -> CommandResult:
        """系统状态概览"""
        try:
            lines = [f"📊 *系统状态*\n"]

            # 版本 & 运行时间
            uptime_sec = int(time.time() - _START_TIME)
            hours, remainder = divmod(uptime_sec, 3600)
            minutes, secs = divmod(remainder, 60)
            if hours > 24:
                days, hours = divmod(hours, 24)
                uptime_str = f"{days}天{hours}时{minutes}分"
            elif hours > 0:
                uptime_str = f"{hours}时{minutes}分{secs}秒"
            else:
                uptime_str = f"{minutes}分{secs}秒"
            lines.append(f"• 版本: v{APP_VERSION}")
            lines.append(f"• 运行: {uptime_str}")

            # 弹幕库统计
            from src.db import crud
            async with self._session_factory() as session:
                lib_result = await crud.get_library_anime(session, page=1, page_size=1)
                total_anime = lib_result.get("total", 0)

                # 定时任务统计
                tasks = await crud.get_scheduled_tasks(session)
                enabled_tasks = sum(1 for t in tasks if t.get("isEnabled"))
                total_tasks = len(tasks)

            lines.append(f"• 弹幕库: {total_anime} 部作品")
            lines.append(f"• 定时任务: {enabled_tasks}/{total_tasks} 已启用")

            # 搜索源：直接使用 scrapers 字典获取已加载的源数量
            if self.scraper_manager:
                enabled_scrapers = len(self.scraper_manager.scrapers)
                lines.append(f"• 搜索源: {enabled_scrapers} 个")

            # 流控状态
            if self.rate_limiter:
                try:
                    status = self.rate_limiter.get_status()
                    used = status.get("globalUsed", 0)
                    limit = status.get("globalLimit", 0)
                    lines.append(f"• 流控: {used}/{limit} 次")
                except Exception:
                    pass

            # 通知渠道
            if self.notification_manager:
                ch_count = len(self.notification_manager.get_all_channels())
                lines.append(f"• 通知渠道: {ch_count} 个已启用")

            lines.append(f"\n💡 使用 /help 查看所有命令")

            return CommandResult(
                text="\n".join(lines),
                parse_mode="Markdown",
                reply_markup=[[{"text": "🔄 刷新状态", "callback_data": "status_refresh"}]],
            )
        except Exception as e:
            logger.error(f"获取系统状态失败: {e}", exc_info=True)
            return CommandResult(text=f"获取系统状态出错: {e}")

    async def cb_status_refresh(self, params, user_id, channel, **kw):
        """刷新状态"""
        result = await self.cmd_status("", user_id, channel, **kw)
        result.edit_message_id = kw.get("message_id")
        return result
