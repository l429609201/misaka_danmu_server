"""
御坂助手 · Agent（ReAct 工具循环，P2）
------------------------------------------------------------
在纯对话基础上支持 function calling：
  模型 →(要调工具)→ 执行工具 → 结果回灌 → 再问模型 →…→ 最终回答（流式）

事件（yield dict）：
  {"type":"round","round_id","status":"running|done|error","started_at","elapsed_ms"}
  thinking/tool/delta 均携带 round_id；round 结束不代表整个对话结束。
  {"type":"thinking","status":"running|done","started_at","elapsed_ms"}
  {"type":"tool","tool_id","label","count","status":"running|done|error"}
  {"type":"choice","title","prompt","options"} 终止本轮等待用户选择
  {"type":"delta","content"} 最终回答增量
  {"type":"done"} / {"type":"error","content"}

普通写工具只发确认事件；管理员本轮已授权的代码修复可自主执行。
依赖导入置于文件头部，避免循环依赖。
"""

import json
import logging
import re
import time
from uuid import uuid4
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, List, Optional
from datetime import datetime

import httpx

from src.utils.assistant_protocol_text import ProtocolTextFilter, sanitize_protocol_text
from src.services.config_service import ConfigService
from src.services.assistant_code_service import code_authorized, code_write_authorized
from .prompt_loader import get_coding_prompt
from .personas import get_persona_prompt, DEFAULT_PERSONA
from ..ai_providers import get_provider_config
from .api_gateway import ConfirmationMode, resolve_api_operation
from .security_gateway import contains_forbidden_control_content
from .mcp import McpManager, McpToolSpec
from .tools import registry
from ..ai_metrics import AIMetricsCollector, AICallMetrics

logger = logging.getLogger(__name__)
ai_responses_logger = logging.getLogger("ai_responses")  # 专用日志器，用于记录原始 AI 交互

_TIMEOUT = 120.0
_MAX_TOOL_ROUNDS = 8  # 最多工具调用轮数，防止无限循环（三段式导入需 搜索→查分集→导入 多步只读调用）
_CHOICE_SECRET = re.compile(r"api[_\s-]?key|access[_\s-]?token|authorization|password|passwd|secret|cookie|密钥|令牌|密码", re.I)
_CHOICE_TOOL = {
    "type": "function",
    "function": {
        "name": "ask_user_choice",
        "description": "需要用户在几个明确选项间选择时终止本轮并等待选择；不是写操作确认。",
        "parameters": {
            "type": "object", "required": ["title", "prompt", "options"],
            "properties": {
                "title": {"type": "string"}, "prompt": {"type": "string"},
                "options": {"type": "array", "minItems": 2, "maxItems": 8, "items": {
                    "type": "object", "required": ["id", "label"],
                    "properties": {"id": {"type": "string"}, "label": {"type": "string"},
                                   "description": {"type": "string"}},
                }},
            },
        },
    },
}


