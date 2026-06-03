import asyncio
import logging
from typing import Callable, Optional
from datetime import timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy import text, select

from src.db import crud, orm_models, _get_db_url
from src.core import settings, get_now
from .base import BaseJob
from src.services import TaskSuccess

logger = logging.getLogger(__name__)

# 图片存储目录
IMAGE_DIR = Path("/app/config/image")

async def _clean_orphaned_images(session: AsyncSession) -> str:
    """清理数据库中不存在的图片文件"""
    if not IMAGE_DIR.exists():
        return "图片目录不存在，跳过清理。"

    # 获取数据库中所有的图片路径
    stmt = select(orm_models.Anime.localImagePath).where(orm_models.Anime.localImagePath.isnot(None))
    result = await session.execute(stmt)
    db_image_paths = set()

    for row in result.scalars().all():
        if row and row.startswith('/data/images/'):
            # 提取文件名
            filename = row.split('/')[-1]
            db_image_paths.add(filename)

    # 获取文件系统中的所有图片文件
    fs_image_files = set()
    for file_path in IMAGE_DIR.iterdir():
        if file_path.is_file() and file_path.suffix.lower() in ['.jpg', '.jpeg', '.png', '.webp']:
            fs_image_files.add(file_path.name)

    # 找出孤立的图片文件（文件系统中存在但数据库中不存在）
    orphaned_files = fs_image_files - db_image_paths

    deleted_count = 0
    for filename in orphaned_files:
        try:
            file_path = IMAGE_DIR / filename
            file_path.unlink()
            deleted_count += 1
            logger.debug(f"删除孤立图片文件: {file_path}")
        except Exception as e:
            logger.error(f"删除图片文件失败 {filename}: {e}")

    return f"图片缓存清理完成，删除了 {deleted_count} 个孤立文件。"

async def _optimize_database(session: AsyncSession, db_type: str) -> str:
    """根据数据库类型执行表优化（PostgreSQL VACUUM，MySQL InnoDB 无需手动 OPTIMIZE）。"""
    if db_type == "postgresql":
        logger.info("检测到 PostgreSQL，正在执行 VACUUM...")
        db_url_obj = _get_db_url()
        auto_commit_engine = create_async_engine(db_url_obj, isolation_level="AUTOCOMMIT")
        try:
            async with auto_commit_engine.connect() as connection:
                await connection.execute(text("VACUUM;"))
            return "VACUUM 执行成功。"
        finally:
            await auto_commit_engine.dispose()
    elif db_type == "mysql":
        # InnoDB 的 OPTIMIZE TABLE 会全表重建，大表上耗时极长且锁 I/O，不适合定时任务。
        # 改为 ANALYZE TABLE：仅更新索引统计信息，不锁表、不重建，几毫秒完成。
        # 在 prune_logs 批量 DELETE 后执行，确保查询优化器使用最新统计。
        tables_to_analyze = ["task_history", "token_access_log", "external_api_logs"]
        analyzed = []
        for table in tables_to_analyze:
            try:
                await session.execute(text(f"ANALYZE TABLE `{table}`"))
                analyzed.append(table)
            except Exception as e:
                logger.warning(f"ANALYZE TABLE `{table}` 失败: {e}")
        if analyzed:
            return f"MySQL ANALYZE TABLE 完成: {', '.join(analyzed)}。"
        return "MySQL ANALYZE TABLE: 无可分析的表。"
    else:
        message = f"不支持的数据库类型 '{db_type}'，跳过优化。"
        logger.warning(message)
        return message

async def _purge_binary_logs(session: AsyncSession, days: int) -> str:
    """
    执行 PURGE BINARY LOGS 命令来清理早于指定天数的 binlog 文件。
    警告：这是一个高风险操作，需要 SUPER 或 BINLOG_ADMIN 权限。
    """
    logger.info(f"准备执行 PURGE BINARY LOGS BEFORE NOW() - INTERVAL {days} DAY...")
    await session.execute(text(f"PURGE BINARY LOGS BEFORE NOW() - INTERVAL {days} DAY"))
    # 这个操作不需要 commit，因为它不是DML
    msg = f"成功执行 PURGE BINARY LOGS，清除了 {days} 天前的日志。"
    logger.info(msg)
    return msg

