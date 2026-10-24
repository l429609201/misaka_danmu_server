"""
/auto 菜单 Mixin — 自动导入（多平台）
"""
import logging
from src.schemas.media_types import is_movie_type
from src.notification.base import CommandResult

logger = logging.getLogger(__name__)


class AutoMenuMixin:
    """处理 /auto 命令的所有 cmd_/cb_/_text_ 方法"""

    async def cmd_auto(self, args: str, user_id: str, channel, **kw) -> CommandResult:
        """自动导入 — 选择搜索方式"""
        return CommandResult(
            text="🔄 自动导入 — 请选择搜索方式：",
            reply_markup=[
                [{"text": "🔑 关键词", "callback_data": "auto_type:keyword"},
                 {"text": "🎬 TMDB", "callback_data": "auto_type:tmdb"}],
                [{"text": "📺 TVDB", "callback_data": "auto_type:tvdb"},
                 {"text": "🎭 豆瓣", "callback_data": "auto_type:douban"}],
                [{"text": "🎞️ IMDB", "callback_data": "auto_type:imdb"},
                 {"text": "📚 Bangumi", "callback_data": "auto_type:bangumi"}],
            ],
        )

    async def cb_auto_type(self, params, user_id, channel, **kw):
        """选择搜索类型后 → 进入输入状态"""
        search_type = params[0] if params else "keyword"
        if search_type == "keyword":
            self.set_conversation(user_id, "auto_keyword_input",
                                  {"search_type": search_type},
                                  chat_id=kw.get("chat_id"))
            return CommandResult(
                text="请输入搜索关键词：",
                edit_message_id=kw.get("message_id"),
            )
        else:
            self.set_conversation(user_id, "auto_id_input",
                                  {"search_type": search_type},
                                  chat_id=kw.get("chat_id"))
            label = {"tmdb": "TMDB", "tvdb": "TVDB", "douban": "豆瓣",
                     "imdb": "IMDB", "bangumi": "Bangumi"}.get(search_type, search_type.upper())
            return CommandResult(
                text=f"请输入 {label} ID：",
                edit_message_id=kw.get("message_id"),
            )

    async def _text_auto_keyword(self, text: str, user_id: str, channel, **kw):
        """关键词输入 → 选择媒体类型"""
        conv = self.get_conversation(user_id)
        search_type = conv.data.get("search_type", "keyword") if conv else "keyword"
        conv_data = {"search_type": search_type, "search_term": text.strip()}
        self.set_conversation(user_id, "auto_media_type_select", conv_data,
                              chat_id=kw.get("chat_id"))
        return CommandResult(
            text=f"搜索: {text.strip()}\n请选择媒体类型：",
            reply_markup=[
                [{"text": "📺 电视剧/番剧", "callback_data": "auto_media_type:tv_series"},
                 {"text": "🎬 电影", "callback_data": "auto_media_type:movie"}],
            ],
        )

    async def _text_auto_id(self, text: str, user_id: str, channel, **kw):
        """ID 输入 → 直接提交导入"""
        conv = self.get_conversation(user_id)
        if not conv:
            return CommandResult(text="操作已过期。")
        search_type = conv.data.get("search_type", "keyword")
        self.clear_conversation(user_id)
        return await self._submit_auto_import(
            search_type=search_type, search_term=text.strip(),
        )

    async def cb_auto_media_type(self, params, user_id, channel, **kw):
        """选择媒体类型：电影直接导入，电视剧选季度"""
        media_type = params[0] if params else "tv_series"
        conv = self.get_conversation(user_id)
        if not conv:
            return CommandResult(text="", answer_callback_text="操作已过期")
        search_term = conv.data.get("search_term", "")
        if is_movie_type(media_type):
            self.clear_conversation(user_id)
            return await self._submit_auto_import(
                search_type=conv.data.get("search_type", "keyword"), search_term=search_term,
                media_type="movie", edit_message_id=kw.get("message_id"),
            )
        conv.data["media_type"] = media_type
        self.set_conversation(user_id, "auto_season_select", conv.data,
                              chat_id=kw.get("chat_id"))
        buttons = []
        row = []
        for s in range(1, 13):
            row.append({"text": f"S{s:02d}", "callback_data": f"auto_season:{s}"})
            if len(row) == 4:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([{"text": "🔢 自动推断", "callback_data": "auto_season:0"}])
        return CommandResult(
            text=f"🔍 搜索词：{search_term}\n🗂 类型：电视剧/番剧\n\n请选择季度（或自动推断）：",
            reply_markup=buttons,
            edit_message_id=kw.get("message_id"),
        )

    async def cb_auto_season(self, params, user_id, channel, **kw):
        """选择季度 → 提交导入"""
        season = int(params[0]) if params else 0
        conv = self.get_conversation(user_id)
        if not conv:
            return CommandResult(text="", answer_callback_text="操作已过期")
        search_term = conv.data.get("search_term", "")
        media_type = conv.data.get("media_type") or "tv_series"
        search_type = conv.data.get("search_type", "keyword")
        self.clear_conversation(user_id)
        return await self._submit_auto_import(
            search_type=search_type, search_term=search_term,
            media_type=media_type,
            season=season if season > 0 else None,
            edit_message_id=kw.get("message_id"),
        )

