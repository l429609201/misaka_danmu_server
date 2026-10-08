"""弹幕源资源 HTTP 入口：鉴权、参数验证与响应构造。"""
import logging
import platform
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from src.api.dependencies import get_scraper_manager, get_config_service
from src.schemas.ui_models import User
from src.services.config_service import ConfigService
from src.workflows.scraper_resources.resources import get_platform_key
from src.utils.auth import security
from src.workflows.scraper_resources import workflow as resource_workflow


logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/scrapers/resource-repo", summary="获取资源仓库配置")
async def get_resource_repo(
    current_user: User = Depends(security.get_current_user),
    config_service: ConfigService = Depends(get_config_service)
):
    """获取当前配置的资源仓库链接"""
    repo_url = await config_service.get("scraper_resource_repo", "")
    return {"repoUrl": repo_url}


@router.get("/scrapers/repo-refs", summary="获取资源仓库的分支和标签列表")
async def get_repo_refs(
    current_user: User = Depends(security.get_current_user),
    config_service: ConfigService = Depends(get_config_service)
):
    """将资源业务委托给编排层，保留原有 HTTP 契约。"""
    try:
        return await resource_workflow.get_repo_refs(current_user, config_service)
    except resource_workflow.ResourceOperationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/scrapers/versions", summary="获取资源包版本信息")
async def get_versions(
    current_user: User = Depends(security.get_current_user),
    config_service: ConfigService = Depends(get_config_service),
    force_refresh: bool = False  # 新增参数：强制刷新缓存
):
    """将资源业务委托给编排层，保留原有 HTTP 契约。"""
    try:
        return await resource_workflow.get_versions(current_user, config_service, force_refresh)
    except resource_workflow.ResourceOperationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.put("/scrapers/resource-repo", status_code=status.HTTP_204_NO_CONTENT, summary="保存资源仓库配置")
async def save_resource_repo(
    payload: Dict[str, str],
    current_user: User = Depends(security.get_current_user),
    config_service: ConfigService = Depends(get_config_service)
):
    """保存资源仓库链接"""
    repo_url = payload.get("repoUrl", "").strip()
    if repo_url:
        # 基础校验：要求是 http/https 链接，其他格式直接拒绝
        if not (repo_url.startswith("http://") or repo_url.startswith("https://")):
            raise HTTPException(status_code=400, detail="资源仓库链接必须以 http:// 或 https:// 开头")

    await config_service.set("scraper_resource_repo", repo_url)
    logger.info(f"用户 '{current_user.username}' 更新了资源仓库配置: {repo_url}")


