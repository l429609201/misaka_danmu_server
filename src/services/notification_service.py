"""
NotificationService — 通知系统的通用内部 API

核心框架：命令路由 / 回调路由 / 对话状态管理 / 事件分发。
所有菜单业务逻辑已拆分到 src/notification/menus/ 目录下，
通过 Mixin 多继承引入。
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from src.notification.base import CommandResult, ConversationState, ChannelCapabilities
from src.notification.menus import (
    ImportBaseMixin,
    MessagesMixin,
    HelpMenuMixin,
    SearchMenuMixin,
    AutoMenuMixin,
    UrlMenuMixin,
    LibraryMenuMixin,
    TokensMenuMixin,
    TasksMenuMixin,
    CacheMenuMixin,
    StatusMenuMixin,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 5


class NotificationService(
    ImportBaseMixin,
    MessagesMixin,
    HelpMenuMixin,
    SearchMenuMixin,
    AutoMenuMixin,
    UrlMenuMixin,
    LibraryMenuMixin,
    TokensMenuMixin,
    TasksMenuMixin,
    CacheMenuMixin,
    StatusMenuMixin,
):
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
        # 任务进度消息跟踪: task_id -> {channel_id: message_id}
        # 用于 TG edit_message 功能（发新消息后记录 message_id，后续进度更新时 edit）
        # 同时覆盖 fallback 和普通下载任务
        self._task_progress_tg_msg: Dict[str, Dict[str, int]] = {}

    def cleanup_task_progress(self, task_id: str):
        """清理指定任务的进度消息缓存（任务结束但无需发通知时调用）"""
        self._task_progress_tg_msg.pop(task_id, None)

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
            "tasks_refresh": self.cb_tasks_refresh,
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
        logger.info(f"[文本输入] user={user_id} text={text[:50]} conversations={list(self._conversations.keys())}")
        conv = self.get_conversation(user_id)
        if not conv:
            logger.info(f"[文本输入] user={user_id} 无活跃对话，忽略")
            return None  # 没有活跃对话，忽略
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

    # ═══════════════════════════════════════════
    # 出站：通知发送（系统 → 用户）
    # ═══════════════════════════════════════════

    # 有进度消息记录时，完成通知会 edit 已有消息（适用于所有任务类型）
    _COMPLETE_EVENT_TYPES = {
        "download_fallback_success", "download_fallback_failed",
        "fallback_search_success", "fallback_search_failed",
        "predownload_success", "predownload_failed",
        "match_fallback_success", "match_fallback_failed",
        "import_success", "import_failed",
        "auto_import_success", "auto_import_failed",
        "webhook_import_success", "webhook_import_failed",
        "refresh_success", "refresh_failed",
        "incremental_refresh_success", "incremental_refresh_failed",
        "scheduled_task_complete", "scheduled_task_failed",
    }

    # fallback 完成事件 → 订阅 check_key 的映射（dict）
    _FALLBACK_COMPLETE_EVENTS = {
        "download_fallback_success": "download_fallback_complete",
        "download_fallback_failed": "download_fallback_complete",
        "fallback_search_success": "fallback_search_complete",
        "fallback_search_failed": "fallback_search_complete",
        "predownload_success": "predownload_complete",
        "predownload_failed": "predownload_complete",
        "match_fallback_success": "match_fallback_complete",
        "match_fallback_failed": "match_fallback_complete",
    }

    async def emit_event(self, event_type: str, data: Dict[str, Any]):
        """向所有订阅了该事件的渠道发送通知

        C 方案重构后：优先走 NotificationManager.notify_event 新路径，
        进度消息编辑（task_progress_tg_msg）逻辑保留在此处处理。
        """
        if not self.notification_manager:
            return

        is_any_complete = event_type in self._COMPLETE_EVENT_TYPES
        task_id: str = data.get("task_id", "")

        # 完成事件有进度消息缓存时，需要特殊处理 edit_message
        if is_any_complete and task_id and self._task_progress_tg_msg.get(task_id):
            # 带进度编辑的场景走旧路径（仅影响有缓存进度消息的渠道）
            await self._emit_event_with_progress_edit(event_type, data, task_id)
        else:
            # 常规场景走新路径
            await self.notification_manager.notify_event(event_type, data)

    async def _emit_event_with_progress_edit(self, event_type: str, data: dict, task_id: str):
        """带进度消息编辑的事件发送 — 处理 TG edit_message 场景

        对有缓存进度消息的渠道：edit 已有消息
        对其他渠道：通过新路径正常发送
        """
        channels = self.notification_manager.get_all_channels()
        cached_channels = set(self._task_progress_tg_msg.get(task_id, {}).keys())

        for ch_id, channel_instance in channels.items():
            try:
                events_cfg = channel_instance.config.get("__events_config", {})
                check_key = self._FALLBACK_COMPLETE_EVENTS.get(event_type, event_type)
                subscribed = events_cfg.get(check_key)

                has_cached_progress = ch_id in cached_channels

                if not subscribed and not has_cached_progress:
                    continue

                fmt_result = self._format_event_message(event_type, data)
                title, text = fmt_result[0], fmt_result[1]
                reply_markup = fmt_result[2] if len(fmt_result) == 3 else None
                image_url: str = data.get("image_url", "") or ""

                if has_cached_progress:
                    # 有进度消息缓存：edit 已有消息
                    edit_mid = self._task_progress_tg_msg.get(task_id, {}).get(ch_id)
                    msg_id_out: List[int] = []
                    await channel_instance.send_message(
                        title=title, text=text, image=image_url,
                        edit_message_id=edit_mid, _msg_id_out=msg_id_out,
                        reply_markup=reply_markup,
                    )
                    # 清理该渠道的缓存
                    if task_id in self._task_progress_tg_msg:
                        self._task_progress_tg_msg[task_id].pop(ch_id, None)
                        if not self._task_progress_tg_msg[task_id]:
                            self._task_progress_tg_msg.pop(task_id, None)
                else:
                    # 无进度缓存：正常发送
                    await channel_instance.send_message(
                        title=title, text=text, image=image_url,
                        reply_markup=reply_markup,
                    )
            except Exception as e:
                logger.error(f"渠道 {ch_id} 发送事件 {event_type} 失败: {e}")

    async def emit_task_progress(self, task_id: str, task_title: str, progress: int,
                                  description: str, check_event_key: str = "task_progress"):
        """向 TG 渠道发送任务进度通知（edit 已有消息，其他渠道不推送进度）

        Args:
            check_event_key: 检查用户是否订阅的事件 key。
                - fallback 任务传 "download_fallback_complete"
                - 普通下载任务传 "task_progress"
        """
        if not self.notification_manager:
            return
        channels = self.notification_manager.get_all_channels()
        for ch_id, channel_instance in channels.items():
            try:
                events_cfg = channel_instance.config.get("__events_config", {})
                if not events_cfg.get(check_event_key, False):
                    continue
                # 仅 Telegram 支持 edit_message，其他渠道跳过进度推送（完成时才收通知）
                if getattr(channel_instance, "channel_type", "") != "telegram":
                    continue
                title, text = self._format_task_progress_message(task_title, progress, description)
                edit_mid = self._task_progress_tg_msg.get(task_id, {}).get(ch_id)
                msg_id_out: List[int] = []
                logger.debug(f"[进度通知] task_id={task_id[:8]} ch={ch_id} edit_mid={edit_mid} progress={progress}%")
                await channel_instance.send_message(
                    title=title, text=text,
                    edit_message_id=edit_mid, _msg_id_out=msg_id_out
                )
                # 记录新发出的 message_id（首次 send 或 edit 失败降级后均更新缓存）
                if msg_id_out:
                    logger.debug(f"[进度通知] task_id={task_id[:8]} 新消息 msg_id={msg_id_out[0]} (edit_mid was {edit_mid})")
                    self._task_progress_tg_msg.setdefault(task_id, {})[ch_id] = msg_id_out[0]
            except Exception as e:
                logger.debug(f"渠道 {ch_id} 发送任务进度通知失败: {e}")