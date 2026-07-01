"""
数据库备份定时任务
使用 JSON 格式导出数据，支持跨数据库（MySQL/PostgreSQL）兼容
"""
import gzip
import hashlib
import json
import logging
import re
import base64
import enum
import uuid
from datetime import datetime, date, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable, List, Dict, Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, inspect
from sqlalchemy.orm import selectinload

from src.db import crud, orm_models
from src.core import settings, get_now
from .base import BaseJob
from src.services import TaskSuccess

logger = logging.getLogger(__name__)

# 默认备份路径
DEFAULT_BACKUP_PATH = "/app/config/sql_backup"
DEFAULT_RETENTION_COUNT = 5


def _build_backup_tables() -> List[tuple]:
    """
    自动发现所有 ORM 模型并按外键依赖拓扑排序。
    SQLAlchemy 的 sorted_tables 已处理好依赖顺序（被依赖的表在前），
    新增 ORM 模型后自动纳入备份，无需手动维护列表。
    """
    # tablename -> ORM class 反向映射
    table_to_class = {
        mapper.local_table.name: mapper.class_
        for mapper in orm_models.Base.registry.mappers
    }
    # 按拓扑排序遍历（SQLAlchemy 已处理外键依赖顺序）
    result = []
    for table in orm_models.Base.metadata.sorted_tables:
        cls = table_to_class.get(table.name)
        if cls is not None:
            result.append((table.name, cls))
    return result


# 动态生成备份表列表（自动包含所有 ORM 模型，按外键依赖正确排序）
BACKUP_TABLES = _build_backup_tables()


def get_column_mapping(model_class) -> Dict[str, str]:
    """获取数据库列名到 Python 属性名的映射"""
    mapper = inspect(model_class)
    mapping = {}
    for attr_name, column_prop in mapper.column_attrs.items():
        # attr_name 是 Python 属性名
        # column_prop.columns[0].name 是数据库列名
        for column in column_prop.columns:
            mapping[column.name] = attr_name
    return mapping


