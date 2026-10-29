"""
/tokens 菜单 Mixin — Token 管理
"""
import secrets
import logging
from src.notification.base import CommandResult
from src.services.service_container import get_database_service

logger = logging.getLogger(__name__)


class TokensMenuMixin:
    """处理 /tokens 命令的所有 cmd_/cb_/_text_ 方法"""

    async def cmd_list_tokens(self, args: str, user_id: str, channel, **kw) -> CommandResult:
        return await self._build_tokens_result()

    async def _build_tokens_result(self, edit_message_id: int = None) -> CommandResult:
        try:
            db = get_database_service()
            async with db.transaction():
                tokens = await db.api_token.get_all_as_dict()
            if not tokens:
                return CommandResult(
                    text="🔑 当前没有 API Token。",
                    reply_markup=[[{"text": "➕ 添加Token", "callback_data": "token_add"}]],
                    edit_message_id=edit_message_id,
                )
            lines = ["🔑 API Token 列表:\n"]
            buttons = []
            for t in tokens:
                status = "✅" if t.get("isEnabled") else "⏸️"
                name = t.get("name", "未知")
                token_id = t.get("id")
                lines.append(f"{status} {name}")
                buttons.append([
                    {"text": f"{'⏸️ 禁用' if t.get('isEnabled') else '✅ 启用'} {name}",
                     "callback_data": f"token_toggle:{token_id}"},
                    {"text": "🗑️ 删除", "callback_data": f"token_delete:{token_id}"},
                ])
            buttons.append([
                {"text": "➕ 添加Token", "callback_data": "token_add"},
                {"text": "🔄 刷新", "callback_data": "tokens_refresh"},
            ])
            return CommandResult(
                text="\n".join(lines),
                reply_markup=buttons,
                edit_message_id=edit_message_id,
            )
        except Exception as e:
            logger.error(f"获取Token列表失败: {e}", exc_info=True)
            return CommandResult(success=False, text=f"获取Token列表出错: {e}")

    async def cb_tokens_refresh(self, params, user_id, channel, **kw):
        return await self._build_tokens_result(edit_message_id=kw.get("message_id"))

    async def cb_token_toggle(self, params, user_id, channel, **kw):
        token_id = int(params[0]) if params else 0
        try:
            db = get_database_service()
            async with db.transaction():
                token = await db.api_token.toggle_enable(token_id)
            if token is None:
                return CommandResult(text="", answer_callback_text="Token未找到")
            new_status = token.isEnabled
            msg = "已启用" if new_status else "已禁用"
            result = await self._build_tokens_result(edit_message_id=kw.get("message_id"))
            result.answer_callback_text = f"Token {msg}"
            return result
        except Exception as e:
            return CommandResult(text="", answer_callback_text=f"操作失败: {e}")

    async def cb_token_delete(self, params, user_id, channel, **kw):
        """显示删除确认"""
        token_id = int(params[0]) if params else 0
        return CommandResult(
            text=f"⚠️ 确认删除 Token (ID: {token_id})？",
            reply_markup=[
                [{"text": "✅ 确认删除", "callback_data": f"token_confirm_delete:{token_id}"},
                 {"text": "❌ 取消", "callback_data": "token_cancel_delete"}],
            ],
            edit_message_id=kw.get("message_id"),
        )

    async def cb_token_confirm_delete(self, params, user_id, channel, **kw):
        token_id = int(params[0]) if params else 0
        try:
            db = get_database_service()
            async with db.transaction():
                ok = await db.api_token.delete(token_id)
            if not ok:
                return CommandResult(text="", answer_callback_text="Token未找到")
            result = await self._build_tokens_result(edit_message_id=kw.get("message_id"))
            result.answer_callback_text = "Token 已删除"
            return result
        except Exception as e:
            return CommandResult(text="", answer_callback_text=f"删除失败: {e}")

    async def cb_token_cancel_delete(self, params, user_id, channel, **kw):
        result = await self._build_tokens_result(edit_message_id=kw.get("message_id"))
        result.answer_callback_text = "已取消"
        return result

    async def cb_token_add(self, params, user_id, channel, **kw):
        """开始添加Token流程 — 进入名称输入状态"""
        self.set_conversation(user_id, "token_name_input", {},
                              chat_id=kw.get("chat_id"))
        return CommandResult(
            text="请输入新 Token 的名称：",
            next_state="token_name_input",
            edit_message_id=kw.get("message_id"),
        )

    async def _text_token_name(self, text: str, user_id: str, channel, **kw):
        """Token名称输入 → 选择有效期"""
        conv = self.get_conversation(user_id)
        if not conv:
            return CommandResult(text="操作已过期，请重新开始。")
        conv.data["token_name"] = text
        self.set_conversation(user_id, "token_validity_select", conv.data,
                              chat_id=kw.get("chat_id"))
        return CommandResult(
            text=f"Token名称: {text}\n请选择有效期：",
            reply_markup=[
                [{"text": "30天", "callback_data": "token_validity:30"},
                 {"text": "90天", "callback_data": "token_validity:90"}],
                [{"text": "180天", "callback_data": "token_validity:180"},
                 {"text": "永久", "callback_data": "token_validity:permanent"}],
            ],
        )

    async def cb_token_validity(self, params, user_id, channel, **kw):
        """选择有效期 → 创建Token"""
        validity = params[0] if params else "permanent"
        conv = self.get_conversation(user_id)
        if not conv:
            return CommandResult(text="", answer_callback_text="操作已过期")
        token_name = conv.data.get("token_name", "未命名")
        self.clear_conversation(user_id)
        try:
            token_str = secrets.token_urlsafe(16)
            db = get_database_service()
            async with db.transaction():
                await db.api_token.create(token_name, token_str, validity, 0)
            result = await self._build_tokens_result(edit_message_id=kw.get("message_id"))
            result.answer_callback_text = f"Token「{token_name}」创建成功"
            return result
        except Exception as e:
            return CommandResult(text=f"创建Token失败: {e}")