class AssistantAgent:
    """支持工具调用的御坂助手 Agent。"""

    def __init__(
        self,
        config_service: ConfigService,
        session_factory=None,
        on_metric_record: Optional[Callable[[AICallMetrics], Awaitable[None]]] = None,
    ) -> None:
        self.config_service = config_service
        self.session_factory = session_factory
        self.logger = logging.getLogger(self.__class__.__name__)
        # 外部 MCP 服务器工具管理器（工具不入静态 registry，按前缀路由）
        self.mcp = McpManager(config_service)
        # 指标只通过事件回调交给上层服务持久化，Agent 不直接接触数据库。
        self.metrics = AIMetricsCollector(on_record=on_metric_record)

    async def _load_ai_config(self) -> Dict[str, str]:
        provider = await self.config_service.get("aiProvider", "deepseek")
        api_key = await self.config_service.get("aiApiKey", "")
        base_url = await self.config_service.get("aiBaseUrl", "")
        model = await self.config_service.get("aiModel", "")

        # 御坂助手高级 LLM 参数
        temperature = float(await self.config_service.get("assistantTemperature", "0.7"))
        max_tokens = int(await self.config_service.get("assistantMaxTokens", "2000"))
        top_p = float(await self.config_service.get("assistantTopP", "0.9"))
        presence_penalty = float(await self.config_service.get("assistantPresencePenalty", "0.0"))
        frequency_penalty = float(await self.config_service.get("assistantFrequencyPenalty", "0.0"))
        timeout = int(await self.config_service.get("assistantTimeout", "120"))
        proxy_enabled = (await self.config_service.get("assistantProxyEnabled", "false")).lower() == "true"
        log_raw = (await self.config_service.get("aiLogRawResponse", "false")).lower() == "true"

        if not base_url:
            cfg = get_provider_config(provider) or {}
            base_url = cfg.get("defaultBaseUrl", "")

        # 代理配置
        proxy_url = ""
        if proxy_enabled:
            proxy_url = await self.config_service.get("proxyUrl", "")

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

    def _build_messages(
        self,
        history: List[Dict[str, Any]],
        persona_key: str,
        rich_text: bool = True,
        is_channel: bool = False,
        supports_table: bool = True,
        rich_message: bool = False,
    ) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = [
            {
                "role": "system",
                "content": get_persona_prompt(
                    persona_key or DEFAULT_PERSONA,
                    rich_text=rich_text,
                    is_channel=is_channel,
                    supports_table=supports_table,
                    rich_message=rich_message,
                ),
            }
        ]
        for m in history:
            role = m.get("role")
            content = m.get("content", "")
            if role == "assistant" and isinstance(content, str):
                # 不把以前持久化的协议泄漏继续送回模型诱发重放。
                content = sanitize_protocol_text(content)[0]
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
        rich_text: bool = True,
        is_channel: bool = False,
        supports_table: bool = True,
        rich_message: bool = False,
        include_write_tools: bool = True,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """为模型请求、公开旁白和工具阶段附加稳定轮次边界。"""
        active: Optional[Dict[str, Any]] = None
        round_start = 0.0
        round_failed = False
        async for event in self._stream_rounds(
            history, persona_key, context_extra, rich_text, is_channel,
            supports_table, rich_message, include_write_tools,
        ):
            etype = event.get("type")
            opens = etype == "thinking" and event.get("status") == "running"
            terminal = etype in {"done", "error", "choice", "confirm", "_round_end"}
            if active and (opens or terminal):
                status = "error" if round_failed or etype == "error" or event.get("status") == "error" else "done"
                yield {**active, "status": status,
                       "elapsed_ms": max(0, int((time.monotonic() - round_start) * 1000))}
                if terminal:
                    event = {**event, "round_id": active["round_id"]}
                active = None
            if opens:
                round_start = time.monotonic()
                round_failed = False
                active = {"type": "round", "round_id": uuid4().hex,
                          "status": "running", "started_at": event["started_at"]}
                yield dict(active)
            if etype == "_round_end":
                continue
            if active:
                event = {**event, "round_id": active["round_id"]}
                if etype == "tool" and event.get("status") == "error":
                    round_failed = True
            yield event
        if active:
            # 提前结束但无终止事件时，不能留下永久运行中的轮次。
            yield {**active, "status": "error",
                   "elapsed_ms": max(0, int((time.monotonic() - round_start) * 1000))}

    async def _stream_rounds(
        self,
        history: List[Dict[str, str]],
        persona_key: str = DEFAULT_PERSONA,
        context_extra: Dict[str, Any] = None,
        rich_text: bool = True,
        is_channel: bool = False,
        supports_table: bool = True,
        rich_message: bool = False,
        include_write_tools: bool = True,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """非流式选择工具，最终回答流式输出；写操作只发确认事件。

        外部 MCP 当前不进入模型工具目录，避免绕过站内流控与密钥边界。

        :param rich_text: 目标渠道是否支持 Markdown 渲染（决定 system prompt 里的排版约束）。
            默认 True 兼容 Web 端；纯文本渠道（企业微信/Server酱）需显式传 False。
        :param is_channel: 是否来自通知渠道对话（决定是否注入方括号标注说明）。
        :param supports_table: 富文本渠道是否支持 Markdown 表格。仅在 rich_message=False
            时生效；无表格语法的发送方式需传 False。
        :param rich_message: 是否走结构化富消息（Telegram sendRichMessage，GFM 兼容）。
            True 时开放表格/标题/任务列表/公式等完整排版能力。
        :param include_write_tools: 仅 Web 确认卡渠道允许写工具；通知渠道必须传 False。
        """
        cfg = await self._load_ai_config()
        if not (cfg["api_key"] and cfg["model"] and cfg["base_url"]):
            yield {"type": "error", "content": "AI 未配置：请先在设置中填写 API Key、Base URL 与模型。"}
            return

        messages = self._build_messages(
            history, persona_key, rich_text=rich_text,
            is_channel=is_channel, supports_table=supports_table,
            rich_message=rich_message,
        )
        if not include_write_tools:
            messages[0]["content"] += "\n\n当前渠道只有只读工具，不支持确认卡或代办写操作；需要修改数据时请引导用户到 Web 界面。"
        # 提示词、目录和执行阶段必须复用同一份已初始化的可信上下文。
        context = {"session_factory": self.session_factory}
        if context_extra:
            context.update(context_extra)
        allow_code = include_write_tools and code_authorized(context)
        allow_code_write = allow_code and code_write_authorized(context)
        if allow_code:
            messages[0]['content'] += '\n\n' + get_coding_prompt()
            messages[0]['content'] += ('\n本轮管理员已明确授权自主代码修复，可应用通过验证的补丁而不重复弹卡；不授权其他业务写操作或部署。'
                                       if allow_code_write else
                                       '\n本轮仅诊断，真实源码应用仍需管理员确认卡，不能将用户聊天中的同意当作程序授权。')
        tools = [*registry.openai_tools(include_write=include_write_tools, include_code=allow_code), _CHOICE_TOOL]
        # 外部 MCP 的“只读”标签不可证明服务端不会读取流控配置；目前不向模型开放。
        mcp_specs: List[McpToolSpec] = []

        log_raw = cfg.get("log_raw_response", False)
        tool_count = 0
        protocol_retries = 0

        try:
            for _round in range(20 if allow_code_write else _MAX_TOOL_ROUNDS):
                started_at = datetime.now().astimezone().isoformat()
                thinking_start = time.monotonic()
                yield {"type": "thinking", "status": "running", "started_at": started_at, "elapsed_ms": 0}
                self._log_raw(log_raw, f"第 {_round + 1} 轮请求 messages", messages)

                start_time = datetime.now()
                try:
                    resp = await self._post(cfg, {
                        "model": cfg["model"],
                        "messages": messages,
                        "tools": tools,
                        "tool_choice": "auto",
                        "stream": False,
                        "temperature": cfg["temperature"],
                        "top_p": cfg["top_p"],
                    })
                except Exception:
                    yield {"type": "thinking", "status": "done", "started_at": started_at,
                           "elapsed_ms": max(0, int((time.monotonic() - thinking_start) * 1000))}
                    raise
                duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
                yield {"type": "thinking", "status": "done", "started_at": started_at,
                       "elapsed_ms": max(0, int((time.monotonic() - thinking_start) * 1000))}

                if resp.status_code != 200:
                    detail = resp.text[:300]
                    self.logger.error(f"AI 工具轮请求失败 {resp.status_code}: {detail}")
                    self._log_raw(log_raw, f"第 {_round + 1} 轮请求失败（HTTP {resp.status_code}）", detail)
                    # 记录失败的 AI 调用
                    self.metrics.record(AICallMetrics(
                        timestamp=datetime.now(),
                        method="assistant_tool_call",
                        success=False,
                        duration_ms=duration_ms,
                        tokens_used=0,
                        model=cfg["model"],
                        error=f"HTTP {resp.status_code}: {detail}",
                        cache_hit=False
                    ))
                    yield {"type": "error", "content": f"AI 请求失败（{resp.status_code}）"}
                    return

                resp_json = resp.json()
                choice = (resp_json.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                tool_calls = msg.get("tool_calls") or []

                # 提取 token 使用量
                usage = resp_json.get("usage") or {}
                tokens_used = usage.get("total_tokens", 0)

                self._log_raw(log_raw, f"第 {_round + 1} 轮模型响应", msg)

                # 记录成功的 AI 工具调用
                self.metrics.record(AICallMetrics(
                    timestamp=datetime.now(),
                    method="assistant_tool_call",
                    success=True,
                    duration_ms=duration_ms,
                    tokens_used=tokens_used,
                    model=cfg["model"],
                    error=None,
                    cache_hit=False
                ))

                if not tool_calls:
                    content, leaked = sanitize_protocol_text(msg.get("content") or "")
                    if leaked:
                        # 文本协议从不执行；保留原请求和工具目录，只允许模型纠正为结构化调用。
                        protocol_retries += 1
                        if protocol_retries <= 2:
                            messages.append({"role": "system", "content":
                                "上次响应出现了无效的工具协议文本，未执行其中任何请求。"
                                "如需工具，请使用响应的结构化 tool_calls 字段，禁止在正文输出 DSML 或历史工具序列化；"
                                "否则直接回答用户，不能把未执行的操作说成已完成。"})
                            yield {"type": "_round_end", "status": "error"}
                            continue
                        if content.strip():
                            yield {"type": "delta", "content": content}
                        yield {"type": "delta", "content":
                               "\n\n本轮未完成：模型未返回有效的结构化工具调用，文本中的工具请求未执行。"}
                        yield {"type": "_round_end", "status": "error"}
                        yield {"type": "done"}
                        return
                    if content.strip():
                        # 已有正常答案无需再次请求，避免无工具重生成丢失原答案。
                        yield {"type": "delta", "content": content}
                        if self._has_failed_tool_results(messages):
                            yield {"type": "delta", "content":
                                   "\n\n本轮存在工具查询失败，相关信息未能核实，不能据此认定任务已完整完成。"}
                        yield {"type": "done"}
                        return
                    async for ev in self._stream_final(cfg, messages):
                        yield ev
                    return

                narration = sanitize_protocol_text(msg.get("content") or "")[0]
                if narration.strip():
                    # 工具轮的合法旁白先公开，协议和工具返回仍只留在内部历史。
                    yield {"type": "delta", "content": narration}
                messages.append({
                    "role": "assistant",
                    "content": narration,
                    "tool_calls": tool_calls,
                })
                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    name = fn.get("name") or ""
                    label = self._tool_label(name, mcp_specs)
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                        if not isinstance(args, dict):
                            args = {}
                    except json.JSONDecodeError:
                        args = {}
                    if name == "ask_user_choice":
                        choice = self._validated_choice(args)
                        if choice is None:
                            yield {"type": "error", "content": "模型返回的选择选项无效，请重试。"}
                        else:
                            yield {"type": "choice", **choice}
                        return
                    if name.startswith('code_') and not allow_code:
                        result = {'ok': False, 'error': '当前请求未开放代码工具'}
                    elif McpManager.is_mcp_tool_name(name):
                        result = {"ok": False, "error": "外部 MCP 工具当前不可用"}
                    else:
                        tool = registry.get(name)
                        operation = resolve_api_operation(args.get("operation_id")) if name == "call_api" else None
                        needs_confirmation = (
                            operation.required_confirmation is ConfirmationMode.REQUIRED
                            if operation is not None else
                            tool.required_confirmation is ConfirmationMode.REQUIRED if tool else False
                        )
                        if name in {'code_apply_patch', 'code_rollback_patch'} and allow_code_write:
                            needs_confirmation = False
                        preview_error = None
                        if needs_confirmation and include_write_tools and name in {'code_apply_patch', 'code_rollback_patch'}:
                            preview_result = await registry.execute('code_patch_status', {'draft_id': args.get('draft_id')}, context)
                            if not preview_result.get('ok', False):
                                preview_error = preview_result
                        if preview_error is not None:
                            result = preview_error
                        elif needs_confirmation:
                            if not include_write_tools:
                                result = {"ok": False, "error": "当前渠道不允许写操作"}
                            else:
                                yield {
                                    "type": "confirm", "name": name, "label": label,
                                    "description": (operation.summary if operation else tool.description),
                                    "arguments": args,
                                    "irreversible": (operation.effect.value == "destructive_write" if operation else tool.irreversible),
                                }
                                return
                        else:
                            tool_id = uuid4().hex
                            tool_count += 1
                            count = tool_count
                            yield {"type": "tool", "tool_id": tool_id, "name": name,
                                   "label": label, "count": count, "status": "running"}
                            try:
                                result = await registry.execute(name, args, context)
                            except Exception:
                                yield {"type": "tool", "tool_id": tool_id, "name": name,
                                       "label": label, "count": count, "status": "error"}
                                raise
                            failure = {} if result.get('ok', False) else self._tool_failure_summary(result)
                            yield {"type": "tool", "tool_id": tool_id, "name": name,
                                   "label": label, "count": count,
                                   "status": "done" if result.get("ok", False) else "error", **failure}
                    self._log_raw(log_raw, f"工具调用 {name} 返回", result)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                yield {"type": "_round_end"}

            # 最终汇报独立分轮，等待态持续到流式结束。
            started_at = datetime.now().astimezone().isoformat()
            yield {"type": "thinking", "status": "running", "started_at": started_at, "elapsed_ms": 0}
            # 预算耗尽必须明确未完成；最终生成只负责总结已有证据。
            yield {"type": "delta", "content": "本轮未完成：工具调用轮数已达上限，后续请求未执行。\n\n"}
            final_start = time.monotonic()
            async for ev in self._stream_final(cfg, [*messages, {"role": "system", "content":
                    "工具轮数预算已耗尽，任务未完成。明确说明未完成部分，禁止宣称全部完成。"}]):
                if ev.get("type") in {"done", "error"}:
                    yield {"type": "thinking", "status": "done", "started_at": started_at,
                           "elapsed_ms": max(0, int((time.monotonic() - final_start) * 1000))}
                yield ev
        except httpx.TimeoutException:
            yield {"type": "error", "content": "AI 响应超时，请稍后重试。"}
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"御坂 Agent 异常: {e}", exc_info=True)
            yield {"type": "error", "content": "对话出错了，请稍后重试。"}

    @staticmethod
    def _has_failed_tool_results(messages: List[Dict[str, Any]]) -> bool:
        """识别未成功的工具结果，复用已有正文时仍保留证据不足提示。"""
        for message in messages:
            if message.get('role') != 'tool':
                continue
            try:
                result = json.loads(message.get('content') or '{}')
            except (ValueError, TypeError):
                return True
            if not isinstance(result, dict) or result.get('ok') is not True:
                return True
        return False

    @staticmethod
    def _tool_failure_summary(result: Dict[str, Any]) -> Dict[str, str]:
        """只公开固定错误分类，不暴露工具返回的任意异常或运行数据。"""
        error = result.get('error')
        error = error if isinstance(error, str) else ''
        if '白名单' in error:
            return {'error_code': 'unsupported_config', 'error_message': '配置键不在可用清单，请按工具参数选择'}
        if '禁止' in error or '不允许' in error or '不可访问' in error:
            return {'error_code': 'access_denied', 'error_message': '该查询不在助手允许访问的范围'}
        if '未初始化' in error or '管理器不可用' in error:
            return {'error_code': 'service_unavailable', 'error_message': '所需服务尚不可用'}
        if '参数' in error or '缺少' in error or '必须' in error:
            return {'error_code': 'invalid_arguments', 'error_message': '查询参数不符合工具要求'}
        return {'error_code': 'tool_failed', 'error_message': '查询未完成，当前信息未能核实'}

    @staticmethod
    def _validated_choice(args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """仅允许有限长度的展示字段进入选择事件。"""
        if contains_forbidden_control_content(args) or _CHOICE_SECRET.search(json.dumps(args, ensure_ascii=False)):
            return None
        title, prompt, options = args.get("title"), args.get("prompt"), args.get("options")
        if not isinstance(title, str) or not isinstance(prompt, str) or not isinstance(options, list):
            return None
        if not (title.strip() and prompt.strip() and 2 <= len(options) <= 8):
            return None
        clean = []
        seen = set()
        for option in options:
            if not isinstance(option, dict):
                return None
            oid, label = option.get("id"), option.get("label")
            if (not isinstance(oid, str) or not isinstance(label, str) or
                    not oid.strip() or not label.strip() or len(oid) > 64 or oid in seen):
                return None
            seen.add(oid)
            clean.append({"id": oid, "label": label[:120],
                          "description": str(option.get("description") or "")[:300]})
        return {"title": title[:120], "prompt": prompt[:1000], "options": clean}

    @staticmethod
    def _tool_label(name: str, mcp_specs: Optional[List[McpToolSpec]] = None) -> str:
        """取工具的中文动作标签，供前端展示"御坂正在…"。"""
        tool = registry.get(name)
        if tool and tool.running_label:
            return tool.running_label
        # MCP 工具没有 running_label，用「服务器 · 工具名」代替裸露的带前缀名
        for spec in mcp_specs or []:
            if spec.agent_tool_name == name:
                return f"调用 {spec.server_name} · {spec.original_name}"
        return f"调用 {name}"

    async def _stream_final(
        self, cfg: Dict[str, str], messages: List[Dict[str, Any]]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """最终回答用流式输出（不再带 tools，纯生成文本）。"""
        incomplete = False
        messages = [*messages, {'role': 'system', 'content':
            '工具阶段已结束。只汇报已经执行并有结果的工具调用；不要继续请求工具，'
            '不要输出 DSML、tool_calls 或历史工具序列化。'}]
        for message in messages:
            if message.get('role') != 'tool':
                continue
            try:
                result = json.loads(message.get('content') or '{}')
            except (ValueError, TypeError):
                incomplete = True
                continue
            if not isinstance(result, dict) or result.get('ok') is not True:
                incomplete = True
        if incomplete:
            messages = [*messages, {'role': 'system', 'content':
                        '本轮存在工具查询失败，证据不完整。最终汇报必须说明未能核实的部分，'
                        '分开已核实事实与待验证建议，不得宣称已完整排查或已测量搜索速度。'}]
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
        collected: List[str] = []  # 仅记录经过协议过滤的公开正文
        output_filter = ProtocolTextFilter()

        start_time = datetime.now()
        total_tokens = 0  # 累计 token 使用量
        completed = False

        timeout = httpx.Timeout(cfg.get("timeout", _TIMEOUT), connect=10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout, proxy=cfg.get("proxy_url")) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        detail = body.decode('utf-8', 'ignore')[:300]
                        self.logger.error(f"AI 最终流式失败 {resp.status_code}: {detail}")
                        self._log_raw(log_raw, f"最终回答请求失败（HTTP {resp.status_code}）", detail)
                        duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
                        # 记录失败的最终回答调用
                        self.metrics.record(AICallMetrics(
                            timestamp=datetime.now(),
                            method="assistant_final_answer",
                            success=False,
                            duration_ms=duration_ms,
                            tokens_used=0,
                            model=cfg["model"],
                            error=f"HTTP {resp.status_code}: {detail}",
                            cache_hit=False
                        ))
                        yield {"type": "error", "content": f"AI 请求失败（{resp.status_code}）"}
                        return
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[len("data:"):].strip()
                        if data == "[DONE]":
                            completed = True
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError as exc:
                            raise ValueError("上游 AI 返回无效的流式数据") from exc
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue

                        # 提取 token 使用量（某些提供商在流式响应中包含）
                        usage = chunk.get("usage")
                        if usage and usage.get("total_tokens"):
                            total_tokens = usage.get("total_tokens", 0)

                        delta = choices[0].get("delta") or {}
                        if delta.get("tool_calls"):
                            # 最终阶段不允许新调用，结构化请求也只标记未完成，绝不执行。
                            output_filter.blocked = True
                        piece = delta.get("content")
                        if piece:
                            safe_piece = output_filter.feed(piece)
                            if safe_piece:
                                collected.append(safe_piece)
                                yield {"type": "delta", "content": safe_piece}

            if not completed:
                raise ValueError("上游 AI 流式回答未完整结束")
            tail = output_filter.finish()
            if tail:
                collected.append(tail)
                yield {"type": "delta", "content": tail}
            if output_filter.blocked:
                notice = "\n\n本轮未完成：最终回答中的工具协议已屏蔽，其中的请求未执行。"
                collected.append(notice)
                yield {"type": "delta", "content": notice}
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            # 记录成功的最终回答调用
            self.metrics.record(AICallMetrics(
                timestamp=datetime.now(),
                method="assistant_final_answer",
                success=True,
                duration_ms=duration_ms,
                tokens_used=total_tokens,
                model=cfg["model"],
                error=None,
                cache_hit=False
            ))

            self._log_raw(log_raw, "最终回答（完整）", "".join(collected))
            yield {"type": "done"}
        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            # 记录异常的最终回答调用
            self.metrics.record(AICallMetrics(
                timestamp=datetime.now(),
                method="assistant_final_answer",
                success=False,
                duration_ms=duration_ms,
                tokens_used=0,
                model=cfg["model"],
                error=str(e),
                cache_hit=False
            ))
            raise
