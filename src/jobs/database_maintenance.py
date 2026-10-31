import asyncio
import logging
import re
from typing import Callable, Optional
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy import text, select

from src.db import orm_models, _get_db_url
from src.core import settings, get_now, get_app_timezone
from .base import BaseJob
from src.utils.diagnostics.task_exceptions import TaskSuccess
from src.services.file_storage_service import CONFIG_DIR, get_file_storage_service
from src.services.service_container import get_database_service
from src.services.performance_service import profile_flow
from src.schemas.performance import FLOW_DATABASE_MAINTENANCE

logger = logging.getLogger(__name__)

# 与图片存储共用配置根目录，兼容源码运行环境和 Docker，避免硬编码 /app。
IMAGE_DIR = CONFIG_DIR / "image"

# 通知/检索生成的公开缩略图（w{宽度}_{哈希}.webp/.jpg）不登记在数据库中，
# 已发出的通知仍会引用它们，因此只清理超过保留期的缩略图，不按"未引用"立即删除。
_THUMBNAIL_NAME_RE = re.compile(r"^w\d+_[0-9a-f]{16}\.(webp|jpg)$")
_THUMBNAIL_RETENTION = timedelta(days=30)
# 非关联孤立图片的默认保留天数（任务配置 orphanImageRetentionDays 未设置或非法时使用）
DEFAULT_ORPHAN_IMAGE_RETENTION_DAYS = 7

# 所有可能保存本地缓存图片（/data/images/...）的列；漏列任意一列都会把仍在使用的图片误删。
_IMAGE_REFERENCE_COLUMNS = (
    orm_models.Anime.localImagePath,
    orm_models.Anime.imageUrl,
    orm_models.MediaItem.posterUrl,
    orm_models.LocalDanmakuItem.posterUrl,
    orm_models.ExternalCalendarItem.imageUrl,
)


