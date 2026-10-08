"""助手内部弹幕数据库诊断工具，只提供固定查询及受限表结构。"""

import logging
from typing import List, Dict, Any, Optional

from src.ai.assistant.tools.db_tools import get_llm_db_tools
from src.ai.assistant.security_gateway import ToolPermission
from src.ai.assistant.tools.base import Tool, registry

logger = logging.getLogger(__name__)


class DatabaseTools:
    """
    LLM 数据库检索内部工具
    
    设计为 LLM 运行时可直接调用的工具类，而非 HTTP API
    """
    
    def __init__(self):
        """初始化数据库工具"""
        self.tools = get_llm_db_tools()
    
    # ===== ORM 结构查询 =====
    
    async def list_tables(self) -> List[Dict[str, Any]]:
        """
        获取所有数据库表的列表
        
        返回:
            表信息列表，每个表包含：
            - table_name: 表名
            - model_class: ORM 模型类名
            - comment: 表注释
            - column_count: 字段数量
            - has_relationships: 是否有关系映射
        
        示例:
            tables = await db_tools.list_tables()
            for table in tables:
                print(f"{table['table_name']}: {table['comment']}")
        """
        try:
            return await self.tools.get_all_tables()
        except Exception as e:
            logger.error(f"获取表列表失败: {e}", exc_info=True)
            return []
    
    async def get_table_schema(self, table_name: str) -> Optional[Dict[str, Any]]:
        """
        获取指定表的详细结构
        
        参数:
            table_name: 表名（如 'anime', 'episode'）
        
        返回:
            表结构信息，包含：
            - table_name: 表名
            - model_class: ORM 模型类
            - comment: 表注释
            - columns: 字段列表（含类型、是否可空、是否敏感等）
            - relationships: 关系映射列表
            - indexes: 索引列表
        
        示例:
            schema = await db_tools.get_table_schema("anime")
            print(f"表 {schema['table_name']} 有 {len(schema['columns'])} 个字段")
            
            for col in schema['columns']:
                print(f"  - {col['name']} ({col['type']})")
        """
        try:
            return await self.tools.get_table_schema(table_name)
        except ValueError as e:
            logger.warning(f"表 '{table_name}' 不存在: {e}")
            return None
        except Exception as e:
            logger.error(f"获取表结构失败: {e}", exc_info=True)
            return None
    
    async def search_tables(self, keyword: str) -> List[Dict[str, Any]]:
        """
        按关键词搜索表和字段
        
        参数:
            keyword: 搜索关键词（匹配表名、字段名、注释）
        
        返回:
            匹配结果列表，每项包含：
            - table_name: 表名
            - model_class: 模型类
            - match_type: 匹配类型（table_name/column_name/comment）
            - matched_columns: 匹配的字段列表
        
        示例:
            results = await db_tools.search_tables("弹幕")
            for result in results:
                print(f"找到表: {result['table_name']} - {result['match_type']}")
        """
        try:
            return await self.tools.search_tables(keyword)
        except Exception as e:
            logger.error(f"搜索表失败: {e}", exc_info=True)
            return []
    
    async def get_config_metadata(self) -> List[Dict[str, str]]:
        """返回固定弹幕配置项的名称和受控说明，不读取配置值。"""
        return await self.tools.get_config_metadata()

    # ===== SQL 查询执行 =====
    
    async def query(
        self,
        sql: str,
        max_rows: int = 100,
        timeout: int = 30
    ) -> Dict[str, Any]:
        """兼容旧接口，任意 SQL 均返回拒绝结果。"""
        try:
            return await self.tools.execute_safe_query(sql, max_rows, timeout)
        except Exception as e:
            logger.error(f"执行查询失败: {e}", exc_info=True)
            return {
                "success": False,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
                "masked_columns": [],
                "execution_time_ms": 0.0,
                "error": str(e)
            }
    
    async def explain(self, sql: str) -> Dict[str, Any]:
        """
        分析 SQL 查询计划（EXPLAIN）
        
        参数:
            sql: SELECT 查询语句
        
        返回:
            {
                "success": True/False,
                "plan": [执行计划详情],
                "warnings": ["全表扫描", ...],
                "suggestions": ["建议添加索引", ...],
                "error": None
            }
        
        示例:
            result = await db_tools.explain(
                "SELECT * FROM anime WHERE title LIKE '%进击%'"
            )
            
            if result['success']:
                if result['warnings']:
                    print("⚠️ 警告:")
                    for warning in result['warnings']:
                        print(f"  - {warning}")
                
                if result['suggestions']:
                    print("💡 优化建议:")
                    for suggestion in result['suggestions']:
                        print(f"  - {suggestion}")
        """
        try:
            return await self.tools.explain_query(sql)
        except Exception as e:
            logger.error(f"EXPLAIN 失败: {e}", exc_info=True)
            return {
                "success": False,
                "plan": [],
                "warnings": [],
                "suggestions": [],
                "error": str(e)
            }
    
    # ===== 便捷方法 =====
    
    async def quick_query(self, sql: str) -> List[Dict[str, Any]]:
        """
        快速查询（只返回数据行，忽略元数据）
        
        参数:
            sql: SQL 查询语句
        
        返回:
            数据行列表，如果失败返回空列表
        
        示例:
            rows = await db_tools.quick_query(
                "SELECT id, title FROM anime LIMIT 5"
            )
            
            for row in rows:
                print(f"{row['id']}: {row['title']}")
        """
        result = await self.query(sql)
        return result['rows'] if result['success'] else []
    
    async def get_table_row_count(self, table_name: str) -> Optional[int]:
        """
        获取表的行数
        
        参数:
            table_name: 表名
        
        返回:
            行数，失败返回 None
        
        示例:
            count = await db_tools.get_table_row_count("anime")
            print(f"anime 表有 {count} 行数据")
        """
        try:
            return await self.tools.get_danmaku_row_count(table_name)
        except ValueError:
            return None

    async def get_recent_anime(self) -> List[Dict[str, Any]]:
        """读取最近作品的固定字段摘要。"""
        return await self.tools.get_recent_anime()
    
    async def table_exists(self, table_name: str) -> bool:
        """
        检查表是否存在
        
        参数:
            table_name: 表名
        
        返回:
            True 表存在，False 表不存在
        """
        schema = await self.get_table_schema(table_name)
        return schema is not None




