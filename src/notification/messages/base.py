"""
消息对象基类与通用数据结构

NotificationMessage — 所有通知/回复消息的基类
RenderedMessage — 渠道发送前的渲染结果
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class MessageCategory(Enum):
    """消息分类"""
    SYSTEM = "system"
    TASK = "task"
    INTERACTION = "interaction"


class MessageSeverity(Enum):
    """消息级别"""
    SUCCESS = "success"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class AggregationPolicy(Enum):
    """聚合策略"""
    NONE = "none"                   # 不聚合，立即发送
    TIME_WINDOW = "time_window"     # 时间窗口内合并
    COUNT_THRESHOLD = "count_threshold"  # 达到数量阈值后合并


@dataclass
class NotificationMessage:
    """标准通知消息基类

    所有消息类型继承此类，实现 to_markdown / to_text 方法。
    """
    message_type: str = ""
    category: MessageCategory = MessageCategory.SYSTEM
    severity: MessageSeverity = MessageSeverity.INFO
    payload: Dict[str, Any] = field(default_factory=dict)
    # 用于匹配渠道事件订阅配置
    subscription_key: str = ""
    # 用于聚合分桶
    aggregation_key: str = ""
    aggregation_policy: AggregationPolicy = AggregationPolicy.NONE

    def to_markdown(self) -> tuple:
        """输出 (title, body) Markdown 内容。子类覆写。"""
        return (self.message_type, str(self.payload))

    def to_text(self) -> tuple:
        """输出 (title, body) 纯文本内容。

        子类应硬编码独立的纯文本模板（不含任何 Markdown 符号/转义）。
        若未覆写，则对 to_markdown 输出做兜底清洗：去除 MarkdownV2 转义反斜杠、
        引用块 > 前缀、加粗 * 符号，保证纯文本渠道（企业微信/Server酱）不显示乱符号。
        """
        title, body = self.to_markdown()
        return (self._strip_markdown(title), self._strip_markdown(body))

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """兜底：将 MarkdownV2 文本清洗为纯文本（去转义反斜杠、引用块前缀、加粗星号）"""
        if not text:
            return ""
        lines = []
        for line in str(text).split("\n"):
            # 去掉行首引用块标记 >
            if line.startswith(">"):
                line = line[1:]
            # 去掉 MarkdownV2 转义反斜杠（\. \! \- 等）
            result = []
            i = 0
            while i < len(line):
                ch = line[i]
                if ch == "\\" and i + 1 < len(line):
                    result.append(line[i + 1])
                    i += 2
                elif ch in ("*", "`"):
                    i += 1  # 去掉加粗/代码符号
                else:
                    result.append(ch)
                    i += 1
            lines.append("".join(result))
        return "\n".join(lines)

    def buttons(self) -> List[List[Dict[str, str]]]:
        """输出平台无关按钮结构。默认无按钮。"""
        return []

    def image(self) -> str:
        """输出可选图片地址。默认空。"""
        return self.payload.get("image_url", "") or ""

    def edit_policy(self) -> Optional[int]:
        """是否允许编辑已有消息。返回 message_id 或 None。"""
        return None

    @staticmethod
    def _escape_markdown(text: str) -> str:
        """转义 Telegram MarkdownV2 特殊字符"""
        if not text:
            return ""
        # MarkdownV2 需要转义的字符: _ * [ ] ( ) ~ ` > # + - = | { } . !
        special_chars = r'_*[]()~`>#+-=|{}.!'
        result = []
        for ch in str(text):
            if ch in special_chars:
                result.append(f'\\{ch}')
            else:
                result.append(ch)
        return ''.join(result)


@dataclass
class RenderedMessage:
    """渠道发送前的渲染结果

    由 NotificationManager.render_for_channel 生成，
    渠道接入器直接消费此结构发送消息。
    """
    title: str = ""
    body: str = ""
    format: str = "markdown"  # "markdown" 或 "text"
    image: str = ""
    buttons: List[List[Dict[str, str]]] = field(default_factory=list)
    edit_message_id: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
