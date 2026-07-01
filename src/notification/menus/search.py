"""
/search 菜单 Mixin — 搜索弹幕源、编辑导入流程
"""
import re
import logging
from typing import TYPE_CHECKING

from src.notification.base import CommandResult

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

PAGE_SIZE = 5

# MarkdownV2 保留字符，进度文本需转义以避免 edit 解析失败导致刷屏
_MDV2_SPECIAL = r'_*[]()~`>#+-=|{}.!'


def _escape_mdv2(text: str) -> str:
    """转义 Telegram MarkdownV2 保留字符。"""
    if not text:
        return ""
    out = []
    for ch in str(text):
        if ch in _MDV2_SPECIAL:
            out.append('\\' + ch)
        else:
            out.append(ch)
    return ''.join(out)


class SearchMenuMixin:
    """处理 /search 命令及编辑导入子流程的所有 cmd_/cb_/_text_ 方法"""

    EDIT_EP_PAGE_SIZE = 8  # 编辑导入分集每页显示数

    # ── 工具 ──

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

    # ── /search ──

    async def cmd_search(self, args: str, user_id: str, channel, **kw) -> CommandResult:
        if not args or not args.strip():
            return CommandResult(
                success=False,
                text=(
                    "请提供搜索关键词。\n\n"
                    "用法: /sh <关键词>\n"
                    "示例:\n"
                    "  /sh 刀剑神域 — 搜索并整季导入\n"
                    "  /sh 刀剑神域 S01 — 指定第1季导入\n"
                    "  /sh 刀剑神域 S01E10 — 仅导入第1季第10集"
                ),
                reply_markup=[
                    [
                        {"text": "🔍 直接搜索", "callback_data": "search_input:any"},
                        {"text": "📅 指定季", "callback_data": "search_input:season"},
                        {"text": "🎯 指定季集", "callback_data": "search_input:episode"},
                    ]
                ],
            )
        raw_keyword = args.strip()
        keyword, parsed_season, parsed_episode = self._parse_season_episode(raw_keyword)
        if not keyword:
            keyword = raw_keyword
        if not self.scraper_manager:
            return CommandResult(success=False, text="搜索服务未就绪。")
        # 进度消息 message_id（由调用方通过 send_quick 发出，或 cmd_search 首次更新时创建）
        edit_mid: list = [kw.get("edit_message_id")]  # 用列表包裹，方便闭包写入
        chat_id = kw.get("chat_id")

        async def _search_progress(progress: int, description: str):
            """实时更新 TG 搜索进度条（edit 已有消息）"""
            _desc_map = {
                "获取别名...": "正在获取别名...",
                "执行全网搜索...": "正在搜索各弹幕源...",
                "搜索完成...": "整理搜索结果...",
                "过滤搜索结果...": "过滤无关结果...",
                "排序搜索结果...": "按优先级排序...",
            }
            desc = _desc_map.get(description, description)
            filled = int(progress / 10)
            bar = "█" * filled + "░" * (10 - filled)
            # 进度条用反引号 code 包裹（█░ 不含保留字符）；百分比与描述需转义，
            # 否则 desc 中的 "..." 等保留字符会导致 MarkdownV2 解析失败 →
            # edit 失败降级发新消息 → 进度刷屏。
            text = f"`[{bar}]` {_escape_mdv2(str(progress) + '%')}\n• {_escape_mdv2(desc)}"
            msg_id_out: list = []
            await channel.send_message(
                title="🔍 搜索中",
                text=text,
                chat_id=chat_id,
                edit_message_id=edit_mid[0],
                _msg_id_out=msg_id_out,
            )
            # 首次 send 时记录 message_id，后续复用
            if msg_id_out and not edit_mid[0]:
                edit_mid[0] = msg_id_out[0]

        try:
            async with self._session_factory() as session:
                from src.services.search import unified_search
                results = await unified_search(
                    search_term=keyword,
                    session=session,
                    scraper_manager=self.scraper_manager,
                    metadata_manager=self.metadata_manager,
                    use_alias_filtering=False,
                    use_title_filtering=True,
                    use_source_priority_sorting=True,
                    progress_callback=_search_progress,
                )
            if not results:
                return CommandResult(
                    text=f"🔍 未找到与「{keyword}」相关的结果。",
                    edit_message_id=edit_mid[0],
                )
            serialized = []
            for r in results:
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
            conv_data = {"keyword": keyword, "results": serialized}
            if parsed_season is not None:
                conv_data["parsed_season"] = parsed_season
            if parsed_episode is not None:
                conv_data["parsed_episode"] = parsed_episode
            self.set_conversation(user_id, "search_results", conv_data,
                                  chat_id=kw.get("chat_id"))
            suffix = ""
            if parsed_season is not None:
                suffix += f" S{parsed_season:02d}"
            if parsed_episode is not None:
                suffix += f"E{parsed_episode}"
            display_keyword = keyword + suffix if suffix else keyword
            edit_mid[0] = edit_mid[0] or kw.get("edit_message_id")
            return await self._build_search_page(serialized, display_keyword, 0, edit_message_id=edit_mid[0])
        except Exception as e:
            logger.error(f"搜索失败: {e}", exc_info=True)
            return CommandResult(success=False, text=f"搜索出错: {e}", edit_message_id=edit_mid[0])

    async def _build_search_page(self, results: list, keyword: str, page: int,
                           edit_message_id: int = None,
                           parsed_season=None, parsed_episode=None) -> CommandResult:
        total = len(results)
        page_size = 10  # 每页 10 条（2行×5个按钮）
        start = page * page_size
        end = min(start + page_size, total)
        page_items = results[start:end]
        total_pages = max(1, (total + page_size - 1) // page_size)

        lines = [f"🔍 搜索「{keyword}」({start+1}-{end}/{total}):\n"]
        articles = []
        for i, r in enumerate(page_items):
            idx = start + i
            year_str = f" ({r['year']})" if r.get('year') else ""
            ep_str = f" {r.get('episodeCount', '?')}集" if r.get('episodeCount') else ""
            lines.append(f"{idx+1}. [{r['provider']}] {r['title']}{year_str}{ep_str}")
            articles.append({
                "title": f"{idx+1}. {r['title']}{year_str}{ep_str}",
                "description": f"[{r['provider']}]  回复 {idx+1} 导入",
                "picurl": r.get("imageUrl") or "",
                "url": "",
            })

        # 按钮：1-5 第一行，6-10 第二行
        buttons = []
        for row_start in range(0, len(page_items), 5):
            row = []
            for i in range(row_start, min(row_start + 5, len(page_items))):
                idx = start + i
                row.append({"text": str(idx + 1), "callback_data": f"search_select:{idx}"})
            buttons.append(row)

        # 第三行：翻页
        nav = []
        if page > 0:
            nav.append({"text": "⬅️ 上一页", "callback_data": f"search_page:{page-1}"})
        if page < total_pages - 1:
            nav.append({"text": "➡️ 下一页", "callback_data": f"search_page:{page+1}"})
        if nav:
            buttons.append(nav)

        # 生成当前页的聚合海报图（带序号），失败则回退为文字+单图模式
        collage = await self._build_page_collage(page_items, start)

        return CommandResult(
            text="\n".join(lines),
            reply_markup=buttons,
            edit_message_id=edit_message_id,
            articles=articles,
            image_bytes=collage,
        )

    async def _build_page_collage(self, page_items: list, start: int):
        """为当前页结果生成带序号的九宫格海报图。

        受配置开关 telegramSearchPosterCollage 控制（默认开启）。
        任何异常都吞掉并返回 None，保证搜索结果正常文字展示不受影响。
        """
        try:
            cfg = getattr(self, "config_manager", None)
            if cfg is not None:
                enabled = await cfg.get("telegramSearchPosterCollage", "true")
                if str(enabled).lower() != "true":
                    return None
            # 至少要有一项带海报才值得聚合
            if not any(it.get("imageUrl") for it in page_items):
                return None

            # 读取代理配置（与 image_utils 下载逻辑保持一致）
            proxy, ssl_verify = await self._get_proxy_for_collage()

            from src.utils.poster_collage import build_poster_collage
            items = [
                {"imageUrl": it.get("imageUrl") or "", "index": start + i + 1}
                for i, it in enumerate(page_items)
            ]
            return await build_poster_collage(items, proxy=proxy, ssl_verify=ssl_verify)
        except Exception as e:
            logger.warning(f"生成搜索结果聚合海报失败（不影响文字结果）: {e}")
            return None

    async def _get_proxy_for_collage(self):
        """读取全局代理配置，返回 (proxy_url 或 None, ssl_verify)。"""
        try:
            cfg = getattr(self, "config_manager", None)
            if cfg is None:
                return None, True
            proxy_enabled = (await cfg.get("proxyEnabled", "false")).lower() == "true"
            proxy_url = await cfg.get("proxyUrl", "")
            ssl_verify = (await cfg.get("proxySslVerify", "true")).lower() == "true"
            return (proxy_url if (proxy_enabled and proxy_url) else None), ssl_verify
        except Exception:
            return None, True

    async def cb_search_page(self, params, user_id, channel, **kw):
        page = int(params[0]) if params else 0
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "search_results":
            return CommandResult(text="", answer_callback_text="搜索已过期，请重新搜索")
        return await self._build_search_page(
            conv.data.get("results", []),
            conv.data.get("keyword", ""),
            page,
            edit_message_id=kw.get("message_id"),
        )

    def _build_select_panel(self, item: dict, idx: int, parsed_season=None,
                             parsed_episode=None, edit_message_id=None) -> CommandResult:
        """构建单条搜索结果的操作面板（有海报时发送图文新消息）"""
        title = item.get("title", "未知")
        provider = item.get("provider", "未知源")
        year_str = f" ({item['year']})" if item.get('year') else ""
        ep_str = f" {item.get('episodeCount', '?')}集" if item.get('episodeCount') else ""
        image_url = item.get("imageUrl") or ""
        text = (
            f"🎯 *已选择目标*\n"
            f"• 标题: {title}{year_str}\n"
            f"• 来源: {provider}{ep_str}\n\n"
            f"请选择导入方式："
        )
        if parsed_episode is not None:
            s = f"S{parsed_season:02d}" if parsed_season is not None else "S01"
            e_label = f"{s}E{parsed_episode:02d}" if isinstance(parsed_episode, int) else f"{s}E{parsed_episode}"
            action_row = [
                {"text": "✏️ 编辑导入", "callback_data": f"search_edit:{idx}"},
                {"text": f"📥 导入 {e_label}", "callback_data": f"search_import:{idx}"},
            ]
        elif parsed_season is not None:
            action_row = [
                {"text": "✏️ 编辑导入", "callback_data": f"search_edit:{idx}"},
                {"text": f"📥 导入 S{parsed_season:02d}", "callback_data": f"search_import:{idx}"},
            ]
        else:
            action_row = [
                {"text": "✏️ 编辑导入", "callback_data": f"search_edit:{idx}"},
                {"text": "📥 直接导入", "callback_data": f"search_import:{idx}"},
            ]
        back_row = [{"text": "⬅️ 返回列表", "callback_data": "search_back:0"}]

        # 有海报图片时：不 edit 已有消息，发送新的图文消息
        if image_url:
            return CommandResult(
                text=text,
                reply_markup=[action_row, back_row],
                articles=[{
                    "title": f"{title}{year_str}",
                    "description": f"[{provider}]{ep_str}",
                    "picurl": image_url,
                    "url": "",
                }],
            )
        return CommandResult(
            text=text,
            reply_markup=[action_row, back_row],
            edit_message_id=edit_message_id,
        )

    async def cb_search_select(self, params, user_id, channel, **kw):
        """选择某条搜索结果 → 进入操作面板"""
        idx = int(params[0]) if params else 0
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "search_results":
            return CommandResult(text="", answer_callback_text="搜索已过期，请重新搜索")
        results = conv.data.get("results", [])
        if idx >= len(results):
            return CommandResult(text="", answer_callback_text="无效的选择")
        item = results[idx]
        parsed_season = conv.data.get("parsed_season")
        parsed_episode = conv.data.get("parsed_episode")
        # 记录当前选中的 idx，供后续操作使用
        conv.data["selected_idx"] = idx
        return self._build_select_panel(
            item, idx,
            parsed_season=parsed_season,
            parsed_episode=parsed_episode,
            edit_message_id=kw.get("message_id"),
        )

    async def cb_search_back(self, params, user_id, channel, **kw):
        """从操作面板返回搜索结果列表"""
        page = int(params[0]) if params else 0
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "search_results":
            return CommandResult(text="", answer_callback_text="搜索已过期，请重新搜索")
        return await self._build_search_page(
            conv.data.get("results", []),
            conv.data.get("keyword", ""),
            page,
            edit_message_id=kw.get("message_id"),
            parsed_season=conv.data.get("parsed_season"),
            parsed_episode=conv.data.get("parsed_episode"),
        )

    async def cb_search_season_input(self, params, user_id, channel, **kw):
        """场景1：点击「📅 指定季」→ 等待用户输入季数"""
        idx = int(params[0]) if params else 0
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "search_results":
            return CommandResult(text="", answer_callback_text="搜索已过期")
        conv.data["selected_idx"] = idx
        results = conv.data.get("results", [])
        title = results[idx].get("title", "未知") if idx < len(results) else "未知"
        self.set_conversation(user_id, "search_season_input",
                              conv.data, chat_id=kw.get("chat_id"))
        return CommandResult(
            text=f"📅 指定季导入「{title}」\n请输入季数（如: 1、2）：",
            edit_message_id=kw.get("message_id"),
        )

    async def cb_search_ep_input(self, params, user_id, channel, **kw):
        """场景2：点击「🎯 单集导入」→ 等待用户输入集数"""
        idx = int(params[0]) if params else 0
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "search_results":
            return CommandResult(text="", answer_callback_text="搜索已过期")
        conv.data["selected_idx"] = idx
        results = conv.data.get("results", [])
        title = results[idx].get("title", "未知") if idx < len(results) else "未知"
        season = conv.data.get("parsed_season", 1)
        self.set_conversation(user_id, "search_ep_input",
                              conv.data, chat_id=kw.get("chat_id"))
        return CommandResult(
            text=f"🎯 单集导入「{title}」S{season:02d}\n请输入集数（如: 1、10）：",
            edit_message_id=kw.get("message_id"),
        )

    async def _text_search_season_input(self, text: str, user_id: str, channel, **kw):
        """场景1：输入季数 → 更新 parsed_season 后提交导入"""
        conv = self.get_conversation(user_id)
        if not conv:
            return CommandResult(text="操作已过期。")
        try:
            season = int(text.strip())
        except ValueError:
            return CommandResult(text="⚠️ 请输入数字季数，如: 1")
        idx = conv.data.get("selected_idx", 0)
        results = conv.data.get("results", [])
        if idx >= len(results):
            self.clear_conversation(user_id)
            return CommandResult(text="无效的选择")
        item = results[idx]
        title = item.get("title", "未知")
        self.clear_conversation(user_id)
        return await self._submit_auto_import(
            search_type="keyword", search_term=title,
            media_type=item.get("type"), season=season,
            episode=None,
        )

    async def _text_search_ep_input(self, text: str, user_id: str, channel, **kw):
        """场景2：输入集数 → 更新 parsed_episode 后提交导入"""
        conv = self.get_conversation(user_id)
        if not conv:
            return CommandResult(text="操作已过期。")
        try:
            episode = int(text.strip())
        except ValueError:
            return CommandResult(text="⚠️ 请输入数字集数，如: 10")
        idx = conv.data.get("selected_idx", 0)
        results = conv.data.get("results", [])
        if idx >= len(results):
            self.clear_conversation(user_id)
            return CommandResult(text="无效的选择")
        item = results[idx]
        title = item.get("title", "未知")
        season = conv.data.get("parsed_season", 1)
        self.clear_conversation(user_id)
        return await self._submit_auto_import(
            search_type="keyword", search_term=title,
            media_type=item.get("type"), season=season,
            episode=episode,
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
        if not media_id:
            return CommandResult(text="", answer_callback_text="该结果无法直接导入")
        parsed_season = conv.data.get("parsed_season")
        parsed_episode = conv.data.get("parsed_episode")
        season = parsed_season if parsed_season is not None else item.get("season")
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
            episodes = await self.scraper_manager.get_episodes_routed(provider, media_id, db_media_type=item.get("type"))
            if not episodes:
                return CommandResult(text="", answer_callback_text="未获取到分集列表")
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
            selected = list(range(len(ep_list)))
            edit_data = {
                "item": item,
                "episodes": ep_list,
                "selected": selected,
                "title": item.get("title", ""),
                "type": item.get("type", "tv_series"),
                "season": item.get("season", 1),
                "_search_snapshot": conv.data,
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
        episodes = edit_data.get("episodes", [])
        selected = set(edit_data.get("selected", []))
        item = edit_data.get("item", {})
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
        for i in range(start, end):
            ep = episodes[i]
            check = "✅" if i in selected else "⬜"
            ep_label = f"{check} {ep['episodeIndex']}. {ep['title']}"
            if len(ep_label) > 40:
                ep_label = ep_label[:37] + "..."
            buttons.append([{
                "text": ep_label,
                "callback_data": f"edit_ep_toggle:{i}:{page}",
            }])

        nav = []
        if page > 0:
            nav.append({"text": "⬅️ 上一页", "callback_data": f"edit_ep_page:{page-1}"})
        if page < total_pages - 1:
            nav.append({"text": "➡️ 下一页", "callback_data": f"edit_ep_page:{page+1}"})
        if nav:
            buttons.append(nav)

        buttons.append([
            {"text": "☑️ 全选", "callback_data": f"edit_ep_all:{page}"},
            {"text": "⬜ 取消全选", "callback_data": f"edit_ep_none:{page}"},
        ])
        buttons.append([
            {"text": f"🔄 改类型", "callback_data": f"edit_type:{media_type}"},
            {"text": f"S{season:02d} 改季度", "callback_data": "edit_season"},
        ])
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
        ep_idx = int(params[0]) if len(params) > 0 else 0
        page = int(params[1]) if len(params) > 1 else 0
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "edit_import":
            return CommandResult(text="", answer_callback_text="操作已过期")
        selected: list = conv.data.get("selected", [])
        if ep_idx in selected:
            selected.remove(ep_idx)
        else:
            selected.append(ep_idx)
        conv.data["selected"] = selected
        self.set_conversation(user_id, "edit_import", conv.data,
                              chat_id=kw.get("chat_id"))
        return self._build_edit_page(conv.data, page, edit_message_id=kw.get("message_id"))

    async def cb_edit_ep_page(self, params, user_id, channel, **kw):
        page = int(params[0]) if params else 0
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "edit_import":
            return CommandResult(text="", answer_callback_text="操作已过期")
        return self._build_edit_page(conv.data, page, edit_message_id=kw.get("message_id"))

    async def cb_edit_ep_all(self, params, user_id, channel, **kw):
        page = int(params[0]) if params else 0
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "edit_import":
            return CommandResult(text="", answer_callback_text="操作已过期")
        conv.data["selected"] = list(range(len(conv.data.get("episodes", []))))
        self.set_conversation(user_id, "edit_import", conv.data,
                              chat_id=kw.get("chat_id"))
        return self._build_edit_page(conv.data, page, edit_message_id=kw.get("message_id"))

    async def cb_edit_ep_none(self, params, user_id, channel, **kw):
        page = int(params[0]) if params else 0
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "edit_import":
            return CommandResult(text="", answer_callback_text="操作已过期")
        conv.data["selected"] = []
        self.set_conversation(user_id, "edit_import", conv.data,
                              chat_id=kw.get("chat_id"))
        return self._build_edit_page(conv.data, page, edit_message_id=kw.get("message_id"))

    async def cb_edit_type(self, params, user_id, channel, **kw):
        current_type = params[0] if params else "tv_series"
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "edit_import":
            return CommandResult(text="", answer_callback_text="操作已过期")
        new_type = "movie" if current_type == "tv_series" else "tv_series"
        conv.data["type"] = new_type
        self.set_conversation(user_id, "edit_import", conv.data,
                              chat_id=kw.get("chat_id"))
        return self._build_edit_page(conv.data, 0, edit_message_id=kw.get("message_id"))

    async def cb_edit_season(self, params, user_id, channel, **kw):
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "edit_import":
            return CommandResult(text="", answer_callback_text="操作已过期")
        current_season = conv.data.get("season", 1)
        return CommandResult(
            text=f"当前季度: S{current_season:02d}\n请选择季度：",
            reply_markup=[
                [{"text": f"S{i:02d}", "callback_data": f"auto_season:{i}"} for i in range(1, 5)],
                [{"text": f"S{i:02d}", "callback_data": f"auto_season:{i}"} for i in range(5, 9)],
            ],
            edit_message_id=kw.get("message_id"),
        )

    async def cb_edit_title(self, params, user_id, channel, **kw):
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "edit_import":
            return CommandResult(text="", answer_callback_text="操作已过期")
        self.set_conversation(user_id, "edit_title_input", conv.data,
                              chat_id=kw.get("chat_id"))
        return CommandResult(
            text=f"当前标题: {conv.data.get('title', '')}\n请输入新标题：",
            edit_message_id=kw.get("message_id"),
        )

    async def _text_edit_title(self, text: str, user_id: str, channel, **kw):
        conv = self.get_conversation(user_id)
        if not conv:
            return CommandResult(text="操作已过期。")
        conv.data["title"] = text.strip()
        self.set_conversation(user_id, "edit_import", conv.data,
                              chat_id=kw.get("chat_id"))
        return self._build_edit_page(conv.data, 0)

    async def cb_edit_confirm(self, params, user_id, channel, **kw):
        """确认编辑导入 → 提交任务"""
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "edit_import":
            return CommandResult(text="", answer_callback_text="操作已过期")
        episodes = conv.data.get("episodes", [])
        selected: list = conv.data.get("selected", [])
        item = conv.data.get("item", {})
        title = conv.data.get("title", item.get("title", ""))
        media_type = conv.data.get("type", "tv_series")
        season = conv.data.get("season", 1)

        if not selected:
            return CommandResult(text="", answer_callback_text="请至少选择一集")

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

    async def cb_edit_back(self, params, user_id, channel, **kw):
        """返回搜索结果"""
        conv = self.get_conversation(user_id)
        if not conv or conv.state != "edit_import":
            return CommandResult(text="", answer_callback_text="操作已过期")
        snapshot = conv.data.get("_search_snapshot")
        if not snapshot or not snapshot.get("results"):
            self.clear_conversation(user_id)
            return CommandResult(
                text="已退出编辑。请使用 /search 重新搜索。",
                edit_message_id=kw.get("message_id"),
            )
        self.set_conversation(user_id, "search_results", snapshot,
                              chat_id=kw.get("chat_id"))
        keyword = snapshot.get("keyword", "")
        return await self._build_search_page(
            snapshot["results"], keyword, 0,
            edit_message_id=kw.get("message_id"),
        )

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

    # ── 后备任务通知快捷按钮回调 ──

    async def cb_search_notify(self, params, user_id, channel, **kw):
        """后备任务通知 → 🔍 搜索（直接用关键词搜索整季）"""
        search_term = ":".join(params) if params else ""
        if not search_term:
            return CommandResult(text="", answer_callback_text="无效的搜索词")
        return await self.cmd_search(search_term, user_id, channel, **kw)

    async def cb_search_notify_season(self, params, user_id, channel, **kw):
        """后备任务通知 → 📅 指定季（进入季数输入状态）"""
        search_term = ":".join(params) if params else ""
        if not search_term:
            return CommandResult(text="", answer_callback_text="无效的搜索词")
        self.set_conversation(user_id, "search_notify_season_input",
                              {"search_term": search_term},
                              chat_id=kw.get("chat_id"))
        return CommandResult(
            text=f"🔍 搜索: {search_term}\n请输入季数（如: 1、2）：",
            edit_message_id=kw.get("message_id"),
        )

    async def cb_search_notify_episode(self, params, user_id, channel, **kw):
        """后备任务通知 → 🎯 指定季集（进入季集输入状态）"""
        search_term = ":".join(params) if params else ""
        if not search_term:
            return CommandResult(text="", answer_callback_text="无效的搜索词")
        self.set_conversation(user_id, "search_notify_episode_input",
                              {"search_term": search_term},
                              chat_id=kw.get("chat_id"))
        return CommandResult(
            text=f"🔍 搜索: {search_term}\n请输入季集（如: S01E05、S02）：",
            edit_message_id=kw.get("message_id"),
        )

    # ── /search 无参数快捷按钮回调 ──

    async def cb_search_input(self, params, user_id, channel, **kw):
        """处理 /search 无参数时三个快捷按钮"""
        mode = params[0] if params else "any"
        mode_map = {
            "any": ("search_keyword_input", "请输入搜索关键词："),
            "season": ("search_keyword_season_input", "请输入关键词和季数（如: 刀剑神域 1）："),
            "episode": ("search_keyword_episode_input", "请输入关键词和季集（如: 刀剑神域 S01E05）："),
        }
        state, prompt = mode_map.get(mode, mode_map["any"])
        self.set_conversation(user_id, state, {"mode": mode}, chat_id=kw.get("chat_id"))
        return CommandResult(
            text=prompt,
            edit_message_id=kw.get("message_id"),
        )

    async def _text_search_keyword_input(self, text: str, user_id: str, channel, **kw):
        """直接搜索关键词输入"""
        self.clear_conversation(user_id)
        chat_id = kw.get("chat_id")
        mid = await channel.send_quick("🔍 搜索中，请稍候...", chat_id=chat_id)
        if mid:
            kw = {**kw, "edit_message_id": mid}
        return await self.cmd_search(text.strip(), user_id, channel, **kw)

    async def _text_search_keyword_season_input(self, text: str, user_id: str, channel, **kw):
        """搜索关键词+季数输入，格式: 关键词 季数数字"""
        self.clear_conversation(user_id)
        chat_id = kw.get("chat_id")
        mid = await channel.send_quick("🔍 搜索中，请稍候...", chat_id=chat_id)
        if mid:
            kw = {**kw, "edit_message_id": mid}
        parts = text.strip().rsplit(None, 1)
        if len(parts) == 2:
            keyword, season_raw = parts
            try:
                season = int(season_raw)
                return await self.cmd_search(f"{keyword} S{season:02d}", user_id, channel, **kw)
            except ValueError:
                pass
        # 无法解析则直接当关键词搜索
        return await self.cmd_search(text.strip(), user_id, channel, **kw)

    async def _text_search_keyword_episode_input(self, text: str, user_id: str, channel, **kw):
        """搜索关键词+季集输入，格式: 关键词 S01E05"""
        self.clear_conversation(user_id)
        chat_id = kw.get("chat_id")
        mid = await channel.send_quick("🔍 搜索中，请稍候...", chat_id=chat_id)
        if mid:
            kw = {**kw, "edit_message_id": mid}
        return await self.cmd_search(text.strip(), user_id, channel, **kw)

    async def _text_search_notify_season(self, text: str, user_id: str, channel, **kw):
        """后备任务通知 指定季输入 → 用 S{N} 触发搜索"""
        conv = self.get_conversation(user_id)
        if not conv:
            return CommandResult(text="操作已过期。")
        search_term = conv.data.get("search_term", "")
        self.clear_conversation(user_id)
        try:
            season = int(text.strip())
        except ValueError:
            return CommandResult(text="⚠️ 请输入数字季数，如: 1")
        return await self.cmd_search(f"{search_term} S{season:02d}", user_id, channel, **kw)

    async def _text_search_notify_episode(self, text: str, user_id: str, channel, **kw):
        """后备任务通知 指定季集输入 → 用 S{N}E{N} 触发搜索"""
        conv = self.get_conversation(user_id)
        if not conv:
            return CommandResult(text="操作已过期。")
        search_term = conv.data.get("search_term", "")
        self.clear_conversation(user_id)
        return await self.cmd_search(f"{search_term} {text.strip()}", user_id, channel, **kw)

