"""御坂助手工具包（P2）"""

from .base import Tool, ToolRegistry, registry
from .readonly_tools import register_readonly_tools
from .write_tools import register_write_tools

# 导入即注册工具：只读 + 写类
register_readonly_tools()
register_write_tools()

__all__ = [
    "Tool", "ToolRegistry", "registry",
    "register_readonly_tools", "register_write_tools",
]
