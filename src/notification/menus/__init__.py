"""
notification/menus — 按菜单条目拆分的 Mixin 模块

每个文件对应一个 /command 菜单，通过 Mixin 继承到 NotificationService。
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
from .cache import CacheMenuMixin
from .status import StatusMenuMixin

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
    "CacheMenuMixin",
    "StatusMenuMixin",
]

