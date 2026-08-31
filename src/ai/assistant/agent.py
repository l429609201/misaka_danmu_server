"""
御坂助手 · Agent（ReAct 工具循环，P2）
------------------------------------------------------------
在纯对话基础上支持 function calling：
  模型 →(要调工具)→ 执行工具 → 结果回灌 → 再问模型 →…→ 最终回答（流式）

事件（yield dict）：
  {"type":"tool","name","label","status":"running|done"}  工具调用进度
  {"type":"delta","content"}   最终回答增量
  {"type":"done"}
  {"type":"error","content"}

P2 只启用只读工具（include_write=False），写类工具留到 P3。
依赖导入置于文件头部，避免循环依赖。
"""

import json
import logging
from typing import Any, AsyncGenerator, Dict, List

import httpx

from src.db import ConfigManager
from .personas import get_persona_prompt, DEFAULT_PERSONA
from ..ai_providers import get_provider_config
from .tools import registry

logger = logging.getLogger(__name__)
ai_responses_logger = logging.getLogger("ai_responses")  # 专用日志器，用于记录原始 AI 交互

_TIMEOUT = 120.0
_MAX_TOOL_ROUNDS = 8  # 最多工具调用轮数，防止无限循环（三段式导入需 搜索→查分集→导入 多步只读调用）


