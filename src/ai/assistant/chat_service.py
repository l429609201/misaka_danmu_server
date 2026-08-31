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
ai_responses_logger = logging.getLogger("ai_responses")  # 专用日志器，用于记录原始 AI 交互

# 默认 OpenAI 兼容 base_url 兜底（provider 未配置 baseUrl 时用其默认值）
_DEFAULT_TIMEOUT = 120.0


class AssistantChatService:
    """御坂助手流式对话服务（复用现有 AI provider 配置）"""

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.logger = logging.getLogger(self.__class__.__name__)

    async def _load_ai_config(self) -> Dict[str, str]:
        """读取 AI 配置（包含御坂助手专属的高级参数）。"""
        provider = await self.config_manager.get("aiProvider", "deepseek")
        api_key = await self.config_manager.get("aiApiKey", "")
        base_url = await self.config_manager.get("aiBaseUrl", "")
        model = await self.config_manager.get("aiModel", "")

        # 御坂助手高级 LLM 参数
        temperature = float(await self.config_manager.get("assistantTemperature", "0.7"))
        max_tokens = int(await self.config_manager.get("assistantMaxTokens", "2000"))
        top_p = float(await self.config_manager.get("assistantTopP", "0.9"))
        presence_penalty = float(await self.config_manager.get("assistantPresencePenalty", "0.0"))
        frequency_penalty = float(await self.config_manager.get("assistantFrequencyPenalty", "0.0"))
        timeout = int(await self.config_manager.get("assistantTimeout", "120"))
        proxy_enabled = (await self.config_manager.get("assistantProxyEnabled", "false")).lower() == "true"

        # base_url 缺省时回退到 provider 默认值
        if not base_url:
            cfg = get_provider_config(provider) or {}
            base_url = cfg.get("defaultBaseUrl", "")

        # 代理配置（复用全局 proxyUrl）
        proxy_url = ""
        if proxy_enabled:
            proxy_url = await self.config_manager.get("proxyUrl", "")

        log_raw = (await self.config_manager.get("aiLogRawResponse", "false")).lower() == "true"

        return {
            "provider": provider,
            "api_key": api_key,
            "base_url": base_url.rstrip("/"),
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": top_p,
            "presence_penalty": presence_penalty,
            "frequency_penalty": frequency_penalty,
            "timeout": timeout,
            "proxy_url": proxy_url if proxy_url else None,
            "log_raw_response": log_raw,  # 是否记录原始交互到 ai_responses.log
        }

    @staticmethod
    def _log_raw(enabled: bool, section: str, content) -> None:
        """按开关将纯对话的原始交互写入 ai_responses.log。

        Args:
            enabled: 是否启用记录（来自 aiLogRawResponse 配置）
            section: 段落标题，如「请求 messages」「完整回答」
            content: 要记录的内容，dict/list 会序列化为 JSON
        """
        if not enabled:
            return
        try:
            if isinstance(content, (dict, list)):
                body = json.dumps(content, ensure_ascii=False, indent=2)
            else:
                body = str(content)
            ai_responses_logger.info(f"[御坂助手·对话] {section}:\n{body}\n{'=' * 80}")
        except Exception as e:  # noqa: BLE001
            # 日志记录本身失败不应影响主流程
            logger.warning(f"记录助手对话原始交互失败: {e}")

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
            "temperature": cfg["temperature"],
            "max_tokens": cfg["max_tokens"],
            "top_p": cfg["top_p"],
            "presence_penalty": cfg["presence_penalty"],
            "frequency_penalty": cfg["frequency_penalty"],
        }

        log_raw = cfg.get("log_raw_response", False)
        self._log_raw(log_raw, "请求 messages", messages)
        collected: List[str] = []  # 收集流式片段，用于完整记录回答

        try:
            timeout = httpx.Timeout(cfg["timeout"], connect=10.0)
            async with httpx.AsyncClient(timeout=timeout, proxy=cfg["proxy_url"]) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        detail = body.decode("utf-8", "ignore")[:300]
                        self.logger.error(f"AI 流式请求失败 {resp.status_code}: {detail}")
                        self._log_raw(log_raw, f"请求失败（HTTP {resp.status_code}）", detail)
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
                            collected.append(piece)
                            yield {"type": "delta", "content": piece}

            self._log_raw(log_raw, "完整回答", "".join(collected))
            yield {"type": "done"}
        except httpx.TimeoutException:
            yield {"type": "error", "content": "AI 响应超时，请稍后重试。"}
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"AI 流式对话异常: {e}", exc_info=True)
            yield {"type": "error", "content": "对话出错了，请稍后重试。"}
