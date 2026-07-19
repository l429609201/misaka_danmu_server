"""
数据库备份管理 API
"""
import logging
from typing import List, Optional
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
import shutil

from src.db import crud, models, get_db_session
from src import security
from src.services import SchedulerManager
from src.jobs.database_backup import (
    create_backup, list_backups, delete_backup, restore_backup,
    get_backup_path, get_retention_count, resolve_backup_file
)
from src.api.dependencies import get_scheduler_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/backup", tags=["备份管理"])


def _resolve_backup_file_or_400(backup_path, filename: str):
    """将底层文件名校验错误转换为稳定的 API 400 响应。"""
    try:
        return resolve_backup_file(backup_path, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class BackupInfo(BaseModel):
    filename: str
    size: int
    created_at: str
    db_type: Optional[str] = None
    sha256: Optional[str] = None
    total_records: Optional[int] = None
    version: Optional[str] = None


class BackupCreateResponse(BaseModel):
    success: bool
    message: str
    filename: Optional[str] = None
    size: Optional[int] = None
    records: Optional[int] = None


class BackupJobStatus(BaseModel):
    exists: bool
    enabled: bool = False
    cron_expression: Optional[str] = None
    next_run_time: Optional[str] = None
    task_id: Optional[str] = None


class RestoreRequest(BaseModel):
    filename: str
    confirm: str  # 必须输入 "RESTORE" 确认
    tables: Optional[List[str]] = None  # 部分表恢复（None = 全部）
    auto_snapshot: bool = True  # 恢复前自动创建快照


@router.get("/list", response_model=List[BackupInfo], summary="获取备份列表")
async def get_backup_list(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """获取所有备份文件列表"""
    try:
        backups = await list_backups(session)
        return [BackupInfo(**b) for b in backups]
    except Exception as e:
        logger.error(f"获取备份列表失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取备份列表失败: {str(e)}")


@router.post("/create", response_model=BackupCreateResponse, summary="立即创建备份")
async def create_backup_now(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """立即创建数据库备份"""
    try:
        result = await create_backup(session)
        await session.commit()
        
        size_mb = result['size'] / (1024 * 1024)
        return BackupCreateResponse(
            success=True,
            message=f"备份成功，文件大小: {size_mb:.2f} MB，共 {result['records']} 条记录",
            filename=result['filename'],
            size=result['size'],
            records=result['records'],
        )
    except Exception as e:
        logger.error(f"创建备份失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"创建备份失败: {str(e)}")


@router.get("/download/{filename}", summary="下载备份文件")
async def download_backup(
    filename: str,
    request: Request,
    token: Optional[str] = Query(None, description="JWT token（用于 window.open 等无法携带 Header 的场景）"),
    header_token: Optional[str] = Depends(security.oauth2_scheme_optional),
    session: AsyncSession = Depends(get_db_session),
):
    """下载指定的备份文件，支持 query parameter 传递 token"""
    # query parameter 优先（window.open 场景），否则用 header
    final_token = token or header_token
    current_user = await security.get_current_user(
        request=request,
        token=final_token,
        session=session,
    )
    _ = current_user
    backup_path = await get_backup_path(session)
    filepath = _resolve_backup_file_or_400(backup_path, filename)
    
    if not filepath.is_file():
        raise HTTPException(status_code=404, detail="备份文件不存在")
    
    return FileResponse(
        path=str(filepath),
        filename=filename,
        media_type="application/gzip"
    )


@router.delete("/delete/{filename}", summary="删除备份文件")
async def delete_backup_file(
    filename: str,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """删除指定的备份文件"""
    try:
        await delete_backup(session, filename)
        return {"success": True, "message": f"已删除备份: {filename}"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"删除备份失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"删除备份失败: {str(e)}")


@router.delete("/delete-batch", summary="批量删除备份文件")
async def delete_backup_files_batch(
    filenames: List[str] = Query(..., description="要删除的文件名列表"),
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """批量删除备份文件"""
    deleted = []
    errors = []

    for filename in filenames:
        try:
            await delete_backup(session, filename)
            deleted.append(filename)
        except Exception as e:
            errors.append({"filename": filename, "error": str(e)})

    return {
        "success": len(errors) == 0,
        "deleted": deleted,
        "errors": errors,
        "message": f"成功删除 {len(deleted)} 个文件" + (f"，{len(errors)} 个失败" if errors else "")
    }


@router.post("/restore", summary="从备份还原数据库")
async def restore_from_backup(
    request: RestoreRequest,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """
    从备份还原数据库
    警告：此操作会清空现有数据！
    支持部分表恢复和恢复前自动快照。
    """
    # 确认检查
    if request.confirm != "RESTORE":
        raise HTTPException(status_code=400, detail="请输入 'RESTORE' 确认还原操作")

    try:
        # 恢复前自动创建快照
        if request.auto_snapshot:
            logger.info("恢复前自动创建临时快照...")
            try:
                snapshot_result = await create_backup(session, progress_callback=None)
                logger.info(f"临时快照已创建: {snapshot_result['filename']}")
            except Exception as e:
                logger.warning(f"创建恢复前快照失败: {e}，继续恢复...")

        result = await restore_backup(
            session, request.filename,
            tables=request.tables,
        )
        await session.commit()

        return {
            "success": True,
            "message": f"还原成功，共还原 {result['records']} 条记录",
            "filename": result['filename'],
            "records": result['records'],
            "source_db_type": result['source_db_type'],
            "restored_tables": result.get('restored_tables'),
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"还原备份失败: {e}", exc_info=True)
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"还原备份失败: {str(e)}")


@router.get("/job-status", response_model=BackupJobStatus, summary="获取备份定时任务状态")
async def get_backup_job_status(
    current_user: models.User = Depends(security.get_current_user),
    scheduler: SchedulerManager = Depends(get_scheduler_manager),
):
    """获取数据库备份定时任务的状态"""
    tasks_list = await scheduler.get_all_tasks()

    # 查找 job_type 为 "databaseBackup" 的任务
    for task in tasks_list:
        if task.get("jobType") == "databaseBackup":
            return BackupJobStatus(
                exists=True,
                enabled=task.get("isEnabled", False),
                cron_expression=task.get("cronExpression"),
                next_run_time=task.get("nextRunTime"),
                task_id=task.get("taskId")
            )

    return BackupJobStatus(exists=False)


@router.get("/config", summary="获取备份配置")
async def get_backup_config(
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """获取备份相关配置"""
    backup_path = await get_backup_path(session)
    retention_count = await get_retention_count(session)

    return {
        "backup_path": str(backup_path),
        "retention_count": retention_count,
    }


@router.post("/upload", summary="上传备份文件")
async def upload_backup_file(
    file: UploadFile = File(...),
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """上传备份文件到服务器"""
    # 验证文件名
    if not file.filename or not file.filename.endswith('.json.gz'):
        raise HTTPException(status_code=400, detail="请上传 .json.gz 格式的备份文件")

    # 验证文件名格式（可选，允许用户上传任意名称的备份）
    # 为了安全，重命名为标准格式
    import re
    from src.core.timezone import get_now

    backup_path = await get_backup_path(session)
    backup_path.mkdir(parents=True, exist_ok=True)

    # 如果文件名符合标准格式，保留原名；否则生成新名称
    if re.match(r'^danmuapi_backup_\d{8}_\d{6}\.json\.gz$', file.filename):
        target_filename = file.filename
    else:
        timestamp = get_now().strftime("%Y%m%d_%H%M%S")
        target_filename = f"danmuapi_backup_{timestamp}.json.gz"

    target_path = backup_path / target_filename

    # 检查文件是否已存在
    if target_path.exists():
        raise HTTPException(status_code=400, detail=f"备份文件已存在: {target_filename}")

    try:
        # 保存文件
        with open(target_path, 'wb') as f:
            shutil.copyfileobj(file.file, f)

        file_size = target_path.stat().st_size
        logger.info(f"上传备份文件成功: {target_filename}, 大小: {file_size} bytes")

        return {
            "success": True,
            "message": f"上传成功: {target_filename}",
            "filename": target_filename,
            "size": file_size,
        }
    except Exception as e:
        # 清理失败的文件
        if target_path.exists():
            target_path.unlink()
        logger.error(f"上传备份文件失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"上传失败: {str(e)}")


@router.get("/detail/{filename}", summary="获取备份文件详情")
async def get_backup_detail(
    filename: str,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """获取备份文件的详细信息，包括各表记录数和元数据。"""
    import gzip
    import json
    import hashlib

    backup_path = await get_backup_path(session)
    filepath = _resolve_backup_file_or_400(backup_path, filename)

    if not filepath.is_file():
        raise HTTPException(status_code=404, detail="备份文件不存在")

    # 计算 SHA256
    sha256_hash = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256_hash.update(chunk)

    # 读取完整元数据
    try:
        with gzip.open(filepath, 'rt', encoding='utf-8') as f:
            backup_data = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取备份文件失败: {e}")

    metadata = backup_data.get("metadata", {})
    data = backup_data.get("data", {})

    # 统计每表记录数
    table_records = {}
    for table_name, records in data.items():
        table_records[table_name] = len(records) if isinstance(records, list) else 0

    total_records = sum(table_records.values())

    return {
        "filename": filename,
        "size": filepath.stat().st_size,
        "sha256": sha256_hash.hexdigest(),
        "metadata": metadata,
        "table_records": table_records,
        "total_records": total_records,
        "table_count": len(table_records),
    }


class DryRunRequest(BaseModel):
    filename: str
    tables: Optional[List[str]] = None  # 部分表预检（None = 全部）


@router.post("/dry-run", summary="恢复预检（dry-run）")
async def backup_dry_run(
    request: DryRunRequest,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """
    恢复预检：对比备份文件与当前数据库的差异，不实际执行恢复。
    返回每张表的当前记录数、备份记录数和差异。
    """
    import gzip
    import json
    from sqlalchemy import select, func

    backup_path = await get_backup_path(session)
    filepath = _resolve_backup_file_or_400(backup_path, request.filename)

    if not filepath.is_file():
        raise HTTPException(status_code=404, detail="备份文件不存在")

    try:
        with gzip.open(filepath, 'rt', encoding='utf-8') as f:
            backup_data = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取备份文件失败: {e}")

    from src.jobs.database_backup import BACKUP_TABLES
    data = backup_data.get("data", {})
    metadata = backup_data.get("metadata", {})

    comparison = []
    for table_name, model_class in BACKUP_TABLES:
        # 如果指定了部分表，跳过不在列表中的
        if request.tables and table_name not in request.tables:
            continue

        backup_count = len(data.get(table_name, []))

        # 获取当前数据库记录数
        try:
            result = await session.execute(select(func.count()).select_from(model_class))
            current_count = result.scalar() or 0
        except Exception:
            current_count = -1  # 无法读取

        comparison.append({
            "table": table_name,
            "current_count": current_count,
            "backup_count": backup_count,
            "diff": backup_count - current_count if current_count >= 0 else None,
        })

    return {
        "filename": request.filename,
        "source_db_type": metadata.get("source_db_type"),
        "backup_version": metadata.get("version"),
        "backup_created_at": metadata.get("created_at"),
        "comparison": comparison,
        "total_backup_records": sum(c["backup_count"] for c in comparison),
        "total_current_records": sum(c["current_count"] for c in comparison if c["current_count"] >= 0),
    }
