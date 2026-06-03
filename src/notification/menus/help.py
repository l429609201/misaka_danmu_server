"""
/help /start /cancel 菜单 Mixin — 帮助文本、快捷按钮、cb_help_cmd、cb_noop
从 notification_service.py 中拆分。
"""
from src.notification.base import CommandResult


class HelpMenuMixin:
    """处理 /start /help /cancel 命令及 help_cmd / noop 回调"""

    HELP_TEXT = (
        "📖 *命令列表:*\n\n"
        "🔍 /sh <关键词> - 搜索弹幕源\n"
        "  _支持指定季集，如: /sh 刀剑神域 S01E10_\n"
        "🔄 /auto - 自动导入（多平台）\n"
        "🔗 /url - 从URL导入弹幕\n"
        "♻️ /refresh - 弹幕库管理\n"
        "🔑 /tokens - Token管理\n"
        "📋 /tasks - 定时任务列表\n"
        "🗑️ /cache - 清除缓存\n"
        "❌ /cancel - 取消当前操作\n\n"
        "💡 点击下方按钮可快速执行命令"
    )

    HELP_BUTTONS = [
        [{"text": "🔍 搜索弹幕", "callback_data": "help_cmd:search"},
         {"text": "🔄 自动导入", "callback_data": "help_cmd:auto"}],
        [{"text": "🔗 URL导入", "callback_data": "help_cmd:url"},
         {"text": "♻️ 弹幕库管理", "callback_data": "help_cmd:refresh"}],
        [{"text": "🔑 Token管理", "callback_data": "help_cmd:tokens"},
         {"text": "📋 任务列表", "callback_data": "help_cmd:tasks"}],
        [{"text": "🗑️ 清除缓存", "callback_data": "help_cmd:cache"}],
    ]

    async def cmd_start(self, args: str, user_id: str, channel, **kw) -> CommandResult:
        return CommandResult(
            text=f"👋 欢迎使用弹幕服务器通知机器人！\n\n{self.HELP_TEXT}",
            reply_markup=self.HELP_BUTTONS,
            parse_mode="Markdown",
        )

    async def cmd_help(self, args: str, user_id: str, channel, **kw) -> CommandResult:
        return CommandResult(
            text=self.HELP_TEXT,
            reply_markup=self.HELP_BUTTONS,
            parse_mode="Markdown",
        )

    async def cmd_cancel(self, args: str, user_id: str, channel, **kw) -> CommandResult:
        self.clear_conversation(user_id)
        return CommandResult(text="✅ 已取消当前操作。")

    async def cb_help_cmd(self, params, user_id, channel, **kw):
        """帮助页内联按钮 — 点击后触发对应命令"""
        cmd = params[0] if params else ""
        handler_map = {
            "search": self.cmd_search,
            "auto": self.cmd_auto,
            "url": self.cmd_url,
            "refresh": self.cmd_refresh,
            "tokens": self.cmd_list_tokens,
            "tasks": self.cmd_list_tasks,
            "cache": self.cmd_cache,
        }
        handler = handler_map.get(cmd)
        if not handler:
            return CommandResult(text="", answer_callback_text="未知命令")
        self.clear_conversation(user_id)
        result = await handler("", user_id, channel, **kw)
        result.answer_callback_text = ""
        result.edit_message_id = kw.get("message_id")
        return result

    async def cb_noop(self, params, user_id, channel, **kw):
        return CommandResult(text="", answer_callback_text="")

