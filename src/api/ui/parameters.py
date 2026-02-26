"""
参数配置相关的API端点
"""
import logging
import json
import tempfile
import zipfile
import tarfile
import shutil
from pathlib import Path
from typing import Dict, Any
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
import httpx

from src.db import models, ConfigManager
from src.security import get_current_user
from src.api.dependencies import get_config_manager, get_scraper_manager

logger = logging.getLogger(__name__)
router = APIRouter()


def _is_docker_environment():
    """检测是否在Docker容器中运行"""
    import os
    if Path("/.dockerenv").exists():
        return True
    if os.getenv("DOCKER_CONTAINER") == "true" or os.getenv("IN_DOCKER") == "true":
        return True
    if Path.cwd() == Path("/app"):
        return True
    return False


def _get_scrapers_dir() -> Path:
    """获取 scrapers 目录路径"""
    if _is_docker_environment():
        return Path("/app/src/scrapers")
    else:
        return Path("src/scrapers")


@router.get("/config/github-token", summary="获取GitHub Token")
async def get_github_token(
    current_user: models.User = Depends(get_current_user),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """获取GitHub Token配置"""
    token = await config_manager.get("github_token", "")
    return {"token": token}


@router.post("/config/github-token", summary="保存GitHub Token")
async def save_github_token(
    payload: Dict[str, Any],
    current_user: models.User = Depends(get_current_user),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """保存GitHub Token配置"""
    token = payload.get("token", "")
    await config_manager.setValue("github_token", token)
    logger.info(f"用户 '{current_user.username}' 保存了GitHub Token")
    return {"message": "保存成功"}


@router.post("/config/github-token/verify", summary="验证GitHub Token")
async def verify_github_token(
    payload: Dict[str, Any],
    current_user: models.User = Depends(get_current_user)
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
    current_user: models.User = Depends(get_current_user),
    manager = Depends(get_scraper_manager)
):
    """上传并安装弹幕源离线包

    - 首次上传（当前目录和备份目录都没有弹幕源）：解压后进行热重载
    - 非首次上传（已有弹幕源）：部署到备份目录，需要重启容器
    """
    try:
        # 创建临时目录
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # 保存上传的文件
            file_path = temp_path / file.filename
            with open(file_path, "wb") as f:
                content = await file.read()
                f.write(content)

            # 解压文件
            extract_dir = temp_path / "extracted"
            extract_dir.mkdir()

            if file.filename.endswith('.zip'):
                with zipfile.ZipFile(file_path, 'r') as zip_ref:
                    # 安全检查：防止 Zip Slip 攻击
                    for member in zip_ref.namelist():
                        # 检查路径穿越
                        member_path = (extract_dir / member).resolve()
                        if not str(member_path).startswith(str(extract_dir.resolve())):
                            raise HTTPException(
                                status_code=400,
                                detail=f"检测到恶意压缩包：路径穿越尝试 ({member})"
                            )
                    zip_ref.extractall(extract_dir)
            elif file.filename.endswith(('.tar.gz', '.tgz')):
                with tarfile.open(file_path, 'r:gz') as tar_ref:
                    # 安全检查：防止 Zip Slip 攻击
                    for member in tar_ref.getmembers():
                        # 检查路径穿越
                        member_path = (extract_dir / member.name).resolve()
                        if not str(member_path).startswith(str(extract_dir.resolve())):
                            raise HTTPException(
                                status_code=400,
                                detail=f"检测到恶意压缩包：路径穿越尝试 ({member.name})"
                            )
                        # 检查符号链接
                        if member.issym() or member.islnk():
                            raise HTTPException(
                                status_code=400,
                                detail=f"检测到恶意压缩包：包含符号链接 ({member.name})"
                            )
                    tar_ref.extractall(extract_dir)
            else:
                raise HTTPException(status_code=400, detail="不支持的文件格式,仅支持 .zip 或 .tar.gz")

            # 验证 versions.json
            versions_file = extract_dir / "versions.json"
            if not versions_file.exists():
                raise HTTPException(status_code=400, detail="压缩包中缺少 versions.json 文件")

            with open(versions_file, 'r', encoding='utf-8') as f:
                versions_data = json.load(f)

            # 验证平台和架构
            import platform
            import sys

            current_platform = platform.system().lower()
            current_arch = platform.machine().lower()

            # 映射平台名称
            platform_map = {
                'linux': 'linux',
                'darwin': 'macos',
                'windows': 'windows'
            }

            # 映射架构名称
            arch_map = {
                'x86_64': 'x86',
                'amd64': 'x86',
                'aarch64': 'arm',
                'arm64': 'arm'
            }

            package_platform = versions_data.get('platform', '').lower()
            package_arch = versions_data.get('type', '').lower()

            expected_platform = platform_map.get(current_platform, current_platform)
            expected_arch = arch_map.get(current_arch, current_arch)

            if package_platform != expected_platform:
                raise HTTPException(
                    status_code=400,
                    detail=f"平台不匹配: 当前系统是 {expected_platform}, 压缩包是 {package_platform}"
                )

            if package_arch != expected_arch:
                raise HTTPException(
                    status_code=400,
                    detail=f"架构不匹配: 当前系统是 {expected_arch}, 压缩包是 {package_arch}"
                )

            # 获取目录路径
            scrapers_dir = _get_scrapers_dir()
            from .scraper_resources import BACKUP_DIR

            # 判断是否是首次上传（当前目录和备份目录都没有弹幕源）
            def _has_scraper_files(directory: Path) -> bool:
                """检查目录中是否有弹幕源文件"""
                if not directory.exists():
                    return False
                for f in directory.glob("*"):
                    if f.is_file() and f.suffix in ['.so', '.pyd']:
                        return True
                return False

            has_current_scrapers = _has_scraper_files(scrapers_dir)
            has_backup_scrapers = _has_scraper_files(BACKUP_DIR)
            is_first_upload = not has_current_scrapers and not has_backup_scrapers

            logger.info(f"离线包上传检测: 当前目录有弹幕源={has_current_scrapers}, 备份目录有弹幕源={has_backup_scrapers}, 首次上传={is_first_upload}")

            # 统计要上传的文件数
            file_count = sum(1 for f in extract_dir.iterdir() if f.is_file() and f.suffix in ['.so', '.pyd'])

            if is_first_upload:
                # ========== 首次上传：部署到 scrapers 和 backup 目录，然后热加载 ==========
                logger.info("首次上传离线包，将部署到 scrapers 目录并执行热加载")

                # 确保目录存在
                scrapers_dir.mkdir(parents=True, exist_ok=True)
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)

                # 复制 .so 和 .pyd 文件到 scrapers 目录和 backup 目录
                for f in extract_dir.iterdir():
                    if f.is_file() and f.suffix in ['.so', '.pyd']:
                        shutil.copy2(f, scrapers_dir / f.name)
                        shutil.copy2(f, BACKUP_DIR / f.name)
                        logger.info(f"已复制文件: {f.name}")

                # 复制 versions.json
                versions_file = extract_dir / "versions.json"
                if versions_file.exists():
                    shutil.copy2(versions_file, scrapers_dir / "versions.json")
                    shutil.copy2(versions_file, BACKUP_DIR / "versions.json")
                    logger.info("已复制 versions.json")

                # 复制或创建 package.json
                package_file = extract_dir / "package.json"
                if package_file.exists():
                    shutil.copy2(package_file, scrapers_dir / "package.json")
                    shutil.copy2(package_file, BACKUP_DIR / "package.json")
                    logger.info("已复制 package.json")
                else:
                    logger.info("离线包中没有 package.json,从 versions.json 创建")
                    package_data = {
                        "version": versions_data.get('version', 'unknown'),
                        "platform": versions_data.get('platform', ''),
                        "type": versions_data.get('type', ''),
                        "created_from_upload": True,
                        "upload_time": datetime.now().isoformat()
                    }
                    package_content = json.dumps(package_data, indent=2, ensure_ascii=False)
                    (scrapers_dir / "package.json").write_text(package_content)
                    (BACKUP_DIR / "package.json").write_text(package_content)
                    logger.info("已创建 package.json")

                logger.info(f"用户 '{current_user.username}' 首次上传了离线包,共 {file_count} 个文件")

                # 在后台异步热加载弹幕源
                async def reload_scrapers_background():
                    try:
                        import asyncio
                        await asyncio.sleep(0.5)  # 延迟0.5秒,确保响应已发送
                        await manager.load_and_sync_scrapers()
                        logger.info("弹幕源热加载完成")
                    except Exception as e:
                        logger.error(f"后台热加载弹幕源失败: {e}", exc_info=True)

                import asyncio
                asyncio.create_task(reload_scrapers_background())

                return {
                    "message": f"上传成功,共安装 {file_count} 个文件（已热加载）",
                    "version": versions_data.get('version'),
                    "scrapers": list(versions_data.get('scrapers', {}).keys()),
                    "need_restart": False
                }

            else:
                # ========== 非首次上传：只部署到 backup 目录，需要重启容器 ==========
                logger.info("非首次上传离线包，将部署到备份目录，需要重启容器")

                # 确保备份目录存在
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)

                # 清空旧备份文件（保留metadata.json）
                for f in BACKUP_DIR.glob("*"):
                    if f.is_file() and f.name != "backup_metadata.json":
                        f.unlink()

                # 复制 .so 和 .pyd 文件到 backup 目录
                for f in extract_dir.iterdir():
                    if f.is_file() and f.suffix in ['.so', '.pyd']:
                        shutil.copy2(f, BACKUP_DIR / f.name)
                        logger.info(f"已复制文件到备份目录: {f.name}")

                # 复制 versions.json 到 backup 目录
                versions_file = extract_dir / "versions.json"
                if versions_file.exists():
                    shutil.copy2(versions_file, BACKUP_DIR / "versions.json")
                    logger.info("已复制 versions.json 到备份目录")

                # 复制或创建 package.json 到 backup 目录
                package_file = extract_dir / "package.json"
                if package_file.exists():
                    shutil.copy2(package_file, BACKUP_DIR / "package.json")
                    logger.info("已复制 package.json 到备份目录")
                else:
                    logger.info("离线包中没有 package.json,从 versions.json 创建")
                    package_data = {
                        "version": versions_data.get('version', 'unknown'),
                        "platform": versions_data.get('platform', ''),
                        "type": versions_data.get('type', ''),
                        "created_from_upload": True,
                        "upload_time": datetime.now().isoformat()
                    }
                    (BACKUP_DIR / "package.json").write_text(json.dumps(package_data, indent=2, ensure_ascii=False))
                    logger.info("已创建 package.json 到备份目录")

                logger.info(f"用户 '{current_user.username}' 上传了离线包到备份目录,共 {file_count} 个文件")

                # 检查是否有 Docker socket
                from src.utils.docker_utils import is_docker_socket_available, restart_container
                docker_available = is_docker_socket_available()

                if docker_available:
                    # 有 Docker socket，执行容器重启
                    logger.info("检测到 Docker socket，将在后台重启容器")

                    async def restart_container_background():
                        try:
                            import asyncio
                            await asyncio.sleep(1.0)  # 延迟1秒,确保响应已发送
                            from src.core.config import get_config_manager
                            config_manager = get_config_manager()
                            fallback_name = await config_manager.get("containerName", "misaka_danmu_server")
                            result = await restart_container(fallback_name)
                            if result.get("success"):
                                logger.info(f"容器重启指令已发送: {result.get('message')}")
                            else:
                                logger.warning(f"容器重启失败: {result.get('message')}")
                        except Exception as e:
                            logger.error(f"后台重启容器失败: {e}", exc_info=True)

                    import asyncio
                    asyncio.create_task(restart_container_background())

                    return {
                        "message": f"上传成功,共 {file_count} 个文件已部署到备份目录，容器正在重启...",
                        "version": versions_data.get('version'),
                        "scrapers": list(versions_data.get('scrapers', {}).keys()),
                        "need_restart": True,
                        "auto_restart": True
                    }
                else:
                    # 没有 Docker socket，提示手动重启
                    logger.info("未检测到 Docker socket，需要手动重启容器")
                    return {
                        "message": f"上传成功,共 {file_count} 个文件已部署到备份目录。请手动重启容器以加载新的弹幕源。",
                        "version": versions_data.get('version'),
                        "scrapers": list(versions_data.get('scrapers', {}).keys()),
                        "need_restart": True,
                        "auto_restart": False
                    }

    except HTTPException:
        raise
    except PermissionError as pe:
        scrapers_dir = _get_scrapers_dir()
        logger.error(
            f"上传弹幕源离线包失败，写入目录 '{scrapers_dir}' 时发生权限错误: {pe}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=403,
            detail=f"权限错误: 无法写入弹幕源文件到 {scrapers_dir}。错误: {pe}",
        )
    except OSError as oe:
        scrapers_dir = _get_scrapers_dir()
        logger.error(
            f"上传弹幕源离线包失败，访问目录 '{scrapers_dir}' 时发生文件系统错误: {oe}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"文件系统错误: 无法写入弹幕源文件到 {scrapers_dir}。错误: {oe}",
        )
    except Exception as e:
        logger.error(f"上传弹幕源离线包失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"上传失败: {str(e)}")

