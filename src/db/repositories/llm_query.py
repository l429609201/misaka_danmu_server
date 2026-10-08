"""LLM 只读数据库查询仓储，隔离 ORM 元数据和 SQL 执行。"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.orm_models import Base


class LLMQueryRepository:
    """仅在 DatabaseService 事务内运行只读查询及模型结构读取。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_table_models(self) -> list[dict[str, Any]]:
        """以普通数据返回 ORM 模型结构，不向服务层暴露 ORM 对象。"""
        tables = []
        for mapper in Base.registry.mappers:
            table = mapper.local_table
            tables.append({
                "table_name": table.name,
                "model_class": mapper.class_.__name__,
                "comment": table.comment or "",
                "columns": [{
                    "name": column.name,
                    "type": str(column.type),
                    "nullable": column.nullable,
                    "primary_key": column.primary_key,
                    "foreign_key": bool(column.foreign_keys),
                    "comment": column.comment or "",
                    "default": str(column.default.arg) if column.default is not None else None,
                } for column in table.columns],
                "relationships": [{
                    "name": rel.key,
                    "target_model": rel.mapper.class_.__name__,
                    "type": "one-to-many" if rel.uselist else "many-to-one",
                } for rel in mapper.relationships],
                "indexes": [{
                    "name": index.name,
                    "columns": [column.name for column in index.columns],
                    "unique": index.unique,
                } for index in table.indexes],
            })
        return tables

    async def execute_readonly(
        self, sql: str, max_rows: int, timeout: int
    ) -> tuple[list[str], list[tuple[Any, ...]], bool]:
        """执行已由服务层验证的只读 SQL，返回有限条目。"""
        await self._session.execute(
            text("SET SESSION max_execution_time = :timeout_ms"),
            {"timeout_ms": timeout * 1000},
        )
        result = await self._session.execute(text(sql))
        columns = list(result.keys())
        rows = result.fetchmany(max_rows + 1)
        return columns, [tuple(row) for row in rows[:max_rows]], len(rows) > max_rows
