"""LLM 弹幕数据库诊断：仅暴露固定只读查询和受限表结构。"""

import logging
from typing import List, Dict, Any, Optional

from src.services.service_container import get_database_service

logger = logging.getLogger(__name__)


# ===== 敏感字段配置 =====
SENSITIVE_FIELD_PATTERNS = [
    'password', 'passwd', 'pwd',
    'token', 'access_token', 'refresh_token', 'api_token',
    'secret', 'app_secret', 'client_secret', 'otp_secret',
    'key', 'api_key', 'private_key', 'public_key',
    'credential', 'auth', 'hash',
]

# LLM 自检只覆盖弹幕业务数据；配置仅通过固定键元数据入口读取。
DANMAKU_TABLES = frozenset({
    "anime", "anime_groups", "anime_sources", "episode", "local_danmaku_items",
    "anime_aliases", "anime_metadata", "tmdb_episode_mapping", "scrapers", "config",
})
_CONFIG_SAFE_COLUMNS = frozenset({"config_key", "description"})
_CONFIG_METADATA = {
    "searchTtlSeconds": "搜索缓存有效期",
    "episodesTtlSeconds": "分集缓存有效期",
    "danmakuChConvert": "弹幕简繁转换方式",
}
QUERY_TIMEOUT_SECONDS = 30


class LLMDatabaseTools:
    """LLM 数据库检索工具类。"""

    def __init__(self) -> None:
        """使用服务容器中的 DatabaseService 作为数据库唯一入口。"""

    # ===== ORM 结构查询 =====

    async def get_all_tables(self) -> List[Dict[str, Any]]:
        """返回弹幕业务表结构；配置表仅展示安全字段名。"""
        db = get_database_service()
        async with db.transaction():
            tables = await db.llm_query.list_table_models()
        result = []
        for table in tables:
            if table.get("table_name") not in DANMAKU_TABLES:
                continue
            if table["table_name"] == "config":
                # 配置表结构也只展示专用入口实际可返回的字段。
                result.append({
                    "table_name": "config",
                    "columns": [
                        {"name": column["name"], "type": column["type"]}
                        for column in table.get("columns", [])
                        if column.get("name") in _CONFIG_SAFE_COLUMNS
                    ],
                })
            else:
                result.append({
                    **table,
                    "columns": [
                        column for column in table.get("columns", [])
                        if not self._is_sensitive_field(column.get("name", ""))
                    ],
                })
        return result

    async def get_config_metadata(self) -> List[Dict[str, str]]:
        """仅读取固定弹幕配置键是否存在，不传出配置值或数据库自由文本。"""
        db = get_database_service()
        async with db.transaction():
            # SQL 文本、投影和行键均为代码常量，调用方不能传入查询片段。
            columns, rows, _ = await db.llm_query.execute_readonly(
                "SELECT config_key FROM config WHERE config_key IN "
                "('searchTtlSeconds', 'episodesTtlSeconds', 'danmakuChConvert')",
                len(_CONFIG_METADATA), QUERY_TIMEOUT_SECONDS,
            )
        if columns != ["config_key"]:
            raise ValueError("配置元数据字段不符合白名单")
        return [
            {"config_key": key, "description": _CONFIG_METADATA[key]}
            for (key,) in rows
            if key in _CONFIG_METADATA
        ]

    async def get_danmaku_row_count(self, table_name: str) -> int:
        """仅统计固定弹幕业务表行数，不接受 SQL 或配置表名。"""
        allowed = DANMAKU_TABLES - {"config"}
        if not isinstance(table_name, str) or table_name not in allowed:
            raise ValueError("只能统计弹幕业务表")
        db = get_database_service()
        async with db.transaction():
            columns, rows, _ = await db.llm_query.execute_readonly(
                f"SELECT COUNT(*) AS row_count FROM `{table_name}`",
                1, QUERY_TIMEOUT_SECONDS,
            )
        if columns != ["row_count"] or len(rows) != 1 or len(rows[0]) != 1:
            raise ValueError("统计结果格式不符合预期")
        return int(rows[0][0])

    async def get_recent_anime(self) -> List[Dict[str, Any]]:
        """读取固定的近期作品摘要，保留常见弹幕库诊断能力。"""
        db = get_database_service()
        async with db.transaction():
            columns, rows, _ = await db.llm_query.execute_readonly(
                "SELECT id, title FROM anime ORDER BY id DESC LIMIT 20",
                20, QUERY_TIMEOUT_SECONDS,
            )
        if columns != ["id", "title"]:
            raise ValueError("作品摘要字段不符合白名单")
        return [{"id": anime_id, "title": title} for anime_id, title in rows]

    async def get_table_schema(self, table_name: str) -> Dict[str, Any]:
        """返回指定弹幕业务表结构，禁止访问敏感或流控表。"""
        normalized = str(table_name or "").strip().strip("`").lower()
        if normalized not in DANMAKU_TABLES:
            raise ValueError("AI 只能查询弹幕业务表结构")
        tables = await self.get_all_tables()
        for table in tables:
            if table.get("table_name") == normalized:
                return table
        raise ValueError(f"弹幕业务表不存在: {normalized}")

    async def search_tables(self, keyword: str) -> List[Dict[str, Any]]:
        """在允许的弹幕业务表及字段中搜索结构。"""
        needle = str(keyword or "").strip().lower()
        if not needle:
            return []
        return [
            table for table in await self.get_all_tables()
            if needle in table.get("table_name", "").lower()
            or needle in table.get("comment", "").lower()
            or any(needle in col.get("name", "").lower() for col in table.get("columns", []))
        ]

    # ===== 安全的 SQL 查询 =====

    async def execute_safe_query(
        self,
        sql: str,
        max_rows: int = 100,
        timeout: int = QUERY_TIMEOUT_SECONDS
    ) -> Dict[str, Any]:
        """兼容旧接口，拒绝所有用户传入的 SQL。"""
        return {
            "success": False,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "truncated": False,
            "masked_columns": [],
            "execution_time_ms": 0.0,
            "error": "任意 SQL 查询已关闭，请使用固定弹幕诊断工具",
        }

    async def explain_query(self, sql: str) -> Dict[str, Any]:
        """兼容旧接口，拒绝用户传入的查询计划。"""
        # EXPLAIN 仍会执行用户提供的查询结构，不能作为安全例外。
        return {
            "success": False,
            "plan": [],
            "warnings": [],
            "suggestions": [],
            "error": "任意 SQL 查询计划已关闭，请使用固定弹幕诊断工具",
        }

    def _is_sensitive_field(self, field_name: str) -> bool:
        """判断字段是否为敏感字段。"""
        field_lower = field_name.lower()
        return any(pattern in field_lower for pattern in SENSITIVE_FIELD_PATTERNS)


# ===== 全局实例（供 API 调用）=====
_llm_db_tools_instance: Optional[LLMDatabaseTools] = None


def init_llm_db_tools() -> None:
    """初始化 LLM 数据库工具"""
    global _llm_db_tools_instance
    _llm_db_tools_instance = LLMDatabaseTools()
    logger.info("LLM 数据库检索工具已初始化")


def get_llm_db_tools() -> LLMDatabaseTools:
    """获取 LLM 数据库工具实例"""
    if _llm_db_tools_instance is None:
        raise RuntimeError("LLM 数据库工具未初始化，请先调用 init_llm_db_tools()")
    return _llm_db_tools_instance

