"""
Processor 层 - 数据处理逻辑

职责：
1. 数据清洗和转换
2. 业务规则判断
3. 文件系统操作
4. 缓存操作
5. 外部 API 调用

不依赖数据库 Session，接收数据返回数据。

命名规范：
- 文件名：<模块名>.py（如 danmaku.py, anime.py）
- 类名：<模块名>Processor（如 DanmakuProcessor, AnimeProcessor）
"""

from .danmaku import DanmakuProcessor

__all__ = [
    "DanmakuProcessor",
]