async def _clean_orphaned_images(
    session: AsyncSession, orphan_retention_days: int = DEFAULT_ORPHAN_IMAGE_RETENTION_DAYS,
) -> str:
    """清理数据库中不存在引用的图片文件。

    非关联孤立图片超过 orphan_retention_days 天（按文件修改时间）才删除，0 表示立即删除；
    公开缩略图固定按 30 天保留期清理。
    """
    if not IMAGE_DIR.exists():
        return f"图片目录不存在（{IMAGE_DIR.resolve()}），跳过清理。"

    image_dir = IMAGE_DIR.resolve()
    storage = get_file_storage_service()
    db_image_paths = set()
    reference_count = 0
    for column in _IMAGE_REFERENCE_COLUMNS:
        result = await session.execute(select(column).where(column.isnot(None)))
        for stored_path in result.scalars().all():
            # 远程 URL 不对应本地文件，直接忽略；其余统一走 resolve_fs_path 解析历史格式。
            if not stored_path or stored_path.startswith(("http://", "https://")):
                continue
            reference_count += 1
            resolved_path = storage.resolve_fs_path(stored_path)
            if resolved_path is None:
                continue
            try:
                if resolved_path.resolve().parent == image_dir:
                    db_image_paths.add(resolved_path.name)
            except OSError:
                logger.warning("图片引用路径解析失败，跳过: %s", stored_path)

    # 安全阀：存在本地图片引用却一个都没解析进图片目录，说明路径映射有问题，宁可不删也不整批误删。
    if reference_count and not db_image_paths:
        logger.error("图片清理中止：数据库有 %d 条本地图片引用，但均未解析到 %s", reference_count, image_dir)
        return f"图片缓存清理已中止：{reference_count} 条引用均未能解析到图片目录，请检查路径映射。"

    # 统一使用应用时区时间工具；文件 mtime 同样换算到应用时区后再比较，避免混用两套时间口径。
    now = get_now()
    thumbnail_cutoff = now - _THUMBNAIL_RETENTION
    orphan_cutoff = now - timedelta(days=orphan_retention_days)
    app_tz = get_app_timezone()
    orphaned_files = []
    for file_path in IMAGE_DIR.iterdir():
        if not file_path.is_file() or file_path.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp'):
            continue
        if file_path.name in db_image_paths:
            continue
        # 缩略图与普通孤立图片分别按各自保留期判断，未到期的文件本轮保留。
        cutoff = thumbnail_cutoff if _THUMBNAIL_NAME_RE.match(file_path.name) else orphan_cutoff
        try:
            modified_at = datetime.fromtimestamp(file_path.stat().st_mtime, app_tz).replace(tzinfo=None)
        except OSError:
            continue
        if modified_at > cutoff:
            continue
        orphaned_files.append(file_path)

    deleted_count = 0
    for file_path in orphaned_files:
        try:
            file_path.unlink()
            deleted_count += 1
            logger.debug(f"删除孤立图片文件: {file_path}")
        except Exception as e:
            logger.error(f"删除图片文件失败 {file_path.name}: {e}")

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

    config_schema = [
        {
            "key": "orphanImageRetentionDays",
            "label": "孤立图片保留天数",
            "label_en": "Orphan Image Retention Days",
            "label_tw": "孤立圖片保留天數",
            "type": "number",
            "default": DEFAULT_ORPHAN_IMAGE_RETENTION_DAYS,
            "min": 0,
            "max": 3650,
            "suffix": "天",
            "suffix_en": "days",
            "suffix_tw": "天",
            "description": "未被任何作品或媒体项引用的缓存图片，修改时间超过该天数后才会删除；0 表示立即删除。默认 7 天。",
            "description_en": "Cached images not referenced by any item are deleted only after this many days since modification; 0 deletes immediately. Default 7 days.",
            "description_tw": "未被任何作品或媒體項引用的快取圖片，修改時間超過該天數後才會刪除；0 表示立即刪除。預設 7 天。",
        }
    ]

    @profile_flow(FLOW_DATABASE_MAINTENANCE)
    async def run(self, session: AsyncSession, progress_callback: Callable, task_config: Optional[dict] = None):
        """
        执行缓存日志清理任务：清理旧日志、优化表和清理无效图片缓存。
        """
        self.logger.info(f"开始执行 [{self.job_name}] 定时任务...")

        # --- 1. 应用日志清理 ---
        await progress_callback(10, "正在清理旧日志...")

        db = get_database_service()
        try:
            # 配置读取使用短事务，避免连接被后续维护操作长期占用。
            async with db.transaction():
                retention_days_str = await db.config.get_value("logRetentionDays", "30")
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
                # 每张表独立提交并释放行锁，避免阻塞任务进度更新和新任务创建。
                async with db.transaction():
                    deleted_count: Optional[int] = await db.utility.prune_logs(model, date_column, cutoff_date)

                # 某些驱动的 DELETE 不返回行数，此时按零计数。
                if deleted_count is None:
                    deleted_count = 0

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
                # 配置访问必须绑定事务，读取结束后再执行独立的 Binlog 维护。
                async with db.transaction():
                    binlog_retention_days_str = await db.config.get_value("mysqlBinlogRetentionDays", "3")
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
        # 读取任务配置中的孤立图片保留天数，非法值回退默认 7 天
        try:
            orphan_retention_days = int((task_config or {}).get(
                "orphanImageRetentionDays", DEFAULT_ORPHAN_IMAGE_RETENTION_DAYS))
        except (TypeError, ValueError):
            orphan_retention_days = DEFAULT_ORPHAN_IMAGE_RETENTION_DAYS
        if orphan_retention_days < 0:
            orphan_retention_days = DEFAULT_ORPHAN_IMAGE_RETENTION_DAYS

        image_cleanup_message = ""
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                image_cleanup_message = await asyncio.wait_for(
                    _clean_orphaned_images(session, orphan_retention_days), timeout=180  # 3 分钟超时
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
