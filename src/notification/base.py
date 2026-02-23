"""
通知渠道抽象基类
所有渠道实现只依赖 NotificationService，不引用系统其他模块。
参考 MoviePilot 架构，引入渠道能力系统实现平台无关的交互抽象。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set
import logging
import time


# ═══════════════════════════════════════════
# 渠道能力系统
# ═══════════════════════════════════════════

class ChannelCapability(Enum):
    """渠道能力枚举 — 声明渠道支持的交互特性"""
    INLINE_BUTTONS = "inline_buttons"       # 支持内联按钮（InlineKeyboard）
    MENU_COMMANDS = "menu_commands"          # 支持菜单命令（BotCommand）
    MESSAGE_EDITING = "message_editing"      # 支持编辑已发送的消息
    MESSAGE_DELETION = "message_deletion"    # 支持删除消息
    CALLBACK_QUERIES = "callback_queries"    # 支持回调查询（按钮点击事件）
    RICH_TEXT = "rich_text"                  # 支持富文本（Markdown/HTML）
    IMAGES = "images"                        # 支持图片发送
    LINKS = "links"                          # 支持链接


@dataclass
class ChannelCapabilities:
    """渠道能力配置 — 描述渠道的能力集合和限制"""
    capabilities: Set[ChannelCapability] = field(default_factory=set)
    max_buttons_per_row: int = 5
    max_button_rows: int = 10
    max_button_text_length: int = 30

    def supports(self, capability: ChannelCapability) -> bool:
        return capability in self.capabilities

    @property
    def supports_buttons(self) -> bool:
        return self.supports(ChannelCapability.INLINE_BUTTONS)

    @property
    def supports_callbacks(self) -> bool:
        return self.supports(ChannelCapability.CALLBACK_QUERIES)

    @property
    def supports_editing(self) -> bool:
        return self.supports(ChannelCapability.MESSAGE_EDITING)

    @property
    def supports_menu(self) -> bool:
        return self.supports(ChannelCapability.MENU_COMMANDS)


# ═══════════════════════════════════════════
# 命令执行结果 & 对话状态
# ═══════════════════════════════════════════

@dataclass
class CommandResult:
    """命令执行结果 — 渠道层根据此结构渲染消息
    reply_markup 使用平台无关的按钮格式：[[{"text": "显示", "callback_data": "action:param"}]]
    渠道层根据自身能力决定如何渲染（InlineKeyboard / 文本列表 / 忽略）
    """
    success: bool = True
    text: str = ""
    data: Any = None
    # 平台无关的按钮定义，渠道层根据能力转换为平台特定格式
    reply_markup: List[List[Dict[str, str]]] = field(default_factory=list)
    # 消息格式: "Markdown" / "HTML" / None(纯文本)
    parse_mode: Optional[str] = None
    # 非 None 时表示编辑已有消息而非发送新消息
    edit_message_id: Optional[int] = None
    # 对话状态控制
    next_state: Optional[str] = None   # 设置下一步等待的状态
    clear_state: bool = False           # 清除当前对话状态
    # 回调查询应答文本（仅 callback_query 场景使用）
    answer_callback_text: Optional[str] = None


@dataclass
class ConversationState:
    """用户对话状态（由 NotificationService 管理）"""
    state: str                          # 当前状态名
    data: Dict[str, Any] = field(default_factory=dict)  # 上下文数据
    message_id: Optional[int] = None    # 关联的消息ID（用于编辑）
    chat_id: Optional[int] = None       # 关联的 chat_id
    created_at: float = field(default_factory=time.time)
    # 超时秒数，默认10分钟
    timeout: float = 600.0

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.timeout


# ═══════════════════════════════════════════
# 渠道抽象基类
# ═══════════════════════════════════════════

class BaseNotificationChannel(ABC):
    """所有通知渠道的抽象基类"""

    channel_type: str = ""       # 渠道标识，如 "telegram"
    display_name: str = ""       # 显示名称，如 "Telegram"

    def __init__(self, channel_id: int, name: str, config: dict, notification_service):
        self.channel_id = channel_id
        self.name = name
        self.config = config
        self.service = notification_service  # 唯一依赖：NotificationService
        self.logger = logging.getLogger(f"{self.__class__.__name__}[{channel_id}]")

    def get_capabilities(self) -> ChannelCapabilities:
        """返回渠道能力配置。子类应覆写此方法声明自身能力。
        默认返回空能力集（仅支持纯文本通知）。
        """
        return ChannelCapabilities()

    def register_commands(self, commands: Dict[str, str]) -> None:
        """注册菜单命令。子类可覆写以实现平台特定的命令菜单。
        :param commands: {"/command": "描述"} 格式的命令字典
        """
        pass  # 默认不支持菜单命令

    @abstractmethod
    async def start(self):
        """启动渠道（开始轮询或注册 webhook 等）"""
        ...

    @abstractmethod
    async def stop(self):
        """停止渠道"""
        ...

    @abstractmethod
    async def send_message(self, title: str, text: str, **kwargs):
        """发送消息到默认接收者"""
        ...

    @abstractmethod
    async def test_connection(self) -> Dict[str, Any]:
        """测试连接，返回 {"success": bool, "message": str}"""
        ...

    def process_webhook_update(self, update_json: dict) -> bool:
        """处理外部 Webhook 回调推送的数据。
        支持 webhook 模式的渠道应覆写此方法。
        返回 True 表示已处理，False 表示不支持。
        """
        return False

    @staticmethod
    @abstractmethod
    def get_config_schema() -> list:
        """返回该渠道类型的配置 Schema 列表"""
        ...

