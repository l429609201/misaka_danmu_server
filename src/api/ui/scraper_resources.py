"""
弹幕源资源管理 API
支持从 GitHub 仓库加载编译好的 scraper 资源文件
"""
import asyncio
import hashlib
import json
import logging
import platform
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from src._version import APP_VERSION
from src.api.dependencies import get_scraper_manager, get_config_service
from src.schemas.ui_models import User
from src.services.cache_service import get_cache_service
from src.services.config_service import ConfigService
from src.services.download_task_manager import TaskStatus, get_download_task_manager
from src.services import scraper_resource_service as resource_service
from src.services.scraper_resource_service import (
    BACKUP_DIR, SCRAPER_DOWNLOAD_TASK_CACHE_PREFIX, _download_lock,
    _get_scrapers_dir, _build_base_url, parse_gitee_url, parse_github_url,
    get_platform_info, get_platform_key, _fetch_github_release_asset,
    _fetch_gitee_release_asset, _download_and_extract_release,
    resource_version_cache,
)
from src.services.scraper_manager import _version_satisfies
from src.utils.runtime.docker_utils import is_docker_socket_available, is_running_in_docker, restart_container
from src.utils.scraper_ops.scraper_download_executor import start_download_task
from src.utils.auth import security
from src.utils.scraper_ops.scraper_version_manager import ScraperVersionManager
# 使用迁移后的工具模块，统一在文件顶部导入以避免请求时导入失败。
from src.utils.scraper_ops.remote_manifest_fetcher import fetch_remote_manifest_info


logger = logging.getLogger(__name__)
router = APIRouter()

# 版本信息缓存
_VERSION_CACHE_DURATION = timedelta(minutes=3)  # 缓存3分钟


def _get_local_min_server_version() -> Optional[str]:
    """从本地 scraper_manifest.json 读取 min_server_version

    使用 ScraperVersionManager 统一管理
    """
    return ScraperVersionManager.get_min_server_version(_get_scrapers_dir())


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
    """从 GitHub/Gitee API 获取仓库的分支列表和最近的标签"""
    repo_url = await config_service.get("scraper_resource_repo", "")
    if not repo_url:
        return {"branches": [], "tags": [], "appVersion": APP_VERSION, "minServerVersion": _get_local_min_server_version()}

    # 解析仓库 URL
    gitee_info = parse_gitee_url(repo_url)
    repo_info = None
    if not gitee_info:
        try:
            repo_info = parse_github_url(repo_url)
        except ValueError:
            pass

    if not repo_info and not gitee_info:
        return {"branches": [], "tags": [], "appVersion": APP_VERSION, "minServerVersion": _get_local_min_server_version()}

    # 构建请求头
    headers = {}
    if repo_info:
        github_token = await config_service.get("github_token", "")
        if github_token:
            headers["Authorization"] = f"Bearer {github_token}"

    # 获取代理
    proxy_url = await config_service.get("proxyUrl", "")
    proxy_enabled = (await config_service.get("proxyEnabled", "false")).lower() == "true"
    proxy = proxy_url if proxy_enabled and proxy_url else None

    branches = []
    tags = []
    timeout = httpx.Timeout(10.0, read=10.0)

    try:
        async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True, proxy=proxy) as client:
            if repo_info:
                owner, repo = repo_info['owner'], repo_info['repo']
                # 获取分支
                try:
                    resp = await client.get(f"https://api.github.com/repos/{owner}/{repo}/branches?per_page=10")
                    if resp.status_code == 200:
                        branches = [b["name"] for b in resp.json()]
                except Exception as e:
                    logger.warning(f"获取 GitHub 分支列表失败: {e}")
                # 获取最近5个 tag，并为每个 tag 获取其 min_server_version
                try:
                    resp = await client.get(f"https://api.github.com/repos/{owner}/{repo}/tags?per_page=5")
                    if resp.status_code == 200:
                        tag_names = [t["name"] for t in resp.json()]
                        # 并发获取每个 tag 的 scraper_manifest.json 中的 min_server_version
                        async def _get_tag_min_ver(tag_name):
                            try:
                                manifest_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{tag_name}/scraper_manifest.json"
                                manifest_resp = await client.get(manifest_url)
                                if manifest_resp.status_code == 200:
                                    return manifest_resp.json().get("min_server_version")
                            except Exception:
                                pass
                            return None

                        min_vers = await asyncio.gather(*[_get_tag_min_ver(t) for t in tag_names])
                        tags = [{"name": t, "minServerVersion": v} for t, v in zip(tag_names, min_vers)]
                except Exception as e:
                    logger.warning(f"获取 GitHub 标签列表失败: {e}")
            elif gitee_info:
                owner, repo = gitee_info['owner'], gitee_info['repo']
                try:
                    resp = await client.get(f"https://gitee.com/api/v5/repos/{owner}/{repo}/branches?per_page=10")
                    if resp.status_code == 200:
                        branches = [b["name"] for b in resp.json()]
                except Exception as e:
                    logger.warning(f"获取 Gitee 分支列表失败: {e}")
                try:
                    resp = await client.get(f"https://gitee.com/api/v5/repos/{owner}/{repo}/tags?per_page=5")
                    if resp.status_code == 200:
                        tag_names = [t["name"] for t in resp.json()]
                        # 并发获取每个 tag 的 scraper_manifest.json 中的 min_server_version
                        async def _get_gitee_tag_min_ver(tag_name):
                            try:
                                manifest_url = f"https://gitee.com/{owner}/{repo}/raw/{tag_name}/scraper_manifest.json"
                                manifest_resp = await client.get(manifest_url)
                                if manifest_resp.status_code == 200:
                                    return manifest_resp.json().get("min_server_version")
                            except Exception:
                                pass
                            return None

                        min_vers = await asyncio.gather(*[_get_gitee_tag_min_ver(t) for t in tag_names])
                        tags = [{"name": t, "minServerVersion": v} for t, v in zip(tag_names, min_vers)]
                except Exception as e:
                    logger.warning(f"获取 Gitee 标签列表失败: {e}")
    except Exception as e:
        logger.warning(f"获取仓库 refs 失败: {e}")

    return {
        "branches": branches,
        "tags": tags,
        "appVersion": APP_VERSION,
        "minServerVersion": _get_local_min_server_version(),
    }


async def _fetch_manifest_info_with_retry(manifest_url: str, headers: Dict[str, str], max_retries: int = 3, proxy: Optional[str] = None) -> Optional[Dict[str, Optional[str]]]:
    """
    带重试机制的版本信息获取函数（已废弃，使用 remote_manifest_fetcher 模块）

    为保持向后兼容，保留此函数作为适配器。
    """
    # 从完整 URL 中提取 base_url
    base_url = manifest_url.rsplit('/', 1)[0]

    return await fetch_remote_manifest_info(
        base_url=base_url,
        headers=headers,
        max_retries=max_retries,
        proxy=proxy,
        timeout_seconds=15.0,
        read_timeout_seconds=8.0
    )


