"""御坂助手工具包（P2）"""

from .base import Tool, ToolRegistry, registry
from .readonly_tools import register_readonly_tools
from .write_tools import register_write_tools
from .metadata_tools import register_metadata_tools
from .config_tools import register_config_tools
from .skill_tools import register_skill_tools

# 导入即注册工具：只读 + 写类 + 元数据/密钥 + 识别词/过滤配置 + 技能管理
register_readonly_tools()
register_write_tools()
register_metadata_tools()
register_config_tools()
register_skill_tools()

__all__ = [
    "Tool", "ToolRegistry", "registry",
    "register_readonly_tools", "register_write_tools",
    "register_metadata_tools", "register_config_tools",
    "register_skill_tools",
]
