"""御坂助手工具包（P2）"""

from .base import Tool, ToolRegistry, registry
from .readonly_tools import register_readonly_tools
from .write_tools import register_write_tools
from .metadata_tools import register_metadata_tools
from .config_tools import register_config_tools
from .general_config_tools import register_general_config_tools
from .skill_tools import register_skill_tools
from .log_tools import register_log_tools
from .doc_tools import register_doc_tools
from .api_tool import register_api_gateway_tools

# 导入即注册工具：只读 + 写类 + 元数据/密钥 + 识别词/过滤配置 + 通用配置 + 技能管理 + 日志查询 + UI文档检索
register_readonly_tools()
register_write_tools()
register_metadata_tools()
register_config_tools()
register_general_config_tools()
register_skill_tools()
register_log_tools()
register_doc_tools()
# API 网关：白名单 operation → 内部 ASGI 调用，后端新增接口只需加白名单
register_api_gateway_tools()

__all__ = [
    "Tool", "ToolRegistry", "registry",
    "register_readonly_tools", "register_write_tools",
    "register_metadata_tools", "register_config_tools",
    "register_general_config_tools", "register_skill_tools",
    "register_log_tools", "register_doc_tools",
    "register_api_gateway_tools",
]