@router.get("/scrapers/versions", summary="获取资源包版本信息")
async def get_versions(
    current_user: User = Depends(security.get_current_user),
    config_service: ConfigService = Depends(get_config_service),
    force_refresh: bool = False  # 新增参数：强制刷新缓存
):
    """获取本地和远程资源包版本号（带缓存机制）"""

    try:
        # 检查缓存是否有效
        if not force_refresh and resource_version_cache.value and resource_version_cache.updated_at:
            cache_age = datetime.now() - resource_version_cache.updated_at
            if cache_age < _VERSION_CACHE_DURATION:
                logger.debug(f"使用缓存的版本信息 (缓存时间: {cache_age.total_seconds():.1f}秒)")
                return resource_version_cache.value

        # 获取本地版本
        # why: 使用 ScraperVersionManager 统一读取
        local_version = ScraperVersionManager.get_local_version(_get_scrapers_dir())

        # 获取代理配置
        proxy_url = await config_service.get("proxyUrl", "")
        proxy_enabled_str = await config_service.get("proxyEnabled", "false")
        proxy_enabled = proxy_enabled_str.lower() == 'true'
        proxy_to_use = proxy_url if proxy_enabled and proxy_url else None

        # 获取远程版本（当前配置的资源仓库）
        remote_version = None
        remote_min_server_version = None
        repo_url = await config_service.get("scraper_resource_repo", "")

        if repo_url:
            headers = {}
            repo_info = None
            gitee_info = None

            # 先尝试解析为 Gitee URL
            gitee_info = parse_gitee_url(repo_url)
            if not gitee_info:
                # 不是 Gitee，尝试解析为 GitHub URL
                try:
                    repo_info = parse_github_url(repo_url)
                except ValueError:
                    pass

            # 如果是GitHub仓库,添加Token（Gitee不需要Token）
            if repo_info:
                github_token = await config_service.get("github_token", "")
                if github_token:
                    headers["Authorization"] = f"Bearer {github_token}"

            base_url = _build_base_url(repo_info, repo_url, gitee_info)
            manifest_url = f"{base_url}/scraper_manifest.json"

            # 区分日志：用户配置的仓库
            platform_name = "Gitee" if gitee_info else "GitHub"
            logger.info(f"[版本检查] 正在获取用户配置仓库版本 ({platform_name}): {repo_url}")
            remote_info = await _fetch_manifest_info_with_retry(manifest_url, headers, max_retries=1, proxy=proxy_to_use)
            if remote_info:
                remote_version = remote_info["version"]
                remote_min_server_version = remote_info.get("minServerVersion")
                logger.info(f"[版本检查] 用户配置仓库版本: {remote_version}")
            else:
                logger.warning(f"[版本检查] 用户配置仓库版本获取失败")

        # 固定源仓库（官方仓库）版本——仅在用户已配置资源仓库时才请求，避免无谓的网络超时
        official_version = None
        if repo_url:
            try:
                official_repo_info = parse_github_url("https://github.com/l429609201/Misaka-Scraper-Resources")

                github_token = await config_service.get("github_token", "")
                headers_official = {}
                if github_token:
                    headers_official["Authorization"] = f"Bearer {github_token}"

                official_base_url = _build_base_url(official_repo_info, "https://github.com/l429609201/Misaka-Scraper-Resources")
                official_manifest_url = f"{official_base_url}/scraper_manifest.json"

                logger.info(f"[版本检查] 正在获取官方仓库版本 (GitHub): https://github.com/l429609201/Misaka-Scraper-Resources")
                official_info = await _fetch_manifest_info_with_retry(official_manifest_url, headers_official, max_retries=1, proxy=proxy_to_use)
                if official_info:
                    official_version = official_info["version"]
                    logger.info(f"[版本检查] 官方仓库版本: {official_version}")
                else:
                    logger.warning(f"[版本检查] 官方仓库版本获取失败")
            except Exception as e:
                logger.warning(f"获取官方资源仓库版本失败: {e}")

        # 构建结果
        result = {
            "localVersion": local_version,
            "remoteVersion": remote_version,
            "officialVersion": official_version,
            "hasUpdate": remote_version and local_version != "unknown" and remote_version != local_version,
            "minServerVersion": remote_min_server_version,
        }

        # 更新缓存
        resource_version_cache.value = result
        resource_version_cache.updated_at = datetime.now()

        return result

    except Exception as e:
        logger.error(f"获取版本信息失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取版本信息失败: {str(e)}")


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
    """通过共享服务备份弹幕源。"""
    try:
        return await resource_service.backup_scrapers(
            current_user, new_versions_data, new_hashes_data, package_data
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/scrapers/backup-info", summary="获取备份信息")
async def get_backup_info(
    current_user: User = Depends(security.get_current_user)
):
    """获取当前备份的详细信息"""
    try:
        # 检查备份目录和 manifest 文件
        manifest_file = BACKUP_DIR / "scraper_manifest.json"
        if not BACKUP_DIR.exists() or not manifest_file.exists():
            return {
                "hasBackup": False,
                "message": "暂无备份"
            }

        # 从 manifest 读取版本信息
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))

        # 统计备份文件数量
        backup_files = list(BACKUP_DIR.glob("*.so")) + list(BACKUP_DIR.glob("*.pyd"))

        return {
            "hasBackup": True,
            "backupTime": manifest.get("updated_at"),
            "fileCount": len(backup_files),
            "platform": manifest.get("platform"),
            "packageVersion": manifest.get("version"),
            "sourceCount": len(manifest.get("sources", {}))
        }

    except Exception as e:
        logger.error(f"获取备份信息失败: {e}", exc_info=True)
        return {
            "hasBackup": False,
            "message": f"读取备份信息失败: {str(e)}"
        }


@router.post("/scrapers/restore", summary="从备份还原弹幕源")
async def restore_scrapers(
    current_user: User = Depends(security.get_current_user),
    manager = Depends(get_scraper_manager),
) -> Dict[str, Any]:
    """通过共享服务还原文件并重载弹幕源。"""
    try:
        result = await resource_service.restore_scrapers(current_user)
        # 保持原接口先响应、延迟重载的行为，依赖迁移不改变请求耗时。
        async def reload_restored_scrapers() -> None:
            await asyncio.sleep(1)
            try:
                await manager.load_and_sync_scrapers()
            except Exception:
                logger.exception("还原后的后台重载弹幕源失败")

        asyncio.create_task(reload_restored_scrapers())
        return result
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"还原失败: {exc}") from exc