@router.post("/scrapers/backup", summary="备份当前弹幕源")
async def backup_scrapers(
    current_user: User = Depends(security.get_current_user),
    new_versions_data: Optional[Dict[str, str]] = None,
    new_hashes_data: Optional[Dict[str, str]] = None,
    package_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """将资源业务委托给编排层，保留原有 HTTP 契约。"""
    try:
        return await resource_workflow.backup_scrapers(current_user, new_versions_data, new_hashes_data, package_data)
    except resource_workflow.ResourceOperationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/scrapers/backup-info", summary="获取备份信息")
async def get_backup_info(
    current_user: User = Depends(security.get_current_user)
):
    """将资源业务委托给编排层，保留原有 HTTP 契约。"""
    try:
        return await resource_workflow.get_backup_info(current_user)
    except resource_workflow.ResourceOperationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/scrapers/restore", summary="从备份还原弹幕源")
async def restore_scrapers(
    current_user: User = Depends(security.get_current_user),
    manager = Depends(get_scraper_manager),
) -> Dict[str, Any]:
    """将资源业务委托给编排层，保留原有 HTTP 契约。"""
    try:
        return await resource_workflow.restore_scrapers(current_user, manager)
    except resource_workflow.ResourceOperationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/scrapers/reload", summary="重载弹幕源")
async def reload_scrapers(
    current_user: User = Depends(security.get_current_user),
    manager = Depends(get_scraper_manager)
):
    """将资源业务委托给编排层，保留原有 HTTP 契约。"""
    try:
        return await resource_workflow.reload_scrapers(current_user, manager)
    except resource_workflow.ResourceOperationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/scrapers/load-resources-stream", summary="从资源仓库加载弹幕源(SSE流式)")
async def load_resources_stream(
    payload: Dict[str, Any],
    current_user: User = Depends(security.get_current_user),
    config_service: ConfigService = Depends(get_config_service),
    manager = Depends(get_scraper_manager)
):
    """构造资源下载 SSE 响应，实际下载由编排层执行。"""

    return StreamingResponse(
        resource_workflow.load_resource_events(payload, current_user, config_service, manager),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


# ========== 新版下载任务 API（后台任务模式）==========

@router.post("/scrapers/download/start", summary="启动下载任务")
async def start_download(
    payload: Dict[str, Any],
    current_user: User = Depends(security.get_current_user),
    config_service: ConfigService = Depends(get_config_service),
    manager = Depends(get_scraper_manager)
):
    """启动弹幕源下载任务（后台运行，不依赖 SSE 连接）"""

    repo_url = payload.get("repoUrl", "")
    use_full_replace = payload.get("fullReplace", False)
    branch = payload.get("branch", "main")  # 获取分支参数，默认 main

    # 检查分支和平台兼容性
    if branch == "test":
        # 检查机器架构（不是 platform_key）
        machine = platform.machine().lower()
        # x86_64 和 amd64 都是 x86 架构
        if machine not in ['x86_64', 'amd64']:
            platform_key = get_platform_key()
            raise HTTPException(
                status_code=400,
                detail=f"test 分支仅支持 x86_64/amd64 平台，当前平台为 {platform_key} (架构: {machine})"
            )

    try:
        task = await resource_workflow.start_download_task(
            repo_url=repo_url,
            use_full_replace=use_full_replace,
            branch=branch,  # 传递分支参数
            config_service=config_service,
            scraper_manager=manager,
            current_user=current_user,
        )
        logger.info(f"用户 '{current_user.username}' 启动了下载任务: {task.task_id} (分支: {branch})")
        return {
            "task_id": task.task_id,
            "status": task.status.value,
            "message": "下载任务已启动"
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"启动下载任务失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"启动下载任务失败: {str(e)}")


@router.get("/scrapers/download/status/{task_id}", summary="获取下载任务状态")
async def get_download_status(
    task_id: str,
    current_user: User = Depends(security.get_current_user),
):
    """将资源业务委托给编排层，保留原有 HTTP 契约。"""
    try:
        return await resource_workflow.get_download_status(task_id, current_user)
    except resource_workflow.ResourceOperationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/scrapers/download/current", summary="获取当前下载任务")
async def get_current_download(
    current_user: User = Depends(security.get_current_user),
):
    """将资源业务委托给编排层，保留原有 HTTP 契约。"""
    try:
        return await resource_workflow.get_current_download(current_user)
    except resource_workflow.ResourceOperationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/scrapers/download/cancel/{task_id}", summary="取消下载任务")
async def cancel_download(
    task_id: str,
    current_user: User = Depends(security.get_current_user),
):
    """将资源业务委托给编排层，保留原有 HTTP 契约。"""
    try:
        return await resource_workflow.cancel_download(task_id, current_user)
    except resource_workflow.ResourceOperationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/scrapers/download/progress/{task_id}", summary="SSE 进度流")
async def download_progress_stream(
    task_id: str,
    current_user: User = Depends(security.get_current_user),
):
    """通过 SSE 实时推送下载进度（可选，用于前端实时显示）"""
    try:
        resource_workflow.require_download_task(task_id)
    except resource_workflow.ResourceOperationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return StreamingResponse(
        resource_workflow.download_progress_events(task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.get("/scrapers/download/cached-status/{task_id}", summary="查询缓存的任务状态")
async def get_cached_task_status(
    task_id: str,
    current_user: User = Depends(security.get_current_user),  # noqa: ARG001
):
    """将资源业务委托给编排层，保留原有 HTTP 契约。"""
    try:
        return await resource_workflow.get_cached_task_status(task_id, current_user)
    except resource_workflow.ResourceOperationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


# ========== 原有 API ==========

@router.get("/scrapers/auto-update", summary="获取自动更新配置")
async def get_auto_update_config(
    current_user: User = Depends(security.get_current_user),
    config_service: ConfigService = Depends(get_config_service)
):
    """获取弹幕源自动更新配置"""
    enabled = await config_service.get("scraperAutoUpdateEnabled", "false")
    interval = await config_service.get("scraperAutoUpdateInterval", "30")
    return {
        "enabled": enabled.lower() == "true",
        "interval": int(interval)
    }


@router.put("/scrapers/auto-update", status_code=status.HTTP_204_NO_CONTENT, summary="保存自动更新配置")
async def save_auto_update_config(
    payload: Dict[str, Any],
    current_user: User = Depends(security.get_current_user),
    config_service: ConfigService = Depends(get_config_service)
):
    """保存弹幕源自动更新配置"""
    enabled = payload.get("enabled", False)
    interval = payload.get("interval", 15)

    await config_service.set("scraperAutoUpdateEnabled", str(enabled).lower())
    await config_service.set("scraperAutoUpdateInterval", str(interval))

    logger.info(f"用户 '{current_user.username}' 更新了自动更新配置: enabled={enabled}, interval={interval}分钟")


@router.get("/scrapers/full-replace", summary="获取全量替换配置")
async def get_full_replace_config(
    current_user: User = Depends(security.get_current_user),
    config_service: ConfigService = Depends(get_config_service)
):
    """获取弹幕源全量替换配置

    全量替换模式：从 GitHub Releases 下载压缩包进行全量替换，
    而不是逐个文件对比哈希值下载。适用于 .so 文件更新不生效的情况。
    """
    enabled = await config_service.get("scraperFullReplaceEnabled", "false")
    return {
        "enabled": enabled.lower() == "true"
    }


@router.put("/scrapers/full-replace", status_code=status.HTTP_204_NO_CONTENT, summary="保存全量替换配置")
async def save_full_replace_config(
    payload: Dict[str, Any],
    current_user: User = Depends(security.get_current_user),
    config_service: ConfigService = Depends(get_config_service)
):
    """保存弹幕源全量替换配置"""
    enabled = payload.get("enabled", False)
    await config_service.set("scraperFullReplaceEnabled", str(enabled).lower())
    logger.info(f"用户 '{current_user.username}' 更新了全量替换配置: enabled={enabled}")


@router.delete("/scrapers/backup", summary="删除弹幕源备份")
async def delete_backup(
    current_user: User = Depends(security.get_current_user)
):
    """将资源业务委托给编排层，保留原有 HTTP 契约。"""
    try:
        return await resource_workflow.delete_backup(current_user)
    except resource_workflow.ResourceOperationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.delete("/scrapers/current", summary="删除当前弹幕源")
async def delete_current_scrapers(
    current_user: User = Depends(security.get_current_user),
    manager = Depends(get_scraper_manager)
):
    """将资源业务委托给编排层，保留原有 HTTP 契约。"""
    try:
        return await resource_workflow.delete_current_scrapers(current_user, manager)
    except resource_workflow.ResourceOperationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.delete("/scrapers/all", summary="删除当前源和备份源")
async def delete_all_scrapers(
    current_user: User = Depends(security.get_current_user),
    manager = Depends(get_scraper_manager)
):
    """将资源业务委托给编排层，保留原有 HTTP 契约。"""
    try:
        return await resource_workflow.delete_all_scrapers(current_user, manager)
    except resource_workflow.ResourceOperationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
