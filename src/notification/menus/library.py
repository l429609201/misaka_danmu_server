"""
/refresh 菜单 Mixin — 弹幕库管理（浏览、刷新、删除源/分集）
"""
import logging
from src.notification.base import CommandResult

logger = logging.getLogger(__name__)

PAGE_SIZE = 5


class LibraryMenuMixin:
    """处理 /refresh 命令及弹幕库管理所有 cmd_/cb_/_text_/_do_ 方法"""

    # ── /refresh 入口 ──

    async def cmd_refresh(self, args: str, user_id: str, channel, **kw) -> CommandResult:
        """弹幕库管理 — 浏览媒体库或搜索"""
        if args and args.strip():
            return await self._refresh_search_library(args.strip(), user_id, 0, **kw)
        return await self._build_library_page(user_id, 0, **kw)

    # ── 媒体库列表页 ──

    async def _build_library_page(self, user_id: str, page: int,
                                   edit_message_id: int = None, **kw) -> CommandResult:
        """构建弹幕库管理分页列表"""
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
                    text="📚 弹幕库为空。\n\n提示: 使用 /refresh <关键词> 搜索",
                    edit_message_id=edit_message_id,
                )
            total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
            start = page * PAGE_SIZE
            lines = [f"📚 弹幕库管理 ({start+1}-{min(start+PAGE_SIZE, total)}/{total}):\n"]
            buttons = []
            articles = []
            for item in items:
                anime_id = item.get("animeId") or item.get("id")
                title = item.get("title", "未知")
                ep_count = item.get("episodeCount", 0)
                lines.append(f"• {title} ({ep_count}集)")
                buttons.append([{
                    "text": f"📂 {title}",
                    "callback_data": f"refresh_anime:{anime_id}",
                }])
                articles.append({
                    "title": title,
                    "description": f"{ep_count}集",
                    "picurl": item.get("imageUrl") or "",
                    "url": "",
                })
            nav = []
            if page > 0:
                nav.append({"text": "⬅️ 上一页", "callback_data": f"lib_page:{page-1}"})
            if page < total_pages - 1:
                nav.append({"text": "➡️ 下一页", "callback_data": f"lib_page:{page+1}"})
            if nav:
                buttons.append(nav)
            self.set_conversation(user_id, "refresh_keyword_input", {},
                                  chat_id=kw.get("chat_id"))
            return CommandResult(
                text="\n".join(lines) + "\n\n💡 也可发送关键词搜索媒体库",
                reply_markup=buttons,
                edit_message_id=edit_message_id or kw.get("message_id"),
                articles=articles,
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
                return CommandResult(text=f"📚 弹幕库中未找到「{keyword}」。")
            self.set_conversation(user_id, "refresh_library_browse", {
                "keyword": keyword,
            }, chat_id=kw.get("chat_id"))
            total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
            start = page * PAGE_SIZE
            lines = [f"📚 弹幕库管理 搜索「{keyword}」({start+1}-{min(start+PAGE_SIZE, total)}/{total}):\n"]
            buttons = []
            articles = []
            for item in items:
                anime_id = item.get("animeId") or item.get("id")
                title = item.get("title", "未知")
                lines.append(f"• {title}")
                buttons.append([{
                    "text": f"📂 {title}",
                    "callback_data": f"refresh_anime:{anime_id}",
                }])
                articles.append({
                    "title": title,
                    "description": "点击管理",
                    "picurl": item.get("imageUrl") or "",
                    "url": "",
                })
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
                articles=articles,
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

    async def _text_refresh_keyword(self, text: str, user_id: str, channel, **kw):
        """刷新命令中的关键词搜索"""
        return await self._refresh_search_library(text.strip(), user_id, 0, **kw)

    # ── 数据源列表页 ──

    async def cb_refresh_anime(self, params, user_id, channel, **kw):
        """选择作品 → 显示数据源列表（刷新 + 删除）"""
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
            lines = [f"📂 {title}\n选择数据源操作：\n"]
            buttons = []
            image_url = (details or {}).get("imageUrl") or "" if details else ""
            for s in sources:
                sid = s.get("sourceId")
                provider = s.get("providerName", "未知")
                ep_count = s.get("episodeCount", 0)
                lines.append(f"• [{provider}] {ep_count}集")
                buttons.append([
                    {"text": f"🔄 [{provider}] {ep_count}集",
                     "callback_data": f"refresh_source:{anime_id}:{sid}"},
                    {"text": "🗑️ 删除源",
                     "callback_data": f"delete_source_do:{anime_id}:{sid}"},
                ])
            articles = [{
                "title": title,
                "description": f"共 {sum(s.get('episodeCount', 0) for s in sources)} 集，{len(sources)} 个源",
                "picurl": image_url,
                "url": "",
            }] if image_url else []
            buttons.append([{"text": "🔙 返回媒体库", "callback_data": "lib_page:0"}])
            return CommandResult(
                text="\n".join(lines),
                reply_markup=buttons,
                edit_message_id=kw.get("message_id"),
                articles=articles,
            )
        except Exception as e:
            return CommandResult(text=f"获取数据源失败: {e}")

    # ── 源操作页（刷新/删除分集）──

    async def cb_refresh_source(self, params, user_id, channel, **kw):
        """选择数据源 → 显示分集预览 + 刷新/删除操作"""
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
            for ep in episodes[:8]:
                idx = ep.get("episodeIndex", "?")
                ep_title = ep.get("title", "")
                count = ep.get("commentCount", 0)
                lines.append(f"  第{idx}集 {ep_title} ({count}条弹幕)")
            if total > 8:
                lines.append(f"  ... 还有 {total - 8} 集")

            buttons = [
                [{"text": "🔄 全部刷新", "callback_data": f"refresh_do:{source_id}:all"},
                 {"text": "✏️ 选择刷新", "callback_data": f"refresh_do:{source_id}:input"}],
                [{"text": "🗑️ 全部删除弹幕", "callback_data": f"delete_ep_all:{source_id}"},
                 {"text": "✏️ 选择删除", "callback_data": f"delete_ep_range:{source_id}"}],
                [{"text": "🔙 返回数据源", "callback_data": f"refresh_anime:{anime_id}"}],
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

    # ── 刷新操作 ──

    async def cb_refresh_do(self, params, user_id, channel, **kw):
        """执行刷新"""
        source_id = int(params[0]) if len(params) > 0 else 0
        mode = params[1] if len(params) > 1 else "all"

        if mode == "input":
            # 获取分集列表，构建内联键盘选集界面
            try:
                from src.db import crud
                async with self._session_factory() as session:
                    ep_result = await crud.get_episodes_for_source(session, source_id)
                episodes = ep_result.get("episodes", [])
                if not episodes:
                    return CommandResult(text="暂无分集数据。", edit_message_id=kw.get("message_id"))
                conv = self.get_conversation(user_id)
                data = conv.data if conv else {}
                data["source_id"] = source_id
                data["episodes"] = [
                    {"idx": e.get("episodeIndex", 0), "id": e["episodeId"],
                     "title": e.get("title", ""), "count": e.get("commentCount", 0)}
                    for e in episodes
                ]
                data["selected"] = []  # 选中的 episodeIndex 列表
                data["ep_page"] = 0
                data["mode"] = "refresh"
                self.set_conversation(user_id, "ep_select", data, chat_id=kw.get("chat_id"))
                return self._build_ep_select_page(data, edit_message_id=kw.get("message_id"))
            except Exception as e:
                return CommandResult(text=f"获取分集列表失败: {e}", edit_message_id=kw.get("message_id"))
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
                task_type="tg_refresh",
                task_parameters={"sourceId": source_id}
            )
            return CommandResult(
                text=f"✅ 刷新任务已提交\n{title} [{provider}]\n集数: {ep_desc}\n任务ID: {task_id}",
                reply_markup=[[{"text": "📋 查看任务状态", "callback_data": f"task_detail:{task_id}"}]],
                edit_message_id=edit_message_id,
            )
        except Exception as e:
            logger.error(f"提交刷新任务失败: {e}", exc_info=True)
            return CommandResult(text=f"❌ 提交刷新任务失败: {e}")

    async def _text_refresh_episode_range(self, text: str, user_id: str, channel, **kw):
        """集数范围输入 → 执行刷新"""
        conv = self.get_conversation(user_id)
        if not conv:
            return CommandResult(text="操作已过期。")
        source_id = conv.data.get("source_id", 0)
        self.clear_conversation(user_id)
        episode_range = None if text.strip().lower() == "all" else text.strip()
        return await self._do_refresh_source(source_id, episode_range)

    # ── 内联键盘选集（刷新/删除共用） ──

    EP_SELECT_PAGE_SIZE = 10  # 每页 10 集（2行×5个）

    @staticmethod
    def _compact_range(indices: list) -> str:
        """将集数列表格式化为紧凑范围: [1,2,3,5,7,8] → '1-3,5,7-8'"""
        if not indices:
            return ""
        nums = sorted(set(indices))
        ranges = []
        start = prev = nums[0]
        for n in nums[1:]:
            if n == prev + 1:
                prev = n
            else:
                ranges.append(f"{start}-{prev}" if prev > start else str(start))
                start = prev = n
        ranges.append(f"{start}-{prev}" if prev > start else str(start))
        return ",".join(ranges)

    def _build_ep_select_page(self, data: dict, edit_message_id: int = None) -> CommandResult:
        """构建集数选择内联键盘

        两种模式：
        - 单选模式（默认）：点击数字直接执行刷新/删除该集
        - 批量模式：点击数字切换选中，确认后批量执行

        布局：
        - 第1行：集1-5
        - 第2行：集6-10
        - 第3行：上下页
        - 第4行：开启批量选择 | 全选 | 返回
        - 第5行（批量模式有选中时）：确认按钮
        """
        episodes = data.get("episodes", [])
        selected = set(data.get("selected", []))
        page = data.get("ep_page", 0)
        mode = data.get("mode", "refresh")  # refresh / delete
        batch = data.get("batch", False)
        source_id = data.get("source_id", 0)
        anime_id = data.get("anime_id", 0)
        title = data.get("title", "未知")
        provider = data.get("provider", "")

        total = len(episodes)
        total_pages = max(1, (total + self.EP_SELECT_PAGE_SIZE - 1) // self.EP_SELECT_PAGE_SIZE)
        start = page * self.EP_SELECT_PAGE_SIZE
        end = min(start + self.EP_SELECT_PAGE_SIZE, total)
        page_eps = episodes[start:end]

        # 标题
        mode_icon = "🔄" if mode == "refresh" else "🗑️"
        mode_verb = "刷新" if mode == "refresh" else "删除"
        header = f"{mode_icon} 选择{mode_verb} — {title}"
        if provider:
            header += f" [{provider}]"

        if batch and selected:
            compact = self._compact_range(list(selected))
            header += f"\n已选: {compact} ({len(selected)}集)"
        elif batch:
            header += f"\n批量选择模式 (第{page+1}/{total_pages}页)"
        else:
            header += f"\n点击集数直接{mode_verb} (第{page+1}/{total_pages}页)"

        # 当前页分集详情列表
        header += "\n"
        for ep in page_eps:
            idx = ep["idx"]
            ep_title = ep.get("title", "")
            count = ep.get("count", 0)
            # 批量模式标记选中
            mark = "✅" if (batch and idx in selected) else "▪️"
            line = f"{mark} E{idx}"
            if ep_title:
                line += f"  {ep_title}"
            line += f"  ({count}条)"
            header += f"\n{line}"

        buttons = []

        # 第1-2行：集数按钮
        for row_start in range(0, len(page_eps), 5):
            row = []
            for ep in page_eps[row_start:row_start + 5]:
                idx = ep["idx"]
                if batch:
                    is_sel = idx in selected
                    label = f"✅{idx}" if is_sel else str(idx)
                    row.append({"text": label, "callback_data": f"ref_ep_tog:{idx}"})
                else:
                    # 单选模式：点击直接执行
                    row.append({"text": str(idx), "callback_data": f"ref_ep_do:{idx}"})
            buttons.append(row)

        # 第3行：翻页
        nav = []
        if page > 0:
            nav.append({"text": "⬅️ 上一页", "callback_data": f"ref_ep_pg:{page - 1}"})
        if page < total_pages - 1:
            nav.append({"text": "➡️ 下一页", "callback_data": f"ref_ep_pg:{page + 1}"})
        if nav:
            buttons.append(nav)

        # 第4行
        if batch:
            all_selected = len(selected) == total
            action_row = [
                {"text": "📋 批量输入", "callback_data": "ref_ep_none:batch"},
                {"text": "❎ 取消全选" if all_selected else "☑️ 全选",
                 "callback_data": "ref_ep_none:clear" if all_selected else "ref_ep_all:all"},
                {"text": "🔙 返回", "callback_data": f"refresh_source:{anime_id}:{source_id}"},
            ]
        else:
            action_row = [
                {"text": "📋 开启批量选择", "callback_data": "ref_ep_batch:on"},
                {"text": "☑️ 全选", "callback_data": "ref_ep_all:all"},
                {"text": "🔙 返回", "callback_data": f"refresh_source:{anime_id}:{source_id}"},
            ]
        buttons.append(action_row)

        # 第5行：确认按钮（批量模式有选中时）
        if batch and selected:
            confirm_label = f"✅ 确认{mode_verb} ({len(selected)}集)"
            buttons.append([{"text": confirm_label, "callback_data": "ref_ep_ok:confirm"}])

        return CommandResult(
            text=header,
            reply_markup=buttons,
            edit_message_id=edit_message_id,
        )

    async def cb_ref_ep_toggle(self, params, user_id, channel, **kw):
        """批量模式：切换单集选中状态"""
        ep_idx = int(params[0]) if params else 0
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "ep_select":
            return CommandResult(text="", answer_callback_text="操作已过期")
        selected = set(conv.data.get("selected", []))
        if ep_idx in selected:
            selected.discard(ep_idx)
        else:
            selected.add(ep_idx)
        conv.data["selected"] = list(selected)
        return self._build_ep_select_page(conv.data, edit_message_id=kw.get("message_id"))

    async def cb_ref_ep_do(self, params, user_id, channel, **kw):
        """单选模式：点击集数直接执行刷新/删除"""
        ep_idx = int(params[0]) if params else 0
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "ep_select":
            return CommandResult(text="", answer_callback_text="操作已过期")
        data = conv.data
        source_id = data.get("source_id", 0)
        anime_id = data.get("anime_id", 0)
        mode = data.get("mode", "refresh")
        self.clear_conversation(user_id)
        episode_range = str(ep_idx)
        if mode == "refresh":
            return await self._do_refresh_source(source_id, episode_range, kw.get("message_id"))
        else:
            return await self._do_delete_source_episodes(source_id, anime_id, episode_range)

    async def cb_ref_ep_batch(self, params, user_id, channel, **kw):
        """开启批量选择模式"""
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "ep_select":
            return CommandResult(text="", answer_callback_text="操作已过期")
        conv.data["batch"] = True
        conv.data["selected"] = []
        return self._build_ep_select_page(conv.data, edit_message_id=kw.get("message_id"))

    async def cb_ref_ep_page(self, params, user_id, channel, **kw):
        """翻页"""
        page = int(params[0]) if params else 0
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "ep_select":
            return CommandResult(text="", answer_callback_text="操作已过期")
        conv.data["ep_page"] = page
        return self._build_ep_select_page(conv.data, edit_message_id=kw.get("message_id"))

    async def cb_ref_ep_all(self, params, user_id, channel, **kw):
        """全选（自动进入批量模式）"""
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "ep_select":
            return CommandResult(text="", answer_callback_text="操作已过期")
        all_indices = [ep["idx"] for ep in conv.data.get("episodes", [])]
        conv.data["selected"] = all_indices
        conv.data["batch"] = True
        return self._build_ep_select_page(conv.data, edit_message_id=kw.get("message_id"))

    async def cb_ref_ep_none(self, params, user_id, channel, **kw):
        """取消全选 或 切换到批量文本输入"""
        action = params[0] if params else "clear"
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "ep_select":
            return CommandResult(text="", answer_callback_text="操作已过期")
        if action == "batch":
            data = conv.data
            mode = data.get("mode", "refresh")
            state = "refresh_episode_range" if mode == "refresh" else "delete_episode_range"
            self.set_conversation(user_id, state, data, chat_id=kw.get("chat_id"))
            return CommandResult(
                text="✏️ 请输入集数范围：\n格式: 1,3,5  /  7-13  /  all（全部）",
                edit_message_id=kw.get("message_id"),
            )
        conv.data["selected"] = []
        return self._build_ep_select_page(conv.data, edit_message_id=kw.get("message_id"))

    async def cb_ref_ep_ok(self, params, user_id, channel, **kw):
        """确认批量选择 → 执行刷新或删除"""
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "ep_select":
            return CommandResult(text="", answer_callback_text="操作已过期")
        data = conv.data
        selected = set(data.get("selected", []))
        if not selected:
            return CommandResult(text="", answer_callback_text="请至少选择一集")
        source_id = data.get("source_id", 0)
        anime_id = data.get("anime_id", 0)
        mode = data.get("mode", "refresh")
        self.clear_conversation(user_id)
        episode_range = ",".join(str(i) for i in sorted(selected))
        if mode == "refresh":
            return await self._do_refresh_source(source_id, episode_range, kw.get("message_id"))
        else:
            return await self._do_delete_source_episodes(source_id, anime_id, episode_range)

    async def cb_delete_source_do(self, params, user_id, channel, **kw):
        """点击「删除源」→ 弹出确认框"""
        anime_id = int(params[0]) if len(params) > 0 else 0
        source_id = int(params[1]) if len(params) > 1 else 0
        try:
            from src.db import crud
            async with self._session_factory() as session:
                source_info = await crud.get_anime_source_info(session, source_id)
            if not source_info:
                return CommandResult(text="", answer_callback_text="数据源不存在")
            provider = source_info.get("providerName", "未知")
            title = source_info.get("title", "未知")
            ep_count = source_info.get("episodeCount", 0)
            return CommandResult(
                text=f"⚠️ 确认删除数据源？\n\n"
                     f"番剧: {title}\n"
                     f"源: [{provider}]  共 {ep_count} 集\n\n"
                     f"此操作将删除该源及所有弹幕文件，不可撤销！",
                reply_markup=[[
                    {"text": "✅ 确认删除",
                     "callback_data": f"delete_source_confirm:{anime_id}:{source_id}"},
                    {"text": "❌ 取消",
                     "callback_data": f"refresh_anime:{anime_id}"},
                ]],
            )
        except Exception as e:
            return CommandResult(text=f"获取数据源信息失败: {e}")

    async def cb_delete_source_confirm(self, params, user_id, channel, **kw):
        """确认删除源 → 提交删除任务"""
        anime_id = int(params[0]) if len(params) > 0 else 0
        source_id = int(params[1]) if len(params) > 1 else 0
        if not self.task_manager:
            return CommandResult(text="任务管理器未就绪")
        try:
            from src.db import crud
            from src import tasks
            async with self._session_factory() as session:
                source_info = await crud.get_anime_source_info(session, source_id)
            if not source_info:
                return CommandResult(text="数据源不存在")
            provider = source_info.get("providerName", "未知")
            title = source_info.get("title", "未知")
            task_title = f"TG删除源: {title} [{provider}]"
            unique_key = f"delete-source-{source_id}"

            async def _task_coro(session, cb):
                return await tasks.delete_source_task(source_id, session, cb)

            task_id, _ = await self.task_manager.submit_task(
                coro_factory=_task_coro, title=task_title,
                unique_key=unique_key,
                task_type="tg_delete",
                task_parameters={"sourceId": source_id},
            )
            return CommandResult(
                text=f"✅ 删除任务已提交\n{title} [{provider}]\n任务ID: {task_id}",
                reply_markup=[[
                    {"text": "📋 查看任务状态", "callback_data": f"task_detail:{task_id}"},
                    {"text": "🔙 返回媒体库", "callback_data": "lib_page:0"},
                ]],
                edit_message_id=kw.get("message_id"),
            )
        except Exception as e:
            logger.error(f"提交删除源任务失败: {e}", exc_info=True)
            return CommandResult(text=f"❌ 提交删除任务失败: {e}")

    # ── 删除分集 ──

    async def cb_delete_ep_all(self, params, user_id, channel, **kw):
        """全部删除该源的所有分集弹幕"""
        source_id = int(params[0]) if params else 0
        conv = self.get_conversation(user_id)
        anime_id = (conv.data.get("anime_id", 0) if conv else 0)
        if not self.task_manager:
            return CommandResult(text="任务管理器未就绪")
        try:
            from src.db import crud
            from src import tasks
            async with self._session_factory() as session:
                source_info = await crud.get_anime_source_info(session, source_id)
            if not source_info:
                return CommandResult(text="数据源不存在")
            provider = source_info.get("providerName", "未知")
            title = source_info.get("title", "未知")
            task_title = f"TG删除弹幕: {title} [{provider}] (全部)"
            unique_key = f"delete-bulk-sources-{source_id}-all"

            async def _task_coro(session, cb):
                ep_result = await crud.get_episodes_for_source(session, source_id)
                ep_ids = [e["episodeId"] for e in ep_result.get("episodes", [])]
                if not ep_ids:
                    return
                return await tasks.delete_bulk_episodes_task(ep_ids, session, cb)

            task_id, _ = await self.task_manager.submit_task(
                coro_factory=_task_coro, title=task_title,
                unique_key=unique_key,
                task_type="tg_delete",
                task_parameters={"sourceId": source_id},
            )
            return CommandResult(
                text=f"✅ 全部删除任务已提交\n{title} [{provider}]\n任务ID: {task_id}",
                reply_markup=[[
                    {"text": "📋 查看任务状态", "callback_data": f"task_detail:{task_id}"},
                    {"text": "🔙 返回", "callback_data": f"refresh_source:{anime_id}:{source_id}"},
                ]],
                edit_message_id=kw.get("message_id"),
            )
        except Exception as e:
            logger.error(f"提交全部删除任务失败: {e}", exc_info=True)
            return CommandResult(text=f"❌ 提交删除任务失败: {e}")

    async def cb_delete_ep_range(self, params, user_id, channel, **kw):
        """「选择删除」→ 内联键盘选集界面"""
        source_id = int(params[0]) if params else 0
        try:
            from src.db import crud
            async with self._session_factory() as session:
                ep_result = await crud.get_episodes_for_source(session, source_id)
            episodes = ep_result.get("episodes", [])
            if not episodes:
                return CommandResult(text="暂无分集数据。", edit_message_id=kw.get("message_id"))
            conv = self.get_conversation(user_id)
            data = conv.data if conv else {}
            data["source_id"] = source_id
            data["episodes"] = [
                {"idx": e.get("episodeIndex", 0), "id": e["episodeId"],
                 "title": e.get("title", ""), "count": e.get("commentCount", 0)}
                for e in episodes
            ]
            data["selected"] = []
            data["ep_page"] = 0
            data["mode"] = "delete"
            self.set_conversation(user_id, "ep_select", data, chat_id=kw.get("chat_id"))
            return self._build_ep_select_page(data, edit_message_id=kw.get("message_id"))
        except Exception as e:
            return CommandResult(text=f"获取分集列表失败: {e}", edit_message_id=kw.get("message_id"))

    async def _text_delete_episode_range(self, text: str, user_id: str, channel, **kw):
        """集数范围输入 → 执行删除"""
        conv = self.get_conversation(user_id)
        if not conv:
            return CommandResult(text="操作已过期。")
        source_id = conv.data.get("source_id", 0)
        anime_id = conv.data.get("anime_id", 0)
        self.clear_conversation(user_id)
        return await self._do_delete_source_episodes(source_id, anime_id, text.strip())

    async def _do_delete_source_episodes(self, source_id: int, anime_id: int,
                                          episode_range: str) -> CommandResult:
        """执行按集数范围删除弹幕"""
        if not self.task_manager:
            return CommandResult(text="任务管理器未就绪")
        try:
            from src.db import crud
            from src import tasks
            async with self._session_factory() as session:
                source_info = await crud.get_anime_source_info(session, source_id)
            if not source_info:
                return CommandResult(text="数据源不存在")
            provider = source_info.get("providerName", "未知")
            title = source_info.get("title", "未知")

            async with self._session_factory() as session:
                ep_result = await crud.get_episodes_for_source(session, source_id)
            episodes = ep_result.get("episodes", [])

            if episode_range.lower() == "all":
                ep_ids = [e["episodeId"] for e in episodes]
                ep_desc = "全部"
            else:
                from src.tasks import parse_episode_ranges
                indices = parse_episode_ranges(episode_range)
                ep_ids = [e["episodeId"] for e in episodes if e.get("episodeIndex") in indices]
                ep_desc = episode_range

            if not ep_ids:
                return CommandResult(text=f"未找到匹配的集数: {episode_range}")

            task_title = f"TG删除弹幕: {title} [{provider}] (E{ep_desc})"
            unique_key = f"delete-bulk-sources-{source_id}-{ep_desc}"

            async def _task_coro(session, cb):
                return await tasks.delete_bulk_episodes_task(ep_ids, session, cb)

            task_id, _ = await self.task_manager.submit_task(
                coro_factory=_task_coro, title=task_title,
                unique_key=unique_key,
                task_type="tg_delete",
                task_parameters={"sourceId": source_id},
            )
            return CommandResult(
                text=f"✅ 删除任务已提交\n{title} [{provider}]\n集数: {ep_desc}\n任务ID: {task_id}",
                reply_markup=[[
                    {"text": "📋 查看任务状态", "callback_data": f"task_detail:{task_id}"},
                    {"text": "🔙 返回", "callback_data": f"refresh_source:{anime_id}:{source_id}"},
                ]],
            )
        except Exception as e:
            logger.error(f"提交删除任务失败: {e}", exc_info=True)
            return CommandResult(text=f"❌ 提交删除任务失败: {e}")