class DatabaseMaintenanceJob(BaseJob):
    """
    缓存日志清理任务，包括清理旧日志、优化表和清理无效图片缓存。
    """
    job_type = "databaseMaintenance"
    job_name = "缓存日志清理任务"
    job_name_en = "Cache & Log Cleanup"
    job_name_tw = "快取日誌清理任務"
    description = "定期清理过期的应用日志、优化数据库表、清理无效的图片缓存文件。帮助保持系统性能和节省存储空间。"
    description_en = "Periodically clean expired logs, optimize database tables, and clean invalid image caches. Helps maintain performance and save storage."
    description_tw = "定期清理過期的應用日誌、最佳化資料庫表、清理無效的圖片快取檔案。幫助保持系統效能和節省儲存空間。"

    async def run(self, session: AsyncSession, progress_callback: Callable):
        """
        执行缓存日志清理任务：清理旧日志、优化表和清理无效图片缓存。
        """
        self.logger.info(f"开始执行 [{self.job_name}] 定时任务...")
        
        # --- 1. 应用日志清理 ---
        await progress_callback(10, "正在清理旧日志...")
        
        try:
            # 日志保留天数，默认为30天。
            retention_days_str = await crud.get_config_value(session, "logRetentionDays", "30")
            retention_days = int(retention_days_str)
        except (ValueError, TypeError):
            retention_days = 30
        
        if retention_days > 0:
            self.logger.info(f"将清理 {retention_days} 天前的日志记录。")
            cutoff_date = get_now() - timedelta(days=retention_days)
            
            tables_to_prune = {
                "任务历史": (orm_models.TaskHistory, orm_models.TaskHistory.createdAt),
                "Token访问日志": (orm_models.TokenAccessLog, orm_models.TokenAccessLog.accessTime),
                "外部API访问日志": (orm_models.ExternalApiLog, orm_models.ExternalApiLog.accessTime),
            }
            
            total_deleted = 0
            for name, (model, date_column) in tables_to_prune.items():
                deleted_count: Optional[int] = await crud.prune_logs(session, model, date_column, cutoff_date)

                # 修正：增加对 deleted_count 的 None 值检查，以提高代码的健壮性。
                # 这可以防止当底层数据库操作（如某些驱动下的DELETE）不返回行数时，任务意外失败。
                if deleted_count is None:
                    deleted_count = 0

                # 关键修复：每个表 DELETE 后立即 commit，释放行锁。
                # 避免批量 DELETE task_history 的行锁长时间持有，阻塞其他任务的进度更新和新任务创建。
                await session.commit()

                if deleted_count > 0:
                    self.logger.info(f"从 {name} 表中删除了 {deleted_count} 条旧记录。")
                total_deleted += deleted_count
            await progress_callback(40, f"应用日志清理完成，共删除 {total_deleted} 条记录。")
        else:
            self.logger.info("日志保留天数设为0或无效，跳过清理。")
            await progress_callback(40, "日志保留天数设为0，跳过清理。")

        # --- 2. Binlog 清理 (仅MySQL) ---
        db_type = settings.database.type.lower()
        if db_type == "mysql":
            await progress_callback(50, "正在清理 MySQL Binlog...")
            try:
                # 新增：从配置中读取binlog保留天数
                binlog_retention_days_str = await crud.get_config_value(session, "mysqlBinlogRetentionDays", "3")
                binlog_retention_days = int(binlog_retention_days_str)

                if binlog_retention_days > 0:
                    # 用户指定清理N天前的日志
                    binlog_cleanup_message = await _purge_binary_logs(session, days=binlog_retention_days)
                    self.logger.info(binlog_cleanup_message)
                    await progress_callback(60, binlog_cleanup_message)
                else:
                    binlog_cleanup_message = "Binlog自动清理已禁用。"
                    self.logger.info(binlog_cleanup_message)
                    await progress_callback(60, binlog_cleanup_message)
            except OperationalError as e:
                # 检查是否是权限不足的错误 (MySQL error code 1227)
                if e.orig and hasattr(e.orig, 'args') and len(e.orig.args) > 0 and e.orig.args[0] == 1227:
                    binlog_cleanup_message = "Binlog 清理失败: 数据库用户缺少 SUPER 或 BINLOG_ADMIN 权限。此为正常现象，可安全忽略。"
                    self.logger.warning(binlog_cleanup_message)
                    await progress_callback(60, binlog_cleanup_message)
                else:
                    # 其他操作错误，仍然记录详细信息
                    binlog_cleanup_message = f"Binlog 清理失败: {e}"
                    self.logger.error(binlog_cleanup_message, exc_info=True)
                    await progress_callback(60, binlog_cleanup_message)
            except Exception as e:
                # 记录错误，但不中断任务
                binlog_cleanup_message = f"Binlog 清理失败: {e}"
                self.logger.error(binlog_cleanup_message, exc_info=True)
                await progress_callback(60, binlog_cleanup_message)

        # --- 3. 数据库表优化 ---
        await progress_callback(70, "正在执行数据库表优化...")
        
        try:
            optimization_message = await asyncio.wait_for(
                _optimize_database(session, db_type), timeout=300  # 5 分钟超时
            )
            self.logger.info(f"数据库优化结果: {optimization_message}")
        except asyncio.TimeoutError:
            optimization_message = "数据库优化超时(5分钟)，已跳过"
            self.logger.warning(optimization_message)
        except Exception as e:
            optimization_message = f"数据库优化失败: {e}"
            self.logger.error(optimization_message, exc_info=True)
            # 即使优化失败，也不应导致整个任务失败，仅记录错误

        await progress_callback(80, optimization_message)

        # --- 4. 图片缓存清理（带重试，防止长时间任务导致连接断开）---
        await progress_callback(85, "正在清理无效图片缓存...")

        image_cleanup_message = ""
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                image_cleanup_message = await asyncio.wait_for(
                    _clean_orphaned_images(session), timeout=180  # 3 分钟超时
                )
                self.logger.info(f"图片缓存清理结果: {image_cleanup_message}")
                break
            except asyncio.TimeoutError:
                image_cleanup_message = "图片缓存清理超时(3分钟)，已跳过"
                self.logger.warning(image_cleanup_message)
                break
            except OperationalError as e:
                if attempt < max_retries:
                    wait_time = 3 * (attempt + 1)
                    self.logger.warning(f"图片缓存清理数据库连接异常 (第{attempt + 1}次)，{wait_time}秒后重试: {e}")
                    await asyncio.sleep(wait_time)
                    # 尝试使连接池回收死连接（通过 pool_pre_ping 机制）
                    try:
                        await session.rollback()
                    except Exception:
                        pass
                else:
                    image_cleanup_message = f"图片缓存清理失败（重试{max_retries}次后仍失败）: {e}"
                    self.logger.error(image_cleanup_message, exc_info=True)
            except Exception as e:
                image_cleanup_message = f"图片缓存清理失败: {e}"
                self.logger.error(image_cleanup_message, exc_info=True)
                break

        await progress_callback(100, "缓存日志清理任务完成")

        final_message = f"缓存日志清理任务完成。{optimization_message} {image_cleanup_message}"
        raise TaskSuccess(final_message)
