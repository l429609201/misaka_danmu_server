"""
LLM 数据库检索工具

为 LLM 提供安全的数据库查询能力，包括：
1. ORM 结构查询（表结构、字段、关系）
2. 安全的 SQL 查询（敏感字段自动脱敏）
3. 查询结果格式化

安全策略：
- 只读查询（SELECT、EXPLAIN、SHOW、DESCRIBE）
- 敏感字段自动脱敏（password、token、secret、key 等）
- SQL 注入防护
- 查询超时限制
- 结果集大小限制
"""

import re
import json
import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from decimal import Decimal
from sqlalchemy import inspect, text, MetaData, Table
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import class_mapper
from sqlalchemy.exc import SQLAlchemyError

from src.db.orm_models import Base

logger = logging.getLogger(__name__)


# ===== 敏感字段配置 =====
SENSITIVE_FIELD_PATTERNS = [
    'password', 'passwd', 'pwd',
    'token', 'access_token', 'refresh_token', 'api_token',
    'secret', 'app_secret', 'client_secret', 'otp_secret',
    'key', 'api_key', 'private_key', 'public_key',
    'credential', 'auth', 'hash',
]

# ===== SQL 安全配置 =====
ALLOWED_SQL_KEYWORDS = ['SELECT', 'EXPLAIN', 'SHOW', 'DESCRIBE', 'DESC', 'WITH']
FORBIDDEN_SQL_KEYWORDS = [
    'INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'CREATE', 'TRUNCATE',
    'GRANT', 'REVOKE', 'EXEC', 'EXECUTE', '--', '/*', '*/', ';'
]

MAX_QUERY_RESULTS = 1000  # 最大返回行数
QUERY_TIMEOUT_SECONDS = 30  # 查询超时时间


