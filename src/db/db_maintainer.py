"""
数据库维护管理器

自动检测 ORM 模型与数据库结构的差异，并进行安全的自动同步：
1. 添加缺失的字段
2. 安全扩展字段类型（如 TEXT → MEDIUMTEXT, VARCHAR(n) → VARCHAR(m) where m > n）

不会自动执行的操作（需要手动迁移脚本）：
- 重命名字段/表
- 收缩字段类型
- 危险的类型转换
- 数据迁移/填充
"""

import logging
from typing import Dict, Set, Tuple, Optional
from sqlalchemy import text, inspect
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.engine import Inspector
from sqlalchemy.dialects import mysql as mysql_dialect, postgresql as pg_dialect

from .orm_models import Base

logger = logging.getLogger(__name__)

# 安全类型扩展映射（小类型 -> 可扩展的大类型列表）
SAFE_TYPE_EXPANSIONS = {
    # MySQL
    'text': ['mediumtext', 'longtext'],
    'tinytext': ['text', 'mediumtext', 'longtext'],
    'mediumtext': ['longtext'],
    'tinyint': ['smallint', 'int', 'integer', 'bigint'],
    'smallint': ['int', 'integer', 'bigint'],
    'int': ['bigint'],
    'integer': ['bigint'],
    'float': ['double', 'double precision'],
    'real': ['double', 'double precision'],
    # PostgreSQL
    'smallserial': ['serial', 'bigserial'],
    'serial': ['bigserial'],
}

# 等效类型映射（这些类型在功能上是等效的，不应发出警告）
# 使用 frozenset 表示一组等效的类型
EQUIVALENT_TYPE_GROUPS = [
    frozenset({'int', 'integer'}),  # MySQL INT 和 INTEGER 是同义词
    frozenset({'tinyint', 'boolean', 'bool'}),  # MySQL 用 TINYINT(1) 表示布尔值
    frozenset({'datetime', 'timestamp', 'timestamp without time zone', 'timestamp with time zone'}),  # 时间戳类型等效
    frozenset({'text', 'mediumtext'}),  # TEXT 变体，通常可互换
    frozenset({'double', 'double precision', 'float8'}),  # 浮点数变体
    frozenset({'varchar', 'character varying'}),  # PostgreSQL VARCHAR 别名
    frozenset({'enum', 'user-defined'}),  # PostgreSQL 枚举类型
    # PG/MySQL 中 DECIMAL 与 NUMERIC 完全等价（PG 的 information_schema 一律报 numeric，
    # 而 SQLAlchemy 的 DECIMAL 列类型名解析为 decimal），避免每次同步误报类型不匹配
    frozenset({'numeric', 'decimal'}),
]


def _are_types_equivalent(type1: str, type2: str) -> bool:
    """检查两个类型名是否在功能上等效"""
    type1 = type1.lower()
    type2 = type2.lower()

    if type1 == type2:
        return True

    for group in EQUIVALENT_TYPE_GROUPS:
        if type1 in group and type2 in group:
            return True

    return False


async def _get_db_columns(conn: AsyncConnection, db_type: str, table_name: str) -> Dict[str, dict]:
    """获取数据库表的实际字段信息"""
    if db_type == "mysql":
        sql = text(f"""
            SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE, COLUMN_DEFAULT
            FROM information_schema.columns 
            WHERE table_schema = DATABASE() AND table_name = :table_name
        """)
    else:  # postgresql
        sql = text(f"""
            SELECT column_name, data_type, character_maximum_length, is_nullable, column_default
            FROM information_schema.columns 
            WHERE table_name = :table_name
        """)
    
    result = await conn.execute(sql, {"table_name": table_name})
    columns = {}
    for row in result.fetchall():
        col_name = row[0]
        columns[col_name] = {
            'data_type': row[1].lower() if row[1] else '',
            'max_length': row[2],
            'is_nullable': row[3],
            'default': row[4]
        }
    return columns


def _get_sqlalchemy_column_type(column) -> Tuple[str, Optional[int]]:
    """从 SQLAlchemy Column 对象获取类型名和长度"""
    col_type = column.type
    type_name = col_type.__class__.__name__.lower()
    
    # 处理常见类型映射
    type_mapping = {
        'string': 'varchar',
        'biginteger': 'bigint',
        'smallinteger': 'smallint',
        'naivedatetime': 'datetime',  # 自定义类型
    }
    type_name = type_mapping.get(type_name, type_name)
    
    # 获取长度（如果有）
    length = getattr(col_type, 'length', None)
    
    return type_name, length


def _is_safe_type_expansion(old_type: str, new_type: str, old_length: Optional[int], new_length: Optional[int]) -> bool:
    """检查是否为安全的类型扩展"""
    old_type = old_type.lower()
    new_type = new_type.lower()
    
    # 同类型，检查长度扩展
    if old_type == new_type:
        if old_length is not None and new_length is not None:
            return new_length >= old_length
        return True  # 无长度信息，认为安全
    
    # 检查类型扩展
    if old_type in SAFE_TYPE_EXPANSIONS:
        return new_type in SAFE_TYPE_EXPANSIONS[old_type]
    
    return False


