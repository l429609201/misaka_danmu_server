"""
notification/menus — 按菜单条目拆分的 Mixin 模块

每个文件对应一个 /command 菜单，通过 Mixin 在上层 NotificationInputAdapter 组装。
基础 NotificationService 只提供会话和进度关联数据端口，不继承菜单。
"""
from ._base import ImportBaseMixin
from .messages import MessagesMixin
from .help import HelpMenuMixin
from .search import SearchMenuMixin
from .auto import AutoMenuMixin
from .url import UrlMenuMixin
from .library import LibraryMenuMixin
from .tokens import TokensMenuMixin
from .tasks_menu import TasksMenuMixin
from .task_manager_menu import TaskManagerMenuMixin
from .cache import CacheMenuMixin
from .status import StatusMenuMixin
from .llm_menu import LlmChatMixin

__all__ = [
    "ImportBaseMixin",
    "MessagesMixin",
    "HelpMenuMixin",
    "SearchMenuMixin",
    "AutoMenuMixin",
    "UrlMenuMixin",
    "LibraryMenuMixin",
    "TokensMenuMixin",
    "TasksMenuMixin",
    "TaskManagerMenuMixin",
    "CacheMenuMixin",
    "StatusMenuMixin",
    "LlmChatMixin",
]