async def _list_danmaku_tables(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """列出允许自检的弹幕业务表。"""
    return {"tables": await get_database_tools().list_tables()}


async def _get_danmaku_schema(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """读取指定弹幕业务表结构。"""
    table_name = arguments.get("tableName")
    if not isinstance(table_name, str) or not table_name.strip():
        return {"error": "缺少 tableName"}
    return await get_database_tools().get_table_schema(table_name)


async def _get_danmaku_config_metadata(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """只返回固定白名单配置键及预设说明。"""
    return {"items": await get_database_tools().get_config_metadata()}


async def _count_danmaku_table(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """统计固定白名单内的弹幕业务表。"""
    table_name = arguments.get("tableName")
    count = await get_database_tools().get_table_row_count(table_name)
    return {"table_name": table_name, "row_count": count} if count is not None else {"error": "不支持该表"}


async def _list_recent_anime(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """返回最近作品的固定摘要字段。"""
    return {"items": await get_database_tools().get_recent_anime()}


def register_database_tools() -> None:
    """注册受限弹幕数据库自检工具。"""
    registry.register(Tool(
        name="list_danmaku_tables",
        description="列出可自检的弹幕业务表及安全字段摘要；配置只展示固定安全结构字段，流控和系统表不返回。",
        parameters={"type": "object", "properties": {}},
        permission=ToolPermission.READ_ONLY,
        executor=_list_danmaku_tables,
        running_label="正在读取弹幕库结构",
    ))
    registry.register(Tool(
        name="get_danmaku_table_schema",
        description="读取弹幕业务表结构，用于定位作品、数据源、分集和本地弹幕之间的数据问题。",
        parameters={
            "type": "object",
            "properties": {"tableName": {"type": "string", "description": "弹幕业务表名"}},
            "required": ["tableName"],
        },
        permission=ToolPermission.READ_ONLY,
        executor=_get_danmaku_schema,
        running_label="正在读取弹幕表结构",
    ))
    registry.register(Tool(
        name="get_danmaku_config_metadata",
        description="读取固定弹幕配置项的名称及说明，不返回配置值或密钥信息。",
        parameters={"type": "object", "properties": {}},
        permission=ToolPermission.READ_ONLY,
        executor=_get_danmaku_config_metadata,
        running_label="正在读取弹幕配置元数据",
    ))
    registry.register(Tool(
        name="count_danmaku_table",
        description="统计指定弹幕业务表的行数，不支持配置、令牌、流控和系统表。",
        parameters={
            "type": "object",
            "properties": {"tableName": {"type": "string", "description": "弹幕业务表名"}},
            "required": ["tableName"],
        },
        permission=ToolPermission.READ_ONLY,
        executor=_count_danmaku_table,
        running_label="正在统计弹幕业务表",
    ))
    registry.register(Tool(
        name="list_recent_anime",
        description="列出最近 20 个作品的编号和标题，用于弹幕库自检。",
        parameters={"type": "object", "properties": {}},
        permission=ToolPermission.READ_ONLY,
        executor=_list_recent_anime,
        running_label="正在读取最近作品",
    ))


_db_tools_instance: Optional[DatabaseTools] = None


def get_database_tools() -> DatabaseTools:
    """获取数据库工具实例（单例）"""
    global _db_tools_instance
    if _db_tools_instance is None:
        _db_tools_instance = DatabaseTools()
    return _db_tools_instance