def _build_add_column_sql(table_name: str, column, db_type: str) -> str:
    """构建 ALTER TABLE ADD COLUMN 语句"""
    col_name = column.name
    col_type = column.type

    # 获取数据库原生类型 - 根据数据库类型使用正确的 dialect
    # PostgreSQL 没有 DATETIME 类型，需要使用 TIMESTAMP
    if db_type == "mysql":
        dialect = mysql_dialect.dialect()
    else:  # postgresql
        dialect = pg_dialect.dialect()
    type_str = col_type.compile(dialect=dialect)
    
    # 处理 nullable
    nullable = column.nullable if column.nullable is not None else True
    null_str = "NULL" if nullable else "NOT NULL"
    
    # 处理默认值
    # MySQL strict mode 下，TEXT/BLOB/JSON/GEOMETRY 类型不允许设置 DEFAULT 值
    # 这些类型的默认值由 SQLAlchemy ORM 层（Python 侧）处理即可
    default_str = ""
    type_str_upper = str(type_str).upper()
    is_lob_type = any(t in type_str_upper for t in ('TEXT', 'BLOB', 'JSON', 'GEOMETRY'))

    if column.default is not None and not (db_type == "mysql" and is_lob_type):
        if hasattr(column.default, 'arg'):
            default_val = column.default.arg
            if callable(default_val):
                default_str = ""  # 函数默认值不在 DDL 中处理
            elif isinstance(default_val, bool):
                default_str = f"DEFAULT {'TRUE' if default_val else 'FALSE'}"
            elif isinstance(default_val, (int, float)):
                default_str = f"DEFAULT {default_val}"
            elif isinstance(default_val, str):
                default_str = f"DEFAULT '{default_val}'"
    
    if db_type == "mysql":
        return f"ALTER TABLE `{table_name}` ADD COLUMN `{col_name}` {type_str} {null_str} {default_str}".strip()
    else:  # postgresql
        return f'ALTER TABLE "{table_name}" ADD COLUMN "{col_name}" {type_str} {null_str} {default_str}'.strip()


def _build_modify_column_sql(table_name: str, col_name: str, new_type: str, db_type: str) -> str:
    """构建修改字段类型的 SQL"""
    if db_type == "mysql":
        return f"ALTER TABLE `{table_name}` MODIFY COLUMN `{col_name}` {new_type}"
    else:  # postgresql
        return f'ALTER TABLE "{table_name}" ALTER COLUMN "{col_name}" TYPE {new_type}'


async def _check_table_exists(conn: AsyncConnection, db_type: str, table_name: str) -> bool:
    """检查表是否存在"""
    if db_type == "mysql":
        sql = text("SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = :table_name")
    else:  # postgresql
        sql = text("SELECT 1 FROM information_schema.tables WHERE table_name = :table_name")

    result = await conn.execute(sql, {"table_name": table_name})
    return result.scalar_one_or_none() is not None


async def sync_database_schema(conn: AsyncConnection, db_type: str):
    """
    同步数据库结构，自动补充缺失的字段和安全扩展字段类型。

    :param conn: 数据库连接
    :param db_type: 数据库类型 ('mysql' 或 'postgresql')
    """
    logger.info("数据库维护管理器开始同步...")

    stats = {
        'tables_checked': 0,
        'columns_added': 0,
        'types_expanded': 0,
        'warnings': 0
    }

    for table in Base.metadata.tables.values():
        table_name = table.name

        # 检查表是否存在（create_all 应该已经创建了）
        if not await _check_table_exists(conn, db_type, table_name):
            logger.debug(f"表 {table_name} 不存在，跳过（应由 create_all 创建）")
            continue

        stats['tables_checked'] += 1

        # 获取 ORM 模型定义的字段
        model_columns = {col.name: col for col in table.columns}

        # 获取数据库实际的字段
        db_columns = await _get_db_columns(conn, db_type, table_name)

        # 找出缺失的字段
        missing_columns = set(model_columns.keys()) - set(db_columns.keys())

        # 添加缺失的字段
        for col_name in missing_columns:
            column = model_columns[col_name]
            try:
                sql = _build_add_column_sql(table_name, column, db_type)
                await conn.execute(text(sql))
                logger.info(f"表 {table_name}: 添加字段 {col_name}")
                stats['columns_added'] += 1
            except Exception as e:
                logger.error(f"表 {table_name}: 添加字段 {col_name} 失败: {e}")
                stats['warnings'] += 1

        # 检查现有字段的类型是否需要扩展
        for col_name, db_col_info in db_columns.items():
            if col_name not in model_columns:
                # 数据库有但模型没有的字段，跳过（不自动删除）
                continue

            model_col = model_columns[col_name]
            model_type, model_length = _get_sqlalchemy_column_type(model_col)
            db_type_name = db_col_info['data_type']
            db_length = db_col_info['max_length']

            # 检查类型是否等效（如 int/integer, tinyint/boolean 等）
            types_equivalent = _are_types_equivalent(db_type_name, model_type)

            # 检查是否需要类型扩展（仅当类型不等效时才检查）
            if not types_equivalent or (model_length and db_length and model_length > db_length):
                if _is_safe_type_expansion(db_type_name, model_type, db_length, model_length):
                    try:
                        # 构建新类型字符串
                        new_type = model_col.type.compile(dialect=None)
                        sql = _build_modify_column_sql(table_name, col_name, new_type, db_type)
                        await conn.execute(text(sql))
                        logger.info(f"表 {table_name}: 字段 {col_name} 类型扩展 {db_type_name} → {model_type}")
                        stats['types_expanded'] += 1
                    except Exception as e:
                        logger.warning(f"表 {table_name}: 字段 {col_name} 类型扩展失败: {e}")
                        stats['warnings'] += 1
                else:
                    # 类型不匹配且不是安全扩展，发出警告（跳过等效类型）
                    if not types_equivalent:
                        logger.warning(
                            f"表 {table_name}: 字段 {col_name} 类型不匹配 "
                            f"(数据库: {db_type_name}, 模型: {model_type})，跳过自动变更，请手动处理"
                        )
                        stats['warnings'] += 1

    logger.info(
        f"数据库结构同步完成: 检查 {stats['tables_checked']} 个表, "
        f"添加 {stats['columns_added']} 个字段, "
        f"扩展 {stats['types_expanded']} 个类型, "
        f"{stats['warnings']} 个警告"
    )

