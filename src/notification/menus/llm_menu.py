"""
LLM 对话 Mixin — 通知渠道接入御坂 Agent
------------------------------------------------------------
当用户在渠道（Telegram/企业微信/SC3）发自然语言、且无活跃命令流程时，
转交御坂 AssistantAgent 处理。复用 Web 端同一套 Agent + 工具集 + 安全网关。

渠道端复用 Web 的 Agent 与工具安全网关：单用户渠道通过一次性按钮确认写操作，支持任务状态继续查询；不把 Web SSE 原样搬到渠道，而是按平台能力降级为编辑消息、内联按钮或编号文本。
- 流式：通过 stream_callback 把增量文本回吐给渠道层，
  支持 edit 的渠道（Telegram）做"伪流式"，不支持的攒完一次性发。
- 渠道会话历史用内存滑动窗口（每用户最近 N 轮），不落库，保持轻量。
"""

import logging
import secrets
from typing import Callable, Dict, List, Optional, Awaitable

from src.notification.base import CommandResult
from src.ai.assistant import AssistantAgent, DEFAULT_PERSONA
from src.ai.assistant.tools import registry
from src.services.ai_service import get_ai_service
from src.utils.auth import security

logger = logging.getLogger(__name__)

# 每个渠道用户在内存里保留的最近对话轮数（user/bot 各算一条）
_LLM_HISTORY_LIMIT = 12


