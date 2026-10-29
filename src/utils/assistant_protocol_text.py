"""助手协议文本的纯输出过滤；绝不将文本解释为可执行工具调用。"""

import json
import re
from typing import Any, Tuple

_TAG = re.compile(r"^<\s*(/?)\s*[|｜]\s*DSML\s*[|｜]\s*(/?)\s*(calls|invoke|parameter)\b[^>]*>$", re.I)
_PREFIXES = ("<|dsml|", "</|dsml|")
_HISTORY = re.compile(r'"(?:tool_calls|tool_call_id)"\s*:')


def _serialized_tool(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    calls = value.get("tool_calls")
    return (value.get("role") == "tool" and "tool_call_id" in value) or (
        isinstance(calls, list) and any(
            isinstance(call, dict) and isinstance(call.get("function"), dict)
            and "arguments" in call["function"] for call in calls
        )
    )


class ProtocolTextFilter:
    """跨任意 chunk 边界屏蔽 DSML 块与历史工具消息，保留普通正文和代码。"""

    def __init__(self) -> None:
        self._tag = ""
        self._stack: list[str] = []
        self._json = ""
        self._depth = 0
        self._quoted = False
        self._escaped = False
        self.blocked = False

    def _visible(self, char: str) -> str:
        # 仅完整的工具消息签名才隐藏 JSON，普通代码/JSON 原样返回。
        if not self._json and char != "{":
            return char
        self._json += char
        if self._quoted:
            if self._escaped:
                self._escaped = False
            elif char == "\\":
                self._escaped = True
            elif char == '"':
                self._quoted = False
        elif char == '"':
            self._quoted = True
        elif char == "{":
            self._depth += 1
        elif char == "}":
            self._depth -= 1
            if self._depth == 0:
                text, self._json = self._json, ""
                try:
                    hidden = _serialized_tool(json.loads(text))
                except ValueError:
                    hidden = False
                if hidden:
                    self.blocked = True
                    return ""
                return text
        return ""

    def feed(self, text: str) -> str:
        """输入一段正文，仅返回已确认不属于协议的文本。"""
        output: list[str] = []
        for char in text:
            if self._tag or char == "<":
                self._tag += char
                normalized = re.sub(r"\s", "", self._tag).replace("｜", "|").lower()
                if any(prefix.startswith(normalized) or normalized.startswith(prefix) for prefix in _PREFIXES):
                    if char != ">":
                        continue
                    match = _TAG.match(self._tag)
                    if match:
                        self.blocked = True
                        outer_slash, inner_slash, kind = match.groups()
                        closing = outer_slash or inner_slash
                        kind = kind.lower()
                        if closing:
                            if kind in self._stack:
                                # 截断/缺失内层结束标记时，外层结束仍能恢复周围普通正文。
                                self._stack = self._stack[:self._stack.index(kind)]
                        else:
                            self._stack.append(kind)
                        self._tag = ""
                        continue
                    # 已确认的 DSML 前缀但非法协议标记也不得进入正文。
                    self.blocked = True
                    self._tag = ""
                    continue
                pending, self._tag = self._tag, ""
                if char == "<" and len(pending) > 1:
                    # 重叠的 '<' 仍可能是下一段协议起点，不能提前发给客户端。
                    pending, self._tag = pending[:-1], "<"
                if not self._stack:
                    output.extend(self._visible(c) for c in pending)
            elif not self._stack:
                output.append(self._visible(char))
        return "".join(output)

    def finish(self) -> str:
        """流结束时丢弃未闭合协议及可识别的截断工具序列化。"""
        tail = ""
        if self._tag:
            normalized = re.sub(r"\s", "", self._tag).replace("｜", "|").lower()
            if len(normalized) > 1 and any(
                    prefix.startswith(normalized) or normalized.startswith(prefix) for prefix in _PREFIXES):
                self.blocked = True
            elif not self._stack:
                tail = "".join(self._visible(c) for c in self._tag)
            self._tag = ""
        if self._stack:
            self.blocked = True
        if self._json:
            if _HISTORY.search(self._json):
                self.blocked = True
            else:
                tail += self._json
            self._json = ""
        return tail


def sanitize_protocol_text(text: str) -> Tuple[str, bool]:
    """返回过滤后的正文和协议泄漏标记，不解析或执行任何工具参数。"""
    guard = ProtocolTextFilter()
    clean = guard.feed(text) + guard.finish()
    return clean, guard.blocked
