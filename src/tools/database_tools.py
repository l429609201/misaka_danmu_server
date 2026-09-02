"""
LLM 内部数据库检索工具

提供给 LLM 在运行时直接调用的数据库查询能力（非 HTTP API）

使用方式：
    from src.tools.database_tools import DatabaseTools
    
    db_tools = DatabaseTools()
    
    # 查询表结构
    tables = await db_tools.list_tables()
    schema = await db_tools.get_table_schema("anime")
    
    # 执行查询
    result = await db_tools.query("SELECT id, title FROM anime LIMIT 10")
"""

import logging
from typing import List, Dict, Any, Optional

from src.services.llm_db_tools import get_llm_db_tools

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
    
    # ===== SQL 查询执行 =====
    
    async def query(
        self,
        sql: str,
        max_rows: int = 100,
        timeout: int = 30
    ) -> Dict[str, Any]:
        """
        执行安全的只读 SQL 查询
        
        安全措施:
        - 只允许 SELECT/EXPLAIN/SHOW/DESCRIBE
        - 自动脱敏敏感字段
        - 查询超时保护
        - 事务回滚（确保无副作用）
        
        参数:
            sql: SQL 查询语句
            max_rows: 最大返回行数（默认 100，最大 1000）
            timeout: 查询超时时间（秒，默认 30）
        
        返回:
            {
                "success": True/False,
                "columns": ["col1", "col2"],
                "rows": [{"col1": "val1", "col2": "val2"}, ...],
                "row_count": 10,
                "truncated": False,
                "masked_columns": ["password"],  # 被脱敏的字段
                "execution_time_ms": 123.45,
                "error": None  # 错误信息（如果失败）
            }
        
        示例:
            result = await db_tools.query(
                "SELECT id, title, season FROM anime LIMIT 10"
            )
            
            if result['success']:
                print(f"查询成功，返回 {result['row_count']} 行")
                for row in result['rows']:
                    print(row)
            else:
                print(f"查询失败: {result['error']}")
        """
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
        result = await self.query(f"SELECT COUNT(*) as count FROM {table_name}")
        if result['success'] and result['rows']:
            return result['rows'][0]['count']
        return None
    
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


# ===== 全局实例（单例模式）=====
_db_tools_instance: Optional[DatabaseTools] = None


def get_database_tools() -> DatabaseTools:
    """获取数据库工具实例（单例）"""
    global _db_tools_instance
    if _db_tools_instance is None:
        _db_tools_instance = DatabaseTools()
    return _db_tools_instance