def _serialize_value(value: Any) -> Any:
    """将 Python 值转换为 JSON 可序列化的类型，全面兼容所有数据库字段类型"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, Decimal):
        # 如果是整数 Decimal 则转 int，否则转 float，保留精度
        if value == int(value):
            return int(value)
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, bytes):
        return base64.b64encode(value).decode('ascii')
    if isinstance(value, bytearray):
        return base64.b64encode(bytes(value)).decode('ascii')
    if isinstance(value, memoryview):
        return base64.b64encode(bytes(value)).decode('ascii')
    if isinstance(value, (set, frozenset)):
        return list(value)
    # 基本类型直接返回
    if isinstance(value, (str, int, float, bool, list, dict)):
        return value
    # 兜底：尝试 str()
    return str(value)


def _json_default(obj: Any) -> Any:
    """json.dump 的 default 回调 — 兜底处理所有未知类型，永远不抛异常"""
    return _serialize_value(obj)


def model_to_dict(obj) -> Dict[str, Any]:
    """将 ORM 对象转换为字典，使用数据库列名作为键，全面处理所有字段类型"""
    result = {}
    mapper = inspect(obj.__class__)
    for attr_name, column_prop in mapper.column_attrs.items():
        for column in column_prop.columns:
            db_column_name = column.name
            try:
                value = getattr(obj, attr_name)
                result[db_column_name] = _serialize_value(value)
            except AttributeError:
                logger.debug(f"属性 {attr_name} 不存在于 {obj.__class__.__name__}")
                continue
    return result


async def get_backup_path(session: AsyncSession) -> Path:
    """获取备份路径"""
    path_str = await crud.get_config_value(session, "backupPath", DEFAULT_BACKUP_PATH)
    # 如果配置值为空字符串，使用默认路径
    if not path_str or not path_str.strip():
        path_str = DEFAULT_BACKUP_PATH
        logger.debug(f"备份路径配置为空，使用默认路径: {path_str}")
    return Path(path_str)


async def get_retention_count(session: AsyncSession) -> int:
    """获取备份保留数量"""
    count_str = await crud.get_config_value(session, "backupRetentionCount", str(DEFAULT_RETENTION_COUNT))
    try:
        return int(count_str)
    except (ValueError, TypeError):
        return DEFAULT_RETENTION_COUNT


async def create_backup(session: AsyncSession, progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
    """
    创建数据库备份
    返回备份信息字典
    """
    backup_path = await get_backup_path(session)
    backup_path.mkdir(parents=True, exist_ok=True)

    # 生成备份文件名（精确到毫秒，避免同一秒内多次备份覆盖）
    timestamp = get_now().strftime("%Y%m%d_%H%M%S_%f")[:-3]  # 去掉最后3位，保留毫秒
    filename = f"danmuapi_backup_{timestamp}.json.gz"
    filepath = backup_path / filename

    # 获取保留数量配置
    retention_count = await get_retention_count(session)

    # 记录备份信息
    logger.info(f"开始创建备份: 路径={backup_path}, 保留数量={retention_count}")
    
    # 构建备份数据
    backup_data = {
        "metadata": {
            "version": "2.0",
            "source_db_type": settings.database.type.lower(),
            "created_at": get_now().isoformat(),
            "tables": [name for name, _ in BACKUP_TABLES],
            "table_records": {},  # 每表记录数
        },
        "data": {}
    }

    total_tables = len(BACKUP_TABLES)
    total_records = 0

    # 收集每个表的导出信息，最后统一打印
    export_summary = []

    for idx, (table_name, model_class) in enumerate(BACKUP_TABLES):
        if progress_callback:
            progress = int((idx / total_tables) * 80)
            await progress_callback(progress, f"正在导出表: {table_name}...")

        try:
            stmt = select(model_class)
            result = await session.execute(stmt)
            records = result.scalars().all()

            backup_data["data"][table_name] = [model_to_dict(r) for r in records]
            table_count = len(records)
            total_records += table_count
            backup_data["metadata"]["table_records"][table_name] = table_count
            export_summary.append(f"{table_name}: {table_count}条")
        except Exception as e:
            logger.warning(f"导出表 {table_name} 失败: {e}")
            backup_data["data"][table_name] = []
            backup_data["metadata"]["table_records"][table_name] = 0
            export_summary.append(f"{table_name}: 失败")

    backup_data["metadata"]["total_records"] = total_records

    # 一次性打印所有表的导出摘要（多行格式）
    summary_lines = "\n".join(f"  - {item}" for item in export_summary)
    logger.info(f"导出完成 ({len(BACKUP_TABLES)}个表, 共{total_records}条记录):\n{summary_lines}")

    # 写入压缩文件
    if progress_callback:
        await progress_callback(85, "正在压缩备份文件...")

    with gzip.open(filepath, 'wt', encoding='utf-8') as f:
        json.dump(backup_data, f, ensure_ascii=False, indent=2, default=_json_default)

    file_size = filepath.stat().st_size

    # 计算 SHA256
    if progress_callback:
        await progress_callback(88, "正在计算校验和...")
    sha256_hash = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256_hash.update(chunk)
    file_sha256 = sha256_hash.hexdigest()

    # 自动校验：确认备份文件可正常读取和解析
    if progress_callback:
        await progress_callback(89, "正在校验备份文件...")
    try:
        with gzip.open(filepath, 'rt', encoding='utf-8') as f:
            verify_data = json.load(f)
        verify_tables = len(verify_data.get("data", {}))
        verify_records = sum(len(v) for v in verify_data.get("data", {}).values())
        logger.info(f"备份校验通过: {verify_tables}个表, {verify_records}条记录, SHA256={file_sha256[:16]}...")
    except Exception as e:
        logger.error(f"备份校验失败: {e}，文件可能已损坏")

    # 清理旧备份
    if progress_callback:
        await progress_callback(90, "正在清理旧备份...")

    await cleanup_old_backups(backup_path, retention_count)

    return {
        "filename": filename,
        "filepath": str(filepath),
        "size": file_size,
        "records": total_records,
        "sha256": file_sha256,
        "table_count": len(BACKUP_TABLES),
        "table_records": backup_data["metadata"]["table_records"],
        "created_at": get_now().isoformat(),
    }


async def cleanup_old_backups(backup_path: Path, retention_count: int):
    """清理旧备份，只保留最近的 N 个"""
    backup_files = sorted(
        [f for f in backup_path.glob("danmuapi_backup_*.json.gz")],
        key=lambda x: x.stat().st_mtime,
        reverse=True
    )

    logger.info(f"备份清理: 当前有 {len(backup_files)} 个备份文件, 保留 {retention_count} 个")

    files_to_delete = backup_files[retention_count:]
    if files_to_delete:
        for old_file in files_to_delete:
            try:
                old_file.unlink()
                logger.info(f"删除旧备份: {old_file.name}")
            except Exception as e:
                logger.error(f"删除旧备份失败 {old_file.name}: {e}")
    else:
        logger.info(f"无需清理，当前备份数量 ({len(backup_files)}) 未超过保留数量 ({retention_count})")


async def list_backups(session: AsyncSession) -> List[Dict[str, Any]]:
    """列出所有备份文件"""
    backup_path = await get_backup_path(session)

    if not backup_path.exists():
        return []

    backups = []
    for filepath in backup_path.glob("danmuapi_backup_*.json.gz"):
        try:
            stat = filepath.stat()
            # 尝试读取元数据（只读前 2KB）
            db_type = None
            total_records = None
            version = None
            try:
                with gzip.open(filepath, 'rt', encoding='utf-8') as f:
                    content = f.read(2048)
                    # 提取 source_db_type
                    match = re.search(r'"source_db_type"\s*:\s*"(\w+)"', content)
                    if match:
                        db_type = match.group(1)
                    # 提取 version
                    match = re.search(r'"version"\s*:\s*"([\d.]+)"', content)
                    if match:
                        version = match.group(1)
                    # 提取 total_records
                    match = re.search(r'"total_records"\s*:\s*(\d+)', content)
                    if match:
                        total_records = int(match.group(1))
            except Exception:
                pass

            # 计算 SHA256（对已压缩的 .gz 文件）
            sha256_hash = hashlib.sha256()
            with open(filepath, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    sha256_hash.update(chunk)

            backups.append({
                "filename": filepath.name,
                "size": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "db_type": db_type,
                "sha256": sha256_hash.hexdigest(),
                "total_records": total_records,
                "version": version,
            })
        except Exception as e:
            logger.error(f"读取备份文件信息失败 {filepath.name}: {e}")

    # 按创建时间倒序排列
    backups.sort(key=lambda x: x["created_at"], reverse=True)
    return backups


async def delete_backup(session: AsyncSession, filename: str) -> bool:
    """删除指定备份文件"""
    backup_path = await get_backup_path(session)
    filepath = backup_path / filename

    # 安全检查：确保文件名合法
    if not filename.startswith("danmuapi_backup_") or not filename.endswith(".json.gz"):
        raise ValueError("无效的备份文件名")

    if not filepath.exists():
        raise FileNotFoundError(f"备份文件不存在: {filename}")

    filepath.unlink()
    logger.info(f"删除备份文件: {filename}")
    return True


async def restore_backup(session: AsyncSession, filename: str, progress_callback: Optional[Callable] = None, tables: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    从备份还原数据库
    警告：此操作会清空现有数据！

    Args:
        tables: 指定要恢复的表列表，None 表示全部恢复
    """
    from sqlalchemy import delete

    backup_path = await get_backup_path(session)
    filepath = backup_path / filename

    if not filepath.exists():
        raise FileNotFoundError(f"备份文件不存在: {filename}")

    # 读取备份数据
    if progress_callback:
        await progress_callback(5, "正在读取备份文件...")

    with gzip.open(filepath, 'rt', encoding='utf-8') as f:
        backup_data = json.load(f)

    metadata = backup_data.get("metadata", {})
    data = backup_data.get("data", {})

    if progress_callback:
        await progress_callback(10, "正在验证备份数据...")

    # 确定要操作的表（部分恢复或全部）
    if tables:
        target_tables = [(name, cls) for name, cls in BACKUP_TABLES if name in tables]
    else:
        target_tables = list(BACKUP_TABLES)

    # 按依赖关系的逆序删除数据（先删除有外键依赖的表）
    tables_reversed = list(reversed(target_tables))
    total_tables = len(tables_reversed)

    for idx, (table_name, model_class) in enumerate(tables_reversed):
        if progress_callback:
            progress = 10 + int((idx / total_tables) * 40)
            await progress_callback(progress, f"正在清空表: {table_name}...")

        try:
            await session.execute(delete(model_class))
        except Exception as e:
            logger.warning(f"清空表 {table_name} 失败: {e}")

    await session.flush()

    # 按依赖顺序插入数据
    total_records = 0
    restored_tables = []
    now = get_now()

    for idx, (table_name, model_class) in enumerate(target_tables):
        if progress_callback:
            progress = 50 + int((idx / total_tables) * 45)
            await progress_callback(progress, f"正在还原表: {table_name}...")

        records = data.get(table_name, [])
        if not records:
            continue

        try:
            # 获取数据库列名到 Python 属性名的映射
            column_mapping = get_column_mapping(model_class)

            # 获取模型的所有有效 Python 属性名
            mapper = inspect(model_class)
            valid_attrs = set(column_mapping.values())

            # 存储 NOT NULL 字段信息
            # not_null_datetime_attrs: {Python属性名: 数据库列名} - 日期时间类型
            # not_null_string_attrs: {Python属性名: 数据库列名} - 字符串类型
            # not_null_required_attrs: {Python属性名: 数据库列名} - 必须有值的字段（如外键）
            not_null_datetime_attrs = {}
            not_null_string_attrs = {}
            not_null_required_attrs = {}  # 无法提供默认值的 NOT NULL 字段
            primary_key_attrs = set()

            # 遍历 column_attrs 获取正确的属性名和列信息
            for attr_name, column_prop in mapper.column_attrs.items():
                for column in column_prop.columns:
                    # 获取主键字段
                    if column.primary_key:
                        primary_key_attrs.add(attr_name)
                        continue

                    # 检查是否为 NOT NULL 字段（非主键）
                    if not column.nullable:
                        col_type_class = type(column.type).__name__.upper()
                        col_type = str(column.type).upper()

                        # 检查是否有外键约束
                        has_foreign_key = len(column.foreign_keys) > 0

                        if 'DATETIME' in col_type or 'TIMESTAMP' in col_type or 'NAIVEDATETIME' in col_type_class:
                            not_null_datetime_attrs[attr_name] = column.name
                        elif 'VARCHAR' in col_type or 'STRING' in col_type or 'TEXT' in col_type:
                            not_null_string_attrs[attr_name] = column.name
                        elif has_foreign_key or 'INT' in col_type or 'BIGINT' in col_type:
                            # 外键或整数类型的 NOT NULL 字段，无法提供默认值
                            not_null_required_attrs[attr_name] = column.name

            if not_null_datetime_attrs:
                logger.debug(f"表 {table_name} 的 NOT NULL 日期时间属性: {not_null_datetime_attrs}")
            if not_null_string_attrs:
                logger.debug(f"表 {table_name} 的 NOT NULL 字符串属性: {not_null_string_attrs}")
            if not_null_required_attrs:
                logger.debug(f"表 {table_name} 的 NOT NULL 必需属性: {not_null_required_attrs}")

            for record in records:
                # 将数据库列名转换为 Python 属性名
                converted_record = {}
                for db_col_name, value in record.items():
                    # 获取对应的 Python 属性名
                    attr_name = column_mapping.get(db_col_name, db_col_name)

                    # 只添加模型中存在的属性，忽略无效字段
                    if attr_name not in valid_attrs:
                        logger.debug(f"忽略无效字段: {db_col_name} -> {attr_name}")
                        continue

                    # 根据目标列类型做反序列化转换
                    if isinstance(value, str):
                        # 获取目标列的类型
                        target_col_type = None
                        for a_name, col_prop in mapper.column_attrs.items():
                            if a_name == attr_name:
                                for col in col_prop.columns:
                                    target_col_type = type(col.type).__name__.upper()
                                break

                        # 目标是日期时间列 → 尝试解析 ISO 字符串
                        if target_col_type and any(t in target_col_type for t in ('DATETIME', 'TIMESTAMP', 'NAIVEDATETIME')):
                            try:
                                value = datetime.fromisoformat(value)
                            except (ValueError, TypeError):
                                pass
                        # 目标是纯日期列
                        elif target_col_type and target_col_type == 'DATE':
                            try:
                                value = date.fromisoformat(value)
                            except (ValueError, TypeError):
                                pass
                        # 目标是二进制列 → base64 解码
                        elif target_col_type and any(t in target_col_type for t in ('BLOB', 'BINARY', 'VARBINARY', 'BYTEA', 'LARGEBINARY')):
                            try:
                                value = base64.b64decode(value)
                            except Exception:
                                pass

                    converted_record[attr_name] = value

                # 为 NOT NULL 的日期时间字段提供默认值
                # 这样可以处理从 MySQL 备份还原到 PostgreSQL 时的 NOT NULL 约束问题
                for attr_name, db_col_name in not_null_datetime_attrs.items():
                    current_value = converted_record.get(attr_name)
                    if current_value is None:
                        converted_record[attr_name] = now
                        logger.info(f"为 {table_name}.{attr_name} (db: {db_col_name}) 设置默认时间: {now}")

                # 为 NOT NULL 的字符串字段提供默认值
                for attr_name, db_col_name in not_null_string_attrs.items():
                    current_value = converted_record.get(attr_name)
                    if current_value is None:
                        # 尝试从字段名推断默认值
                        default_value = ""
                        attr_lower = attr_name.lower()
                        if 'provider' in attr_lower:
                            # 对于 provider 类型字段，尝试从其他字段推断
                            if 'name' in converted_record:
                                name_val = converted_record.get('name', '')
                                if name_val:
                                    default_value = str(name_val).lower()
                        converted_record[attr_name] = default_value
                        logger.info(f"为 {table_name}.{attr_name} (db: {db_col_name}) 设置默认值: '{default_value}'")

                # 检查主键字段是否为空，如果为空则跳过该记录
                skip_record = False
                for pk_attr in primary_key_attrs:
                    if pk_attr not in converted_record or converted_record.get(pk_attr) is None:
                        logger.warning(f"跳过记录: {table_name} 主键字段 {pk_attr} 为空")
                        skip_record = True
                        break

                if skip_record:
                    continue

                # 检查必需字段（如外键）是否为空，如果为空则跳过该记录
                for req_attr, db_col_name in not_null_required_attrs.items():
                    if req_attr not in converted_record or converted_record.get(req_attr) is None:
                        logger.warning(f"跳过记录: {table_name} 必需字段 {req_attr} (db: {db_col_name}) 为空")
                        skip_record = True
                        break

                if skip_record:
                    continue

                obj = model_class(**converted_record)
                session.add(obj)

            total_records += len(records)
            restored_tables.append(table_name)
            logger.info(f"还原表 {table_name}: {len(records)} 条记录")
        except Exception as e:
            logger.error(f"还原表 {table_name} 失败: {e}")
            raise

    await session.flush()

    # 重置 PostgreSQL 自增序列
    # 这是必要的，因为还原数据时插入了带有 id 的记录，
    # 但 PostgreSQL 的序列没有自动更新，会导致后续插入时主键冲突
    if settings.database.type.lower() == "postgresql":
        from sqlalchemy import text

        # 自动检测所有有整数自增主键的表，无需手动维护
        autoincrement_tables = []
        for table_name, model_cls in BACKUP_TABLES:
            for col in model_cls.__table__.primary_key.columns:
                if col.autoincrement in (True, 'auto') and hasattr(col.type, 'precision'):
                    autoincrement_tables.append((table_name, col.name))
                    break
                # Integer/BigInteger 没有 precision 属性，用类名判断
                type_name = type(col.type).__name__
                if col.autoincrement in (True, 'auto') and type_name in ('Integer', 'BigInteger'):
                    autoincrement_tables.append((table_name, col.name))
                    break

        for table_name, pk_column in autoincrement_tables:
            try:
                # 获取表中最大的 id 值，然后重置序列
                # PostgreSQL 序列名通常是 {table_name}_{column}_seq
                reset_sql = text(f"""
                    SELECT setval(
                        pg_get_serial_sequence('{table_name}', '{pk_column}'),
                        COALESCE((SELECT MAX({pk_column}) FROM {table_name}), 0) + 1,
                        false
                    )
                """)
                await session.execute(reset_sql)
                logger.debug(f"重置序列: {table_name}.{pk_column}")
            except Exception as e:
                # 某些表可能没有序列（如字符串主键），忽略错误
                logger.debug(f"重置序列 {table_name}.{pk_column} 失败（可能不存在）: {e}")

        await session.flush()
        logger.info("已重置所有 PostgreSQL 自增序列")

    return {
        "filename": filename,
        "records": total_records,
        "source_db_type": metadata.get("source_db_type"),
        "backup_created_at": metadata.get("created_at"),
        "restored_tables": restored_tables,
    }


