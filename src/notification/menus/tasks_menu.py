"""
/tasks 菜单 Mixin — 定时任务列表 + 任务详情
（文件名 tasks_menu.py 避免与 src/tasks 包冲突）
"""
import re
import logging
from src.db import crud
from src.notification.base import CommandResult

logger = logging.getLogger(__name__)


class TasksMenuMixin:
    """处理 /tasks 命令及任务详情回调的所有方法"""

    async def cmd_list_tasks(self, args: str, user_id: str, channel, **kw) -> CommandResult:
        return await self._build_tasks_result()

    def _get_job_name(self, job_type: str) -> str:
        """将 jobType 映射为可读的中文名"""
        if self.scheduler_manager:
            for job in self.scheduler_manager.get_available_jobs():
                if job.get("jobType") == job_type:
                    return job.get("name", job_type)
        return job_type

    async def _build_tasks_result(self, edit_message_id: int = None) -> CommandResult:
        if not self.scheduler_manager:
            return CommandResult(success=False, text="调度服务未就绪。")
        try:
            tasks = await self.scheduler_manager.get_all_tasks()
            if not tasks:
                return CommandResult(
                    text="📋 当前没有定时任务。",
                    reply_markup=[[{"text": "➕ 添加任务", "callback_data": "task_add:start"}]],
                    edit_message_id=edit_message_id,
                )
            lines = ["📋 定时任务列表:\n"]
            buttons = []
            for t in tasks:
                status = "✅" if t.get("isEnabled") else "⏸️"
                job_type = t.get("jobType", "")
                name = self._get_job_name(job_type)
                cron = t.get("cronExpression", "")
                task_id = t.get("taskId", "")
                lines.append(f"{status} {name} (`{cron}`)")
                buttons.append([
                    {"text": f"{'⏸️' if t.get('isEnabled') else '▶️'} {name}",
                     "callback_data": f"task_toggle:{task_id}"},
                    {"text": "▶️", "callback_data": f"task_run:{task_id}"},
                    {"text": "🗑️", "callback_data": f"task_del:{task_id}"},
                ])
            buttons.append([
                {"text": "➕ 添加任务", "callback_data": "task_add:start"},
                {"text": "🔄 刷新", "callback_data": "tasks_refresh"},
            ])
            return CommandResult(
                text="\n".join(lines),
                reply_markup=buttons,
                parse_mode="Markdown",
                edit_message_id=edit_message_id,
            )
        except Exception as e:
            logger.error(f"获取任务列表失败: {e}", exc_info=True)
            return CommandResult(success=False, text=f"获取任务列表出错: {e}")

    async def cb_tasks_refresh(self, params, user_id, channel, **kw):
        return await self._build_tasks_result(edit_message_id=kw.get("message_id"))

    async def cb_task_detail(self, params, user_id, channel, **kw):
        """查看单个任务的实时状态，支持追踪子任务链"""
        task_id = params[0] if params else ""
        if not task_id:
            return CommandResult(text="", answer_callback_text="缺少任务ID")
        try:
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
                    buttons.append([{"text": "📦 查看执行任务", "callback_data": f"task_detail:{exec_task_id}"}])

            return CommandResult(
                text="\n".join(lines),
                reply_markup=buttons,
                edit_message_id=kw.get("message_id"),
            )
        except Exception as e:
            logger.error(f"查询任务详情失败: {e}", exc_info=True)
            return CommandResult(text="", answer_callback_text=f"查询失败: {e}")


    # ── 定时任务操作按钮 ──

    async def cb_task_toggle(self, params, user_id, channel, **kw):
        """启用/禁用定时任务"""
        task_id = params[0] if params else ""
        if not task_id or not self.scheduler_manager:
            return CommandResult(text="", answer_callback_text="操作失败")
        try:
            async with self._session_factory() as session:
                task_info = await crud.get_scheduled_task(session, task_id)
            if not task_info:
                return CommandResult(text="", answer_callback_text="任务不存在")
            new_enabled = not task_info.get("isEnabled", False)
            await self.scheduler_manager.update_task(
                task_id,
                name=task_info["name"],
                cron=task_info["cronExpression"],
                is_enabled=new_enabled,
                task_config=task_info.get("taskConfig"),
            )
            status_text = "已启用 ✅" if new_enabled else "已禁用 ⏸️"
            result = await self._build_tasks_result(edit_message_id=kw.get("message_id"))
            result.answer_callback_text = f"{self._get_job_name(task_info['jobType'])} {status_text}"
            return result
        except Exception as e:
            return CommandResult(text="", answer_callback_text=f"操作失败: {e}")

    async def cb_task_run(self, params, user_id, channel, **kw):
        """立即执行定时任务"""
        task_id = params[0] if params else ""
        if not task_id or not self.scheduler_manager:
            return CommandResult(text="", answer_callback_text="操作失败")
        try:
            async with self._session_factory() as session:
                task_info = await crud.get_scheduled_task(session, task_id)
            if not task_info:
                return CommandResult(text="", answer_callback_text="任务不存在")
            # 异步执行，不等待完成
            import asyncio
            asyncio.create_task(self.scheduler_manager.run_task_now(task_id))
            return CommandResult(text="", answer_callback_text=f"▶️ {self._get_job_name(task_info['jobType'])} 已触发执行")
        except Exception as e:
            return CommandResult(text="", answer_callback_text=f"执行失败: {e}")

    async def cb_task_del(self, params, user_id, channel, **kw):
        """删除定时任务 — 先确认"""
        task_id = params[0] if params else ""
        if not task_id or not self.scheduler_manager:
            return CommandResult(text="", answer_callback_text="操作失败")
        try:
            async with self._session_factory() as session:
                task_info = await crud.get_scheduled_task(session, task_id)
            if not task_info:
                return CommandResult(text="", answer_callback_text="任务不存在")
            name = self._get_job_name(task_info['jobType'])
            return CommandResult(
                text=f"⚠️ 确认删除定时任务？\n\n"
                     f"名称: {name}\n"
                     f"Cron: {task_info['cronExpression']}",
                reply_markup=[
                    [{"text": "✅ 确认删除", "callback_data": f"task_del_ok:{task_id}"},
                     {"text": "❌ 取消", "callback_data": "tasks_refresh"}],
                ],
                edit_message_id=kw.get("message_id"),
            )
        except Exception as e:
            return CommandResult(text="", answer_callback_text=f"操作失败: {e}")

    async def cb_task_del_ok(self, params, user_id, channel, **kw):
        """确认删除定时任务"""
        task_id = params[0] if params else ""
        if not task_id or not self.scheduler_manager:
            return CommandResult(text="", answer_callback_text="操作失败")
        try:
            ok = await self.scheduler_manager.delete_task(task_id)
            if not ok:
                return CommandResult(text="", answer_callback_text="任务不存在或已删除")
            result = await self._build_tasks_result(edit_message_id=kw.get("message_id"))
            result.answer_callback_text = "🗑️ 已删除"
            return result
        except Exception as e:
            return CommandResult(text="", answer_callback_text=f"删除失败: {e}")

    async def cb_task_add(self, params, user_id, channel, **kw):
        """添加定时任务 — 选择任务类型"""
        if not self.scheduler_manager:
            return CommandResult(text="", answer_callback_text="调度服务未就绪")
        try:
            available = self.scheduler_manager.get_available_jobs()
            if not available:
                return CommandResult(text="没有可用的任务类型。", edit_message_id=kw.get("message_id"))
            lines = ["➕ 选择要添加的任务类型:\n"]
            buttons = []
            for job in available:
                job_type = job.get("jobType", "")
                name = job.get("name", job_type)
                desc = job.get("description", "")
                lines.append(f"• {name}" + (f" — {desc}" if desc else ""))
                buttons.append([{"text": name, "callback_data": f"task_add_type:{job_type}"}])
            buttons.append([{"text": "🔙 返回任务列表", "callback_data": "tasks_refresh"}])
            return CommandResult(
                text="\n".join(lines),
                reply_markup=buttons,
                edit_message_id=kw.get("message_id"),
            )
        except Exception as e:
            return CommandResult(text="", answer_callback_text=f"获取任务类型失败: {e}")

    async def cb_task_add_type(self, params, user_id, channel, **kw):
        """选定任务类型 → 显示 cron 预设按钮 + 等待自定义输入"""
        job_type = params[0] if params else ""
        if not job_type:
            return CommandResult(text="", answer_callback_text="无效操作")
        available = self.scheduler_manager.get_available_jobs()
        job_info = next((j for j in available if j.get("jobType") == job_type), None)
        name = job_info.get("name", job_type) if job_info else job_type
        self.set_conversation(user_id, "task_cron_input", {
            "job_type": job_type, "job_name": name,
        }, chat_id=kw.get("chat_id"))
        return CommandResult(
            text=f"➕ 添加任务: *{name}*\n\n"
                 f"选择执行频率：",
            parse_mode="Markdown",
            reply_markup=[
                [{"text": "🕐 每3小时", "callback_data": f"task_cron:0 */3 * * *"},
                 {"text": "🕕 每6小时", "callback_data": f"task_cron:0 */6 * * *"}],
                [{"text": "🌅 每天6点", "callback_data": f"task_cron:0 6 * * *"},
                 {"text": "🌙 每天0点", "callback_data": f"task_cron:0 0 * * *"}],
                [{"text": "📅 每周日", "callback_data": f"task_cron:0 0 * * 0"},
                 {"text": "📆 每月1号", "callback_data": f"task_cron:0 0 1 * *"}],
                [{"text": "✏️ 自定义Cron", "callback_data": f"task_cron_custom:{job_type}"},
                 {"text": "🔙 返回", "callback_data": "task_add:start"}],
            ],
            edit_message_id=kw.get("message_id"),
        )

    async def cb_task_cron_custom(self, params, user_id, channel, **kw):
        """自定义 Cron — 提示用户输入"""
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "task_cron_input":
            return CommandResult(text="", answer_callback_text="操作已过期")
        job_name = conv.data.get("job_name", "")
        return CommandResult(
            text=f"✏️ *{job_name}* — 自定义 Cron\n\n"
                 f"请输入 Cron 表达式（5段格式）：\n"
                 f"格式: `分 时 日 月 周`\n"
                 f"示例: `30 2 * * *` = 每天2:30",
            parse_mode="Markdown",
            edit_message_id=kw.get("message_id"),
        )

    async def cb_task_cron(self, params, user_id, channel, **kw):
        """预设 cron 按钮 → 直接创建任务"""
        cron = params[0] if params else ""
        if not cron:
            return CommandResult(text="", answer_callback_text="无效操作")
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "task_cron_input":
            return CommandResult(text="", answer_callback_text="操作已过期")
        job_type = conv.data.get("job_type", "")
        job_name = conv.data.get("job_name", job_type)
        self.clear_conversation(user_id)
        try:
            await self.scheduler_manager.add_task(
                name=job_name, job_type=job_type,
                cron=cron, is_enabled=True,
            )
            result = await self._build_tasks_result(edit_message_id=kw.get("message_id"))
            result.answer_callback_text = f"✅ 已添加: {job_name}"
            return result
        except Exception as e:
            return CommandResult(text="", answer_callback_text=f"添加失败: {e}")

    async def _text_task_cron_input(self, text: str, user_id: str, channel, **kw):
        """输入 cron 表达式 → 创建任务"""
        conv = self.get_conversation(user_id)
        if not conv:
            return CommandResult(text="操作已过期。")
        job_type = conv.data.get("job_type", "")
        job_name = conv.data.get("job_name", job_type)
        cron = text.strip()
        self.clear_conversation(user_id)
        try:
            await self.scheduler_manager.add_task(
                name=job_name, job_type=job_type,
                cron=cron, is_enabled=True,
            )
            result = await self._build_tasks_result()
            result.text = f"✅ 已添加任务: {job_name} ({cron})\n\n" + result.text
            return result
        except Exception as e:
            return CommandResult(text=f"❌ 添加失败: {e}")

