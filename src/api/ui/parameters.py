"""
参数配置相关的API端点
"""
import logging
from pathlib import Path
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
import httpx

from src.schemas import ui_models
from src.utils.auth import security
from src.api.dependencies import get_config_service, get_scraper_manager
from src.workflows.scraper_resources import offline_upload

logger = logging.getLogger(__name__)
router = APIRouter()
_MAX_UPLOAD_BYTES = 256 * 1024 * 1024


@router.get("/config/github-token", summary="获取GitHub Token")
async def get_github_token(
    current_user: ui_models.User = Depends(security.get_current_user),
    config_service = Depends(get_config_service)
):
    """获取GitHub Token配置"""
    token = await config_service.get("github_token", "")
    return {"token": token}


@router.post("/config/github-token", summary="保存GitHub Token")
async def save_github_token(
    payload: Dict[str, Any],
    current_user: ui_models.User = Depends(security.get_current_user),
    config_service = Depends(get_config_service)
):
    """保存GitHub Token配置"""
    token = payload.get("token", "")
    await config_service.set("github_token", token)
    logger.info(f"用户 '{current_user.username}' 保存了GitHub Token")
    return {"message": "保存成功"}


@router.post("/config/github-token/verify", summary="验证GitHub Token")
async def verify_github_token(
    payload: Dict[str, Any],
    current_user: ui_models.User = Depends(security.get_current_user)
):
    """验证GitHub Token有效性"""
    token = payload.get("token", "")
    if not token:
        raise HTTPException(status_code=400, detail="Token不能为空")

    try:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json"
        }

        async with httpx.AsyncClient() as client:
            # 获取用户信息
            user_response = await client.get("https://api.github.com/user", headers=headers)
            if user_response.status_code != 200:
                raise HTTPException(status_code=400, detail="Token无效")

            user_data = user_response.json()

            # 获取速率限制信息
            rate_response = await client.get("https://api.github.com/rate_limit", headers=headers)
            rate_data = rate_response.json()

            return {
                "valid": True,
                "username": user_data.get("login"),
                "rateLimit": {
                    "limit": rate_data["rate"]["limit"],
                    "remaining": rate_data["rate"]["remaining"],
                    "reset": rate_data["rate"]["reset"]
                }
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"验证GitHub Token失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"验证失败: {str(e)}")


@router.post("/scrapers/upload-package", summary="上传弹幕源离线包")
async def upload_scraper_package(
    file: UploadFile = File(...),
    current_user: ui_models.User = Depends(security.get_current_user),
    manager = Depends(get_scraper_manager),
    config_service = Depends(get_config_service)
):
    """读取并验证上传请求，离线包安装统一委托给流程层。"""
    try:
        safe_filename = Path(file.filename or "").name
        if not safe_filename or safe_filename != file.filename:
            raise HTTPException(status_code=400, detail="无效的上传文件名")
        if not safe_filename.endswith(('.zip', '.tar.gz', '.tgz')):
            raise HTTPException(status_code=400, detail="不支持的文件格式，仅支持 .zip 或 .tar.gz")
        uploaded_size = 0

        async def read_chunk(size: int) -> bytes:
            """请求层保留分块读取与上传体积限制，不持有文件系统句柄。"""
            nonlocal uploaded_size
            chunk = await file.read(size)
            uploaded_size += len(chunk)
            if uploaded_size > _MAX_UPLOAD_BYTES:
                raise offline_upload.OfflineUploadError(413, "上传文件超过 256 MiB 限制")
            return chunk

        return await offline_upload.install_offline_package(
            safe_filename, read_chunk, manager, current_user.username,
        )
    except offline_upload.OfflineUploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"上传弹幕源离线包失败: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"上传失败: {exc}") from exc