class LLMDatabaseTools:
    """LLM 数据库检索工具类"""

    def __init__(self, session_factory):
        self.session_factory = session_factory

    # ===== ORM 结构查询 =====

    async def get_all_tables(self) -> List[Dict[str, Any]]:
        """
        获取所有 ORM 表的基本信息

        返回格式：
        [
            {
                "table_name": "anime",
                "model_class": "Anime",
                "comment": "动画番剧主表",
                "column_count": 10,
                "has_relationships": True
            },
            ...
        ]
        """
        try:
            tables_info = []
            
            for mapper in Base.registry.mappers:
                model_class = mapper.class_
                table = mapper.local_table
                
                # 获取关系数量
                relationships = [rel.key for rel in mapper.relationships]
                
                table_info = {
                    "table_name": table.name,
                    "model_class": model_class.__name__,
                    "comment": table.comment or "",
                    "column_count": len(table.columns),
                    "has_relationships": len(relationships) > 0,
                    "relationship_count": len(relationships)
                }
                
                tables_info.append(table_info)
            
            # 按表名排序
            tables_info.sort(key=lambda x: x['table_name'])
            
            logger.info(f"获取 ORM 表列表成功，共 {len(tables_info)} 个表")
            return tables_info
            
        except Exception as e:
            logger.error(f"获取表列表失败: {e}", exc_info=True)
            raise

    async def get_table_schema(self, table_name: str) -> Dict[str, Any]:
        """
        获取指定表的详细结构

        参数：
            table_name: 表名（如 'anime', 'episode'）

        返回格式：
        {
            "table_name": "anime",
            "model_class": "Anime",
            "comment": "动画番剧主表",
            "columns": [
                {
                    "name": "id",
                    "type": "BigInteger",
                    "nullable": False,
                    "primary_key": True,
                    "comment": "主键ID",
                    "is_sensitive": False
                },
                ...
            ],
            "relationships": [
                {
                    "name": "sources",
                    "target_model": "AnimeSource",
                    "type": "one-to-many"
                },
                ...
            ],
            "indexes": [...]
        }
        """
        try:
            # 查找对应的 ORM 模型
            model_class = None
            for mapper in Base.registry.mappers:
                if mapper.local_table.name == table_name:
                    model_class = mapper.class_
                    break
            
            if not model_class:
                raise ValueError(f"表 '{table_name}' 不存在")
            
            mapper = class_mapper(model_class)
            table = mapper.local_table
            
            # 提取列信息
            columns = []
            for column in table.columns:
                column_info = {
                    "name": column.name,
                    "type": str(column.type),
                    "nullable": column.nullable,
                    "primary_key": column.primary_key,
                    "foreign_key": column.foreign_keys is not None and len(column.foreign_keys) > 0,
                    "comment": column.comment or "",
                    "is_sensitive": self._is_sensitive_field(column.name)
                }
                
                # 添加默认值
                if column.default is not None:
                    column_info["default"] = str(column.default.arg) if hasattr(column.default, 'arg') else str(column.default)
                
                columns.append(column_info)
            
            # 提取关系信息
            relationships = []
            for rel in mapper.relationships:
                rel_info = {
                    "name": rel.key,
                    "target_model": rel.mapper.class_.__name__,
                    "type": "one-to-many" if rel.uselist else "many-to-one"
                }
                relationships.append(rel_info)
            
            # 提取索引信息
            indexes = []
            for index in table.indexes:
                index_info = {
                    "name": index.name,
                    "columns": [col.name for col in index.columns],
                    "unique": index.unique
                }
                indexes.append(index_info)
            
            schema = {
                "table_name": table.name,
                "model_class": model_class.__name__,
                "comment": table.comment or "",
                "columns": columns,
                "relationships": relationships,
                "indexes": indexes
            }
            
            logger.info(f"获取表 '{table_name}' 结构成功")
            return schema
            
        except Exception as e:
            logger.error(f"获取表结构失败: {e}", exc_info=True)
            raise

    async def search_tables(self, keyword: str) -> List[Dict[str, Any]]:
        """
        按关键词搜索表和字段

        参数：
            keyword: 搜索关键词（匹配表名、字段名、注释）

        返回格式：
        [
            {
                "table_name": "anime",
                "model_class": "Anime",
                "match_type": "table_name",  # 或 "column_name" 或 "comment"
                "matched_columns": ["title", "season"]  # 匹配的字段（如果是字段匹配）
            },
            ...
        ]
        """
        try:
            keyword_lower = keyword.lower()
            results = []
            
            for mapper in Base.registry.mappers:
                model_class = mapper.class_
                table = mapper.local_table
                matched_columns = []
                match_type = None
                
                # 匹配表名
                if keyword_lower in table.name.lower():
                    match_type = "table_name"
                
                # 匹配表注释
                if table.comment and keyword_lower in table.comment.lower():
                    if not match_type:
                        match_type = "table_comment"
                
                # 匹配字段名和注释
                for column in table.columns:
                    if keyword_lower in column.name.lower():
                        matched_columns.append(column.name)
                        if not match_type:
                            match_type = "column_name"
                    elif column.comment and keyword_lower in column.comment.lower():
                        matched_columns.append(column.name)
                        if not match_type:
                            match_type = "column_comment"
                
                if match_type:
                    result = {
                        "table_name": table.name,
                        "model_class": model_class.__name__,
                        "match_type": match_type,
                        "matched_columns": matched_columns
                    }
                    results.append(result)
            
            logger.info(f"搜索关键词 '{keyword}' 匹配到 {len(results)} 个表")
            return results
            
        except Exception as e:
            logger.error(f"搜索表失败: {e}", exc_info=True)
            raise

    # ===== 安全的 SQL 查询 =====

    async def execute_safe_query(
        self,
        sql: str,
        max_rows: int = 100,
        timeout: int = QUERY_TIMEOUT_SECONDS
    ) -> Dict[str, Any]:
        """
        执行安全的只读 SQL 查询

        安全措施：
        1. 只允许 SELECT/EXPLAIN/SHOW/DESCRIBE
        2. 自动脱敏敏感字段
        3. 限制返回行数
        4. 查询超时保护
        5. 在事务中执行并回滚（确保无副作用）

        参数：
            sql: SQL 查询语句
            max_rows: 最大返回行数（默认 100，最大 1000）
            timeout: 查询超时时间（秒）

        返回格式：
        {
            "success": True,
            "columns": ["id", "title", "season"],
            "rows": [
                {"id": 1, "title": "xxx", "season": 1},
                ...
            ],
            "row_count": 10,
            "truncated": False,
            "masked_columns": ["api_token"],  # 被脱敏的字段
            "execution_time_ms": 123.45,
            "error": None
        }
        """
        start_time = datetime.now()
        
        try:
            # 1. 安全检查
            self._validate_sql(sql)
            
            # 2. 限制返回行数
            max_rows = min(max_rows, MAX_QUERY_RESULTS)
            
            # 3. 执行查询
            async with self.session_factory() as session:
                # 设置查询超时（MySQL）
                await session.execute(text(f"SET SESSION max_execution_time = {timeout * 1000}"))
                
                # 在事务中执行（确保只读）
                async with session.begin():
                    result = await session.execute(text(sql))
                    
                    # 获取列名
                    columns = list(result.keys()) if result.keys() else []
                    
                    # 识别敏感字段
                    masked_columns = [col for col in columns if self._is_sensitive_field(col)]
                    
                    # 获取数据行
                    rows_raw = result.fetchmany(max_rows + 1)  # 多取一行判断是否截断
                    truncated = len(rows_raw) > max_rows
                    rows_raw = rows_raw[:max_rows]
                    
                    # 转换为字典列表并脱敏
                    rows = []
                    for row in rows_raw:
                        row_dict = dict(zip(columns, row))
                        row_dict = self._mask_sensitive_data(row_dict, masked_columns)
                        rows.append(row_dict)
                    
                    # 强制回滚（确保无副作用）
                    await session.rollback()
            
            execution_time_ms = (datetime.now() - start_time).total_seconds() * 1000
            
            return {
                "success": True,
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "truncated": truncated,
                "masked_columns": masked_columns,
                "execution_time_ms": round(execution_time_ms, 2),
                "error": None
            }
            
        except SQLAlchemyError as e:
            execution_time_ms = (datetime.now() - start_time).total_seconds() * 1000
            error_msg = str(e)
            logger.error(f"SQL 查询失败: {error_msg}", exc_info=True)
            
            return {
                "success": False,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
                "masked_columns": [],
                "execution_time_ms": round(execution_time_ms, 2),
                "error": error_msg
            }
        
        except Exception as e:
            execution_time_ms = (datetime.now() - start_time).total_seconds() * 1000
            error_msg = f"查询执行异常: {str(e)}"
            logger.error(error_msg, exc_info=True)
            
            return {
                "success": False,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
                "masked_columns": [],
                "execution_time_ms": round(execution_time_ms, 2),
                "error": error_msg
            }

    async def explain_query(self, sql: str) -> Dict[str, Any]:
        """
        分析 SQL 查询计划（EXPLAIN）

        参数：
            sql: SELECT 查询语句

        返回格式：
        {
            "success": True,
            "plan": [
                {
                    "id": 1,
                    "select_type": "SIMPLE",
                    "table": "anime",
                    "type": "ALL",
                    "possible_keys": None,
                    "key": None,
                    "rows": 50000,
                    "Extra": "Using where"
                }
            ],
            "warnings": ["全表扫描", "未使用索引"],
            "suggestions": ["建议在 anime.title 上添加索引"]
        }
        """
        try:
            # 验证是 SELECT 语句
            if not sql.strip().upper().startswith('SELECT'):
                raise ValueError("EXPLAIN 只支持 SELECT 语句")
            
            # 执行 EXPLAIN
            explain_sql = f"EXPLAIN {sql}"
            result = await self.execute_safe_query(explain_sql, max_rows=100)
            
            if not result["success"]:
                return result
            
            # 分析执行计划
            warnings = []
            suggestions = []
            
            for row in result["rows"]:
                # 检测全表扫描
                if row.get("type") == "ALL":
                    warnings.append(f"表 '{row.get('table')}' 执行全表扫描")
                    suggestions.append(f"考虑在 {row.get('table')} 表的 WHERE 条件字段上添加索引")
                
                # 检测未使用索引
                if row.get("possible_keys") and not row.get("key"):
                    warnings.append(f"表 '{row.get('table')}' 有可用索引但未使用")
                
                # 检测扫描行数过多
                if row.get("rows") and row.get("rows") > 10000:
                    warnings.append(f"表 '{row.get('table')}' 扫描行数过多 ({row.get('rows')} 行)")
            
            return {
                "success": True,
                "plan": result["rows"],
                "warnings": warnings,
                "suggestions": suggestions,
                "error": None
            }
            
        except Exception as e:
            logger.error(f"EXPLAIN 查询失败: {e}", exc_info=True)
            return {
                "success": False,
                "plan": [],
                "warnings": [],
                "suggestions": [],
                "error": str(e)
            }

    # ===== 辅助方法 =====

    def _validate_sql(self, sql: str):
        """
        验证 SQL 语句安全性

        检查项：
        1. 只允许 SELECT/EXPLAIN/SHOW/DESCRIBE
        2. 禁止危险关键字
        3. 禁止 SQL 注释
        """
        sql_upper = sql.strip().upper()
        
        # 检查允许的关键字
        is_allowed = any(sql_upper.startswith(kw) for kw in ALLOWED_SQL_KEYWORDS)
        if not is_allowed:
            raise ValueError(
                f"只允许以下 SQL 关键字开头: {', '.join(ALLOWED_SQL_KEYWORDS)}"
            )
        
        # 检查禁止的关键字
        for forbidden in FORBIDDEN_SQL_KEYWORDS:
            if forbidden in sql_upper:
                raise ValueError(f"SQL 语句包含禁止的关键字: {forbidden}")
        
        logger.debug(f"SQL 安全验证通过: {sql[:100]}...")

    def _is_sensitive_field(self, field_name: str) -> bool:
        """判断字段是否为敏感字段"""
        field_lower = field_name.lower()
        return any(pattern in field_lower for pattern in SENSITIVE_FIELD_PATTERNS)

    def _mask_sensitive_data(self, row_dict: Dict[str, Any], masked_columns: List[str]) -> Dict[str, Any]:
        """
        脱敏敏感字段

        脱敏规则：
        - 字符串：显示前4位***后4位(len=N)
        - 数字/其他：***REDACTED***
        """
        for col in masked_columns:
            if col in row_dict and row_dict[col] is not None:
                value = row_dict[col]
                
                if isinstance(value, str) and len(value) > 8:
                    # 前4位***后4位(len=N)
                    masked = f"{value[:4]}***{value[-4:]}(len={len(value)})"
                else:
                    masked = "***REDACTED***"
                
                row_dict[col] = masked
        
        return row_dict


