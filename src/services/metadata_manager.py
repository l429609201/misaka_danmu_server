"""搜索源相关类型标注的旧路径兼容；运行入口统一为 MetadataService。"""

from src.services.metadata_service import MetadataService as MetadataSourceManager

__all__ = ["MetadataSourceManager"]
