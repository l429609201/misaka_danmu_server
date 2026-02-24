"""
NotificationService — 通知系统的通用内部 API
所有渠道实现只调用此类的方法，不引用系统其他模块。
支持命令处理、回调处理、对话状态管理、事件分发。
按钮使用平台无关格式，渠道层根据自身能力决定渲染方式。
"""

import logging
import re
import secrets
from typing import Any, Callable, Dict, List, Optional

from src.notification.base import CommandResult, ConversationState, ChannelCapabilities

logger = logging.getLogger(__name__)

# 分页常量
PAGE_SIZE = 5


class NotificationService:
    """通知系统核心服务：命令处理 / 回调处理 / 对话状态 / 事件分发"""

    def __init__(self, session_factory: Callable):
        self._session_factory = session_factory
        # 以下依赖在 main.py 中通过 set_dependencies 注入
        self.scraper_manager = None
        self.metadata_manager = None
        self.task_manager = None
        self.scheduler_manager = None
        self.config_manager = None
        self.rate_limiter = None
        self.title_recognition_manager = None
        self.ai_matcher_manager = None
        # 渠道管理器引用（由 NotificationManager 设置）
        self.notification_manager = None
        # 对话状态: user_id -> ConversationState
        self._conversations: Dict[str, ConversationState] = {}

    def set_dependencies(self, **kwargs):
        """注入系统依赖"""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)

    # ═══════════════════════════════════════════
    # 菜单命令定义
    # ═══════════════════════════════════════════

    # 命令定义：{"/command": "描述"}
    MENU_COMMANDS = {
        "/search": "搜索弹幕源",
        "/auto": "自动导入（多平台）",
        "/url": "从URL导入弹幕",
        "/refresh": "刷新弹幕源",
        "/tokens": "Token管理",
        "/tasks": "定时任务列表",
        "/cache": "清除缓存",
        "/cancel": "取消当前操作",
        "/help": "显示帮助",
    }

    def get_menu_commands(self) -> Dict[str, str]:
        """返回菜单命令定义，供渠道层注册 BotCommand 等菜单"""
        return self.MENU_COMMANDS

    # ═══════════════════════════════════════════
    # 能力感知辅助
    # ═══════════════════════════════════════════

    @staticmethod
    def _buttons_to_text_fallback(text: str, buttons: List[List[Dict[str, str]]]) -> str:
        """将按钮列表降级为纯文本附加到消息末尾（用于不支持按钮的渠道）"""
        if not buttons:
            return text
        lines = [text, "", "可用操作："]
        idx = 1
        for row in buttons:
            for btn in row:
                btn_text = btn.get("text", "")
                callback = btn.get("callback_data", "")
                if callback:
                    lines.append(f"  {idx}. {btn_text}")
                    idx += 1
        return "\n".join(lines)

    # ═══════════════════════════════════════════
    # 对话状态管理
    # ═══════════════════════════════════════════

    def get_conversation(self, user_id: str) -> Optional[ConversationState]:
        """获取用户当前对话状态（自动清理过期状态）"""
        conv = self._conversations.get(user_id)
        if conv and conv.is_expired:
            del self._conversations[user_id]
            return None
        return conv

    def set_conversation(self, user_id: str, state: str, data: dict = None,
                         message_id: int = None, chat_id: int = None):
        """设置用户对话状态"""
        self._conversations[user_id] = ConversationState(
            state=state,
            data=data or {},
            message_id=message_id,
            chat_id=chat_id,
        )

    def clear_conversation(self, user_id: str):
        """清除用户对话状态"""
        self._conversations.pop(user_id, None)

    def update_conversation_message_id(self, user_id: str, message_id: int):
        """更新对话关联的消息ID（由渠道层回写）"""
        conv = self._conversations.get(user_id)
        if conv:
            conv.message_id = message_id

    # ═══════════════════════════════════════════
    # 入站：命令处理（用户 → 系统）
    # ═══════════════════════════════════════════

    async def handle_command(self, command: str, user_id: str, args: str,
                             channel, **kwargs) -> CommandResult:
        """统一命令分发"""
        # 新命令进来时清除旧对话状态
        self.clear_conversation(user_id)
        handler_map = {
            "start": self.cmd_start,
            "help": self.cmd_help,
            "search": self.cmd_search,
            "tasks": self.cmd_list_tasks,
            "tokens": self.cmd_list_tokens,
            "auto": self.cmd_auto,
            "refresh": self.cmd_refresh,
            "url": self.cmd_url,
            "cache": self.cmd_cache,
        }
        handler = handler_map.get(command)
        if not handler:
            return CommandResult(success=False, text=f"未知命令: /{command}\n使用 /help 查看可用命令。")
        try:
            return await handler(args, user_id, channel, **kwargs)
        except Exception as e:
            logger.error(f"命令 /{command} 执行失败: {e}", exc_info=True)
            return CommandResult(success=False, text=f"命令执行出错: {e}")

    async def handle_callback(self, callback_data: str, user_id: str,
                               channel, **kwargs) -> CommandResult:
        """统一回调分发 — callback_data 格式: action:param1:param2:..."""
        parts = callback_data.split(":")
        action = parts[0] if parts else ""
        params = parts[1:] if len(parts) > 1 else []
        callback_map = {
            # tasks
            "tasks_refresh": self.cb_tasks_refresh,
            # tokens
            "tokens_refresh": self.cb_tokens_refresh,
            "token_toggle": self.cb_token_toggle,
            "token_delete": self.cb_token_delete,
            "token_confirm_delete": self.cb_token_confirm_delete,
            "token_cancel_delete": self.cb_token_cancel_delete,
            "token_add": self.cb_token_add,
            "token_validity": self.cb_token_validity,
            # search
            "search_page": self.cb_search_page,
            "search_import": self.cb_search_import,
            "search_episodes": self.cb_search_episodes,
            "ep_page": self.cb_episode_page,
            # search → edit import
            "search_edit": self.cb_search_edit,
            "edit_ep_toggle": self.cb_edit_ep_toggle,
            "edit_ep_page": self.cb_edit_ep_page,
            "edit_ep_all": self.cb_edit_ep_all,
            "edit_ep_none": self.cb_edit_ep_none,
            "edit_type": self.cb_edit_type,
            "edit_season": self.cb_edit_season,
            "edit_title": self.cb_edit_title,
            "edit_confirm": self.cb_edit_confirm,
            "edit_back": self.cb_edit_back,
            # auto
            "auto_type": self.cb_auto_type,
            "auto_media_type": self.cb_auto_media_type,
            "auto_season": self.cb_auto_season,
            # refresh
            "refresh_anime": self.cb_refresh_anime,
            "refresh_source": self.cb_refresh_source,
            "refresh_ep_page": self.cb_refresh_ep_page,
            "refresh_do": self.cb_refresh_do,
            "lib_page": self.cb_lib_page,
            # task detail
            "task_detail": self.cb_task_detail,
            # help inline buttons
            "help_cmd": self.cb_help_cmd,
            # noop
            "noop": self.cb_noop,
        }
        handler = callback_map.get(action)
        if not handler:
            return CommandResult(text="", answer_callback_text="未知操作")
        try:
            return await handler(params, user_id, channel, **kwargs)
        except Exception as e:
            logger.error(f"回调 {action} 执行失败: {e}", exc_info=True)
            return CommandResult(text="", answer_callback_text=f"操作失败: {e}")

    async def handle_text_input(self, text: str, user_id: str,
                                 channel, **kwargs) -> Optional[CommandResult]:
        """处理对话状态机中的文本输入"""
        conv = self.get_conversation(user_id)
        if not conv:
            return None  # 没有活跃对话，忽略
        state = conv.state
        text_handler_map = {
            "token_name_input": self._text_token_name,
            "auto_keyword_input": self._text_auto_keyword,
            "auto_id_input": self._text_auto_id,
            "url_input": self._text_url_input,
            "refresh_keyword_input": self._text_refresh_keyword,
            "search_episode_range": self._text_search_episode_range,
            "refresh_episode_range": self._text_refresh_episode_range,
            "edit_title_input": self._text_edit_title,
        }
        handler = text_handler_map.get(state)
        if not handler:
            return None
        try:
            return await handler(text, user_id, channel, **kwargs)
        except Exception as e:
            logger.error(f"文本处理 {state} 失败: {e}", exc_info=True)
            self.clear_conversation(user_id)
            return CommandResult(text=f"处理出错: {e}")

    async def handle_cancel(self, user_id: str) -> CommandResult:
        """取消当前对话"""
        conv = self.get_conversation(user_id)
        self.clear_conversation(user_id)
        if conv:
            return CommandResult(text="✅ 已取消当前操作。")
        return CommandResult(text="当前没有进行中的操作。")

    # ═══════════════════════════════════════════
    # 出站：通知发送（系统 → 用户）
    # ═══════════════════════════════════════════

    async def emit_event(self, event_type: str, data: Dict[str, Any]):
        """向所有订阅了该事件的渠道发送通知"""
        if not self.notification_manager:
            return
        channels = self.notification_manager.get_all_channels()
        for ch_id, channel_instance in channels.items():
            try:
                events_cfg = channel_instance.config.get("__events_config", {})
                if not events_cfg.get(event_type, False):
                    continue
                title, text = self._format_event_message(event_type, data)
                await channel_instance.send_message(title=title, text=text)
            except Exception as e:
                logger.error(f"渠道 {ch_id} 发送事件 {event_type} 失败: {e}")

    # ═══════════════════════════════════════════
    # 事件消息格式化
    # ═══════════════════════════════════════════

    EVENT_LABELS = {
        "import_success": ("导入成功", True),
        "import_failed": ("导入失败", False),
        "refresh_success": ("刷新成功", True),
        "refresh_failed": ("刷新失败", False),
        "auto_import_success": ("自动导入成功", True),
        "auto_import_failed": ("自动导入失败", False),
        "webhook_triggered": ("Webhook 触发", True),
        "webhook_import_success": ("Webhook 导入成功", True),
        "webhook_import_failed": ("Webhook 导入失败", False),
        "incremental_refresh_success": ("追更刷新成功", True),
        "incremental_refresh_failed": ("追更刷新失败", False),
        "media_scan_complete": ("媒体库扫描完成", True),
        "scheduled_task_complete": ("定时任务完成", True),
        "scheduled_task_failed": ("定时任务失败", False),
        "system_start": ("系统启动", True),
    }

    def _format_event_message(self, event_type: str, data: Dict[str, Any]) -> tuple:
        """根据事件类型格式化通知消息，返回 (title, text)"""
        label_info = self.EVENT_LABELS.get(event_type)
        if not label_info:
            return (event_type, data.get("text", ""))

        label, is_success = label_info
        task_title = data.get("task_title", "")
        message = data.get("message", "")

        if event_type == "system_start":
            return (label, "弹幕服务器已启动完成 ✓")

        if event_type == "webhook_triggered":
            anime = data.get("anime_title", "未知")
            source = data.get("webhook_source", "")
            return (label, f"媒体: {anime}\n来源: {source}")

        # 通用任务类消息
        lines = []
        if task_title:
            lines.append(f"任务: {task_title}")
        if message:
            lines.append(f"{'结果' if is_success else '错误'}: {message}")
        return (label, "\n".join(lines) if lines else label)

    # ═══════════════════════════════════════════
    # 命令实现
    # ═══════════════════════════════════════════

    HELP_TEXT = (
        "📖 *命令列表:*\n\n"
        "🔍 /search <关键词> - 搜索弹幕源\n"
        "  _支持指定季集，如: /search 刀剑神域 S01E10_\n"
        "🔄 /auto - 自动导入（多平台）\n"
        "🔗 /url - 从URL导入弹幕\n"
        "♻️ /refresh - 刷新弹幕源\n"
        "🔑 /tokens - Token管理\n"
        "📋 /tasks - 定时任务列表\n"
        "🗑️ /cache - 清除缓存\n"
        "❌ /cancel - 取消当前操作\n"
        "📖 /help - 显示此帮助\n\n"
        "💡 点击下方按钮可快速执行命令"
    )

    HELP_BUTTONS = [
        [{"text": "🔍 搜索弹幕", "callback_data": "help_cmd:search"},
         {"text": "🔄 自动导入", "callback_data": "help_cmd:auto"}],
        [{"text": "🔗 URL导入", "callback_data": "help_cmd:url"},
         {"text": "♻️ 刷新弹幕", "callback_data": "help_cmd:refresh"}],
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

    # ── /tasks ──

    async def cmd_list_tasks(self, args: str, user_id: str, channel, **kw) -> CommandResult:
        return await self._build_tasks_result()

    async def _build_tasks_result(self, edit_message_id: int = None) -> CommandResult:
        if not self.scheduler_manager:
            return CommandResult(success=False, text="调度服务未就绪。")
        try:
            tasks = await self.scheduler_manager.get_all_tasks()
            if not tasks:
                return CommandResult(text="📋 当前没有定时任务。")
            lines = ["📋 定时任务列表:\n"]
            for t in tasks:
                status = "✅" if t.get("isEnabled") else "⏸️"
                name = t.get("name", "未知")
                cron = t.get("cron", "")
                lines.append(f"{status} {name} ({cron})")
            return CommandResult(
                text="\n".join(lines),
                reply_markup=[[{"text": "🔄 刷新", "callback_data": "tasks_refresh"}]],
                edit_message_id=edit_message_id,
            )
        except Exception as e:
            logger.error(f"获取任务列表失败: {e}", exc_info=True)
            return CommandResult(success=False, text=f"获取任务列表出错: {e}")

    async def cb_tasks_refresh(self, params, user_id, channel, **kw):
        msg_id = kw.get("message_id")
        return await self._build_tasks_result(edit_message_id=msg_id)

    async def cb_noop(self, params, user_id, channel, **kw):
        return CommandResult(text="", answer_callback_text="")

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

    # ── /search ──

    @staticmethod
    def _parse_season_episode(keyword: str):
        """从关键词中解析 S01E10 / S01 格式，返回 (clean_keyword, season, episode)"""
        m = re.search(r'\bS(\d{1,2})E(\d{1,3})\b', keyword, re.IGNORECASE)
        if m:
            clean = keyword[:m.start()].strip() + ' ' + keyword[m.end():].strip()
            return clean.strip(), int(m.group(1)), str(int(m.group(2)))
        m = re.search(r'\bS(\d{1,2})\b', keyword, re.IGNORECASE)
        if m:
            clean = keyword[:m.start()].strip() + ' ' + keyword[m.end():].strip()
            return clean.strip(), int(m.group(1)), None
        return keyword, None, None

    async def cmd_search(self, args: str, user_id: str, channel, **kw) -> CommandResult:
        if not args or not args.strip():
            return CommandResult(success=False, text=(
                "请提供搜索关键词。\n\n"
                "用法: /search <关键词>\n"
                "示例:\n"
                "  /search 刀剑神域 — 搜索并整季导入\n"
                "  /search 刀剑神域 S01 — 指定第1季导入\n"
                "  /search 刀剑神域 S01E10 — 仅导入第1季第10集"
            ))
        raw_keyword = args.strip()
        # 解析 S0XE0X 格式
        keyword, parsed_season, parsed_episode = self._parse_season_episode(raw_keyword)
        if not keyword:
            keyword = raw_keyword  # 防止关键词被完全清空
        if not self.scraper_manager:
            return CommandResult(success=False, text="搜索服务未就绪。")
        try:
            async with self._session_factory() as session:
                from src.services.search import unified_search
                results = await unified_search(
                    search_term=keyword,
                    session=session,
                    scraper_manager=self.scraper_manager,
                    metadata_manager=self.metadata_manager,
                    use_alias_expansion=False,
                    use_alias_filtering=False,
                    use_title_filtering=True,
                    use_source_priority_sorting=True,
                    progress_callback=None,
                )
            if not results:
                return CommandResult(text=f"🔍 未找到与「{keyword}」相关的结果。")
            # 序列化结果存入对话状态（字段来自 ProviderSearchInfo）
            serialized = []
            for r in results:
                # 兼容 Pydantic model 和 dict 两种情况
                if hasattr(r, 'model_dump'):
                    d = r.model_dump()
                elif isinstance(r, dict):
                    d = r
                else:
                    d = vars(r) if hasattr(r, '__dict__') else {}
                serialized.append({
                    "title": d.get('title', '未知'),
                    "provider": d.get('provider', '未知源'),
                    "mediaId": d.get('mediaId'),
                    "type": d.get('type', ''),
                    "season": d.get('season', 1),
                    "year": d.get('year'),
                    "episodeCount": d.get('episodeCount'),
                    "imageUrl": d.get('imageUrl'),
                })
            # 存入对话状态，包含解析出的季集信息
            conv_data = {"keyword": keyword, "results": serialized}
            if parsed_season is not None:
                conv_data["parsed_season"] = parsed_season
            if parsed_episode is not None:
                conv_data["parsed_episode"] = parsed_episode
            self.set_conversation(user_id, "search_results", conv_data,
                                  chat_id=kw.get("chat_id"))
            # 如果解析出了季集信息，在搜索结果标题中提示
            suffix = ""
            if parsed_season is not None:
                suffix += f" S{parsed_season:02d}"
            if parsed_episode is not None:
                suffix += f"E{parsed_episode}"
            display_keyword = keyword + suffix if suffix else keyword
            return self._build_search_page(serialized, display_keyword, 0)
        except Exception as e:
            logger.error(f"搜索失败: {e}", exc_info=True)
            return CommandResult(success=False, text=f"搜索出错: {e}")

    def _build_search_page(self, results: list, keyword: str, page: int,
                           edit_message_id: int = None) -> CommandResult:
        total = len(results)
        start = page * PAGE_SIZE
        end = min(start + PAGE_SIZE, total)
        page_items = results[start:end]
        total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE

        lines = [f"🔍 搜索「{keyword}」({start+1}-{end}/{total}):\n"]
        buttons = []
        for i, r in enumerate(page_items):
            idx = start + i
            # 显示更丰富的信息：源、标题、年份、集数
            year_str = f" ({r['year']})" if r.get('year') else ""
            ep_str = f" {r.get('episodeCount', '?')}集" if r.get('episodeCount') else ""
            lines.append(f"{idx+1}. [{r['provider']}] {r['title']}{year_str}{ep_str}")
            row = [
                {"text": f"📥 导入 {idx+1}", "callback_data": f"search_import:{idx}"},
                {"text": f"✏️ 编辑 {idx+1}", "callback_data": f"search_edit:{idx}"},
            ]
            buttons.append(row)

        # 分页按钮
        nav = []
        if page > 0:
            nav.append({"text": "⬅️ 上一页", "callback_data": f"search_page:{page-1}"})
        if page < total_pages - 1:
            nav.append({"text": "➡️ 下一页", "callback_data": f"search_page:{page+1}"})
        if nav:
            buttons.append(nav)

        return CommandResult(
            text="\n".join(lines),
            reply_markup=buttons,
            edit_message_id=edit_message_id,
        )

    async def cb_search_page(self, params, user_id, channel, **kw):
        page = int(params[0]) if params else 0
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "search_results":
            return CommandResult(text="", answer_callback_text="搜索已过期，请重新搜索")
        return self._build_search_page(
            conv.data["results"], conv.data["keyword"], page,
            edit_message_id=kw.get("message_id"),
        )

    async def cb_search_import(self, params, user_id, channel, **kw):
        """直接导入选中的搜索结果"""
        idx = int(params[0]) if params else 0
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "search_results":
            return CommandResult(text="", answer_callback_text="搜索已过期")
        results = conv.data.get("results", [])
        if idx >= len(results):
            return CommandResult(text="", answer_callback_text="无效的选择")
        item = results[idx]
        media_id = item.get("mediaId")
        title = item.get("title", "未知")
        provider = item.get("provider", "")
        if not media_id:
            return CommandResult(text="", answer_callback_text="该结果无法直接导入")
        # 读取对话中解析出的季集信息
        parsed_season = conv.data.get("parsed_season")
        parsed_episode = conv.data.get("parsed_episode")
        season = parsed_season if parsed_season is not None else item.get("season")
        # 提交自动导入任务
        self.clear_conversation(user_id)
        return await self._submit_auto_import(
            search_type="keyword", search_term=title,
            media_type=item.get("type"), season=season,
            episode=parsed_episode,
            edit_message_id=kw.get("message_id"),
        )

    async def cb_search_episodes(self, params, user_id, channel, **kw):
        return CommandResult(text="", answer_callback_text="分集导入暂未实现")

    async def cb_episode_page(self, params, user_id, channel, **kw):
        return CommandResult(text="", answer_callback_text="分集分页暂未实现")

    # ── 编辑导入流程 ──

    EDIT_EP_PAGE_SIZE = 8  # 编辑导入分集每页显示数

    async def cb_search_edit(self, params, user_id, channel, **kw):
        """点击「编辑导入」→ 获取分集列表 → 进入编辑界面"""
        idx = int(params[0]) if params else 0
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "search_results":
            return CommandResult(text="", answer_callback_text="搜索已过期")
        results = conv.data.get("results", [])
        if idx >= len(results):
            return CommandResult(text="", answer_callback_text="无效的选择")
        item = results[idx]
        provider = item.get("provider", "")
        media_id = item.get("mediaId")
        if not media_id or not provider:
            return CommandResult(text="", answer_callback_text="该结果不支持编辑导入")
        if not self.scraper_manager:
            return CommandResult(text="", answer_callback_text="搜索服务未就绪")
        try:
            scraper = self.scraper_manager.get_scraper(provider)
            episodes = await scraper.get_episodes(media_id, db_media_type=item.get("type"))
            if not episodes:
                return CommandResult(text="", answer_callback_text="未获取到分集列表")
            # 序列化分集
            ep_list = []
            for ep in episodes:
                if hasattr(ep, 'model_dump'):
                    d = ep.model_dump()
                elif isinstance(ep, dict):
                    d = ep
                else:
                    d = vars(ep) if hasattr(ep, '__dict__') else {}
                ep_list.append({
                    "provider": d.get("provider", provider),
                    "episodeId": d.get("episodeId", ""),
                    "title": d.get("title", f"第{d.get('episodeIndex', '?')}集"),
                    "episodeIndex": d.get("episodeIndex", 0),
                    "url": d.get("url"),
                })
            # 默认全选
            selected = list(range(len(ep_list)))
            edit_data = {
                "item": item,
                "episodes": ep_list,
                "selected": selected,
                "title": item.get("title", ""),
                "type": item.get("type", "tv_series"),
                "season": item.get("season", 1),
            }
            self.set_conversation(user_id, "edit_import", edit_data,
                                  chat_id=kw.get("chat_id"))
            return self._build_edit_page(edit_data, 0, edit_message_id=kw.get("message_id"))
        except Exception as e:
            logger.error(f"获取分集列表失败: {e}", exc_info=True)
            return CommandResult(text=f"❌ 获取分集列表失败: {e}",
                                 edit_message_id=kw.get("message_id"))

    def _build_edit_page(self, edit_data: dict, page: int,
                         edit_message_id: int = None) -> CommandResult:
        """构建编辑导入界面"""
        item = edit_data["item"]
        episodes = edit_data["episodes"]
        selected = set(edit_data.get("selected", []))
        title = edit_data.get("title", item.get("title", ""))
        media_type = edit_data.get("type", "tv_series")
        season = edit_data.get("season", 1)
        type_label = "📺 电视剧" if media_type == "tv_series" else "🎬 电影"

        total_eps = len(episodes)
        ps = self.EDIT_EP_PAGE_SIZE
        start = page * ps
        end = min(start + ps, total_eps)
        total_pages = (total_eps + ps - 1) // ps

        lines = [
            f"✏️ 编辑导入: {title}",
            f"源: {item.get('provider', '?')} | {type_label} | S{season:02d}",
            f"已选: {len(selected)}/{total_eps} 集\n",
        ]
        buttons = []
        # 分集列表（带选择状态）
        for i in range(start, end):
            ep = episodes[i]
            check = "✅" if i in selected else "⬜"
            ep_label = f"{check} {ep['episodeIndex']}. {ep['title']}"
            # 截断过长的标题
            if len(ep_label) > 40:
                ep_label = ep_label[:37] + "..."
            buttons.append([{
                "text": ep_label,
                "callback_data": f"edit_ep_toggle:{i}:{page}",
            }])

        # 分页导航
        nav = []
        if page > 0:
            nav.append({"text": "⬅️ 上一页", "callback_data": f"edit_ep_page:{page-1}"})
        if page < total_pages - 1:
            nav.append({"text": "➡️ 下一页", "callback_data": f"edit_ep_page:{page+1}"})
        if nav:
            buttons.append(nav)

        # 全选/取消全选
        buttons.append([
            {"text": "☑️ 全选", "callback_data": "edit_ep_all"},
            {"text": "⬜ 取消全选", "callback_data": "edit_ep_none"},
        ])
        # 编辑操作
        buttons.append([
            {"text": "📝 改标题", "callback_data": "edit_title"},
            {"text": "🔄 改类型", "callback_data": f"edit_type:{media_type}"},
            {"text": f"S{season:02d} 改季度", "callback_data": "edit_season"},
        ])
        # 确认/返回
        buttons.append([
            {"text": "🔙 返回搜索", "callback_data": "edit_back"},
            {"text": f"✅ 确认导入 ({len(selected)}集)", "callback_data": "edit_confirm"},
        ])

        return CommandResult(
            text="\n".join(lines),
            reply_markup=buttons,
            edit_message_id=edit_message_id,
        )

    async def cb_edit_ep_toggle(self, params, user_id, channel, **kw):
        """切换单集选择状态"""
        ep_idx = int(params[0]) if params else 0
        page = int(params[1]) if len(params) > 1 else 0
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "edit_import":
            return CommandResult(text="", answer_callback_text="操作已过期")
        selected = set(conv.data.get("selected", []))
        if ep_idx in selected:
            selected.discard(ep_idx)
        else:
            selected.add(ep_idx)
        conv.data["selected"] = list(selected)
        return self._build_edit_page(conv.data, page,
                                     edit_message_id=kw.get("message_id"))

    async def cb_edit_ep_page(self, params, user_id, channel, **kw):
        """编辑导入分集分页"""
        page = int(params[0]) if params else 0
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "edit_import":
            return CommandResult(text="", answer_callback_text="操作已过期")
        return self._build_edit_page(conv.data, page,
                                     edit_message_id=kw.get("message_id"))

    async def cb_edit_ep_all(self, params, user_id, channel, **kw):
        """全选分集"""
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "edit_import":
            return CommandResult(text="", answer_callback_text="操作已过期")
        total = len(conv.data.get("episodes", []))
        conv.data["selected"] = list(range(total))
        return self._build_edit_page(conv.data, 0,
                                     edit_message_id=kw.get("message_id"))

    async def cb_edit_ep_none(self, params, user_id, channel, **kw):
        """取消全选"""
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "edit_import":
            return CommandResult(text="", answer_callback_text="操作已过期")
        conv.data["selected"] = []
        return self._build_edit_page(conv.data, 0,
                                     edit_message_id=kw.get("message_id"))

    async def cb_edit_type(self, params, user_id, channel, **kw):
        """切换媒体类型"""
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "edit_import":
            return CommandResult(text="", answer_callback_text="操作已过期")
        current = conv.data.get("type", "tv_series")
        conv.data["type"] = "movie" if current == "tv_series" else "tv_series"
        if conv.data["type"] == "movie":
            conv.data["season"] = 1
        return self._build_edit_page(conv.data, 0,
                                     edit_message_id=kw.get("message_id"))

    async def cb_edit_season(self, params, user_id, channel, **kw):
        """显示季度选择键盘，或直接设置季度（带参数时）"""
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "edit_import":
            return CommandResult(text="", answer_callback_text="操作已过期")
        # 带参数 → 直接设置季度并返回编辑页
        if params:
            season = int(params[0])
            conv.data["season"] = season
            return self._build_edit_page(conv.data, 0,
                                         edit_message_id=kw.get("message_id"))
        # 无参数 → 显示季度选择键盘
        buttons = []
        row = []
        for s in range(1, 13):
            row.append({"text": f"S{s:02d}", "callback_data": f"edit_season:{s}"})
            if len(row) == 4:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([{"text": "🔙 返回编辑", "callback_data": "edit_ep_page:0"}])
        return CommandResult(
            text=f"✏️ 选择季度（当前: S{conv.data.get('season', 1):02d}）：",
            reply_markup=buttons,
            edit_message_id=kw.get("message_id"),
        )

    async def cb_edit_title(self, params, user_id, channel, **kw):
        """进入标题编辑模式（等待文本输入）"""
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "edit_import":
            return CommandResult(text="", answer_callback_text="操作已过期")
        # 切换到标题输入状态，保留编辑数据
        self.set_conversation(user_id, "edit_title_input", conv.data,
                              chat_id=kw.get("chat_id"))
        return CommandResult(
            text=f"📝 当前标题: {conv.data.get('title', '')}\n\n请输入新标题：",
            edit_message_id=kw.get("message_id"),
        )

    async def _text_edit_title(self, text: str, user_id: str, channel, **kw):
        """接收新标题 → 返回编辑界面"""
        conv = self.get_conversation(user_id)
        if not conv:
            return CommandResult(text="操作已过期。")
        conv.data["title"] = text.strip()
        self.set_conversation(user_id, "edit_import", conv.data,
                              chat_id=kw.get("chat_id"))
        return self._build_edit_page(conv.data, 0)

    async def cb_edit_back(self, params, user_id, channel, **kw):
        """返回搜索结果"""
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "edit_import":
            return CommandResult(text="", answer_callback_text="操作已过期")
        # 恢复搜索结果状态（从 item 中恢复）
        # 需要重新搜索或从缓存恢复，这里简单提示用户重新搜索
        self.clear_conversation(user_id)
        return CommandResult(
            text="已退出编辑导入。请使用 /search 重新搜索。",
            edit_message_id=kw.get("message_id"),
        )

    async def cb_edit_confirm(self, params, user_id, channel, **kw):
        """确认编辑导入 → 提交任务"""
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "edit_import":
            return CommandResult(text="", answer_callback_text="操作已过期")
        selected = set(conv.data.get("selected", []))
        episodes = conv.data.get("episodes", [])
        item = conv.data.get("item", {})
        title = conv.data.get("title", item.get("title", ""))
        media_type = conv.data.get("type", "tv_series")
        season = conv.data.get("season", 1)

        if not selected:
            return CommandResult(text="", answer_callback_text="请至少选择一集")

        # 构建选中的分集列表
        selected_episodes = [episodes[i] for i in sorted(selected) if i < len(episodes)]
        self.clear_conversation(user_id)

        return await self._submit_edited_import(
            provider=item.get("provider", ""),
            media_id=item.get("mediaId", ""),
            title=title,
            media_type=media_type,
            season=season,
            year=item.get("year"),
            image_url=item.get("imageUrl"),
            episodes=selected_episodes,
            edit_message_id=kw.get("message_id"),
        )

    async def _submit_edited_import(self, provider: str, media_id: str,
                                     title: str, media_type: str, season: int,
                                     year=None, image_url=None,
                                     episodes: list = None,
                                     edit_message_id: int = None) -> CommandResult:
        """构造 EditedImportRequest 并提交到 TaskManager"""
        if not self.task_manager:
            return CommandResult(success=False, text="任务管理器未就绪。")
        try:
            import hashlib
            from src.db.models import EditedImportRequest, ProviderEpisodeInfo
            from src.tasks import edited_import_task

            # 构建 ProviderEpisodeInfo 列表
            ep_models = []
            for ep in (episodes or []):
                ep_models.append(ProviderEpisodeInfo(
                    provider=ep.get("provider", provider),
                    episodeId=ep.get("episodeId", ""),
                    title=ep.get("title", ""),
                    episodeIndex=ep.get("episodeIndex", 0),
                    url=ep.get("url"),
                ))

            request_data = EditedImportRequest(
                provider=provider,
                mediaId=media_id,
                animeTitle=title,
                mediaType=media_type,
                season=season,
                year=year,
                imageUrl=image_url,
                episodes=ep_models,
            )

            task_title = f"TG编辑导入: {title} ({provider})"
            task_coro = lambda session, cb: edited_import_task(
                request_data=request_data,
                progress_callback=cb,
                session=session,
                config_manager=self.config_manager,
                manager=self.scraper_manager,
                rate_limiter=self.rate_limiter,
                metadata_manager=self.metadata_manager,
                title_recognition_manager=self.title_recognition_manager,
            )

            episode_indices_str = ",".join(sorted([str(ep.episodeIndex) for ep in ep_models]))
            episodes_hash = hashlib.md5(episode_indices_str.encode('utf-8')).hexdigest()[:8]
            unique_key = f"import-{provider}-{media_id}-{episodes_hash}"

            task_id, _ = await self.task_manager.submit_task(
                task_coro, task_title, unique_key=unique_key
            )
            return CommandResult(
                text=f"✅ 编辑导入任务已提交\n标题: {title}\n源: {provider}\n"
                     f"类型: {media_type} | 季度: S{season:02d}\n"
                     f"选中: {len(ep_models)} 集\n任务ID: {task_id}",
                reply_markup=[[{"text": "📋 查看任务状态", "callback_data": f"task_detail:{task_id}"}]],
                edit_message_id=edit_message_id,
            )
        except Exception as e:
            logger.error(f"提交编辑导入任务失败: {e}", exc_info=True)
            return CommandResult(success=False, text=f"❌ 提交任务失败: {e}")

    # ── /tokens ──

    async def cmd_list_tokens(self, args: str, user_id: str, channel, **kw) -> CommandResult:
        return await self._build_tokens_result()

    async def _build_tokens_result(self, edit_message_id: int = None) -> CommandResult:
        try:
            from src.db import crud
            async with self._session_factory() as session:
                tokens = await crud.get_all_api_tokens(session)
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
                    {"text": f"🗑️ 删除", "callback_data": f"token_delete:{token_id}"},
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
            from src.db import crud
            async with self._session_factory() as session:
                new_status = await crud.toggle_api_token(session, token_id)
            if new_status is None:
                return CommandResult(text="", answer_callback_text="Token未找到")
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
            from src.db import crud
            async with self._session_factory() as session:
                ok = await crud.delete_api_token(session, token_id)
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
            from src.db import crud
            token_str = secrets.token_urlsafe(16)
            async with self._session_factory() as session:
                await crud.create_api_token(session, token_name, token_str, validity, 0)
            result = await self._build_tokens_result(edit_message_id=kw.get("message_id"))
            result.answer_callback_text = f"Token「{token_name}」创建成功"
            return result
        except Exception as e:
            return CommandResult(text=f"创建Token失败: {e}")


    # ── 通用辅助：提交自动导入任务 ──

    async def _submit_auto_import(self, search_type: str, search_term: str,
                                   media_type: str = None, season: int = None,
                                   episode: str = None,
                                   edit_message_id: int = None) -> CommandResult:
        """构造 ControlAutoImportRequest 并提交到 TaskManager"""
        if not self.task_manager:
            return CommandResult(success=False, text="任务管理器未就绪。")
        try:
            from src.api.control.models import (
                ControlAutoImportRequest, AutoImportSearchType, AutoImportMediaType,
            )
            st = AutoImportSearchType(search_type)
            mt = AutoImportMediaType(media_type) if media_type else None
            payload = ControlAutoImportRequest(
                searchType=st, searchTerm=search_term,
                season=season, episode=episode, mediaType=mt,
            )
            from src.tasks import auto_search_and_import_task
            title_parts = [f"TG导入: {search_term} ({search_type})"]
            if season is not None:
                title_parts.append(f"S{season:02d}")
            if episode is not None:
                title_parts.append(f"E{episode}")
            task_title = " ".join(title_parts)

            task_coro = lambda session, cb: auto_search_and_import_task(
                payload, cb, session,
                self.config_manager, self.scraper_manager,
                self.metadata_manager, self.task_manager,
                ai_matcher_manager=self.ai_matcher_manager,
                rate_limiter=self.rate_limiter,
                title_recognition_manager=self.title_recognition_manager,
            )
            task_id, _ = await self.task_manager.submit_task(
                coro_factory=task_coro, title=task_title,
            )
            return CommandResult(
                text=f"✅ 导入任务已提交\n标题: {search_term}\n任务ID: {task_id}",
                reply_markup=[[{"text": "📋 查看任务状态", "callback_data": f"task_detail:{task_id}"}]],
                edit_message_id=edit_message_id,
            )
        except Exception as e:
            logger.error(f"提交自动导入失败: {e}", exc_info=True)
            return CommandResult(text=f"❌ 提交导入任务失败: {e}")

    # ── /auto ──

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
                     "imdb": "IMDB", "bangumi": "Bangumi"}.get(search_type, search_type)
            return CommandResult(
                text=f"请输入 {label} ID 或链接：",
                edit_message_id=kw.get("message_id"),
            )

    async def _text_auto_keyword(self, text: str, user_id: str, channel, **kw):
        """关键词输入 → 选择媒体类型"""
        conv = self.get_conversation(user_id)
        if not conv:
            return CommandResult(text="操作已过期。")
        conv.data["search_term"] = text
        self.set_conversation(user_id, "auto_media_type_select", conv.data,
                              chat_id=kw.get("chat_id"))
        return CommandResult(
            text=f"关键词: {text}\n请选择媒体类型：",
            reply_markup=[
                [{"text": "📺 电视剧/番剧", "callback_data": "auto_media_type:tv_series"},
                 {"text": "🎬 电影", "callback_data": "auto_media_type:movie"}],
            ],
        )

    async def _text_auto_id(self, text: str, user_id: str, channel, **kw):
        """平台ID输入 → 直接提交导入（平台搜索不需要选媒体类型）"""
        conv = self.get_conversation(user_id)
        if not conv:
            return CommandResult(text="操作已过期。")
        search_type = conv.data.get("search_type", "tmdb")
        self.clear_conversation(user_id)
        return await self._submit_auto_import(
            search_type=search_type, search_term=text.strip(),
        )

    async def cb_auto_media_type(self, params, user_id, channel, **kw):
        """选择媒体类型 → 电影直接导入，电视剧选季度"""
        media_type = params[0] if params else "tv_series"
        conv = self.get_conversation(user_id)
        if not conv:
            return CommandResult(text="", answer_callback_text="操作已过期")
        search_term = conv.data.get("search_term", "")
        if media_type == "movie":
            self.clear_conversation(user_id)
            return await self._submit_auto_import(
                search_type="keyword", search_term=search_term,
                media_type="movie", edit_message_id=kw.get("message_id"),
            )
        # 电视剧 → 选季度
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
            text=f"关键词: {search_term}\n类型: 电视剧/番剧\n请选择季度：",
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
        media_type = conv.data.get("media_type", "tv_series")
        self.clear_conversation(user_id)
        return await self._submit_auto_import(
            search_type="keyword", search_term=search_term,
            media_type=media_type,
            season=season if season > 0 else None,
            edit_message_id=kw.get("message_id"),
        )


    # ── /refresh ──

    async def cmd_refresh(self, args: str, user_id: str, channel, **kw) -> CommandResult:
        """刷新弹幕源 — 浏览媒体库或搜索"""
        if args and args.strip():
            return await self._refresh_search_library(args.strip(), user_id, 0, **kw)
        return await self._build_library_page(user_id, 0, **kw)

    async def _build_library_page(self, user_id: str, page: int,
                                   edit_message_id: int = None, **kw) -> CommandResult:
        """构建媒体库分页列表"""
        try:
            from src.db import crud
            async with self._session_factory() as session:
                result = await crud.get_library_anime(
                    session, page=page + 1, page_size=PAGE_SIZE,
                )
            total = result.get("total", 0)
            items = result.get("list", [])
            if not items:
                return CommandResult(
                    text="📚 媒体库为空。\n\n提示: 使用 /refresh <关键词> 搜索媒体库",
                    edit_message_id=edit_message_id,
                )
            total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
            start = page * PAGE_SIZE
            lines = [f"📚 媒体库 ({start+1}-{min(start+PAGE_SIZE, total)}/{total}):\n"]
            buttons = []
            for item in items:
                anime_id = item.get("animeId") or item.get("id")
                title = item.get("title", "未知")
                ep_count = item.get("episodeCount", 0)
                lines.append(f"• {title} ({ep_count}集)")
                buttons.append([{
                    "text": f"📂 {title}",
                    "callback_data": f"refresh_anime:{anime_id}",
                }])
            nav = []
            if page > 0:
                nav.append({"text": "⬅️ 上一页", "callback_data": f"lib_page:{page-1}"})
            if page < total_pages - 1:
                nav.append({"text": "➡️ 下一页", "callback_data": f"lib_page:{page+1}"})
            if nav:
                buttons.append(nav)
            # 设置对话状态，让用户可以直接发关键词搜索
            self.set_conversation(user_id, "refresh_keyword_input", {},
                                  chat_id=kw.get("chat_id"))
            return CommandResult(
                text="\n".join(lines) + "\n\n💡 也可发送关键词搜索媒体库",
                reply_markup=buttons,
                edit_message_id=edit_message_id or kw.get("message_id"),
            )
        except Exception as e:
            logger.error(f"获取媒体库失败: {e}", exc_info=True)
            return CommandResult(text=f"获取媒体库出错: {e}")

    async def _refresh_search_library(self, keyword: str, user_id: str,
                                       page: int, edit_message_id: int = None,
                                       **kw) -> CommandResult:
        """在媒体库中搜索"""
        try:
            from src.db import crud
            async with self._session_factory() as session:
                result = await crud.get_library_anime(
                    session, keyword=keyword, page=page + 1, page_size=PAGE_SIZE,
                )
            total = result.get("total", 0)
            items = result.get("list", [])
            if not items:
                return CommandResult(text=f"📚 媒体库中未找到「{keyword}」。")
            # 存入对话以支持分页
            self.set_conversation(user_id, "refresh_library_browse", {
                "keyword": keyword,
            }, chat_id=kw.get("chat_id"))
            total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
            start = page * PAGE_SIZE
            lines = [f"📚 搜索「{keyword}」({start+1}-{min(start+PAGE_SIZE, total)}/{total}):\n"]
            buttons = []
            for item in items:
                anime_id = item.get("animeId") or item.get("id")
                title = item.get("title", "未知")
                lines.append(f"• {title}")
                buttons.append([{
                    "text": f"📂 {title}",
                    "callback_data": f"refresh_anime:{anime_id}",
                }])
            nav = []
            if page > 0:
                nav.append({"text": "⬅️", "callback_data": f"lib_page:{page-1}"})
            if page < total_pages - 1:
                nav.append({"text": "➡️", "callback_data": f"lib_page:{page+1}"})
            if nav:
                buttons.append(nav)
            return CommandResult(
                text="\n".join(lines),
                reply_markup=buttons,
                edit_message_id=edit_message_id or kw.get("message_id"),
            )
        except Exception as e:
            return CommandResult(text=f"搜索媒体库出错: {e}")

    async def cb_lib_page(self, params, user_id, channel, **kw):
        page = int(params[0]) if params else 0
        conv = self.get_conversation(user_id)
        if conv and conv.data.get("keyword"):
            return await self._refresh_search_library(
                conv.data["keyword"], user_id, page,
                edit_message_id=kw.get("message_id"), **kw,
            )
        return await self._build_library_page(
            user_id, page, edit_message_id=kw.get("message_id"), **kw,
        )

    async def cb_refresh_anime(self, params, user_id, channel, **kw):
        """选择作品 → 显示数据源列表"""
        anime_id = int(params[0]) if params else 0
        try:
            from src.db import crud
            async with self._session_factory() as session:
                sources = await crud.get_anime_sources(session, anime_id)
                details = await crud.get_anime_full_details(session, anime_id)
            title = details.get("title", "未知") if details else "未知"
            if not sources:
                return CommandResult(
                    text=f"📂 {title}\n暂无数据源。",
                    edit_message_id=kw.get("message_id"),
                )
            lines = [f"📂 {title}\n选择要刷新的数据源：\n"]
            buttons = []
            for s in sources:
                sid = s.get("sourceId")
                provider = s.get("providerName", "未知")
                ep_count = s.get("episodeCount", 0)
                lines.append(f"• [{provider}] {ep_count}集")
                buttons.append([{
                    "text": f"🔄 [{provider}] {ep_count}集",
                    "callback_data": f"refresh_source:{anime_id}:{sid}",
                }])
            return CommandResult(
                text="\n".join(lines),
                reply_markup=buttons,
                edit_message_id=kw.get("message_id"),
            )
        except Exception as e:
            return CommandResult(text=f"获取数据源失败: {e}")

    async def cb_refresh_source(self, params, user_id, channel, **kw):
        """选择数据源 → 显示分集列表，提供全部刷新/输入集数"""
        anime_id = int(params[0]) if len(params) > 0 else 0
        source_id = int(params[1]) if len(params) > 1 else 0
        try:
            from src.db import crud
            async with self._session_factory() as session:
                ep_result = await crud.get_episodes_for_source(session, source_id)
                source_info = await crud.get_anime_source_info(session, source_id)
            episodes = ep_result.get("episodes", [])
            total = ep_result.get("total", 0)
            provider = source_info.get("providerName", "未知") if source_info else "未知"
            title = source_info.get("title", "未知") if source_info else "未知"

            self.set_conversation(user_id, "refresh_episode_select", {
                "anime_id": anime_id, "source_id": source_id,
                "provider": provider, "title": title,
            }, chat_id=kw.get("chat_id"))

            lines = [f"📂 {title} [{provider}]\n共 {total} 集\n"]
            # 显示前几集预览
            for ep in episodes[:8]:
                idx = ep.get("episodeIndex", "?")
                ep_title = ep.get("title", "")
                count = ep.get("commentCount", 0)
                lines.append(f"  第{idx}集 {ep_title} ({count}条弹幕)")
            if total > 8:
                lines.append(f"  ... 还有 {total - 8} 集")

            buttons = [
                [{"text": "🔄 全部刷新", "callback_data": f"refresh_do:{source_id}:all"}],
                [{"text": "📝 输入集数范围", "callback_data": f"refresh_do:{source_id}:input"}],
            ]
            return CommandResult(
                text="\n".join(lines),
                reply_markup=buttons,
                edit_message_id=kw.get("message_id"),
            )
        except Exception as e:
            return CommandResult(text=f"获取分集列表失败: {e}")

    async def cb_refresh_ep_page(self, params, user_id, channel, **kw):
        """分集列表分页（预留）"""
        return CommandResult(text="", answer_callback_text="分集分页暂未实现")

    async def cb_refresh_do(self, params, user_id, channel, **kw):
        """执行刷新"""
        source_id = int(params[0]) if len(params) > 0 else 0
        mode = params[1] if len(params) > 1 else "all"

        if mode == "input":
            # 进入集数输入状态
            conv = self.get_conversation(user_id)
            data = conv.data if conv else {}
            data["source_id"] = source_id
            self.set_conversation(user_id, "refresh_episode_range", data,
                                  chat_id=kw.get("chat_id"))
            return CommandResult(
                text="请输入要刷新的集数范围：\n"
                     "格式: 1,3,5,7-13 或 all（全部）",
                edit_message_id=kw.get("message_id"),
            )

        # mode == "all" → 提交全部刷新任务
        return await self._do_refresh_source(source_id, None, kw.get("message_id"))

    async def _do_refresh_source(self, source_id: int, episode_range: str = None,
                                  edit_message_id: int = None) -> CommandResult:
        """执行数据源刷新 — 全量用 full_refresh_task，指定集数用 refresh_bulk_episodes_task"""
        if not self.task_manager:
            return CommandResult(success=False, text="任务管理器未就绪。")
        try:
            from src.db import crud
            async with self._session_factory() as session:
                source_info = await crud.get_anime_source_info(session, source_id)
            if not source_info:
                return CommandResult(text="数据源未找到。")
            provider = source_info.get("providerName", "未知")
            title = source_info.get("title", "未知")

            if episode_range:
                # 指定集数 → 解析范围，查找对应 episodeId
                from src.tasks import parse_episode_ranges, refresh_bulk_episodes_task
                indices = parse_episode_ranges(episode_range)
                async with self._session_factory() as session:
                    ep_result = await crud.get_episodes_for_source(session, source_id)
                episodes = ep_result.get("episodes", [])
                ep_ids = [e["episodeId"] for e in episodes
                          if e.get("episodeIndex") in indices]
                if not ep_ids:
                    return CommandResult(text=f"未找到匹配的集数: {episode_range}")
                ep_desc = episode_range
                task_title = f"TG刷新: {title} [{provider}] (E{ep_desc})"
                task_coro = lambda session, cb: refresh_bulk_episodes_task(
                    ep_ids, session, self.scraper_manager,
                    self.rate_limiter, cb, self.config_manager,
                )
            else:
                # 全量刷新
                from src.tasks import full_refresh_task
                ep_desc = "全部"
                task_title = f"TG刷新: {title} [{provider}] (全部)"
                task_coro = lambda session, cb: full_refresh_task(
                    source_id, session, self.scraper_manager,
                    self.task_manager, self.rate_limiter, cb,
                    self.metadata_manager, self.config_manager,
                )

            task_id, _ = await self.task_manager.submit_task(
                coro_factory=task_coro, title=task_title,
            )
            return CommandResult(
                text=f"✅ 刷新任务已提交\n{title} [{provider}]\n集数: {ep_desc}\n任务ID: {task_id}",
                reply_markup=[[{"text": "📋 查看任务状态", "callback_data": f"task_detail:{task_id}"}]],
                edit_message_id=edit_message_id,
            )
        except Exception as e:
            logger.error(f"提交刷新任务失败: {e}", exc_info=True)
            return CommandResult(text=f"❌ 提交刷新任务失败: {e}")

    async def _text_refresh_keyword(self, text: str, user_id: str, channel, **kw):
        """刷新命令中的关键词搜索"""
        return await self._refresh_search_library(text.strip(), user_id, 0, **kw)

    async def _text_refresh_episode_range(self, text: str, user_id: str, channel, **kw):
        """集数范围输入 → 执行刷新"""
        conv = self.get_conversation(user_id)
        if not conv:
            return CommandResult(text="操作已过期。")
        source_id = conv.data.get("source_id", 0)
        self.clear_conversation(user_id)
        episode_range = None if text.strip().lower() == "all" else text.strip()
        return await self._do_refresh_source(source_id, episode_range)

    # ── /url ──

    async def cmd_url(self, args: str, user_id: str, channel, **kw) -> CommandResult:
        """从URL导入弹幕"""
        if args and args.strip():
            # 直接传入了URL
            return await self._process_url_input(args.strip(), user_id, **kw)
        self.set_conversation(user_id, "url_input", {},
                              chat_id=kw.get("chat_id"))
        return CommandResult(text="🔗 请输入视频页面URL：")

    async def _text_url_input(self, text: str, user_id: str, channel, **kw):
        """URL输入 → 解析并导入"""
        self.clear_conversation(user_id)
        return await self._process_url_input(text.strip(), user_id, **kw)

    async def _process_url_input(self, url: str, user_id: str, **kw) -> CommandResult:
        """解析URL并尝试匹配弹幕源"""
        if not self.scraper_manager:
            return CommandResult(success=False, text="搜索服务未就绪。")
        try:
            scraper = self.scraper_manager.get_scraper_by_domain(url)
            if not scraper:
                return CommandResult(text=f"❌ 无法识别该URL的平台。\n支持的平台取决于已启用的弹幕源。")
            # 调用 scraper 的 get_info_from_url
            info = await scraper.get_info_from_url(url)
            if not info:
                return CommandResult(text="❌ 无法从该URL解析出媒体信息。")
            platform = getattr(scraper, 'provider_name', '未知')
            title = info.get("title", "未知") if isinstance(info, dict) else getattr(info, "title", "未知")
            media_id = info.get("mediaId", "") if isinstance(info, dict) else getattr(info, "mediaId", "")
            return CommandResult(
                text=f"🔗 URL解析成功\n平台: {platform}\n标题: {title}\n\n正在提交导入任务...",
            )
        except Exception as e:
            logger.error(f"URL解析失败: {e}", exc_info=True)
            return CommandResult(text=f"❌ URL解析失败: {e}")

    # ── 搜索分集范围文本处理 ──

    async def _text_search_episode_range(self, text: str, user_id: str, channel, **kw):
        """搜索结果的集数范围输入"""
        conv = self.get_conversation(user_id)
        if not conv:
            return CommandResult(text="操作已过期。")
        search_term = conv.data.get("search_term", "")
        self.clear_conversation(user_id)
        return await self._submit_auto_import(
            search_type="keyword", search_term=search_term,
            episode=text.strip(),
        )

    # ── /cache ──

    async def cmd_cache(self, args: str, user_id: str, channel, **kw) -> CommandResult:
        """清除系统缓存（内存 + 数据库）"""
        cleared = []
        errors = []

        # 1. 清除内存配置缓存
        if self.config_manager:
            try:
                self.config_manager.clear_cache()
                cleared.append("✓ 内存配置缓存")
            except Exception as e:
                errors.append(f"✗ 内存配置缓存: {e}")

        # 2. 清除数据库缓存
        try:
            from src.db import crud
            async with self._session_factory() as session:
                count = await crud.clear_all_cache(session)
                cleared.append(f"✓ 数据库缓存 ({count} 条)")
        except Exception as e:
            errors.append(f"✗ 数据库缓存: {e}")

        # 3. 清除 AI 缓存（如果可用）
        if self.ai_matcher_manager:
            try:
                matcher = await self.ai_matcher_manager.get_matcher()
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

    # ── 任务详情回调 ──

    async def cb_task_detail(self, params, user_id, channel, **kw):
        """查看单个任务的实时状态，支持追踪子任务链"""
        task_id = params[0] if params else ""
        if not task_id:
            return CommandResult(text="", answer_callback_text="缺少任务ID")
        try:
            from src.db import crud
            async with self._session_factory() as session:
                detail = await crud.get_task_details_from_history(session, task_id)
            if not detail:
                return CommandResult(text="", answer_callback_text="任务不存在或已被清理")

            status = detail.get("status", "未知")
            progress = detail.get("progress", 0)
            title = detail.get("title", "")
            desc = detail.get("description", "")
            status_icon = {
                "排队中": "⏳", "运行中": "▶️", "已完成": "✅",
                "失败": "❌", "已暂停": "⏸️",
            }.get(status, "❓")

            lines = [
                f"{status_icon} 调度任务: {title}",
                f"状态: {status} | 进度: {progress}%",
            ]
            if desc:
                short_desc = desc[:200] + "..." if len(desc) > 200 else desc
                lines.append(f"详情: {short_desc}")

            buttons = [[{"text": "🔄 刷新状态", "callback_data": f"task_detail:{task_id}"}]]

            # 追踪子任务链：从描述中解析 "执行任务ID: xxx"
            exec_task_id = None
            if desc:
                m = re.search(r'执行任务ID:\s*([a-f0-9\-]+)', desc)
                if m:
                    exec_task_id = m.group(1)

            if exec_task_id:
                async with self._session_factory() as session:
                    exec_detail = await crud.get_task_details_from_history(session, exec_task_id)
                if exec_detail:
                    exec_status = exec_detail.get("status", "未知")
                    exec_progress = exec_detail.get("progress", 0)
                    exec_title = exec_detail.get("title", "")
                    exec_desc = exec_detail.get("description", "")
                    exec_icon = {
                        "排队中": "⏳", "运行中": "▶️", "已完成": "✅",
                        "失败": "❌", "已暂停": "⏸️",
                    }.get(exec_status, "❓")
                    lines.append("")
                    lines.append(f"{exec_icon} 执行任务: {exec_title}")
                    lines.append(f"状态: {exec_status} | 进度: {exec_progress}%")
                    if exec_desc:
                        short_exec = exec_desc[:200] + "..." if len(exec_desc) > 200 else exec_desc
                        lines.append(f"详情: {short_exec}")
                    # 添加查看执行任务详情的按钮
                    buttons.append([{"text": "📦 查看执行任务", "callback_data": f"task_detail:{exec_task_id}"}])

            return CommandResult(
                text="\n".join(lines),
                reply_markup=buttons,
                edit_message_id=kw.get("message_id"),
            )
        except Exception as e:
            logger.error(f"查询任务详情失败: {e}", exc_info=True)
            return CommandResult(text="", answer_callback_text=f"查询失败: {e}")