class LlmChatMixin:
    """为 NotificationService 提供御坂 LLM 对话能力（渠道端）。"""

    # 渠道 LLM 对话历史：{user_id: [{"role","content"}, ...]}（内存滑动窗口）
    # 渠道 Agent 的一次性选择票据：{user_id: {token: choice}}，不持久化敏感内容。
    _llm_pending_choices: Dict[str, Dict[str, dict]] = {}
    _llm_pending_confirms: Dict[str, Dict[str, dict]] = {}

    def _agent_context_extra(self) -> dict:
        """写类工具执行所需的管理器（渠道端）。"""
        return {
            "task_manager": getattr(self, "task_manager", None),
            "scraper_manager": getattr(self, "scraper_manager", None),
            "rate_limiter": getattr(self, "rate_limiter", None),
            "scheduler_manager": getattr(self, "scheduler_manager", None),
            "metadata_manager": getattr(self, "metadata_manager", None),
            "ai_service": getattr(self, "ai_service", None),
            "title_recognition_manager": getattr(self, "title_recognition_manager", None),
            "config_service": self.config_service,
        }

    async def is_llm_chat_enabled(self) -> bool:
        """渠道 LLM 兜底是否可用：需 AI 已配置 + 开关开启。"""
        if not self.config_service:
            return False
        enabled = (await self.config_service.get("assistantChannelChatEnabled", "true")).lower() == "true"
        if not enabled:
            return False
        ai_service = getattr(self, "ai_service", None) or get_ai_service()
        agent = AssistantAgent(
            self.config_service,
            session_factory=self._session_factory,
            on_metric_record=ai_service.persist_metric,
        )
        cfg = await agent._load_ai_config()
        return bool(cfg["api_key"] and cfg["model"] and cfg["base_url"])

    def _get_llm_history(self, user_id: str) -> List[dict]:
        return self._llm_histories.setdefault(user_id, [])

    def _append_llm_history(self, user_id: str, role: str, content: str):
        hist = self._get_llm_history(user_id)
        hist.append({"role": role, "content": content})
        # 只保留最近 N 条
        if len(hist) > _LLM_HISTORY_LIMIT:
            del hist[: len(hist) - _LLM_HISTORY_LIMIT]

    def _save_llm_choice(self, user_id: str, choice: dict) -> List[List[dict]]:
        """保存一次性渠道选择并生成平台无关按钮。"""
        token = secrets.token_urlsafe(9)
        options = choice.get("options") or []
        self._llm_pending_choices[user_id] = {
            "token": token,
            "options": options,
            "title": choice.get("title") or "请选择下一步",
            "prompt": choice.get("prompt") or "请选择一个选项：",
        }
        return [[{
            "text": str(option.get("label") or f"选项 {index + 1}"),
            "callback_data": f"llm_choice:{token}:{index}",
        } for index, option in enumerate(options) if isinstance(option, dict)][:5]]

    async def cb_llm_choice(self, params, user_id: str, channel, **kwargs) -> CommandResult:
        """消费渠道 Agent 的选择按钮，并继续同一用户的对话。"""
        if len(params) < 2:
            return CommandResult(text="选项已失效，请重新描述需求。")
        pending = self._llm_pending_choices.get(user_id) or {}
        if pending.get("token") != params[0]:
            return CommandResult(text="选项已失效，请重新描述需求。")
        try:
            index = int(params[1])
            option = pending["options"][index]
        except (ValueError, IndexError, TypeError):
            return CommandResult(text="选项已失效，请重新描述需求。")
        self._llm_pending_choices.pop(user_id, None)
        label = str(option.get("label") or "")
        return await self.handle_llm_chat(label, user_id, rich_text=False, rich_message=False)

    async def cb_llm_confirm(self, params, user_id: str, channel, **kwargs) -> CommandResult:
        """消费渠道写操作确认，并执行已保存的单次动作。"""
        if len(params) < 2:
            return CommandResult(text="确认已失效，请重新发起操作。")
        pending = self._llm_pending_confirms.get(user_id) or {}
        if pending.get("token") != params[0]:
            return CommandResult(text="确认已失效，请重新发起操作。")
        approved = params[1] == "yes"
        self._llm_pending_confirms.pop(user_id, None)
        if not approved:
            return CommandResult(text="已取消本次操作。")
        username = await self.config_service.get("assistantChannelUsername", "admin")
        token, _, _ = await security.create_access_token(data={"sub": username})
        context = self._agent_context_extra()
        context.update({"authorization": f"Bearer {token}", "confirmed_action": True})
        result = await registry.execute(pending["name"], pending["arguments"], context)
        if result.get("error"):
            return CommandResult(text=f"操作失败：{result['error']}")
        task_id = result.get("taskId")
        if task_id:
            return CommandResult(text=f"操作已提交，任务 ID：{task_id}\n请选择下一步：", reply_markup=[[
                {"text": "查看当前进度", "callback_data": f"llm_task:{task_id}"},
                {"text": "稍后再查", "callback_data": "llm_task:later"},
            ]])
        return CommandResult(text="操作已完成。")

    async def cb_llm_task(self, params, user_id: str, channel, **kwargs) -> CommandResult:
        """在当前渠道查询 Agent 提交的后台任务。"""
        if not params or params[0] == "later":
            return CommandResult(text="好的，任务仍会在后台继续执行。")
        status = await registry.execute("get_task_status", {"taskId": params[0]}, self._agent_context_extra())
        return CommandResult(text=f"任务当前状态：{status}")

    async def handle_llm_chat(
        self,
        text: str,
        user_id: str,
        stream_callback: Optional[Callable[[str], Awaitable[None]]] = None,
        images: Optional[List[str]] = None,
        rich_text: bool = False,
        rich_message: bool = False,
    ) -> Optional[CommandResult]:
        """
        用御坂 Agent 处理一句自然语言。
        - stream_callback：若提供，则每积累一段增量就回调一次（供 Telegram 伪流式 edit）。
        - images：图片 data URL 列表（渠道收到图片/贴纸时传入），需 vision 模型才生效。
        - rich_text：目标渠道是否支持 Markdown 渲染。由渠道按自身
          ChannelCapability.RICH_TEXT 传入；默认 False 走纯文本，
          这样新接入的渠道不会因为漏传参数就把 Markdown 符号裸露给用户。
        - rich_message：目标渠道是否走结构化富消息（Telegram 的 sendRichMessage）。
          由渠道按 ChannelCapability.RICH_MESSAGE 并结合运行时可用性传入；
          默认 False，未支持的渠道行为不变。
        - 返回最终 CommandResult（完整文本），供渠道兜底一次性发送。
        """
        if not await self.is_llm_chat_enabled():
            return None

        ai_service = getattr(self, "ai_service", None) or get_ai_service()
        agent = AssistantAgent(
            self.config_service,
            session_factory=self._session_factory,
            on_metric_record=ai_service.persist_metric,
        )
        context_extra = self._agent_context_extra()  # 渠道端也注入写工具依赖
        # 图片只挂在本轮 user 消息上；历史里不留图片，避免 token 随轮数膨胀
        current_turn = {"role": "user", "content": text}
        if images:
            current_turn["images"] = images
        history = self._get_llm_history(user_id) + [current_turn]

        reply = ""
        try:
            # is_channel=True：渠道侧会把贴纸/图片/引用翻译成方括号标注，需让模型知道这套约定。
            # supports_table=False：非富消息渠道实际使用的发送方式都没有表格语法——
            # Telegram 降级路径走 sendMessage（MarkdownV2/HTML 均无 table），企业微信与
            # Server酱走纯文本。输出表格后竖线只会原样堆叠，故统一禁用，改用「每条一段」列表。
            # rich_message=True 时该参数不生效：富消息的 markdown 与 GFM 兼容，表格原生支持。
            # 渠道写操作使用单用户绑定账号，并必须先经过本渠道一次性确认按钮。
            async for event in agent.stream(
                history, DEFAULT_PERSONA, context_extra,
                rich_text=rich_text, is_channel=True, supports_table=False,
                rich_message=rich_message,
                include_write_tools=True,
            ):
                etype = event.get("type")
                if etype == "delta":
                    reply += event.get("content", "")
                    if stream_callback:
                        await stream_callback(reply)
                elif etype == "tool" and event.get("status") == "running":
                    if stream_callback:
                        note = f"🔧 {event.get('label', '处理中')}…"
                        await stream_callback(reply + ("\n" + note if reply else note))
                elif etype == "choice":
                    buttons = self._save_llm_choice(user_id, event)
                    title = event.get("title") or "请选择下一步"
                    prompt = event.get("prompt") or "请选择一个选项："
                    reply = f"{title}\n{prompt}"
                    result = CommandResult(text=reply, reply_markup=buttons)
                    self._append_llm_history(user_id, "user", text)
                    self._append_llm_history(user_id, "assistant", reply)
                    return result
                elif etype == "confirm":
                    token = secrets.token_urlsafe(9)
                    self._llm_pending_confirms[user_id] = {
                        "token": token, "name": event.get("name"),
                        "arguments": event.get("arguments") or {},
                    }
                    reply = f"确认操作：{event.get('description') or event.get('label') or '执行此操作'}"
                    self._append_llm_history(user_id, "user", text)
                    self._append_llm_history(user_id, "assistant", reply)
                    return CommandResult(text=reply, reply_markup=[[
                        {"text": "确认执行", "callback_data": f"llm_confirm:{token}:yes"},
                        {"text": "取消", "callback_data": f"llm_confirm:{token}:no"},
                    ]])
                elif etype == "error":
                    reply = event.get("content") or "对话出错了"
                    break
        except Exception as e:  # noqa: BLE001
            logger.error(f"[渠道LLM] 处理失败 user={user_id}: {e}", exc_info=True)
            return CommandResult(text="御坂御坂遇到点问题，稍后再试吧。")

        reply = reply.strip() or "……（御坂御坂想不出该说什么）"
        # 写入内存历史（用户问 + 御坂答）
        self._append_llm_history(user_id, "user", text)
        self._append_llm_history(user_id, "assistant", reply)
        return CommandResult(text=reply)

    def clear_llm_history(self, user_id: str):
        """清除某用户的渠道 LLM 对话历史。"""
        self._llm_histories.pop(user_id, None)
