"""LLM 数据工具旧导入路径兼容层；实现位于 AI 助手工具目录。"""

from src.ai.assistant.tools.database_tools import DatabaseTools, get_database_tools

__all__ = ["DatabaseTools", "get_database_tools"]
