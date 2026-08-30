"""
御坂助手 · 流式对话服务（P1）
------------------------------------------------------------
复用项目现有 AI 配置（config: aiProvider/aiApiKey/aiBaseUrl/aiModel），
用 httpx 以 stream=True 调用 OpenAI 兼容的 /chat/completions，逐块产出增量文本。

P1 只做纯对话（无工具调用）。后续 P2+ 在此基础上扩展 ReAct 工具循环。

依赖导入统一置于文件头部，避免函数内导入与循环依赖（遵循项目规范）。
"""

import json
import logging
from typing import AsyncGenerator, Dict, List, Optional

import httpx

from src.db import ConfigManager
from .personas import get_persona_prompt, DEFAULT_PERSONA
from ..ai_providers import get_provider_config

logger = logging.getLogger(__name__)

# 默认 OpenAI 兼容 base_url 兜底（provider 未配置 baseUrl 时用其默认值）
_DEFAULT_TIMEOUT = 120.0


class AssistantChatService:
    """御坂助手流式对话服务（复用现有 AI provider 配置）"""

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.logger = logging.getLogger(self.__class__.__name__)

    async def _load_ai_config(self) -> Dict[str, str]:
        """读取现有 AI 配置（与 ai_matcher_manager 同一套 key）。"""
        provider = await self.config_manager.get("aiProvider", "deepseek")
        api_key = await self.config_manager.get("aiApiKey", "")
        base_url = await self.config_manager.get("aiBaseUrl", "")
        model = await self.config_manager.get("aiModel", "")

        # base_url 缺省时回退到 provider 默认值
        if not base_url:
            cfg = get_provider_config(provider) or {}
            base_url = cfg.get("defaultBaseUrl", "")

        return {
            "provider": provider,
            "api_key": api_key,
            "base_url": base_url.rstrip("/"),
            "model": model,
        }

    async def is_ready(self) -> bool:
        """对话是否可用：需已配置 apiKey 与 model。"""
        cfg = await self._load_ai_config()
        return bool(cfg["api_key"] and cfg["model"] and cfg["base_url"])

    def _build_messages(
        self, history: List[Dict[str, str]], persona_key: str
    ) -> List[Dict[str, str]]:
        """组装发送给模型的 messages：system 人设 + 历史对话。"""
        system_prompt = get_persona_prompt(persona_key or DEFAULT_PERSONA)
        messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
        # 仅保留合法的 user/assistant 文本消息
        for m in history:
            role = m.get("role")
            content = m.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        return messages

    async def stream_chat(
        self,
        history: List[Dict[str, str]],
        persona_key: str = DEFAULT_PERSONA,
    ) -> AsyncGenerator[Dict[str, str], None]:
        """
        流式对话核心。逐步 yield 事件字典：
          {"type": "delta", "content": "增量文本"}
          {"type": "done"}
          {"type": "error", "content": "错误说明"}
        """
        cfg = await self._load_ai_config()
        if not (cfg["api_key"] and cfg["model"] and cfg["base_url"]):
            yield {"type": "error", "content": "AI 未配置：请先在设置中填写 API Key、Base URL 与模型。"}
            return

        messages = self._build_messages(history, persona_key)
        url = f"{cfg['base_url']}/chat/completions"
        headers = {
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": cfg["model"],
            "messages": messages,
            "stream": True,
        }

        try:
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        detail = body.decode("utf-8", "ignore")[:300]
                        self.logger.error(f"AI 流式请求失败 {resp.status_code}: {detail}")
                        yield {"type": "error", "content": f"AI 请求失败（{resp.status_code}）"}
                        return

                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[len("data:"):].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        # 提取增量内容（OpenAI 兼容格式）
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            yield {"type": "delta", "content": piece}
            yield {"type": "done"}
        except httpx.TimeoutException:
            yield {"type": "error", "content": "AI 响应超时，请稍后重试。"}
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"AI 流式对话异常: {e}", exc_info=True)
            yield {"type": "error", "content": "对话出错了，请稍后重试。"}