@router.post("/scrapers/reload", summary="重载弹幕源")
async def reload_scrapers(
    current_user: User = Depends(security.get_current_user),
    manager = Depends(get_scraper_manager)
):
    """重新加载所有弹幕源"""
    try:
        logger.info(f"用户 '{current_user.username}' 请求重载弹幕源")

        # 创建后台任务重新加载 scrapers
        async def reload_scrapers_background():
            await asyncio.sleep(1)  # 延迟1秒,确保响应已发送
            try:
                await manager.load_and_sync_scrapers()
                logger.info(f"用户 '{current_user.username}' 成功重载了弹幕源")
            except Exception as e:
                logger.error(f"后台重载弹幕源失败: {e}", exc_info=True)

        # 启动后台任务
        asyncio.create_task(reload_scrapers_background())

        return {"message": "弹幕源重载请求已提交，正在后台重载..."}
    except Exception as e:
        logger.error(f"重载弹幕源失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"重载失败: {str(e)}")


@router.post("/scrapers/load-resources-stream", summary="从资源仓库加载弹幕源(SSE流式)")
async def load_resources_stream(
    payload: Dict[str, Any],
    current_user: User = Depends(security.get_current_user),
    config_service: ConfigService = Depends(get_config_service),
    manager = Depends(get_scraper_manager)
):
    """从资源仓库下载并加载弹幕源文件,通过SSE推送进度"""

    async def event_generator():
        """SSE事件生成器"""
        # 检查是否有其他下载任务正在进行
        if _download_lock.locked():
            logger.warning("检测到并发下载请求，已拒绝")
            yield f"data: {json.dumps({'type': 'error', 'message': '已有下载任务正在进行，请稍后再试'}, ensure_ascii=False)}\n\n"
            return

        # 获取锁，防止并发下载
        logger.info("开始下载任务，已获取下载锁")
        async with _download_lock:
            try:
                last_heartbeat = asyncio.get_event_loop().time()

                async def send_heartbeat_if_needed():
                    """如果距离上次发送超过2秒,发送心跳（缩短间隔防止连接超时）"""
                    nonlocal last_heartbeat
                    current_time = asyncio.get_event_loop().time()
                    if current_time - last_heartbeat > 2:
                        last_heartbeat = current_time
                        return ": heartbeat\n\n"
                    return None

                try:
                    # 获取仓库链接
                    repo_url = payload.get("repoUrl")
                    if not repo_url:
                        repo_url = await config_service.get("scraper_resource_repo", "")

                    if not repo_url:
                        yield f"data: {json.dumps({'type': 'error', 'message': '未配置资源仓库链接'}, ensure_ascii=False)}\n\n"
                        return

                    # 获取分支/标签参数（前端传递的版本选择）
                    branch = payload.get("branch", "main")
                    logger.info(f"用户选择的版本/分支: {branch}")

                    # 获取平台信息
                    platform_key = get_platform_key()
                    platform_info = get_platform_info()
                    last_heartbeat = asyncio.get_event_loop().time()
                    yield f"data: {json.dumps({'type': 'info', 'message': f'当前平台: {platform_key}'}, ensure_ascii=False)}\n\n"

                    # 解析仓库URL并构造base_url
                    headers = {}
                    repo_info = None
                    gitee_info = None

                    # 先尝试解析为 Gitee URL
                    gitee_info = parse_gitee_url(repo_url)
                    if not gitee_info:
                        # 不是 Gitee，尝试解析为 GitHub URL
                        try:
                            repo_info = parse_github_url(repo_url)
                        except ValueError:
                            pass

                    # 如果是GitHub仓库,添加Token（Gitee不需要Token）
                    if repo_info:
                        github_token = await config_service.get("github_token", "")
                        if github_token:
                            headers["Authorization"] = f"Bearer {github_token}"

                    base_url = _build_base_url(repo_info, repo_url, gitee_info, branch)  # 传递 branch 参数

                    # 获取代理配置
                    proxy_url = await config_service.get("proxyUrl", "")
                    proxy_enabled_str = await config_service.get("proxyEnabled", "false")
                    proxy_enabled = proxy_enabled_str.lower() == 'true'
                    proxy_to_use = proxy_url if proxy_enabled and proxy_url else None

                    if proxy_to_use:
                        logger.info(f"GitHub资源下载将使用代理: {proxy_to_use}")
                        yield f"data: {json.dumps({'type': 'info', 'message': f'使用代理: {proxy_to_use}'}, ensure_ascii=False)}\n\n"

                    # 检查是否启用全量替换模式
                    full_replace_enabled = await config_service.get("scraperFullReplaceEnabled", "false")
                    use_full_replace = full_replace_enabled.lower() == "true"

                    # ========== 全量替换模式 ==========
                    # 支持从 GitHub 或 Gitee 的 Releases 下载压缩包
                    asset_info = None
                    if use_full_replace and gitee_info:
                        # Gitee 仓库全量替换
                        logger.info("使用全量替换模式，从 Gitee Releases 下载压缩包")
                        yield f"data: {json.dumps({'type': 'info', 'message': '全量替换模式：正在从 Gitee Releases 获取压缩包...'}, ensure_ascii=False)}\n\n"

                        # 获取 Gitee Release 资产信息
                        asset_info = await _fetch_gitee_release_asset(
                            gitee_info=gitee_info,
                            platform_key=platform_key,
                            headers=headers,
                            proxy=proxy_to_use,
                            tag_or_branch=branch  # 传递用户选择的版本/分支
                        )

                        if not asset_info:
                            logger.warning("Gitee: 未找到匹配的 Release 压缩包，回退到逐文件下载模式")
                            yield f"data: {json.dumps({'type': 'info', 'message': 'Gitee 未找到 Release 压缩包，回退到逐文件下载模式'}, ensure_ascii=False)}\n\n"
                            use_full_replace = False
                    elif use_full_replace and repo_info:
                        # GitHub 仓库全量替换
                        logger.info("使用全量替换模式，从 GitHub Releases 下载压缩包")
                        yield f"data: {json.dumps({'type': 'info', 'message': '全量替换模式：正在从 GitHub Releases 获取压缩包...'}, ensure_ascii=False)}\n\n"

                        # 获取 GitHub Release 资产信息
                        asset_info = await _fetch_github_release_asset(
                            repo_info=repo_info,
                            platform_key=platform_key,
                            headers=headers,
                            proxy=proxy_to_use,
                            tag_or_branch=branch  # 传递用户选择的版本/分支
                        )

                        if not asset_info:
                            logger.warning("GitHub: 未找到匹配的 Release 压缩包，回退到逐文件下载模式")
                            yield f"data: {json.dumps({'type': 'info', 'message': 'GitHub 未找到 Release 压缩包，回退到逐文件下载模式'}, ensure_ascii=False)}\n\n"
                            use_full_replace = False

                    if use_full_replace and (repo_info or gitee_info) and asset_info:
                        asset_filename = asset_info['filename']
                        asset_version = asset_info['version']
                        yield f"data: {json.dumps({'type': 'info', 'message': f'找到压缩包: {asset_filename} (版本: {asset_version})'}, ensure_ascii=False)}\n\n"

                        # 前置版本校验：在备份/下载之前先取 scraper_manifest.json（几KB）核验 min_server_version。
                        # why：整包下载+备份耗时可达数分钟，版本不满足时应在任何
                        #      磁盘写入之前快速失败。此处与 incremental 路径的前置校验对齐。
                        try:
                            _pre_timeout = httpx.Timeout(15.0, read=15.0)
                            async with httpx.AsyncClient(
                                timeout=_pre_timeout, headers=headers,
                                follow_redirects=True, proxy=proxy_to_use
                            ) as _pre_client:
                                _pre_resp = await _pre_client.get(f"{base_url}/scraper_manifest.json")
                                if _pre_resp.status_code == 200:
                                    _pre_manifest = _pre_resp.json()
                                    _min_req = _pre_manifest.get("min_server_version")
                                    if _min_req:
                                        if not _version_satisfies(APP_VERSION, _min_req):
                                            _vmsg = (
                                                f"弹幕源包要求服务器版本 >= {_min_req}，"
                                                f"当前版本 {APP_VERSION}，请先升级服务器再下载"
                                            )
                                            logger.warning(f"[全量替换版本预检失败] {_vmsg}")
                                            yield f"data: {json.dumps({'type': 'error', 'message': _vmsg}, ensure_ascii=False)}\n\n"
                                            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                                            return
                        except Exception as _pre_err:
                            # 预检网络失败不阻断：解压后的后置校验仍会兜底
                            logger.debug(f"全量替换版本预检跳过（网络异常: {_pre_err}）")

                        # 先备份当前文件
                        yield f"data: {json.dumps({'type': 'info', 'message': '正在备份当前弹幕源...'}, ensure_ascii=False)}\n\n"
                        try:
                            await backup_scrapers(current_user)
                            logger.info("备份当前弹幕源成功")
                            yield f"data: {json.dumps({'type': 'info', 'message': '备份完成'}, ensure_ascii=False)}\n\n"
                        except Exception as backup_error:
                            logger.error(f"备份失败: {backup_error}")
                            yield f"data: {json.dumps({'type': 'error', 'message': f'备份失败: {str(backup_error)}'}, ensure_ascii=False)}\n\n"
                            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                            return

                        # 下载并解压
                        scrapers_dir = _get_scrapers_dir()

                        async def progress_cb(msg):
                            nonlocal last_heartbeat
                            last_heartbeat = asyncio.get_event_loop().time()

                        # 使用生成器无法直接 yield，需要手动发送进度
                        yield f"data: {json.dumps({'type': 'info', 'message': '正在下载压缩包...'}, ensure_ascii=False)}\n\n"

                        success = await _download_and_extract_release(
                            asset_info=asset_info,
                            scrapers_dir=scrapers_dir,
                            headers=headers,
                            proxy=proxy_to_use,
                            progress_callback=progress_cb
                        )

                        if success:
                            # _download_and_extract_release 已经完成了：
                            # 1. 解压到临时目录
                            # 2. 从 package.json + versions.json 生成 scraper_manifest.json
                            # 3. 删除临时目录的 package.json 和 versions.json
                            # 4. 持久化 scraper_manifest.json + .so 到 backup 目录
                            # 5. 覆盖到运行目录
                            # 6. 删除运行目录的 package.json 和 versions.json
                            # 此时运行目录和备份目录都只有 scraper_manifest.json + .so 文件

                            yield f"data: {json.dumps({'type': 'complete', 'downloaded': 1, 'skipped': 0, 'failed': 0, 'failed_list': [], 'full_replace': True}, ensure_ascii=False)}\n\n"
                            yield f"data: {json.dumps({'type': 'info', 'message': '⚠️ 全量替换完成，由于 .so 文件已被替换，建议重启服务以确保更新生效'}, ensure_ascii=False)}\n\n"
                            yield f"data: {json.dumps({'type': 'restart_required', 'message': '建议重启服务以确保 .so 文件更新生效'}, ensure_ascii=False)}\n\n"
                            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"

                            # 后台重载任务
                            async def reload_scrapers_background():
                                await asyncio.sleep(0.5)
                                try:
                                    logger.info("开始重载弹幕源...")
                                    await manager.load_and_sync_scrapers()
                                    logger.info(f"用户 '{current_user.username}' 通过全量替换模式更新了弹幕源")
                                    resource_version_cache.value = None
                                    resource_version_cache.updated_at = None
                                except Exception as e:
                                    logger.error(f"后台加载弹幕源失败: {e}", exc_info=True)

                            asyncio.create_task(reload_scrapers_background())
                            return
                        else:
                            logger.error("全量替换失败")
                            yield f"data: {json.dumps({'type': 'error', 'message': '全量替换失败，请检查日志'}, ensure_ascii=False)}\n\n"
                            # 尝试还原备份
                            try:
                                await restore_scrapers(current_user, manager)
                                yield f"data: {json.dumps({'type': 'info', 'message': '已还原备份'}, ensure_ascii=False)}\n\n"
                            except Exception as restore_error:
                                logger.error(f"还原备份失败: {restore_error}")
                            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                            return

                    # ========== 逐文件下载模式（默认）==========
                    # 下载 scraper_manifest.json 获取资源包信息
                    manifest_url = f"{base_url}/scraper_manifest.json"
                    logger.info(f"正在从 {manifest_url} 获取资源包信息...")
                    yield f"data: {json.dumps({'type': 'info', 'message': '正在获取资源包信息...'}, ensure_ascii=False)}\n\n"

                    # 设置更详细的超时配置: 连接超时30秒, 读取超时30秒
                    timeout_config = httpx.Timeout(30.0, read=30.0)
                    max_manifest_retries = 3  # 获取 scraper_manifest.json 的重试次数
                    package_data = None

                    for pkg_retry in range(max_manifest_retries + 1):
                        try:
                            if pkg_retry > 0:
                                wait_time = min(2 ** pkg_retry, 8)
                                logger.warning(f"获取资源包信息重试 {pkg_retry}/{max_manifest_retries}，等待 {wait_time} 秒...")
                                yield f"data: {json.dumps({'type': 'info', 'message': f'获取资源包信息失败，正在重试 ({pkg_retry}/{max_manifest_retries})...'}, ensure_ascii=False)}\n\n"
                                await asyncio.sleep(wait_time)

                            async with httpx.AsyncClient(timeout=timeout_config, headers=headers, follow_redirects=True, proxy=proxy_to_use) as client:
                                response = await client.get(manifest_url)
                                if response.status_code == 200:
                                    package_data = response.json()
                                    logger.info("成功获取资源包信息")
                                    break  # 成功，跳出重试循环
                                else:
                                    logger.warning(f"获取资源包信息失败: HTTP {response.status_code} (重试 {pkg_retry}/{max_manifest_retries})")
                                    if pkg_retry == max_manifest_retries:
                                        yield f"data: {json.dumps({'type': 'error', 'message': f'无法获取资源包信息 (HTTP {response.status_code})，请检查仓库链接或更换CDN节点'}, ensure_ascii=False)}\n\n"
                                        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                                        return

                        except httpx.TimeoutException as timeout_err:
                            logger.warning(f"连接超时 (重试 {pkg_retry}/{max_manifest_retries}): {timeout_err}")
                            if pkg_retry == max_manifest_retries:
                                yield f"data: {json.dumps({'type': 'error', 'message': '连接超时，请检查网络或更换CDN节点'}, ensure_ascii=False)}\n\n"
                                yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                                return
                        except httpx.ConnectError as conn_err:
                            logger.warning(f"连接失败 (重试 {pkg_retry}/{max_manifest_retries}): {conn_err}")
                            if pkg_retry == max_manifest_retries:
                                yield f"data: {json.dumps({'type': 'error', 'message': '无法连接到资源仓库，请检查网络或更换CDN节点'}, ensure_ascii=False)}\n\n"
                                yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                                return
                        except Exception as e:
                            logger.warning(f"获取资源包信息异常 (重试 {pkg_retry}/{max_package_retries}): {e}")
                            if pkg_retry == max_package_retries:
                                yield f"data: {json.dumps({'type': 'error', 'message': f'获取资源包信息失败: {str(e)}'}, ensure_ascii=False)}\n\n"
                                yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                                return

                    if not package_data:
                        yield f"data: {json.dumps({'type': 'error', 'message': '获取资源包信息失败'}, ensure_ascii=False)}\n\n"
                        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                        return

                    # 前置检查：远程弹幕源包是否要求更高的服务器版本（两字段语义相同）
                    pkg_min_server_ver = package_data.get('min_server_version') or package_data.get('min_fetchable_version')
                    if pkg_min_server_ver:
                        if not _version_satisfies(APP_VERSION, pkg_min_server_ver):
                            msg = f"远程弹幕源包要求服务器版本 >= {pkg_min_server_ver}，当前版本 {APP_VERSION}，请先升级服务器"
                            logger.warning(msg)
                            yield f"data: {json.dumps({'type': 'error', 'message': msg}, ensure_ascii=False)}\n\n"
                            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                            return

                    # 获取资源列表 (支持 resources 字段)
                    resources = package_data.get('resources', {})
                    if not resources:
                        logger.error("资源包中未找到弹幕源文件")
                        yield f"data: {json.dumps({'type': 'error', 'message': '资源包中未找到弹幕源文件'}, ensure_ascii=False)}\n\n"
                        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                        return

                    # 计算总数
                    total_count = len(resources)
                    logger.info(f"检测到 {total_count} 个弹幕源，开始比对哈希值...")
                    yield f"data: {json.dumps({'type': 'info', 'message': f'检测到 {total_count} 个弹幕源，正在比对哈希值...'}, ensure_ascii=False)}\n\n"

                    # ========== 第一阶段：比对所有哈希值，确定需要下载的文件 ==========
                    scrapers_dir = _get_scrapers_dir()
                    to_download = []  # 需要下载的源列表 [(scraper_name, scraper_info, file_path, filename, remote_hash), ...]
                    to_skip = []  # 不需要下载的源列表
                    unsupported = []  # 不支持当前平台的源
                    versions_data = {}  # 用于保存版本信息
                    hashes_data = {}  # 用于保存哈希值

                    # 读取本地 manifest 的哈希值（只读一次）
                    # why: 新架构下，只保留 scraper_manifest.json 作为唯一权威文件
                    local_hashes = {}
                    local_manifest_file = scrapers_dir / "scraper_manifest.json"
                    if local_manifest_file.exists():
                        try:
                            local_manifest = json.loads(await asyncio.to_thread(local_manifest_file.read_text))
                            # 从 manifest 的 sources 字段提取哈希值
                            for scraper_name, source_info in local_manifest.get("sources", {}).items():
                                if isinstance(source_info, dict) and "hash" in source_info:
                                    local_hashes[scraper_name] = source_info["hash"]
                            logger.info(f"已读取本地 manifest，包含 {len(local_hashes)} 个哈希值")
                        except Exception as e:
                            logger.warning(f"读取本地 manifest 失败: {e}")

                    if not local_hashes:
                        logger.info("本地无版本信息，所有源都需要下载")

                    # 遍历所有源，比对哈希值
                    for scraper_name, scraper_info in resources.items():
                        # 获取当前平台的文件路径
                        files = scraper_info.get('files', {})
                        file_path = files.get(platform_key)

                        if not file_path:
                            unsupported.append(scraper_name)
                            logger.warning(f"弹幕源 {scraper_name} 不支持当前平台 {platform_key}")
                            continue

                        # 从路径中提取文件名
                        filename = Path(file_path).name

                        # 获取远程文件的哈希值
                        remote_hashes = scraper_info.get('hashes', {})
                        remote_hash = remote_hashes.get(platform_key)

                        # 比对哈希值
                        local_hash = local_hashes.get(scraper_name)
                        version = scraper_info.get('version', 'unknown')

                        if remote_hash and local_hash and local_hash == remote_hash:
                            # 哈希值相同，不需要下载
                            to_skip.append(scraper_name)
                            versions_data[scraper_name] = version
                            hashes_data[scraper_name] = remote_hash
                            logger.info(f"✓ {scraper_name}: 哈希值相同，跳过")
                        else:
                            # 需要下载
                            to_download.append((scraper_name, scraper_info, file_path, filename, remote_hash))
                            if local_hash:
                                logger.info(f"↓ {scraper_name}: 哈希值不同，需要下载 (本地: {local_hash[:16]}..., 远程: {remote_hash[:16] if remote_hash else 'N/A'}...)")
                            elif remote_hash:
                                logger.info(f"↓ {scraper_name}: 本地无哈希记录，需要下载")
                            else:
                                logger.info(f"↓ {scraper_name}: 远程无哈希值，需要下载")

                    # 发送比对结果
                    skip_count = len(to_skip)
                    need_download_count = len(to_download)
                    logger.info(f"哈希比对完成: 需要下载 {need_download_count} 个，跳过 {skip_count} 个，不支持 {len(unsupported)} 个")
                    yield f"data: {json.dumps({'type': 'compare_result', 'to_download': need_download_count, 'to_skip': skip_count, 'unsupported': len(unsupported), 'total': total_count}, ensure_ascii=False)}\n\n"

                    # 如果没有需要下载的文件
                    if need_download_count == 0:
                        logger.info("所有弹幕源都是最新的，无需下载")
                        yield f"data: {json.dumps({'type': 'info', 'message': '所有弹幕源都是最新的，无需下载'}, ensure_ascii=False)}\n\n"
                        yield f"data: {json.dumps({'type': 'complete', 'downloaded': 0, 'skipped': skip_count, 'failed': len(unsupported), 'failed_list': unsupported}, ensure_ascii=False)}\n\n"
                        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                        return

                    # ========== 第二阶段：备份并下载需要更新的文件 ==========
                    # 判断是"新增源"还是"更新已有源"
                    # 检查本地是否已存在这些源文件
                    existing_scrapers = set()
                    for f in scrapers_dir.iterdir():
                        if f.is_file() and f.suffix in ['.so', '.pyd']:
                            # 从文件名提取源名称（如 bilibili.cpython-312-x86_64-linux-gnu.so -> bilibili）
                            existing_scrapers.add(f.name.split('.')[0])

                    # 分类：新增 vs 更新
                    new_scrapers = []  # 新增的源
                    update_scrapers = []  # 更新已有的源
                    for scraper_name, _, _, _, _ in to_download:
                        if scraper_name in existing_scrapers:
                            update_scrapers.append(scraper_name)
                        else:
                            new_scrapers.append(scraper_name)

                    has_updates = len(update_scrapers) > 0  # 是否有更新已有源
                    logger.info(f"下载分类: 新增 {len(new_scrapers)} 个, 更新 {len(update_scrapers)} 个")

                    # 先备份当前文件
                    yield f"data: {json.dumps({'type': 'info', 'message': '正在备份当前弹幕源...'}, ensure_ascii=False)}\n\n"
                    try:
                        await backup_scrapers(current_user)
                        logger.info("备份当前弹幕源成功")
                        yield f"data: {json.dumps({'type': 'info', 'message': f'备份完成，开始下载 {need_download_count} 个文件...'}, ensure_ascii=False)}\n\n"
                    except Exception as backup_error:
                        logger.error(f"备份失败: {backup_error}")
                        yield f"data: {json.dumps({'type': 'error', 'message': f'备份失败: {str(backup_error)}'}, ensure_ascii=False)}\n\n"
                        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                        return

                    # 发送下载总数
                    yield f"data: {json.dumps({'type': 'total', 'total': need_download_count}, ensure_ascii=False)}\n\n"

                    # 下载文件
                    download_count = 0  # 已完成下载的数量
                    failed_downloads = []
                    # 增加超时时间：连接30秒，读取60秒（适应网络不稳定的情况）
                    download_timeout = httpx.Timeout(30.0, read=60.0)

                    for index, (scraper_name, scraper_info, file_path, filename, remote_hash) in enumerate(to_download, 1):
                        logger.info(f"正在下载 [{index}/{need_download_count}]: {scraper_name}")
                        await asyncio.sleep(0)

                        # 发送心跳
                        heartbeat = await send_heartbeat_if_needed()
                        if heartbeat:
                            yield heartbeat

                        try:
                            target_path = scrapers_dir / filename

                            # 推送下载进度
                            progress = int((index / need_download_count) * 100)
                            last_heartbeat = asyncio.get_event_loop().time()
                            yield f"data: {json.dumps({'type': 'progress', 'scraper': scraper_name, 'filename': filename, 'current': index, 'total': need_download_count, 'download_index': index, 'progress': progress}, ensure_ascii=False)}\n\n"

                            # 下载文件 - 增强重试机制
                            file_url = f"{base_url}/{file_path}"
                            max_retries = 3  # 增加重试次数
                            logger.info(f"\t开始下载 {scraper_name} ({filename}) [{index}/{need_download_count}]")

                            for retry_count in range(max_retries + 1):
                                try:
                                    if retry_count > 0:
                                        # 指数退避：1秒, 2秒, 4秒, 8秒, 10秒(最大)
                                        wait_time = min(2 ** (retry_count - 1), 10)
                                        logger.warning(f"\t\t[重试 {retry_count}/{max_retries}] 下载 {scraper_name}，等待 {wait_time} 秒...")
                                        await asyncio.sleep(wait_time)
                                        # 重试时发送心跳
                                        heartbeat = await send_heartbeat_if_needed()
                                        if heartbeat:
                                            yield heartbeat

                                    # 开始下载前发送心跳
                                    heartbeat = await send_heartbeat_if_needed()
                                    if heartbeat:
                                        yield heartbeat

                                    # 每次重试创建新的连接（避免连接池问题）
                                    async with httpx.AsyncClient(timeout=download_timeout, headers=headers, follow_redirects=True, proxy=proxy_to_use) as client:
                                        response = await asyncio.wait_for(client.get(file_url), timeout=60.0)

                                    # 下载完成后发送心跳
                                    heartbeat = await send_heartbeat_if_needed()
                                    if heartbeat:
                                        yield heartbeat

                                    if response.status_code == 200:
                                        # 写入文件
                                        file_content = response.content

                                        # 让出控制权，防止阻塞
                                        await asyncio.sleep(0)

                                        # 验证文件哈希值（如果远程提供了哈希值）- 使用异步方式
                                        if remote_hash:
                                            # 将哈希计算放到线程池，防止阻塞事件循环（大文件可能耗时数秒）
                                            local_hash = await asyncio.to_thread(
                                                lambda data: hashlib.sha256(data).hexdigest(),
                                                file_content
                                            )
                                            # 哈希计算完成后发送心跳
                                            heartbeat = await send_heartbeat_if_needed()
                                            if heartbeat:
                                                yield heartbeat

                                            if local_hash != remote_hash:
                                                # 哈希值不匹配，文件可能损坏，删除并标记失败
                                                logger.warning(f"\t\t[重试 {retry_count + 1}/{max_retries + 1}] {scraper_name} 哈希验证失败: 期望 {remote_hash[:16]}..., 实际 {local_hash[:16]}...")
                                                try:
                                                    await asyncio.to_thread(target_path.unlink)
                                                except Exception:
                                                    pass
                                                if retry_count == max_retries:
                                                    failed_downloads.append(scraper_name)
                                                    logger.error(f"\t✗ 下载失败 {scraper_name}: 哈希验证失败 (已重试 {max_retries} 次)")
                                                    yield f"data: {json.dumps({'type': 'failed', 'scraper': scraper_name, 'message': f'哈希验证失败 (重试{max_retries}次后失败)'}, ensure_ascii=False)}\n\n"
                                                continue  # 重试下载
                                            hashes_data[scraper_name] = remote_hash
                                            logger.debug(f"\t\t哈希验证通过: {scraper_name}")

                                        # 写入文件（异步方式，防止阻塞）
                                        logger.debug(f"\t\t正在写入文件: {filename} ({len(file_content)} 字节)")
                                        await asyncio.to_thread(target_path.write_bytes, file_content)
                                        logger.debug(f"\t\t文件写入完成: {filename}")

                                        # 文件写入后发送心跳
                                        heartbeat = await send_heartbeat_if_needed()
                                        if heartbeat:
                                            yield heartbeat

                                        download_count += 1
                                        version = scraper_info.get('version', 'unknown')
                                        versions_data[scraper_name] = version

                                        logger.info(f"\t✓ 成功下载: {filename} (版本: {version}, 大小: {len(file_content)} 字节)")
                                        last_heartbeat = asyncio.get_event_loop().time()
                                        yield f"data: {json.dumps({'type': 'success', 'scraper': scraper_name, 'filename': filename}, ensure_ascii=False)}\n\n"

                                        # 下载成功后让出控制权
                                        await asyncio.sleep(0)
                                        break  # 下载成功，跳出重试循环
                                    else:
                                        # HTTP 非 200 状态码
                                        logger.warning(f"\t\t[重试 {retry_count + 1}/{max_retries + 1}] 下载 {scraper_name} 返回 HTTP {response.status_code}")
                                        if retry_count == max_retries:
                                            failed_downloads.append(scraper_name)
                                            logger.error(f"\t✗ 下载失败 {scraper_name}: HTTP {response.status_code} (已重试 {max_retries} 次)")
                                            yield f"data: {json.dumps({'type': 'failed', 'scraper': scraper_name, 'message': f'HTTP {response.status_code} (重试{max_retries}次后失败)'}, ensure_ascii=False)}\n\n"
                                        continue  # 继续重试

                                except (httpx.TimeoutException, asyncio.TimeoutError, httpx.ConnectError) as e:
                                    error_msg = "超时" if isinstance(e, (httpx.TimeoutException, asyncio.TimeoutError)) else "连接失败"
                                    logger.warning(f"\t\t[重试 {retry_count + 1}/{max_retries + 1}] 下载 {scraper_name} {error_msg}")
                                    if retry_count == max_retries:
                                        failed_downloads.append(scraper_name)
                                        logger.error(f"\t✗ 下载失败 {scraper_name}: {error_msg} (已重试 {max_retries} 次)")
                                        yield f"data: {json.dumps({'type': 'failed', 'scraper': scraper_name, 'message': f'{error_msg} (重试{max_retries}次后失败)'}, ensure_ascii=False)}\n\n"
                                    # 让出控制权，防止连续重试时阻塞
                                    await asyncio.sleep(0)
                                    continue  # 继续重试
                                except Exception as retry_error:
                                    # 捕获其他异常（如网络错误、解析错误等）
                                    logger.warning(f"\t\t[重试 {retry_count + 1}/{max_retries + 1}] 下载 {scraper_name} 异常: {retry_error}")
                                    if retry_count == max_retries:
                                        failed_downloads.append(scraper_name)
                                        logger.error(f"\t✗ 下载失败 {scraper_name}: {retry_error} (已重试 {max_retries} 次)", exc_info=True)
                                        yield f"data: {json.dumps({'type': 'failed', 'scraper': scraper_name, 'message': f'异常: {str(retry_error)} (重试{max_retries}次后失败)'}, ensure_ascii=False)}\n\n"
                                    await asyncio.sleep(0)
                                    continue  # 继续重试

                        except Exception as e:
                                # 外层异常处理：捕获整个文件处理流程中的错误
                                failed_downloads.append(scraper_name)
                                logger.error(f"\t处理 {scraper_name} 时发生严重错误: {e}", exc_info=True)
                                yield f"data: {json.dumps({'type': 'failed', 'scraper': scraper_name, 'message': f'严重错误: {str(e)}'}, ensure_ascii=False)}\n\n"
                                # 让出控制权
                                await asyncio.sleep(0)

                    logger.info(f"下载完成: 成功 {download_count}/{need_download_count} 个，跳过 {skip_count} 个，失败 {len(failed_downloads)} 个")

                    # 注意：版本信息的保存移到后台任务中，只有首次下载时才保存到 scrapers 目录
                    # 非首次下载时，版本信息只保存在备份目录中

                    # 检查下载结果
                    if download_count == 0 and skip_count == 0:
                        logger.error("没有成功下载任何弹幕源,取消重载")
                        yield f"data: {json.dumps({'type': 'error', 'message': '没有成功下载任何弹幕源,已取消重载。请检查网络连接或更换CDN节点'}, ensure_ascii=False)}\n\n"
                        try:
                            await restore_scrapers(current_user, manager)
                            logger.info("已还原备份")
                        except Exception as restore_error:
                            logger.error(f"还原备份失败: {restore_error}", exc_info=True)
                        # 发送流结束信号
                        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                        return

                    # 推送完成信息
                    logger.info(f"下载完成: 成功 {download_count} 个, 跳过 {skip_count} 个, 失败 {len(failed_downloads)} 个")
                    yield f"data: {json.dumps({'type': 'complete', 'downloaded': download_count, 'skipped': skip_count, 'failed': len(failed_downloads), 'failed_list': failed_downloads}, ensure_ascii=False)}\n\n"

                    # 检查是否有下载失败的文件 - 有失败则不触发任何操作
                    if failed_downloads:
                        logger.warning(f"有 {len(failed_downloads)} 个文件下载失败: {failed_downloads}")
                        yield f"data: {json.dumps({'type': 'warning', 'message': f'有 {len(failed_downloads)} 个文件下载失败，不更新版本信息，不执行重启'}, ensure_ascii=False)}\n\n"

                        # 清除版本缓存
                        resource_version_cache.value = None
                        resource_version_cache.updated_at = None

                        # 发送流结束信号
                        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                        return  # 有失败则不继续执行

                    # 判断是否是首次下载（本地没有任何弹幕源）
                    is_first_download = len(manager.scrapers) == 0


                    docker_available = is_docker_socket_available() and is_running_in_docker()

                    # ========== 先备份新下载的资源到持久化目录（在 SSE 流中同步执行）==========
                    if download_count > 0:
                        yield f"data: {json.dumps({'type': 'info', 'message': '正在备份新下载的资源...'}, ensure_ascii=False)}\n\n"
                        try:
                            logger.info("正在备份新下载的资源到持久化目录...")
                            # 非首次下载时，传入新版本信息以保存到备份目录
                            if not is_first_download:
                                await backup_scrapers(
                                    current_user,
                                    new_versions_data=versions_data,
                                    new_hashes_data=hashes_data,
                                    package_data=package_data
                                )
                            else:
                                await backup_scrapers(current_user)
                            logger.info("新资源备份完成")
                            yield f"data: {json.dumps({'type': 'info', 'message': '✓ 新资源备份完成'}, ensure_ascii=False)}\n\n"
                        except Exception as backup_error:
                            logger.warning(f"备份新资源失败: {backup_error}")
                            yield f"data: {json.dumps({'type': 'warning', 'message': f'备份失败: {str(backup_error)}'}, ensure_ascii=False)}\n\n"

                    # ========== 根据情况提示用户 ==========
                    if download_count > 0:
                        if is_first_download:
                            # 首次下载：保存版本信息并热加载
                            yield f"data: {json.dumps({'type': 'info', 'message': '首次下载弹幕源，正在热加载...'}, ensure_ascii=False)}\n\n"
                        elif docker_available:
                            # 非首次下载且有 Docker socket：提示将重启容器
                            yield f"data: {json.dumps({'type': 'info', 'message': '检测到弹幕源更新，将在 2 秒后重启容器以确保 .so 文件更新生效'}, ensure_ascii=False)}\n\n"
                            yield f"data: {json.dumps({'type': 'container_restart_required', 'message': '需要重启容器'}, ensure_ascii=False)}\n\n"
                        else:
                            # 非首次下载且没有 Docker socket：提示手动重启
                            yield f"data: {json.dumps({'type': 'info', 'message': '⚠️ 弹幕源更新需要重启容器，但未检测到 Docker 套接字，请手动重启容器'}, ensure_ascii=False)}\n\n"
                            yield f"data: {json.dumps({'type': 'restart_suggested', 'message': '建议手动重启容器'}, ensure_ascii=False)}\n\n"

                    # 发送流结束信号
                    yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"

                    # 刷新日志缓冲，确保日志输出
                    for handler in logging.getLogger().handlers:
                        handler.flush()
                    sys.stdout.flush()
                    sys.stderr.flush()

                    # 后台重载任务
                    async def reload_scrapers_background():
                        # 等待 SSE 流完全关闭和日志刷新
                        await asyncio.sleep(1.0)
                        try:
                            if is_first_download:
                                # 首次下载：生成 scraper_manifest.json 并执行热加载
                                # why: 新架构只保留 scraper_manifest.json 作为唯一权威文件
                                if versions_data:
                                    try:
                                        # 从 package_data 读取全局版本限制字段（两字段语义相同）
                                        pkg_min_ver = package_data.get('min_server_version') or package_data.get('min_fetchable_version')

                                        # 构建 manifest 数据
                                        manifest = {
                                            "version": package_data.get("version", "unknown"),
                                            "min_server_version": pkg_min_ver,
                                            "updated_at": datetime.now().isoformat(),
                                            "sources": {}
                                        }

                                        # 填充各源的版本和哈希信息
                                        for scraper_name, version in versions_data.items():
                                            manifest["sources"][scraper_name] = {
                                                "version": version,
                                                "hash": hashes_data.get(scraper_name)
                                            }

                                        # 保存 manifest 到运行目录
                                        ScraperVersionManager.save_manifest(manifest, scrapers_dir)
                                        logger.info(f"已生成 scraper_manifest.json: {len(versions_data)} 个源")

                                        # 删除运行目录中的 legacy 文件（如果存在）
                                        for legacy_file in ["package.json", "versions.json"]:
                                            legacy_path = scrapers_dir / legacy_file
                                            if legacy_path.exists():
                                                await asyncio.to_thread(legacy_path.unlink)
                                                logger.info(f"已删除运行目录的 legacy 文件: {legacy_file}")
                                    except Exception as e:
                                        logger.warning(f"生成 manifest 失败: {e}")

                                logger.info("首次下载，开始热加载弹幕源...")
                                await manager.load_and_sync_scrapers()
                                logger.info(f"用户 '{current_user.username}' 首次下载并成功加载了 {download_count} 个弹幕源")
                            elif docker_available:
                                # 非首次下载且有 Docker socket：重启容器
                                # 版本信息只在备份中，不保存到 scrapers 目录
                                logger.info("检测到弹幕源更新，准备重启容器...")

                                # 再次刷新日志，确保上面的日志输出
                                for handler in logging.getLogger().handlers:
                                    handler.flush()
                                sys.stdout.flush()

                                # 等待日志写入完成
                                await asyncio.sleep(1.0)

                                container_name = await config_service.get("containerName", "misaka_danmu_server")
                                result = await restart_container(container_name)
                                if result.get("success"):
                                    logger.info(f"已向容器 '{container_name}' 发送重启指令")
                                else:
                                    logger.warning(f"重启容器失败: {result.get('message')}")
                                    logger.warning("⚠️ 请手动重启容器以加载新的弹幕源")
                            else:
                                # 非首次下载且没有 Docker socket：仅提示，不执行热加载
                                # 版本信息只在备份中，不保存到 scrapers 目录
                                logger.info(f"用户 '{current_user.username}' 下载了 {download_count} 个弹幕源，需要手动重启容器")
                                logger.warning("⚠️ 未检测到 Docker 套接字，请手动重启容器以加载新的弹幕源")

                            resource_version_cache.value = None
                            resource_version_cache.updated_at = None
                        except Exception as e:
                            logger.error(f"后台加载弹幕源失败: {e}", exc_info=True)
                            try:
                                logger.info("尝试还原备份...")
                                await restore_scrapers(current_user, manager)
                                logger.info("已还原备份")
                            except Exception as restore_error:
                                logger.error(f"还原备份失败: {restore_error}", exc_info=True)

                    asyncio.create_task(reload_scrapers_background())

                except Exception as e:
                    logger.error(f"加载资源失败: {e}", exc_info=True)
                    yield f"data: {json.dumps({'type': 'error', 'message': f'加载失败: {str(e)}'}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
            finally:
                logger.info("下载任务结束，释放下载锁")

    return StreamingResponse(
        event_generator(),
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
        task = await start_download_task(
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
    """获取下载任务的当前状态和进度"""
    task_manager = get_download_task_manager()
    task = task_manager.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    return task.to_dict()


@router.get("/scrapers/download/current", summary="获取当前下载任务")
async def get_current_download(
    current_user: User = Depends(security.get_current_user),
):
    """获取当前正在运行的下载任务（如果有）"""
    task_manager = get_download_task_manager()
    task = task_manager.current_task

    if task and task.status == TaskStatus.RUNNING:
        return task.to_dict()

    return {"task_id": None, "status": "idle", "message": "没有正在运行的下载任务"}


@router.post("/scrapers/download/cancel/{task_id}", summary="取消下载任务")
async def cancel_download(
    task_id: str,
    current_user: User = Depends(security.get_current_user),
):
    """取消正在运行的下载任务"""
    task_manager = get_download_task_manager()

    if task_manager.cancel_task(task_id):
        logger.info(f"用户 '{current_user.username}' 取消了下载任务: {task_id}")
        return {"message": "任务已取消"}
    else:
        raise HTTPException(status_code=400, detail="无法取消任务（任务不存在或已完成）")


@router.get("/scrapers/download/progress/{task_id}", summary="SSE 进度流")
async def download_progress_stream(
    task_id: str,
    current_user: User = Depends(security.get_current_user),
):
    """通过 SSE 实时推送下载进度（可选，用于前端实时显示）"""
    task_manager = get_download_task_manager()
    task = task_manager.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    async def event_generator():
        last_message_count = 0
        while True:
            # 获取最新状态
            current_task = task_manager.get_task(task_id)
            if not current_task:
                yield f"data: {json.dumps({'type': 'error', 'message': '任务不存在'}, ensure_ascii=False)}\n\n"
                break

            # 发送进度更新
            progress_data = {
                "type": "progress",
                "status": current_task.status.value,
                "current": current_task.progress.current,
                "total": current_task.progress.total,
                "current_file": current_task.progress.current_file,
                "downloaded_count": len(current_task.progress.downloaded),
                "skipped_count": len(current_task.progress.skipped),
                "failed_count": len(current_task.progress.failed),
                "error_message": current_task.error_message,
                "success_message": current_task.success_message,  # 成功完成时的友好消息
                "need_restart": current_task.need_restart or current_task.restart_pending,  # 添加重启标记
            }

            # 发送新消息
            new_messages = current_task.progress.messages[last_message_count:]
            if new_messages:
                progress_data["messages"] = new_messages
                last_message_count = len(current_task.progress.messages)

            yield f"data: {json.dumps(progress_data, ensure_ascii=False)}\n\n"

            # 检测是否需要发送终止消息（restart_pending 用于通知 SSE 流退出）
            if current_task.restart_pending:
                if current_task.need_restart:
                    # 需要重启容器的情况
                    logger.info(f"[SSE] 任务 {task_id} 需要重启容器，发送 restart 和 done 消息")
                    yield f"data: {json.dumps({'type': 'restart', 'message': '弹幕源更新完成，容器即将重启...'}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'status': 'completed', 'need_restart': True}, ensure_ascii=False)}\n\n"
                else:
                    # 热加载完成，不需要重启容器
                    logger.info(f"[SSE] 任务 {task_id} 热加载完成，发送 done 消息 (need_restart=False)")
                    done_data = {'type': 'done', 'status': 'completed', 'need_restart': False}
                    if current_task.success_message:
                        done_data['success_message'] = current_task.success_message
                    yield f"data: {json.dumps(done_data, ensure_ascii=False)}\n\n"
                logger.info(f"[SSE] 任务 {task_id} done 消息已发送，退出 SSE 流")
                break

            # 任务完成则退出（备用逻辑，正常情况下应该通过 restart_pending 退出）
            # why：FAILED/CANCELLED 路径不设 restart_pending，必须靠此处退出；
            #      同时兼容 status 为枚举成员或字符串值两种情况（避免 in 比较失效）。
            _terminal_values = {TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}
            _status_val = current_task.status.value if hasattr(current_task.status, 'value') else str(current_task.status)
            if _status_val in _terminal_values:
                logger.info(f"[SSE] 任务 {task_id} 状态为 {current_task.status.value}，准备发送 done 消息")
                # 等待一小段时间，确保前端有时间处理最后的 progress 消息
                await asyncio.sleep(0.1)
                logger.info(f"[SSE] 任务 {task_id} 发送 done 消息")
                done_data = {'type': 'done', 'status': current_task.status.value, 'need_restart': current_task.need_restart}
                if current_task.success_message:
                    done_data['success_message'] = current_task.success_message
                yield f"data: {json.dumps(done_data, ensure_ascii=False)}\n\n"
                logger.info(f"[SSE] 任务 {task_id} done 消息已发送，退出 SSE 流")
                break

            await asyncio.sleep(0.5)  # 每 0.5 秒更新一次

    return StreamingResponse(
        event_generator(),
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
    """
    查询持久化到数据库的任务状态

    用于容器重启后前端查询之前任务的完成状态
    缓存有效期为1小时
    """
    # 与执行器使用同一 CacheService 区域，保留数据库回退能力。
    cached_data = await get_cache_service().get(
        key=task_id, region=SCRAPER_DOWNLOAD_TASK_CACHE_PREFIX
    )

    if cached_data:
        return {
            "found": True,
            "data": cached_data
        }
    else:
        return {
            "found": False,
            "data": None
        }


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
    """删除持久化备份目录中的所有备份文件"""
    try:
        if not BACKUP_DIR.exists():
            raise HTTPException(status_code=404, detail="未找到备份目录")

        # 统计并删除备份文件
        deleted_count = 0
        for file in BACKUP_DIR.glob("*"):
            if file.is_file():
                file.unlink()
                deleted_count += 1

        # 删除备份目录（如果为空）
        try:
            BACKUP_DIR.rmdir()
        except OSError:
            pass  # 目录不为空或其他原因无法删除，忽略

        logger.info(f"用户 '{current_user.username}' 删除了 {deleted_count} 个备份文件")
        return {"message": f"成功删除 {deleted_count} 个备份文件"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除备份失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"删除备份失败: {str(e)}")


@router.delete("/scrapers/current", summary="删除当前弹幕源")
async def delete_current_scrapers(
    current_user: User = Depends(security.get_current_user),
    manager = Depends(get_scraper_manager)
):
    """删除当前 scrapers 目录下的所有编译文件（.so/.pyd）"""
    try:
        scrapers_dir = _get_scrapers_dir()

        if not scrapers_dir.exists():
            raise HTTPException(status_code=404, detail="未找到弹幕源目录")

        # 删除 .so 和 .pyd 文件
        deleted_count = 0
        for file in scrapers_dir.glob("*"):
            if file.suffix in ['.so', '.pyd']:
                file.unlink()
                deleted_count += 1

        # 删除所有版本相关文件（legacy + 新架构）
        version_files = ["package.json", "versions.json", "scraper_manifest.json"]
        for version_file in version_files:
            version_path = scrapers_dir / version_file
            if version_path.exists():
                version_path.unlink()
                logger.info(f"已删除 {version_file}")

        # 清除版本缓存
        resource_version_cache.value = None
        resource_version_cache.updated_at = None

        logger.info(f"用户 '{current_user.username}' 删除了 {deleted_count} 个弹幕源文件")

        # 创建后台任务重新加载 scrapers（此时应该是空的）
        async def reload_scrapers_background():
            await asyncio.sleep(1)
            try:
                await manager.load_and_sync_scrapers()
                logger.info(f"用户 '{current_user.username}' 删除弹幕源后已重载")
            except Exception as e:
                logger.error(f"后台重载弹幕源失败: {e}", exc_info=True)

        asyncio.create_task(reload_scrapers_background())

        return {"message": f"成功删除 {deleted_count} 个弹幕源文件，正在后台重载..."}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除当前弹幕源失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")


@router.delete("/scrapers/all", summary="删除当前源和备份源")
async def delete_all_scrapers(
    current_user: User = Depends(security.get_current_user),
    manager = Depends(get_scraper_manager)
):
    """删除当前弹幕源和备份目录中的所有文件"""
    try:
        scrapers_dir = _get_scrapers_dir()
        deleted_current = 0
        deleted_backup = 0

        # 删除当前源
        if scrapers_dir.exists():
            for file in scrapers_dir.glob("*"):
                if file.suffix in ['.so', '.pyd']:
                    file.unlink()
                    deleted_current += 1

            # 删除所有版本相关文件（legacy + 新架构）
            version_files = ["package.json", "versions.json", "scraper_manifest.json"]
            for version_file in version_files:
                version_path = scrapers_dir / version_file
                if version_path.exists():
                    version_path.unlink()
                    logger.info(f"已删除 {version_file}")

        # 删除备份
        if BACKUP_DIR.exists():
            for file in BACKUP_DIR.glob("*"):
                if file.is_file():
                    file.unlink()
                    deleted_backup += 1
            # 尝试删除空目录
            try:
                BACKUP_DIR.rmdir()
            except OSError:
                pass

        # 清除版本缓存
        resource_version_cache.value = None
        resource_version_cache.updated_at = None

        logger.info(f"用户 '{current_user.username}' 删除了 {deleted_current} 个当前源文件和 {deleted_backup} 个备份文件")

        # 创建后台任务重新加载 scrapers（此时应该是空的）
        async def reload_scrapers_background():
            await asyncio.sleep(1)
            try:
                await manager.load_and_sync_scrapers()
                logger.info(f"用户 '{current_user.username}' 删除所有弹幕源后已重载")
            except Exception as e:
                logger.error(f"后台重载弹幕源失败: {e}", exc_info=True)

        asyncio.create_task(reload_scrapers_background())

        return {"message": f"成功删除 {deleted_current} 个当前源文件和 {deleted_backup} 个备份文件，正在后台重载..."}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除所有弹幕源失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")
