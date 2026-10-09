"""通知渠道输入适配器：协议分发与上层菜单业务组装。"""

import logging
from typing import Any, Callable, Dict, List, Optional

from src.notification.base import (
    CommandResult, ConversationState, ChannelCapabilities,
)
from src.services.notification_service import NotificationService
from src.notification.menus import (
    ImportBaseMixin,
    HelpMenuMixin,
    SearchMenuMixin,
    AutoMenuMixin,
    UrlMenuMixin,
    LibraryMenuMixin,
    TokensMenuMixin,
    TasksMenuMixin,
    TaskManagerMenuMixin,
    CacheMenuMixin,
    StatusMenuMixin,
    LlmChatMixin,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 5


class NotificationInputAdapter(
    ImportBaseMixin,
    HelpMenuMixin,
    SearchMenuMixin,
    AutoMenuMixin,
    UrlMenuMixin,
    LibraryMenuMixin,
    TokensMenuMixin,
    TasksMenuMixin,
    TaskManagerMenuMixin,
    CacheMenuMixin,
    StatusMenuMixin,
    LlmChatMixin,
):
    """通知输入命令、回调和菜单业务的上层适配器。"""

    def __init__(self, state: NotificationService, session_factory: Callable) -> None:
        self.state = state
        self._session_factory = session_factory
        self.notification_manager = None
        self._tm_refresh_tasks = {}
        self.app = None
        self.scraper_manager = None
        self.metadata_manager = None
        self.task_manager = None
        self.scheduler_manager = None
        self.config_service = None
        self.rate_limiter = None
        self.title_recognition_manager = None
        self.ai_service = None

    def set_dependencies(self, **kwargs: Any) -> None:
        """由组合根为菜单业务注入协作者。"""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)

    # ═══════════════════════════════════════════
    # 菜单命令定义
    # ═══════════════════════════════════════════

    # 命令定义：{"/command": "描述"}
    MENU_COMMANDS = {
        "/help": "显示帮助",
        "/status": "系统状态",
        "/sh": "搜索弹幕源",
        "/auto": "自动导入（多平台）",
        "/url": "从URL导入弹幕",
        "/refresh": "弹幕库管理",
        "/tokens": "Token管理",
        "/tasks": "定时任务管理",
        "/cache": "清除缓存",
        "/cancel": "取消当前操作",
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
        """读取对话状态。"""
        return self.state.get_conversation(user_id)

    def set_conversation(self, user_id: str, state: str, data: dict = None, message_id: int = None, chat_id: int = None) -> None:
        """存储对话状态。"""
        self.state.set_conversation(user_id, state, data, message_id, chat_id)

    def clear_conversation(self, user_id: str) -> None:
        """清除对话状态。"""
        self.state.clear_conversation(user_id)

    def update_conversation_message_id(self, user_id: str, message_id: int) -> None:
        """回写渠道消息编号。"""
        self.state.update_conversation_message_id(user_id, message_id)

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
            "status": self.cmd_status,
            "sh": self.cmd_search,
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
            # status
            "status_refresh": self.cb_status_refresh,
            # tasks
            "tasks_home": self.cb_tasks_home,
            "tasks_sched": self.cb_tasks_sched,
            "tasks_refresh": self.cb_tasks_refresh,
            # 任务管理器（后台任务）
            "tm_list": self.cb_tm_list,
            "tm_auto": self.cb_tm_auto,
            "tm_pause": self.cb_tm_pause,
            "tm_resume": self.cb_tm_resume,
            "tm_abort": self.cb_tm_abort,
            "task_toggle": self.cb_task_toggle,
            "task_run": self.cb_task_run,
            "task_del": self.cb_task_del,
            "task_del_ok": self.cb_task_del_ok,
            "task_add": self.cb_task_add,
            "task_add_type": self.cb_task_add_type,
            "task_cron": self.cb_task_cron,
            "task_cron_custom": self.cb_task_cron_custom,
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
            "search_select": self.cb_search_select,
            "search_back": self.cb_search_back,
            "search_import": self.cb_search_import,
            "search_episodes": self.cb_search_episodes,
            "search_season_input": self.cb_search_season_input,
            "search_ep_input": self.cb_search_ep_input,
            "ep_page": self.cb_episode_page,
            # search notify 快捷按钮（后备任务完成通知）
            "search_notify": self.cb_search_notify,
            "search_notify_season": self.cb_search_notify_season,
            "search_notify_episode": self.cb_search_notify_episode,
            # search 无参数快捷按钮
            "search_input": self.cb_search_input,
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
            # refresh / library
            "refresh_anime": self.cb_refresh_anime,
            "refresh_source": self.cb_refresh_source,
            "refresh_ep_page": self.cb_refresh_ep_page,
            "refresh_do": self.cb_refresh_do,
            "lib_page": self.cb_lib_page,
            # refresh episode select (inline keyboard)
            "ref_ep_tog": self.cb_ref_ep_toggle,
            "ref_ep_do": self.cb_ref_ep_do,
            "ref_ep_batch": self.cb_ref_ep_batch,
            "ref_ep_pg": self.cb_ref_ep_page,
            "ref_ep_all": self.cb_ref_ep_all,
            "ref_ep_none": self.cb_ref_ep_none,
            "ref_ep_ok": self.cb_ref_ep_ok,
            # delete source
            "delete_source_do": self.cb_delete_source_do,
            "delete_source_confirm": self.cb_delete_source_confirm,
            # delete episodes
            "delete_ep_all": self.cb_delete_ep_all,
            "delete_ep_range": self.cb_delete_ep_range,
            # task detail
            "task_detail": self.cb_task_detail,
            # help inline buttons
            "help_cmd": self.cb_help_cmd,
            # 渠道 Agent 选项
            "llm_choice": self.cb_llm_choice,
            "llm_confirm": self.cb_llm_confirm,
            "llm_task": self.cb_llm_task,
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
        logger.info(f"[文本输入] user={user_id} text={text[:50]}")
        conv = self.get_conversation(user_id)
        if not conv:
            # 无活跃命令流程 → 交给御坂 LLM 对话兜底（渠道端纯问答+只读工具，非流式）。
            # 渠道若支持伪流式（Telegram），会在渠道层用 stream_callback 覆盖此路径。
            logger.info(f"[文本输入] user={user_id} 无活跃对话，转御坂 LLM")
            return await self.handle_llm_chat(text, user_id)
        state = conv.state
        logger.info(f"[文本输入] user={user_id} state={state}")
        text_handler_map = {
            "token_name_input": self._text_token_name,
            "auto_keyword_input": self._text_auto_keyword,
            "auto_id_input": self._text_auto_id,
            "url_input": self._text_url_input,
            "refresh_keyword_input": self._text_refresh_keyword,
            "search_episode_range": self._text_search_episode_range,
            "refresh_episode_range": self._text_refresh_episode_range,
            "delete_episode_range": self._text_delete_episode_range,
            "edit_title_input": self._text_edit_title,
            # 后备任务通知快捷按钮的文本输入
            "search_notify_season_input": self._text_search_notify_season,
            "search_notify_episode_input": self._text_search_notify_episode,
            # /search 无参数快捷按钮的文本输入
            "search_keyword_input": self._text_search_keyword_input,
            "search_keyword_season_input": self._text_search_keyword_season_input,
            "search_keyword_episode_input": self._text_search_keyword_episode_input,
            # 搜索结果操作面板的文本输入
            "search_season_input": self._text_search_season_input,
            "search_ep_input": self._text_search_ep_input,
            # 定时任务添加
            "task_cron_input": self._text_task_cron_input,
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

