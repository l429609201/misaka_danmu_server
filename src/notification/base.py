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

from src.notification.messages.base import NotificationMessage, RenderedMessage


# ═══════════════════════════════════════════
# 渠道能力系统
# ═══════════════════════════════════════════

class ChannelCapability(Enum):
    """渠道能力枚举 — 声明渠道支持的交互特性"""
    INLINE_BUTTONS = "inline_buttons"        # 支持内联按钮（InlineKeyboard）
    MENU_COMMANDS = "menu_commands"          # 支持菜单命令（BotCommand）
    MESSAGE_EDITING = "message_editing"      # 支持编辑已发送的消息
    MESSAGE_DELETION = "message_deletion"    # 支持删除消息
    CALLBACK_QUERIES = "callback_queries"    # 支持回调查询（按钮点击事件）
    RICH_TEXT = "rich_text"                  # 支持富文本（Markdown/HTML）
    IMAGES = "images"                        # 支持图片发送
    LINKS = "links"                          # 支持链接
    RICH_MESSAGE = "rich_message"            # 支持「结构化富消息」


# ── 图片发送模式（各渠道通用）──
IMAGE_MODE_TEXT = "text"          # 纯文字：丢弃图片，只发文本
IMAGE_MODE_POSTER = "poster"      # 海报：图文合一（图片带 caption）
IMAGE_MODE_SEPARATE = "separate"  # 图片模式：图片与文字分两条消息发送
IMAGE_MODE_PUBLIC_URL = "public_url"  # 外链模式：图片压缩为 W500 存本地，以外网 HTTPS URL 发送
IMAGE_MODE_DEFAULT = IMAGE_MODE_POSTER

# 各渠道 configFields 共享的「图片发送模式」四档开关定义。
# why: 四个渠道都需要该配置，集中定义避免重复；前端 renderConfigField 的
# segmented 分支会渲染成左中右拨动开关。
IMAGE_MODE_FIELD = {
    "key": "image_mode",
    "label": "图片发送模式",
    "label_en": "Image Sending Mode",
    "label_tw": "圖片傳送模式",
    "type": "segmented",
    "description": "纯文字=不发图片；海报=图文合一；图片模式=图片与文字分两条发送；外链模式=图片缩略为 W500 存本地后，把图片链接交给平台抓取。注意：外链模式必须先在「弹幕 → Token 管理 → 自定义域名」填写公网可访问的 HTTPS 域名，否则本次通知自动降级为海报模式。",
    "description_en": "Text only=no image; Poster=image with caption; Separate=image and text as two messages; URL Link=resize to W500, store locally and let the platform fetch the image link. Note: URL Link requires a publicly reachable HTTPS domain configured under Danmaku → Token Management → Custom Domain, otherwise it falls back to Poster mode.",
    "description_tw": "純文字=不傳圖片；海報=圖文合一；圖片模式=圖片與文字分兩條傳送；外鏈模式=圖片縮圖為 W500 存本機後，把圖片連結交給平台抓取。注意：外鏈模式必須先在「彈幕 → Token 管理 → 自訂網域」填寫公網可存取的 HTTPS 網域，否則本次通知自動降級為海報模式。",
    "options": [
        {"value": IMAGE_MODE_TEXT,     "label": "纯文字",   "label_en": "Text Only", "label_tw": "純文字"},
        {"value": IMAGE_MODE_POSTER,   "label": "海报",     "label_en": "Poster",    "label_tw": "海報"},
        {"value": IMAGE_MODE_SEPARATE, "label": "图片模式", "label_en": "Separate",  "label_tw": "圖片模式"},
        {"value": IMAGE_MODE_PUBLIC_URL, "label": "外链模式", "label_en": "URL Link", "label_tw": "外鏈模式"},
    ],
    "default": IMAGE_MODE_DEFAULT,
}


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
    def supports_rich_message(self) -> bool:
        """是否支持结构化富消息（表格、标题、脚注、公式等）。"""
        return self.supports(ChannelCapability.RICH_MESSAGE)

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
    # 任务ID：发完消息后用于注册进度跟踪（telegram.py _render_result 使用）
    task_id: Optional[str] = None
    # 图文文章列表（渠道层有能力时优先展示带图版本）
    # 每项: {"title": str, "description": str, "picurl": str, "url": str}
    articles: List[Dict[str, str]] = field(default_factory=list)
    # 聚合海报图（PNG bytes）。非空时渠道层优先以图片消息发送，
    # caption 取 text，附带 reply_markup 按钮。用于搜索结果九宫格海报。
    image_bytes: Optional[bytes] = None


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
# 进度条工具
# ═══════════════════════════════════════════