class AssistantAgent:
    """支持工具调用的御坂助手 Agent。"""

    def __init__(self, config_manager: ConfigManager, session_factory=None):
        self.config_manager = config_manager
        self.session_factory = session_factory
        self.logger = logging.getLogger(self.__class__.__name__)

    async def _load_ai_config(self) -> Dict[str, str]:
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
        log_raw = (await self.config_manager.get("aiLogRawResponse", "false")).lower() == "true"

        if not base_url:
            cfg = get_provider_config(provider) or {}
            base_url = cfg.get("defaultBaseUrl", "")

        # 代理配置
        proxy_url = ""
        if proxy_enabled:
            proxy_url = await self.config_manager.get("proxyUrl", "")

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
            "log_raw_response": log_raw,  
        }

    def _build_messages(self, history: List[Dict[str, Any]], persona_key: str) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": get_persona_prompt(persona_key or DEFAULT_PERSONA)}
        ]
        for m in history:
            role = m.get("role")
            content = m.get("content", "")
            images = m.get("images") or []
            if role not in ("user", "assistant"):
                continue
            if not content and not images:
                continue
            # user 带图片 → 组装成 OpenAI vision 多模态 content 数组（需 vision 模型）
            if role == "user" and images:
                parts: List[Dict[str, Any]] = []
                if content:
                    parts.append({"type": "text", "text": content})
                for url in images:
                    parts.append({"type": "image_url", "image_url": {"url": url}})
                messages.append({"role": role, "content": parts})
            else:
                messages.append({"role": role, "content": content})
        return messages

    @staticmethod
    def _log_raw(enabled: bool, section: str, content: Any) -> None:
        """按开关将助手的原始交互写入 ai_responses.log。

        Args:
            enabled: 是否启用记录（来自 aiLogRawResponse 配置）
            section: 段落标题，如「请求 messages」「响应」「工具执行」
            content: 要记录的内容，dict/list 会序列化为 JSON
        """
        if not enabled:
            return
        try:
            if isinstance(content, (dict, list)):
                body = json.dumps(content, ensure_ascii=False, indent=2)
            else:
                body = str(content)
            ai_responses_logger.info(f"[御坂助手] {section}:\n{body}\n{'=' * 80}")
        except Exception as e:  # noqa: BLE001
            # 日志记录本身失败不应影响主流程
            logger.warning(f"记录助手原始交互失败: {e}")

    async def _post(self, cfg: Dict[str, str], payload: Dict[str, Any]) -> httpx.Response:
        url = f"{cfg['base_url']}/chat/completions"
        headers = {
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
        }
        # 使用配置的超时与代理
        timeout = httpx.Timeout(cfg.get("timeout", _TIMEOUT), connect=10.0)
        async with httpx.AsyncClient(timeout=timeout, proxy=cfg.get("proxy_url")) as client:
            return await client.post(url, headers=headers, json=payload)

    async def stream(
        self,
        history: List[Dict[str, str]],
        persona_key: str = DEFAULT_PERSONA,
        context_extra: Dict[str, Any] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """ReAct 主循环：先用非流式判断工具调用，最终回答用流式输出。

        写类工具需二次确认：遇到写工具的 tool_call 时不直接执行，
        而是产出 confirm 事件并结束本轮，等前端把用户确认后的选择作为
        新一轮 user 消息带回（P3 采用"确认即在对话里回一句同意"的轻量方案）。
        """
        cfg = await self._load_ai_config()
        if not (cfg["api_key"] and cfg["model"] and cfg["base_url"]):
            yield {"type": "error", "content": "AI 未配置：请先在设置中填写 API Key、Base URL 与模型。"}
            return

        messages = self._build_messages(history, persona_key)
        tools = registry.openai_tools(include_write=True)  # P3 暴露只读+写工具
        context = {"session_factory": self.session_factory}
        if context_extra:
            context.update(context_extra)

        log_raw = cfg.get("log_raw_response", False)

        try:
            for _round in range(_MAX_TOOL_ROUNDS):
                self._log_raw(log_raw, f"第 {_round + 1} 轮请求 messages", messages)

                resp = await self._post(cfg, {
                    "model": cfg["model"],
                    "messages": messages,
                    "tools": tools,
                    "tool_choice": "auto",
                    "stream": False,
                    "temperature": cfg["temperature"],
                    "top_p": cfg["top_p"],
                })
                if resp.status_code != 200:
                    detail = resp.text[:300]
                    self.logger.error(f"AI 工具轮请求失败 {resp.status_code}: {detail}")
                    self._log_raw(log_raw, f"第 {_round + 1} 轮请求失败（HTTP {resp.status_code}）", detail)
                    yield {"type": "error", "content": f"AI 请求失败（{resp.status_code}）"}
                    return

                choice = (resp.json().get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                tool_calls = msg.get("tool_calls") or []

                self._log_raw(log_raw, f"第 {_round + 1} 轮模型响应", msg)

                if not tool_calls:
                    async for ev in self._stream_final(cfg, messages):
                        yield ev
                    return

                # 写操作确认已改为自然对话方式：由 persona 提示词约束 AI
                # 先说明意图并等用户同意，不再产出 confirm 事件打断前端
                messages.append({
                    "role": "assistant",
                    "content": msg.get("content") or "",
                    "tool_calls": tool_calls,
                })
                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    name = fn.get("name") or ""
                    label = self._tool_label(name)
                    yield {"type": "tool", "name": name, "label": label, "status": "running"}
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    self._log_raw(log_raw, f"工具调用 {name} 入参", args)
                    result = await registry.execute(name, args, context)
                    self._log_raw(log_raw, f"工具调用 {name} 返回", result)
                    yield {"type": "tool", "name": name, "label": label, "status": "done"}
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "content": json.dumps(result, ensure_ascii=False),
                    })

            # 超过最大轮数仍未收敛 → 强制生成一次最终回答
            async for ev in self._stream_final(cfg, messages):
                yield ev
        except httpx.TimeoutException:
            yield {"type": "error", "content": "AI 响应超时，请稍后重试。"}
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"御坂 Agent 异常: {e}", exc_info=True)
            yield {"type": "error", "content": "对话出错了，请稍后重试。"}

    @staticmethod
    def _tool_label(name: str) -> str:
        """取工具的中文动作标签，供前端展示"御坂正在…"。"""
        tool = registry.get(name)
        return (tool.running_label if tool and tool.running_label else f"调用 {name}")

    async def _stream_final(
        self, cfg: Dict[str, str], messages: List[Dict[str, Any]]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """最终回答用流式输出（不再带 tools，纯生成文本）。"""
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
        self._log_raw(log_raw, "最终回答请求 messages", messages)
        collected: List[str] = []  # 收集流式片段，用于完整记录最终回答

        timeout = httpx.Timeout(cfg.get("timeout", _TIMEOUT), connect=10.0)
        async with httpx.AsyncClient(timeout=timeout, proxy=cfg.get("proxy_url")) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    detail = body.decode('utf-8', 'ignore')[:300]
                    self.logger.error(f"AI 最终流式失败 {resp.status_code}: {detail}")
                    self._log_raw(log_raw, f"最终回答请求失败（HTTP {resp.status_code}）", detail)
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
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    piece = (choices[0].get("delta") or {}).get("content")
                    if piece:
                        collected.append(piece)
                        yield {"type": "delta", "content": piece}

        self._log_raw(log_raw, "最终回答（完整）", "".join(collected))
        yield {"type": "done"}
