"""
任务管理器菜单 Mixin — 后台任务列表（带进度条 + 自动刷新）

与 tasks_menu.py（定时任务）区分：
- 本文件负责「任务管理器」，即 TaskHistory 里正在执行/历史的后台任务
- tasks_menu.py 负责「定时任务」，即 scheduler_manager 管理的 cron 任务
"""
import asyncio
import hashlib
import logging
from typing import Any, Dict, List, Optional

from src.db import crud
from src.notification.base import CommandResult

logger = logging.getLogger(__name__)

# 自动刷新参数
TM_REFRESH_INTERVAL = 3.0   # 刷新间隔（秒）
TM_REFRESH_MAX_ROUNDS = 100  # 最多刷新轮数（3s x 100 = 5 分钟）
TM_PAGE_SIZE = 5             # 每页任务数

# 进行中的状态集合（与 crud.get_tasks_from_history 的 in_progress 过滤保持一致）
TM_RUNNING_STATES = ("排队中", "运行中", "已暂停")

_STATUS_ICONS = {
    "排队中": "⏳", "运行中": "▶️", "已完成": "✅", "成功": "✅",
    "失败": "❌", "已暂停": "⏸️", "已中止": "⏹️",
}


class TaskManagerMenuMixin:
    """处理任务管理器列表、进度条渲染、自动刷新协程"""

    # ── 工具方法 ──

    @staticmethod
    def _tm_progress_bar(progress: Any) -> str:
        """渲染 10 格进度条，样式与 messages/system.py、menus/search.py 保持一致"""
        try:
            pct = int(progress or 0)
        except (TypeError, ValueError):
            pct = 0
        pct = max(0, min(100, pct))
        filled = pct // 10
        return "█" * filled + "░" * (10 - filled)

    @staticmethod
    def _tm_status_icon(status: str) -> str:
        return _STATUS_ICONS.get(status, "❓")

    async def _tm_fetch(self, status_filter: str, page: int) -> Dict[str, Any]:
        """查询后台任务列表"""
        async with self._session_factory() as session:
            return await crud.get_tasks_from_history(
                session, None, status_filter, "all", page, TM_PAGE_SIZE
            )

    async def _tm_count_running(self) -> int:
        """统计进行中的后台任务数（用于一级菜单概览）"""
        try:
            data = await self._tm_fetch("in_progress", 1)
            return int(data.get("total") or 0)
        except Exception as e:
            logger.warning(f"统计进行中任务失败: {e}")
            return 0

    # ── 列表渲染 ──

    async def _build_task_manager_result(
        self,
        status_filter: str = "in_progress",
        page: int = 1,
        auto: bool = True,
        edit_message_id: Optional[int] = None,
        footer_note: str = "",
    ) -> CommandResult:
        """构建任务管理器列表（带进度条）"""
        try:
            data = await self._tm_fetch(status_filter, page)
        except Exception as e:
            logger.error(f"获取后台任务列表失败: {e}", exc_info=True)
            return CommandResult(success=False, text=f"获取任务列表出错: {e}",
                                 edit_message_id=edit_message_id)

        items: List[Dict[str, Any]] = data.get("list") or []
        total = int(data.get("total") or 0)
        total_pages = max(1, (total + TM_PAGE_SIZE - 1) // TM_PAGE_SIZE)
        page = max(1, min(page, total_pages))

        scope_label = "进行中" if status_filter == "in_progress" else "全部"
        lines = [f"⚙️ *任务管理器 · {scope_label}*", ""]

        if not items:
            lines.append("当前没有任务。" if status_filter == "in_progress"
                         else "暂无任务记录。")
        else:
            for t in items:
                status = t.get("status", "未知")
                icon = self._tm_status_icon(status)
                title = t.get("title", "") or "(无标题)"
                progress = t.get("progress", 0) or 0
                bar = self._tm_progress_bar(progress)
                lines.append(f"{icon} {title}")
                lines.append(f"`[{bar}]` {progress}% · {status}")
                desc = t.get("description", "") or ""
                if desc:
                    short = desc[:80] + "..." if len(desc) > 80 else desc
                    lines.append(f"📋 {short}")
                lines.append("")

        footer = footer_note or f"第 {page}/{total_pages} 页 · 共 {total} 个"
        lines.append(footer)

        buttons = self._build_tm_buttons(items, status_filter, page, total_pages, auto)
        return CommandResult(
            text="\n".join(lines),
            reply_markup=buttons,
            parse_mode="Markdown",
            edit_message_id=edit_message_id,
        )

    def _build_tm_buttons(self, items, status_filter, page, total_pages, auto) -> list:
        """构建任务管理器按钮：任务操作 + 状态切换 + 自动刷新 + 分页 + 返回"""
        buttons = []
        for t in items:
            task_id = t.get("taskId", "")
            if not task_id:
                continue
            status = t.get("status", "")
            title = t.get("title", "") or "(无标题)"
            label = title[:20] + "…" if len(title) > 20 else title
            buttons.append([
                {"text": f"{self._tm_status_icon(status)} {label}",
                 "callback_data": f"task_detail:{task_id}"}
            ])
            # 状态相关操作：运行中可暂停，已暂停可恢复，未结束可中止
            ops = []
            if status == "运行中":
                ops.append({"text": "⏸️ 暂停", "callback_data": f"tm_pause:{task_id}"})
            elif status == "已暂停":
                ops.append({"text": "▶️ 恢复", "callback_data": f"tm_resume:{task_id}"})
            if status in TM_RUNNING_STATES:
                ops.append({"text": "⏹️ 中止", "callback_data": f"tm_abort:{task_id}"})
            if ops:
                buttons.append(ops)

        # 状态切换 + 自动刷新开关
        other = "all" if status_filter == "in_progress" else "in_progress"
        other_label = "📜 全部" if other == "all" else "🔄 进行中"
        buttons.append([
            {"text": other_label, "callback_data": f"tm_list:{other}:1:{1 if auto else 0}"},
            {"text": f"⏱ 自动刷新: {'开' if auto else '关'}",
             "callback_data": f"tm_auto:{status_filter}:{page}:{0 if auto else 1}"},
        ])
        # 手动刷新
        buttons.append([
            {"text": "🔃 手动刷新",
             "callback_data": f"tm_list:{status_filter}:{page}:{1 if auto else 0}"},
        ])
        # 分页
        if total_pages > 1:
            nav = []
            if page > 1:
                nav.append({"text": "⬅️ 上一页",
                            "callback_data": f"tm_list:{status_filter}:{page - 1}:{1 if auto else 0}"})
            if page < total_pages:
                nav.append({"text": "➡️ 下一页",
                            "callback_data": f"tm_list:{status_filter}:{page + 1}:{1 if auto else 0}"})
            if nav:
                buttons.append(nav)
        # 返回一级菜单
        buttons.append([{"text": "🔙 返回", "callback_data": "tasks_home"}])
        return buttons

    # ── 自动刷新协程 ──

    def _tm_stop_auto_refresh(self, user_id: str):
        """停止指定用户的自动刷新协程（切页/返回/重复进入时调用）"""
        tasks = getattr(self, "_tm_refresh_tasks", None)
        if not tasks:
            return
        task = tasks.pop(user_id, None)
        if task and not task.done():
            task.cancel()

    def _tm_start_auto_refresh(self, user_id: str, channel, chat_id,
                               message_id: int, status_filter: str, page: int):
        """启动自动刷新协程：定期 edit 同一条消息，让进度条自增长

        why: 任务管理器列表是数据库快照，没有 progress_callback 可挂，
        只能靠轮询定期重绘。为避免 Telegram 限流，做了三重保护：
        内容无变化跳过 edit、无进行中任务即停止、最多刷新 TM_REFRESH_MAX_ROUNDS 轮。
        """
        if not hasattr(self, "_tm_refresh_tasks"):
            self._tm_refresh_tasks = {}
        # 同一用户只保留一个刷新协程
        self._tm_stop_auto_refresh(user_id)
        if not message_id or chat_id is None:
            return

        async def _loop():
            last_hash = ""
            try:
                for _ in range(TM_REFRESH_MAX_ROUNDS):
                    await asyncio.sleep(TM_REFRESH_INTERVAL)
                    data = await self._tm_fetch(status_filter, page)
                    items = data.get("list") or []
                    # 判断是否仍有进行中任务
                    has_running = any(
                        (t.get("status") in TM_RUNNING_STATES) for t in items
                    )
                    result = await self._build_task_manager_result(
                        status_filter=status_filter, page=page, auto=True,
                        edit_message_id=message_id,
                        footer_note="" if has_running else "✅ 已全部完成",
                    )
                    # 内容无变化就跳过 edit，避免 TG 返回 message is not modified
                    digest = hashlib.md5(result.text.encode("utf-8")).hexdigest()
                    if digest != last_hash:
                        last_hash = digest
                        await channel.send_message(
                            title="",
                            text=result.text,
                            chat_id=chat_id,
                            edit_message_id=message_id,
                            reply_markup=result.reply_markup,
                        )
                    if not has_running:
                        break
                else:
                    # 达到轮数上限，提示用户手动刷新
                    result = await self._build_task_manager_result(
                        status_filter=status_filter, page=page, auto=False,
                        edit_message_id=message_id,
                        footer_note="⏸ 自动刷新已停止，请点击手动刷新",
                    )
                    await channel.send_message(
                        title="", text=result.text, chat_id=chat_id,
                        edit_message_id=message_id, reply_markup=result.reply_markup,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"任务管理器自动刷新失败: {e}")
            finally:
                tasks = getattr(self, "_tm_refresh_tasks", {})
                if tasks.get(user_id) is asyncio.current_task():
                    tasks.pop(user_id, None)

        self._tm_refresh_tasks[user_id] = asyncio.create_task(_loop())

    # ── 回调处理 ──

    async def _tm_show(self, params, user_id, channel, force_auto=None, **kw):
        """展示任务管理器列表并按需启动自动刷新（tm_list / tm_auto 共用）"""
        status_filter = params[0] if params else "in_progress"
        if status_filter not in ("in_progress", "all"):
            status_filter = "in_progress"
        try:
            page = int(params[1]) if len(params) > 1 else 1
        except (TypeError, ValueError):
            page = 1
        if force_auto is not None:
            auto = force_auto
        else:
            auto = (params[2] == "1") if len(params) > 2 else True

        message_id = kw.get("message_id")
        result = await self._build_task_manager_result(
            status_filter=status_filter, page=page, auto=auto,
            edit_message_id=message_id,
        )
        # 切页/切状态时先停掉旧协程，避免多个协程抢着 edit 同一条消息
        self._tm_stop_auto_refresh(user_id)
        if auto:
            data = await self._tm_fetch(status_filter, page)
            has_running = any(
                (t.get("status") in TM_RUNNING_STATES) for t in (data.get("list") or [])
            )
            # 仅当确实有进行中任务时才启动轮询
            if has_running:
                self._tm_start_auto_refresh(
                    user_id, channel, kw.get("chat_id"), message_id,
                    status_filter, page,
                )
        return result

    async def cb_tm_list(self, params, user_id, channel, **kw):
        """进入 / 翻页 / 切换状态过滤"""
        return await self._tm_show(params, user_id, channel, **kw)

    async def cb_tm_auto(self, params, user_id, channel, **kw):
        """切换自动刷新开关 — params: status:page:新状态(1开/0关)"""
        want_auto = (params[2] == "1") if len(params) > 2 else False
        result = await self._tm_show(params, user_id, channel,
                                    force_auto=want_auto, **kw)
        result.answer_callback_text = f"自动刷新已{'开启' if want_auto else '关闭'}"
        return result

    # ── 任务操作（暂停 / 恢复 / 中止）──

    async def _tm_after_action(self, user_id, channel, toast: str, **kw):
        """操作后重绘列表（保持在进行中视图第一页）"""
        result = await self._tm_show(["in_progress", "1", "1"], user_id, channel, **kw)
        result.answer_callback_text = toast
        return result

    async def cb_tm_pause(self, params, user_id, channel, **kw):
        """暂停正在运行的后台任务"""
        task_id = params[0] if params else ""
        if not task_id or not self.task_manager:
            return CommandResult(text="", answer_callback_text="任务服务未就绪")
        try:
            ok = await self.task_manager.pause_task(task_id)
            toast = "⏸️ 已暂停" if ok else "暂停失败（任务可能已结束）"
            return await self._tm_after_action(user_id, channel, toast, **kw)
        except Exception as e:
            logger.error(f"暂停任务失败: {e}", exc_info=True)
            return CommandResult(text="", answer_callback_text=f"暂停失败: {e}")

    async def cb_tm_resume(self, params, user_id, channel, **kw):
        """恢复已暂停的后台任务"""
        task_id = params[0] if params else ""
        if not task_id or not self.task_manager:
            return CommandResult(text="", answer_callback_text="任务服务未就绪")
        try:
            ok = await self.task_manager.resume_task(task_id)
            toast = "▶️ 已恢复" if ok else "恢复失败（任务可能已结束）"
            return await self._tm_after_action(user_id, channel, toast, **kw)
        except Exception as e:
            logger.error(f"恢复任务失败: {e}", exc_info=True)
            return CommandResult(text="", answer_callback_text=f"恢复失败: {e}")

    async def cb_tm_abort(self, params, user_id, channel, **kw):
        """中止后台任务（排队中走 cancel_pending，运行中/已暂停走 abort_current）"""
        task_id = params[0] if params else ""
        if not task_id or not self.task_manager:
            return CommandResult(text="", answer_callback_text="任务服务未就绪")
        try:
            async with self._session_factory() as session:
                detail = await crud.get_task_details_from_history(session, task_id)
            status = (detail or {}).get("status", "")
            if status == "排队中":
                ok = await self.task_manager.cancel_pending_task(task_id)
            else:
                ok = await self.task_manager.abort_current_task(task_id)
            toast = "⏹️ 已中止" if ok else "中止失败（任务可能已结束）"
            return await self._tm_after_action(user_id, channel, toast, **kw)
        except Exception as e:
            logger.error(f"中止任务失败: {e}", exc_info=True)
            return CommandResult(text="", answer_callback_text=f"中止失败: {e}")