class DatabaseBackupJob(BaseJob):
    """
    数据库备份定时任务
    """
    job_type = "databaseBackup"
    job_name = "数据库备份"
    job_name_en = "Database Backup"
    job_name_tw = "資料庫備份"
    description = "定期备份数据库数据为 JSON 格式，支持跨数据库（MySQL/PostgreSQL）还原。"
    description_en = "Periodically backup database data in JSON format, supporting cross-database (MySQL/PostgreSQL) restoration."
    description_tw = "定期備份資料庫資料為 JSON 格式，支援跨資料庫（MySQL/PostgreSQL）還原。"

    async def run(self, session: AsyncSession, progress_callback: Callable):
        """
        执行数据库备份任务
        """
        self.logger.info(f"开始执行 [{self.job_name}] 定时任务...")

        await progress_callback(0, "开始备份数据库...")

        try:
            result = await create_backup(session, progress_callback)

            await progress_callback(100, "备份完成")

            size_mb = result['size'] / (1024 * 1024)
            final_message = (
                f"数据库备份完成。文件: {result['filename']}\n"
                f"大小: {size_mb:.2f} MB, 记录数: {result['records']}"
            )
            self.logger.info(final_message.replace("\n", " | "))
            raise TaskSuccess(final_message)

        except TaskSuccess:
            raise
        except Exception as e:
            self.logger.error(f"数据库备份失败: {e}", exc_info=True)
            raise

