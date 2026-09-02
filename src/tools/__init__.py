"""
LLM 内部工具集

提供给 LLM 运行时直接调用的工具集合（非 HTTP API）

包含：
- DatabaseTools: 数据库查询工具（ORM 结构 + 安全 SQL）
- PerformanceTools: 性能监测工具（指标查询 + 告警查询）

使用方式：
    from src.tools import get_database_tools, get_performance_tools
    
    # 数据库工具
    db = get_database_tools()
    tables = await db.list_tables()
    result = await db.query("SELECT * FROM anime LIMIT 10")
    
    # 性能监测工具
    perf = get_performance_tools()
    metrics = await perf.get_latest_metrics("database")
    alerts = await perf.get_active_alerts()
"""

from .database_tools import DatabaseTools, get_database_tools
from .performance_tools import PerformanceTools, get_performance_tools

__all__ = [
    'DatabaseTools',
    'get_database_tools',
    'PerformanceTools',
    'get_performance_tools',
]