def _progress_bar_str(progress: int) -> str:
    """将进度百分比转换为 10 格进度条字符串（纯 █░，无任何转义）。

    why：进度条的条形计算在三处独立实现（base.py、system.py、task_manager_menu.py），
    容易偏移（有的用 // 10、有的用 int(p/10)、有的用 int(p/10) 不 clamp）。
    这里是唯一来源，所有渠道渲染、消息模板、菜单都从这里取。
    平台专属的格式化（MarkdownV2 反引号、纯文本括号）由调用方附加。
    """
    pct = max(0, min(100, int(progress or 0)))
    filled = pct // 10
    return "█" * filled + "░" * (10 - filled)




class ProgressTracker:
    """长耗时操作的进度反馈句柄。

    why：进度反馈依赖「发一条占位消息 → 反复编辑刷新百分比 → 编辑成最终结果」这套
    生命周期，只有声明了 MESSAGE_EDITING 能力的渠道才成立。不具备该能力的渠道若
    逐条推送，10%/20%/40% 会各占一条变成刷屏垃圾，占位消息也会永久停在「进行中」。

    这里把「能力判断 + 占位消息 + message_id 生命周期 + 文本渲染」全部收口，
    业务侧只调用 begin_progress/update，不再各自写 supports_editing 判断，
    也不再手工传递 edit_message_id。不支持的渠道拿到的是静默降级的空实现。
    """

    def __init__(
        self,
        channel: "BaseNotificationChannel",
        title: str,
        chat_id=None,
        message_id: Optional[int] = None,
    ):
        self._channel = channel
        self._title = title
        self._chat_id = chat_id
        self._message_id: Optional[int] = message_id
        # 渠道不支持编辑时全程静默，业务侧无需感知
        self._enabled = channel.get_capabilities().supports_editing

    @property
    def message_id(self) -> Optional[int]:
        """占位消息 ID，供最终结果复用以编辑同一条消息。不支持时为 None。"""
        return self._message_id

    async def start(self, text: str) -> "ProgressTracker":
        """发出占位消息。不支持编辑的渠道直接跳过，不留僵尸消息。"""
        if not self._enabled:
            return self
        self._message_id = await self._channel.send_quick(text, chat_id=self._chat_id)
        return self

    async def update(self, progress: int, description: str) -> None:
        """刷新进度。文本渲染委托给渠道，各平台自行处理转义与格式。"""
        if not self._enabled:
            return
        text = self._channel.render_progress_text(progress, description)
        msg_id_out: list = []
        try:
            await self._channel.send_message(
                title=self._title,
                text=text,
                chat_id=self._chat_id,
                edit_message_id=self._message_id,
                _msg_id_out=msg_id_out,
            )
        except Exception:
            # 进度刷新失败不应中断主流程（搜索/导入本身仍要继续）
            self._channel.logger.debug("进度消息刷新失败，已忽略", exc_info=True)
            return
        # 首次发送时记录 message_id，后续复用以编辑同一条消息
        if msg_id_out and not self._message_id:
            self._message_id = msg_id_out[0]



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
        # 从注入的特殊字段读取代理 URL（由 NotificationManager 在加载时注入）
        self.proxy_url: str = config.get("__proxy_url", "")

    # 渠道能力声明：子类在类体里覆写此类属性即可，无需再覆写 get_capabilities。
    # why：能力属于渠道类型而非实例（同类型多实例能力一致），放类属性可避免每个
    # 实例复制一份相同数据，也支持不实例化就查询能力。
    _CAPABILITIES: ChannelCapabilities = ChannelCapabilities()

    def get_capabilities(self) -> ChannelCapabilities:
        """返回渠道能力配置，取子类声明的 _CAPABILITIES 类属性。

        未声明的渠道拿到基类的空能力集（仅支持纯文本通知）。
        """
        return self._CAPABILITIES

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

    @property
    def image_mode(self) -> str:
        """当前渠道的图片发送模式（text / poster / separate / public_url）"""
        mode = (self.config or {}).get("image_mode") or IMAGE_MODE_DEFAULT
        if mode not in (
            IMAGE_MODE_TEXT, IMAGE_MODE_POSTER,
            IMAGE_MODE_SEPARATE, IMAGE_MODE_PUBLIC_URL,
        ):
            return IMAGE_MODE_DEFAULT
        return mode

    def public_base_url(self) -> str:
        """取本渠道可对外访问的站点根地址（必须是 https，否则视为不可用）。

        优先级（由高到低）：
        1. Token 管理 → 自定义域名（__custom_api_domain，全局唯一，由 NotificationManager 注入）
        2. 本渠道配置的外部访问地址（webhook_base_url / server_url）

        why：外链模式要把本机图片地址交给第三方平台抓取，必须有可公网访问的域名。
        Token 管理里的自定义域名是用户专门为此场景配置的全局地址，渠道自己的地址
        作为备用兜底。仅接受 https：http 明文地址会被大多数平台拒绝加载。
        外链模式不可用时请在「弹幕 → Token 管理 → 自定义域名」填写可访问的 HTTPS 域名。
        """
        cfg = self.config or {}
        for key in ("__custom_api_domain", "webhook_base_url", "server_url"):
            raw = str(cfg.get(key) or "").strip().rstrip("/")
            if raw.startswith("https://"):
                return raw
        return ""

    async def _build_public_image_url(self, rendered: RenderedMessage) -> str:
        """把消息图片转存为 W500 缩略图，并拼成外网可访问的 https 地址。"""
        return await self.build_public_image_url(
            rendered.image, image_bytes=rendered.image_bytes
        )

    async def build_public_image_url(
        self, image_url: str = "", image_bytes: Optional[bytes] = None,
    ) -> str:
        """把任意图片引用转成当前渠道的本地 W500 公网地址。

        why：事件通知和交互卡片都可能携带图片，统一入口可避免某条发送路径
        绕过外链模式后继续暴露源站地址。
        """
        base = self.public_base_url()
        if not base:
            return ""

        if image_url.startswith(f"{base}/data/images/"):
            return image_url

        source = image_bytes or image_url
        if not source:
            return ""

        try:
            from src.utils.image_utils import save_public_thumbnail
            web_path = await save_public_thumbnail(source)
        except Exception as e:
            self.logger.warning(f"生成对外分享缩略图失败: {e}")
            return ""

        return f"{base}{web_path}" if web_path else ""

    async def localize_articles(self, articles: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """按外链模式本地化交互卡片海报，并避免点击后跳回源站图片。"""
        if self.image_mode != IMAGE_MODE_PUBLIC_URL:
            return articles

        localized = []
        for article in articles:
            item = dict(article)
            original = item.get("picurl", "")
            if original:
                public_url = await self.build_public_image_url(original)
                if public_url:
                    item["picurl"] = public_url
                    # why：空链接或原本就是图片链接时，点击目标也必须同步换成本地外链。
                    if not item.get("url") or item.get("url") == original:
                        item["url"] = public_url
            localized.append(item)
        return localized

    async def send_rendered(self, rendered: RenderedMessage):
        """发送标准渲染消息。

        默认实现将 RenderedMessage 转发到 send_message，保持与旧渠道实现兼容。
        消息正文已自带标题行，因此 title 传空；原标题通过 article_title 透传。
        """
        title = rendered.title
        body = rendered.body
        kwargs = {}
        mode = self.image_mode
        if mode != IMAGE_MODE_TEXT:
            image_ref = rendered.image
            image_bytes = rendered.image_bytes

            if mode == IMAGE_MODE_PUBLIC_URL:
                original_image = image_ref
                public_url = await self._build_public_image_url(rendered)
                if public_url:
                    image_ref = public_url
                    image_bytes = None
                    if original_image:
                        # why：正文模板也会展示海报链接，必须与卡片图片同步替换。
                        body = body.replace(original_image, public_url)
                        body = body.replace(
                            NotificationMessage._escape_markdown(original_image),
                            NotificationMessage._escape_markdown(public_url),
                        )
                else:
                    self.logger.warning(
                        "外链模式不可用，本次按海报模式发送。"
                        "请在「弹幕 → Token 管理 → 自定义域名」填写公网可访问的 "
                        "HTTPS 域名（必须以 https:// 开头，http 地址平台会拒绝加载）"
                    )
                    mode = IMAGE_MODE_POSTER

            if image_ref:
                kwargs["image"] = image_ref
            if image_bytes:
                kwargs["image_bytes"] = image_bytes
            if mode == IMAGE_MODE_SEPARATE and (image_ref or image_bytes):
                kwargs["image_separate"] = True
        if rendered.buttons:
            kwargs["reply_markup"] = rendered.buttons
        if rendered.edit_message_id:
            kwargs["edit_message_id"] = rendered.edit_message_id
        kwargs.update(rendered.metadata)
        kwargs["article_title"] = title
        await self.send_message(title="", text=body, **kwargs)

    async def edit_rendered(self, rendered: RenderedMessage):
        """编辑已有消息。默认无操作，支持编辑的渠道覆写。"""
        pass

    async def send_quick(self, text: str, chat_id=None) -> Optional[int]:
        """发送一条快速消息并返回 message_id（用于后续 edit）。
        不支持的渠道返回 None。子类可覆写实现。
        """
        return None

    async def begin_progress(
        self,
        title: str,
        placeholder: str = "",
        chat_id=None,
        message_id: Optional[int] = None,
    ) -> ProgressTracker:
        """创建进度反馈句柄，供长耗时操作（搜索/导入/刷新）汇报进度。

        业务侧统一走这里，不需要自己判断渠道能力或管理 message_id：
        不支持消息编辑的渠道会拿到静默降级的句柄，全程不发任何进度消息。

        :param title: 进度消息标题，如 "🔍 搜索中"
        :param placeholder: 占位消息文本，留空则不发占位、等首次 update 时创建
        :param chat_id: 目标会话，多会话渠道需传入
        :param message_id: 已存在的消息 ID（如调用方先发了占位消息），复用它而非新发
        """
        tracker = ProgressTracker(self, title, chat_id=chat_id, message_id=message_id)
        if placeholder and not message_id:
            await tracker.start(placeholder)
        return tracker

    def render_progress_text(self, progress: int, description: str) -> str:
        """渲染进度文本。默认纯文本，有富文本格式要求的渠道覆写此方法。

        why：进度条的转义规则是平台专属的（Telegram 要转义 MarkdownV2 保留字符，
        企业微信用纯文本）。业务侧只提供 progress/description 两个语义值，
        具体呈现由各渠道自行决定，避免格式化逻辑泄漏到菜单代码里。
        """
        return f"[{_progress_bar_str(progress)}] {progress}%\n• {description}"

    @staticmethod
    @abstractmethod
    def get_config_schema() -> list:
        """返回该渠道类型的配置 Schema 列表"""
        ...