# ===== 全局实例（供 API 调用）=====
_llm_db_tools_instance: Optional[LLMDatabaseTools] = None


def init_llm_db_tools(session_factory):
    """初始化 LLM 数据库工具"""
    global _llm_db_tools_instance
    _llm_db_tools_instance = LLMDatabaseTools(session_factory)
    logger.info("LLM 数据库检索工具已初始化")


def get_llm_db_tools() -> LLMDatabaseTools:
    """获取 LLM 数据库工具实例"""
    if _llm_db_tools_instance is None:
        raise RuntimeError("LLM 数据库工具未初始化，请先调用 init_llm_db_tools()")
    return _llm_db_tools_instance


# ===== 常用查询模板（供 LLM 参考）=====
SQL_TEMPLATES = {
    "查看弹幕库列表": """
        SELECT id, title, type, season, episode_count, source_count, created_at
        FROM anime
        ORDER BY created_at DESC
        LIMIT 20
    """,

    "查询单个作品详情": """
        SELECT a.*,
               (SELECT COUNT(*) FROM anime_source WHERE anime_id = a.id) as source_count,
               (SELECT COUNT(*) FROM episode WHERE anime_source_id IN (SELECT id FROM anime_source WHERE anime_id = a.id)) as episode_count
        FROM anime a
        WHERE a.id = {anime_id}
    """,

    "查询数据库表大小": """
        SELECT
            table_name,
            ROUND(((data_length + index_length) / 1024 / 1024), 2) AS size_mb,
            table_rows
        FROM information_schema.TABLES
        WHERE table_schema = DATABASE()
        ORDER BY (data_length + index_length) DESC
        LIMIT 20
    """,

    "查询慢查询日志": """
        SELECT
            sql_text,
            query_time,
            lock_time,
            rows_examined,
            start_time
        FROM mysql.slow_log
        WHERE start_time >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
        ORDER BY query_time DESC
        LIMIT 10
    """,

    "查询性能指标统计": """
        SELECT
            category,
            metric_name,
            AVG(value_float) as avg_value,
            MAX(value_float) as max_value,
            MIN(value_float) as min_value,
            COUNT(*) as sample_count
        FROM system_metrics
        WHERE collected_at >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
          AND value_float IS NOT NULL
        GROUP BY category, metric_name
        ORDER BY category, metric_name
    """,

    "查询未解决的告警": """
        SELECT
            metric_category,
            metric_name,
            alert_level,
            alert_message,
            current_value,
            threshold_value,
            created_at
        FROM performance_alerts
        WHERE is_resolved = 0
        ORDER BY alert_level DESC, created_at DESC
        LIMIT 20
    """,
}
