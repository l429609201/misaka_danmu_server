"""
LLM 对话 Mixin — 通知渠道接入御坂 Agent
------------------------------------------------------------
当用户在渠道（Telegram/企业微信/SC3）发自然语言、且无活跃命令流程时，
转交御坂 AssistantAgent 处理。复用 Web 端同一套 Agent + 工具集 + 安全网关。

渠道端策略（P-渠道）：
- 工具能力与 Web 端对齐（含写类工具与 MCP 外部工具），风险确认由 agent
  在对话中自然完成，渠道侧不再维护挂起-回复的确认状态机。
- 流式：通过 stream_callback 把增量文本回吐给渠道层，
  支持 edit 的渠道（Telegram）做"伪流式"，不支持的攒完一次性发。
- 渠道会话历史用内存滑动窗口（每用户最近 N 轮），不落库，保持轻量。
"""

import logging
from typing import Callable, Dict, List, Optional, Awaitable

from src.notification.base import CommandResult
from src.ai.assistant import AssistantAgent, DEFAULT_PERSONA

logger = logging.getLogger(__name__)

# 每个渠道用户在内存里保留的最近对话轮数（user/bot 各算一条）
_LLM_HISTORY_LIMIT = 12


class LlmChatMixin:
    """为 NotificationService 提供御坂 LLM 对话能力（渠道端）。"""

    # 渠道 LLM 对话历史：{user_id: [{"role","content"}, ...]}（内存滑动窗口）
    _llm_histories: Dict[str, List[dict]] = {}

    def _agent_context_extra(self) -> dict:
        """写类工具执行所需的管理器（渠道端）。"""
        return {
            "task_manager": getattr(self, "task_manager", None),
            "scraper_manager": getattr(self, "scraper_manager", None),
            "rate_limiter": getattr(self, "rate_limiter", None),
            "scheduler_manager": getattr(self, "scheduler_manager", None),
            "metadata_manager": getattr(self, "metadata_manager", None),
            "ai_matcher_manager": getattr(self, "ai_matcher_manager", None),
            "title_recognition_manager": getattr(self, "title_recognition_manager", None),
            "config_manager": self.config_manager,
        }

    async def is_llm_chat_enabled(self) -> bool:
        """渠道 LLM 兜底是否可用：需 AI 已配置 + 开关开启。"""
        if not self.config_manager:
            return False
        enabled = (await self.config_manager.get("assistantChannelChatEnabled", "true")).lower() == "true"
        if not enabled:
            return False
        agent = AssistantAgent(self.config_manager, session_factory=self._session_factory)
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

        agent = AssistantAgent(self.config_manager, session_factory=self._session_factory)
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
            # include_write_tools=True：渠道端与 Web 端能力对齐（含写类工具与 MCP 外部工具），
            # 写操作的风险确认由 agent 在对话中自然完成，渠道侧不再维护挂起-回复状态机。
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
