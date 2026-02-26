"""
弹幕源资源管理 API
支持从 GitHub 仓库加载编译好的 scraper 资源文件
"""
import logging
import shutil
import platform
import sys
import re
import json
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from src.db import models, get_db_session, ConfigManager
from src import security
from src.security import get_current_user
from src.services import get_download_task_manager
from src.services.download_task_manager import TaskStatus
from src.api.dependencies import get_scraper_manager, get_config_manager

logger = logging.getLogger(__name__)
router = APIRouter()

# 全局锁：防止并发下载（保留用于旧 SSE 接口的兼容）
_download_lock = asyncio.Lock()

# 版本信息缓存
_version_cache: Optional[Dict[str, Any]] = None
_version_cache_time: Optional[datetime] = None
_VERSION_CACHE_DURATION = timedelta(minutes=3)  # 缓存3分钟


def _is_docker_environment():
    """检测是否在Docker容器中运行"""
    import os
    # 方法1: 检查 /.dockerenv 文件（Docker标准做法）
    if Path("/.dockerenv").exists():
        return True
    # 方法2: 检查环境变量
    if os.getenv("DOCKER_CONTAINER") == "true" or os.getenv("IN_DOCKER") == "true":
        return True
    # 方法3: 检查当前工作目录是否为 /app
    if Path.cwd() == Path("/app"):
        return True
    return False


def _get_scrapers_dir() -> Path:
    """获取 scrapers 目录路径"""
    if _is_docker_environment():
        return Path("/app/src/scrapers")
    else:
        return Path("src/scrapers")


def _get_backup_dir() -> Path:
    """获取备份目录路径"""
    if _is_docker_environment():
        return Path("/app/config/scrapers_backup")
    else:
        return Path("config/scrapers_backup")


# 备份目录配置
BACKUP_DIR = _get_backup_dir()
BACKUP_METADATA_FILE = BACKUP_DIR / "backup_metadata.json"

# 弹幕源版本信息文件
SCRAPERS_VERSIONS_FILE = _get_scrapers_dir() / "versions.json"
SCRAPERS_PACKAGE_FILE = _get_scrapers_dir() / "package.json"


def get_platform_info() -> Dict[str, str]:
    """获取当前平台信息"""
    system = platform.system().lower()
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    machine = platform.machine().lower()

    # 映射平台名称
    platform_map = {
        'linux': 'linux',
        'darwin': 'macos',
        'windows': 'windows'
    }

    # 映射架构
    arch_map = {
        'x86_64': 'x86_64',
        'amd64': 'x86_64',
        'aarch64': 'aarch64',
        'arm64': 'aarch64'
    }

    return {
        'platform': platform_map.get(system, system),
        'python_version': python_version,
        'arch': arch_map.get(machine, machine)
    }


def get_platform_key() -> str:
    """获取当前平台的资源key (linux-x86/linux-arm/windows-amd64)"""
    system = platform.system().lower()
    machine = platform.machine().lower()

    # 映射架构
    arch_map = {
        'x86_64': 'x86',
        'amd64': 'amd64',
        'aarch64': 'arm',
        'arm64': 'arm'
    }

    arch = arch_map.get(machine, machine)

    if system == 'linux':
        return f'linux-{arch}'
    elif system == 'windows':
        return f'windows-{arch}'
    elif system == 'darwin':
        return f'macos-{arch}'
    else:
        return f'{system}-{arch}'


def _build_base_url(repo_info: Optional[Dict[str, str]], repo_url: str, gitee_info: Optional[Dict[str, str]] = None, branch: str = "main") -> str:
    """构造资源下载的base URL

    Args:
        repo_info: GitHub仓库解析信息 (包含owner, repo, proxy, proxy_type)
        repo_url: 原始仓库URL
        gitee_info: Gitee仓库解析信息 (包含owner, repo, platform)
        branch: Git分支名称，默认为 main

    Returns:
        构造好的base URL
    """
    # 优先处理 Gitee
    if gitee_info:
        owner = gitee_info['owner']
        repo = gitee_info['repo']
        # Gitee raw 文件 URL 格式: https://gitee.com/owner/repo/raw/branch/path
        return f"https://gitee.com/{owner}/{repo}/raw/{branch}"

    if repo_info:
        owner = repo_info['owner']
        repo = repo_info['repo']
        proxy = repo_info.get('proxy')
        proxy_type = repo_info.get('proxy_type')

        if proxy:
            if proxy_type == 'jsdelivr':
                return f"{proxy}/gh/{owner}/{repo}@{branch}"
            else:  # generic_proxy
                return f"{proxy}/https://raw.githubusercontent.com/{owner}/{repo}/{branch}"
        else:
            return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}"
    else:
        # 非 GitHub/Gitee 地址：视为静态资源根路径
        return repo_url.rstrip("/")


def parse_gitee_url(url: str) -> Optional[Dict[str, str]]:
    """解析 Gitee 仓库 URL

    支持的格式:
    - https://gitee.com/owner/repo
    - https://gitee.com/owner/repo.git

    返回:
        {
            'owner': 'owner',
            'repo': 'repo',
            'platform': 'gitee'
        }
        如果不是 Gitee URL，返回 None
    """
    # Gitee URL 格式
    gitee_match = re.match(r'^https?://gitee\.com/([^/]+)/([^/]+?)(?:\.git)?$', url)
    if gitee_match:
        return {
            'owner': gitee_match.group(1),
            'repo': gitee_match.group(2).replace('.git', ''),
            'platform': 'gitee'
        }

    # 也支持路径中带有额外部分的情况
    gitee_match2 = re.match(r'^https?://gitee\.com/([^/]+)/([^/]+)', url)
    if gitee_match2:
        return {
            'owner': gitee_match2.group(1),
            'repo': gitee_match2.group(2).replace('.git', '').split('/')[0],
            'platform': 'gitee'
        }

    return None


def parse_github_url(url: str) -> Dict[str, str]:
    """解析 GitHub 仓库 URL,支持代理链接

    支持的格式:
    - https://github.com/owner/repo
    - https://github.com/owner/repo.git
    - https://任意域名/https://github.com/owner/repo (通用代理格式)
    - https://任意域名/https://raw.githubusercontent.com/owner/repo/main (通用代理格式)
    - https://cdn.jsdelivr.net/gh/owner/repo@main (jsDelivr CDN)
    - https://cdn.jsdelivr.net/gh/owner/repo (jsDelivr CDN)

    返回:
        {
            'owner': 'owner',
            'repo': 'repo',
            'proxy': 'https://代理域名' (如果有代理),
            'proxy_type': 'generic_proxy' | 'jsdelivr' (代理类型)
        }
    """
    # 检查是否是 jsDelivr CDN 格式
    jsdelivr_match = re.match(r'^https?://cdn\.jsdelivr\.net/gh/([^/]+)/([^/@]+)(?:@[^/]+)?', url)
    if jsdelivr_match:
        return {
            'owner': jsdelivr_match.group(1),
            'repo': jsdelivr_match.group(2),
            'proxy': 'https://cdn.jsdelivr.net',
            'proxy_type': 'jsdelivr'
        }

    # 检查是否是通用代理格式: https://任意域名/https://github.com/... 或 https://任意域名/github.com/...
    generic_proxy_match = re.match(r'^(https?://[^/]+)/https?://(github\.com|raw\.githubusercontent\.com)/([^/]+)/([^/]+)', url)
    if generic_proxy_match:
        return {
            'owner': generic_proxy_match.group(3),
            'repo': generic_proxy_match.group(4).replace('.git', '').split('/')[0],  # 去掉可能的路径部分
            'proxy': generic_proxy_match.group(1),
            'proxy_type': 'generic_proxy'
        }

    # 检查是否是简化的代理格式: https://任意域名/github.com/... (不带 https://)
    simple_proxy_match = re.match(r'^(https?://[^/]+)/(github\.com|raw\.githubusercontent\.com)/([^/]+)/([^/]+)', url)
    if simple_proxy_match:
        return {
            'owner': simple_proxy_match.group(3),
            'repo': simple_proxy_match.group(4).replace('.git', '').split('/')[0],  # 去掉可能的路径部分
            'proxy': simple_proxy_match.group(1),
            'proxy_type': 'generic_proxy'
        }

    # 普通 GitHub URL
    patterns = [
        r'github\.com/([^/]+)/([^/]+?)(?:\.git)?$',
        r'github\.com/([^/]+)/([^/]+)',
        r'raw\.githubusercontent\.com/([^/]+)/([^/]+)',
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return {
                'owner': match.group(1),
                'repo': match.group(2).replace('.git', '').split('/')[0]  # 去掉可能的路径部分
            }

    raise ValueError("无效的 GitHub 仓库链接")


@router.get("/scrapers/resource-repo", summary="获取资源仓库配置")
async def get_resource_repo(
    current_user: models.User = Depends(get_current_user),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """获取当前配置的资源仓库链接"""
    repo_url = await config_manager.get("scraper_resource_repo", "")
    return {"repoUrl": repo_url}


async def _fetch_package_info_with_retry(package_url: str, headers: Dict[str, str], max_retries: int = 3, proxy: Optional[str] = None) -> Optional[Dict[str, Optional[str]]]:
    """
    带重试机制的版本信息获取函数

    Args:
        package_url: package.json 的 URL
        headers: HTTP 请求头
        max_retries: 最大重试次数（默认3次）
        proxy: 代理URL（可选）

    Returns:
        包含 version 和 changelog 的字典，失败返回 None
    """
    timeout_config = httpx.Timeout(30.0, read=30.0)  # 连接30秒，读取30秒

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=timeout_config, headers=headers, follow_redirects=True, proxy=proxy) as client:
                response = await client.get(package_url)
                if response.status_code == 200:
                    package_data = response.json()
                    version = package_data.get("version", "unknown")
                    changelog = package_data.get("changelog", None)
                    logger.info(f"成功获取版本信息: {version} (尝试 {attempt + 1}/{max_retries})")
                    return {"version": version, "changelog": changelog}
                else:
                    logger.warning(f"获取版本失败 HTTP {response.status_code} (尝试 {attempt + 1}/{max_retries})")
        except httpx.TimeoutException:
            logger.warning(f"连接超时 (尝试 {attempt + 1}/{max_retries})")
        except httpx.ConnectError as e:
            logger.warning(f"连接失败: {e} (尝试 {attempt + 1}/{max_retries})")
        except Exception as e:
            logger.warning(f"获取版本异常: {e} (尝试 {attempt + 1}/{max_retries})")

        # 如果不是最后一次尝试，等待一小段时间再重试
        if attempt < max_retries - 1:
            await asyncio.sleep(0.5)

    logger.error(f"获取版本失败，已重试 {max_retries} 次: {package_url}")
    return None


@router.get("/scrapers/versions", summary="获取资源包版本信息")
async def get_versions(
    current_user: models.User = Depends(get_current_user),
    config_manager: ConfigManager = Depends(get_config_manager),
    force_refresh: bool = False  # 新增参数：强制刷新缓存
):
    """获取本地和远程资源包版本号（带缓存机制）"""
    global _version_cache, _version_cache_time

    try:
        # 检查缓存是否有效
        if not force_refresh and _version_cache and _version_cache_time:
            cache_age = datetime.now() - _version_cache_time
            if cache_age < _VERSION_CACHE_DURATION:
                logger.debug(f"使用缓存的版本信息 (缓存时间: {cache_age.total_seconds():.1f}秒)")
                return _version_cache

        # 获取本地版本和changelog
        local_version = "unknown"
        local_changelog = None
        local_package_file = _get_scrapers_dir() / "package.json"
        if local_package_file.exists():
            try:
                local_package = json.loads(local_package_file.read_text())
                local_version = local_package.get("version", "unknown")
                local_changelog = local_package.get("changelog", None)
            except Exception as e:
                logger.warning(f"读取本地 package.json 失败: {e}")

        # 获取代理配置
        proxy_url = await config_manager.get("proxyUrl", "")
        proxy_enabled_str = await config_manager.get("proxyEnabled", "false")
        proxy_enabled = proxy_enabled_str.lower() == 'true'
        proxy_to_use = proxy_url if proxy_enabled and proxy_url else None

        # 获取远程版本（当前配置的资源仓库）
        remote_version = None
        remote_changelog = None
        repo_url = await config_manager.get("scraper_resource_repo", "")

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
                github_token = await config_manager.get("github_token", "")
                if github_token:
                    headers["Authorization"] = f"Bearer {github_token}"

            base_url = _build_base_url(repo_info, repo_url, gitee_info)
            package_url = f"{base_url}/package.json"

            # 区分日志：用户配置的仓库
            platform_name = "Gitee" if gitee_info else "GitHub"
            logger.info(f"[版本检查] 正在获取用户配置仓库版本 ({platform_name}): {repo_url}")
            remote_info = await _fetch_package_info_with_retry(package_url, headers, proxy=proxy_to_use)
            if remote_info:
                remote_version = remote_info["version"]
                remote_changelog = remote_info.get("changelog")
                logger.info(f"[版本检查] 用户配置仓库版本: {remote_version}")
            else:
                logger.warning(f"[版本检查] 用户配置仓库版本获取失败")

        # 固定源仓库（官方仓库）版本
        official_version = None
        official_changelog = None
        try:
            official_repo_info = parse_github_url("https://github.com/l429609201/Misaka-Scraper-Resources")

            github_token = await config_manager.get("github_token", "")
            headers_official = {}
            if github_token:
                headers_official["Authorization"] = f"Bearer {github_token}"

            official_base_url = _build_base_url(official_repo_info, "https://github.com/l429609201/Misaka-Scraper-Resources")
            official_package_url = f"{official_base_url}/package.json"

            # 区分日志：官方仓库
            logger.info(f"[版本检查] 正在获取官方仓库版本 (GitHub): https://github.com/l429609201/Misaka-Scraper-Resources")
            official_info = await _fetch_package_info_with_retry(official_package_url, headers_official, proxy=proxy_to_use)
            if official_info:
                official_version = official_info["version"]
                official_changelog = official_info.get("changelog")
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
            "localChangelog": local_changelog,
            "remoteChangelog": remote_changelog,
            "officialChangelog": official_changelog
        }

        # 更新缓存
        _version_cache = result
        _version_cache_time = datetime.now()

        return result

    except Exception as e:
        logger.error(f"获取版本信息失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取版本信息失败: {str(e)}")


@router.put("/scrapers/resource-repo", status_code=status.HTTP_204_NO_CONTENT, summary="保存资源仓库配置")
async def save_resource_repo(
    payload: Dict[str, str],
    current_user: models.User = Depends(get_current_user),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """保存资源仓库链接"""
    repo_url = payload.get("repoUrl", "").strip()
    if repo_url:
        # 基础校验：要求是 http/https 链接，其他格式直接拒绝
        if not (repo_url.startswith("http://") or repo_url.startswith("https://")):
            raise HTTPException(status_code=400, detail="资源仓库链接必须以 http:// 或 https:// 开头")

    await config_manager.setValue("scraper_resource_repo", repo_url)
    logger.info(f"用户 '{current_user.username}' 更新了资源仓库配置: {repo_url}")


@router.post("/scrapers/backup", summary="备份当前弹幕源")
async def backup_scrapers(
    current_user: models.User = Depends(get_current_user),
):
    """备份当前 scrapers 目录下的编译文件到持久化目录

    直接从 scrapers 目录复制所有 .so/.pyd 文件和 versions.json 到备份目录
    """
    try:
        scrapers_dir = _get_scrapers_dir()

        # 创建备份目录
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        # 读取版本信息（用于元数据）
        versions = {}
        if SCRAPERS_VERSIONS_FILE.exists():
            try:
                versions = json.loads(SCRAPERS_VERSIONS_FILE.read_text())
            except Exception as e:
                logger.warning(f"读取版本信息失败: {e}")

        # 清空旧备份文件（保留metadata.json）
        for file in BACKUP_DIR.glob("*"):
            if file.is_file() and file.name != "backup_metadata.json":
                file.unlink()

        # 备份 .so 和 .pyd 文件
        backup_count = 0
        backed_files = []
        for file in scrapers_dir.glob("*"):
            if file.suffix in ['.so', '.pyd']:
                shutil.copy2(file, BACKUP_DIR / file.name)

                # 从文件名提取弹幕源名称
                # 文件名格式: bilibili.cpython-312-aarch64-linux-gnu.so
                # 需要提取第一个 '.' 之前的部分作为弹幕源名称
                scraper_name = file.name.split('.')[0]

                file_info = {
                    "name": file.name,
                    "scraper": scraper_name,
                    "size": file.stat().st_size,
                    "modified": datetime.fromtimestamp(file.stat().st_mtime).isoformat()
                }

                # 添加版本号（如果有）- 从 versions.json 的 scrapers 字段中查找
                if scraper_name in versions:
                    file_info["version"] = versions[scraper_name]
                elif isinstance(versions, dict) and 'scrapers' in versions:
                    # 兼容新格式的 versions.json
                    if scraper_name in versions.get('scrapers', {}):
                        file_info["version"] = versions['scrapers'][scraper_name]

                backed_files.append(file_info)
                backup_count += 1

        # 备份 package.json
        if SCRAPERS_PACKAGE_FILE.exists():
            shutil.copy2(SCRAPERS_PACKAGE_FILE, BACKUP_DIR / "package.json")
            logger.info("已备份 package.json")

        # 备份 versions.json（直接复制，因为版本信息已经保存到 scrapers 目录了）
        if SCRAPERS_VERSIONS_FILE.exists():
            shutil.copy2(SCRAPERS_VERSIONS_FILE, BACKUP_DIR / "versions.json")
            logger.info("已备份 versions.json")

        # 读取 package.json 的版本号（用于元数据）
        package_version = None
        if SCRAPERS_PACKAGE_FILE.exists():
            try:
                local_package_data = json.loads(SCRAPERS_PACKAGE_FILE.read_text())
                package_version = local_package_data.get("version")
            except Exception as e:
                logger.warning(f"读取 package.json 失败: {e}")

        # 保存备份元数据
        metadata = {
            "backup_time": datetime.now().isoformat(),
            "backup_user": current_user.username,
            "file_count": backup_count,
            "files": backed_files,
            "platform": get_platform_key(),
            "package_version": package_version  # 添加资源包版本号
        }

        BACKUP_METADATA_FILE.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))

        logger.info(f"用户 '{current_user.username}' 备份了 {backup_count} 个弹幕源文件到 {BACKUP_DIR}")
        return {"message": f"成功备份 {backup_count} 个文件", "count": backup_count}

    except Exception as e:
        logger.error(f"备份弹幕源失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"备份失败: {str(e)}")


@router.get("/scrapers/backup-info", summary="获取备份信息")
async def get_backup_info(
    current_user: models.User = Depends(get_current_user)
):
    """获取当前备份的详细信息"""
    try:
        if not BACKUP_DIR.exists() or not BACKUP_METADATA_FILE.exists():
            return {
                "hasBackup": False,
                "message": "暂无备份"
            }

        metadata = json.loads(BACKUP_METADATA_FILE.read_text())

        return {
            "hasBackup": True,
            "backupTime": metadata.get("backup_time"),
            "backupUser": metadata.get("backup_user"),
            "fileCount": metadata.get("file_count"),
            "platform": metadata.get("platform"),
            "files": metadata.get("files", [])
        }

    except Exception as e:
        logger.error(f"获取备份信息失败: {e}", exc_info=True)
        return {
            "hasBackup": False,
            "message": f"读取备份信息失败: {str(e)}"
        }


@router.post("/scrapers/restore", summary="从备份还原弹幕源")
async def restore_scrapers(
    current_user: models.User = Depends(get_current_user),
    manager = Depends(get_scraper_manager)
):
    """从持久化备份目录还原弹幕源文件"""
    try:
        scrapers_dir = _get_scrapers_dir()

        if not BACKUP_DIR.exists():
            raise HTTPException(status_code=404, detail="未找到备份目录")

        # 读取备份元数据
        backup_info = None
        if BACKUP_METADATA_FILE.exists():
            try:
                backup_info = json.loads(BACKUP_METADATA_FILE.read_text())
                logger.info(f"备份信息: {backup_info.get('backup_time')} by {backup_info.get('backup_user')}")
            except Exception as e:
                logger.warning(f"读取备份元数据失败: {e}")

        # 还原文件
        restore_count = 0
        for file in BACKUP_DIR.glob("*"):
            if file.is_file() and file.suffix in ['.so', '.pyd']:
                shutil.copy2(file, scrapers_dir / file.name)
                restore_count += 1

        if restore_count == 0:
            raise HTTPException(status_code=404, detail="备份目录为空")

        # 还原 package.json
        backup_package_file = BACKUP_DIR / "package.json"
        if backup_package_file.exists():
            try:
                shutil.copy2(backup_package_file, SCRAPERS_PACKAGE_FILE)
                logger.info("已还原 package.json")
            except Exception as e:
                logger.warning(f"还原 package.json 失败: {e}")

        # 还原 versions.json
        backup_versions_file = BACKUP_DIR / "versions.json"
        if backup_versions_file.exists():
            try:
                shutil.copy2(backup_versions_file, SCRAPERS_VERSIONS_FILE)
                logger.info("已还原 versions.json")
            except Exception as e:
                logger.warning(f"还原 versions.json 失败: {e}")
        else:
            # 如果备份中没有 versions.json,尝试从备份元数据恢复
            if backup_info and "files" in backup_info:
                versions = {}
                for file_info in backup_info["files"]:
                    if "version" in file_info and "scraper" in file_info:
                        versions[file_info["scraper"]] = file_info["version"]

                # 写入 versions.json
                if versions:
                    try:
                        SCRAPERS_VERSIONS_FILE.write_text(json.dumps(versions, indent=2, ensure_ascii=False))
                        logger.info(f"从元数据恢复了 {len(versions)} 个弹幕源的版本信息")
                    except Exception as e:
                        logger.warning(f"写入版本信息失败: {e}")

        # 从备份元数据恢复 package.json
        if backup_info and "package_version" in backup_info:
            try:
                package_data = {
                    "version": backup_info["package_version"],
                    "restored_from_backup": True,
                    "restore_time": datetime.now().isoformat()
                }
                SCRAPERS_PACKAGE_FILE.write_text(json.dumps(package_data, indent=2, ensure_ascii=False))
                logger.info(f"恢复了资源包版本信息: {backup_info['package_version']}")
            except Exception as e:
                logger.warning(f"写入 package.json 失败: {e}")

        logger.info(f"用户 '{current_user.username}' 从备份还原了 {restore_count} 个弹幕源文件")

        result = {
            "message": f"成功还原 {restore_count} 个文件，正在后台重载...",
            "count": restore_count
        }

        if backup_info:
            result["backupInfo"] = {
                "backupTime": backup_info.get("backup_time"),
                "backupUser": backup_info.get("backup_user"),
                "fileCount": backup_info.get("file_count")
            }

        # 创建后台任务重新加载 scrapers
        async def reload_scrapers_background():
            await asyncio.sleep(1)  # 延迟1秒,确保响应已发送
            try:
                await manager.load_and_sync_scrapers()
                logger.info(f"用户 '{current_user.username}' 成功从备份重载了 {restore_count} 个弹幕源")
            except Exception as e:
                logger.error(f"后台重载弹幕源失败: {e}", exc_info=True)

        # 启动后台任务
        asyncio.create_task(reload_scrapers_background())

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"还原弹幕源失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"还原失败: {str(e)}")


@router.post("/scrapers/reload", summary="重载弹幕源")
async def reload_scrapers(
    current_user: models.User = Depends(get_current_user),
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
    current_user: models.User = Depends(get_current_user),
    config_manager: ConfigManager = Depends(get_config_manager),
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
                        repo_url = await config_manager.get("scraper_resource_repo", "")

                    if not repo_url:
                        yield f"data: {json.dumps({'type': 'error', 'message': '未配置资源仓库链接'}, ensure_ascii=False)}\n\n"
                        return

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
                        github_token = await config_manager.get("github_token", "")
                        if github_token:
                            headers["Authorization"] = f"Bearer {github_token}"

                    base_url = _build_base_url(repo_info, repo_url, gitee_info)

                    # 获取代理配置
                    proxy_url = await config_manager.get("proxyUrl", "")
                    proxy_enabled_str = await config_manager.get("proxyEnabled", "false")
                    proxy_enabled = proxy_enabled_str.lower() == 'true'
                    proxy_to_use = proxy_url if proxy_enabled and proxy_url else None

                    if proxy_to_use:
                        logger.info(f"GitHub资源下载将使用代理: {proxy_to_use}")
                        yield f"data: {json.dumps({'type': 'info', 'message': f'使用代理: {proxy_to_use}'}, ensure_ascii=False)}\n\n"

                    # 检查是否启用全量替换模式
                    full_replace_enabled = await config_manager.get("scraperFullReplaceEnabled", "false")
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
                            proxy=proxy_to_use
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
                            proxy=proxy_to_use
                        )

                        if not asset_info:
                            logger.warning("GitHub: 未找到匹配的 Release 压缩包，回退到逐文件下载模式")
                            yield f"data: {json.dumps({'type': 'info', 'message': 'GitHub 未找到 Release 压缩包，回退到逐文件下载模式'}, ensure_ascii=False)}\n\n"
                            use_full_replace = False

                    if use_full_replace and (repo_info or gitee_info) and asset_info:
                        asset_filename = asset_info['filename']
                        asset_version = asset_info['version']
                        yield f"data: {json.dumps({'type': 'info', 'message': f'找到压缩包: {asset_filename} (版本: {asset_version})'}, ensure_ascii=False)}\n\n"

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
                            # 更新 versions.json
                            platform_info = get_platform_info()
                            release_version = asset_info['version'].lstrip('v')

                            # 从解压后的 package.json 读取各个源的版本信息
                            scrapers_versions = {}
                            scrapers_hashes = {}
                            local_package_file = scrapers_dir / "package.json"
                            try:
                                if local_package_file.exists():
                                    package_content = json.loads(await asyncio.to_thread(local_package_file.read_text))
                                    # 从 resources 字段提取各个源的版本号和哈希值
                                    resources = package_content.get('resources', {})
                                    for scraper_name, scraper_info in resources.items():
                                        if isinstance(scraper_info, dict):
                                            version = scraper_info.get('version')
                                            if version:
                                                scrapers_versions[scraper_name] = version
                                            # 提取哈希值
                                            hashes = scraper_info.get('hashes', {})
                                            platform_key = f"{platform_info['platform']}_{platform_info['arch']}"
                                            if platform_key in hashes:
                                                scrapers_hashes[scraper_name] = hashes[platform_key]
                                    logger.info(f"从 package.json 读取到 {len(scrapers_versions)} 个源的版本信息")
                            except Exception as e:
                                logger.warning(f"读取 package.json 中的源版本信息失败: {e}")

                            versions_data = {
                                "platform": platform_info['platform'],
                                "type": platform_info['arch'],
                                "version": release_version,
                                "scrapers": scrapers_versions,
                                "hashes": scrapers_hashes,
                                "full_replace": True,
                                "update_time": datetime.now().isoformat()
                            }
                            versions_json_str = json.dumps(versions_data, indent=2, ensure_ascii=False)
                            await asyncio.to_thread(SCRAPERS_VERSIONS_FILE.write_text, versions_json_str)
                            logger.info(f"已更新 versions.json: {len(scrapers_versions)} 个源版本, {len(scrapers_hashes)} 个哈希值")

                            # 同时更新 package.json 的版本号（前端从这里读取整体版本）
                            try:
                                if local_package_file.exists():
                                    package_content = json.loads(await asyncio.to_thread(local_package_file.read_text))
                                    package_content['version'] = release_version
                                else:
                                    package_content = {"version": release_version}
                                package_json_str = json.dumps(package_content, indent=2, ensure_ascii=False)
                                await asyncio.to_thread(local_package_file.write_text, package_json_str)
                                logger.info(f"已更新 package.json 版本号为: {release_version}")
                            except Exception as pkg_err:
                                logger.warning(f"更新 package.json 失败: {pkg_err}")

                            yield f"data: {json.dumps({'type': 'complete', 'downloaded': 1, 'skipped': 0, 'failed': 0, 'failed_list': [], 'full_replace': True}, ensure_ascii=False)}\n\n"
                            yield f"data: {json.dumps({'type': 'info', 'message': '⚠️ 全量替换完成，由于 .so 文件已被替换，建议重启服务以确保更新生效'}, ensure_ascii=False)}\n\n"
                            yield f"data: {json.dumps({'type': 'restart_required', 'message': '建议重启服务以确保 .so 文件更新生效'}, ensure_ascii=False)}\n\n"
                            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"

                            # 后台重载任务
                            async def reload_scrapers_background():
                                global _version_cache, _version_cache_time
                                await asyncio.sleep(0.5)
                                try:
                                    logger.info("开始重载弹幕源...")
                                    await manager.load_and_sync_scrapers()
                                    logger.info(f"用户 '{current_user.username}' 通过全量替换模式更新了弹幕源")
                                    _version_cache = None
                                    _version_cache_time = None
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
                    # 下载 package.json
                    package_url = f"{base_url}/package.json"
                    logger.info(f"正在从 {package_url} 获取资源包信息...")
                    yield f"data: {json.dumps({'type': 'info', 'message': '正在获取资源包信息...'}, ensure_ascii=False)}\n\n"

                    # 设置更详细的超时配置: 连接超时30秒, 读取超时30秒
                    timeout_config = httpx.Timeout(30.0, read=30.0)
                    max_package_retries = 3  # 获取 package.json 的重试次数
                    package_data = None

                    for pkg_retry in range(max_package_retries + 1):
                        try:
                            if pkg_retry > 0:
                                wait_time = min(2 ** pkg_retry, 8)
                                logger.warning(f"获取资源包信息重试 {pkg_retry}/{max_package_retries}，等待 {wait_time} 秒...")
                                yield f"data: {json.dumps({'type': 'info', 'message': f'获取资源包信息失败，正在重试 ({pkg_retry}/{max_package_retries})...'}, ensure_ascii=False)}\n\n"
                                await asyncio.sleep(wait_time)

                            async with httpx.AsyncClient(timeout=timeout_config, headers=headers, follow_redirects=True, proxy=proxy_to_use) as client:
                                response = await client.get(package_url)
                                if response.status_code == 200:
                                    package_data = response.json()
                                    logger.info("成功获取资源包信息")
                                    break  # 成功，跳出重试循环
                                else:
                                    logger.warning(f"获取资源包信息失败: HTTP {response.status_code} (重试 {pkg_retry}/{max_package_retries})")
                                    if pkg_retry == max_package_retries:
                                        yield f"data: {json.dumps({'type': 'error', 'message': f'无法获取资源包信息 (HTTP {response.status_code})，请检查仓库链接或更换CDN节点'}, ensure_ascii=False)}\n\n"
                                        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                                        return

                        except httpx.TimeoutException as timeout_err:
                            logger.warning(f"连接超时 (重试 {pkg_retry}/{max_package_retries}): {timeout_err}")
                            if pkg_retry == max_package_retries:
                                yield f"data: {json.dumps({'type': 'error', 'message': '连接超时，请检查网络或更换CDN节点'}, ensure_ascii=False)}\n\n"
                                yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                                return
                        except httpx.ConnectError as conn_err:
                            logger.warning(f"连接失败 (重试 {pkg_retry}/{max_package_retries}): {conn_err}")
                            if pkg_retry == max_package_retries:
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

                    # 读取本地 versions.json 的哈希值（只读一次）
                    local_hashes = {}
                    if SCRAPERS_VERSIONS_FILE.exists():
                        try:
                            local_versions = json.loads(await asyncio.to_thread(SCRAPERS_VERSIONS_FILE.read_text))
                            local_hashes = local_versions.get('hashes', {})
                            logger.info(f"已读取本地 versions.json，包含 {len(local_hashes)} 个哈希值")
                        except Exception as e:
                            logger.warning(f"读取本地版本文件失败: {e}")
                    else:
                        logger.info("本地 versions.json 不存在，所有源都需要下载")

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

                    # 保存 package.json 到本地 - 使用异步IO
                    local_package_file = scrapers_dir / "package.json"
                    package_json_str = json.dumps(package_data, indent=2, ensure_ascii=False)
                    await asyncio.to_thread(local_package_file.write_text, package_json_str)

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
                                            import hashlib
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
                        global _version_cache, _version_cache_time
                        _version_cache = None
                        _version_cache_time = None

                        # 发送流结束信号
                        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                        return  # 有失败则不继续执行

                    # 判断是否是首次下载（本地没有任何弹幕源）
                    is_first_download = len(manager.scrapers) == 0

                    from src.utils.docker_utils import is_docker_socket_available, restart_container
                    import sys

                    docker_available = is_docker_socket_available()

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
                        global _version_cache, _version_cache_time
                        # 等待 SSE 流完全关闭和日志刷新
                        await asyncio.sleep(1.0)
                        try:
                            if is_first_download:
                                # 首次下载：保存版本信息并执行热加载
                                # 保存版本信息
                                if versions_data:
                                    try:
                                        full_versions_data = {
                                            "platform": platform_info['platform'],
                                            "type": platform_info['arch'],
                                            "version": package_data.get("version", "unknown"),
                                            "scrapers": versions_data
                                        }
                                        if hashes_data:
                                            full_versions_data["hashes"] = hashes_data
                                        versions_json_str = json.dumps(full_versions_data, indent=2, ensure_ascii=False)
                                        await asyncio.to_thread(SCRAPERS_VERSIONS_FILE.write_text, versions_json_str)
                                        logger.info(f"已保存 {len(versions_data)} 个弹幕源的版本信息")
                                    except Exception as e:
                                        logger.warning(f"保存版本信息失败: {e}")

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

                                container_name = await config_manager.get("containerName", "misaka_danmu_server")
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

                            _version_cache = None
                            _version_cache_time = None
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
    current_user: models.User = Depends(get_current_user),
    config_manager: ConfigManager = Depends(get_config_manager),
    manager = Depends(get_scraper_manager)
):
    """启动弹幕源下载任务（后台运行，不依赖 SSE 连接）"""
    from src.utils.scraper_download_executor import start_download_task

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
            config_manager=config_manager,
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
    current_user: models.User = Depends(get_current_user),
):
    """获取下载任务的当前状态和进度"""
    task_manager = get_download_task_manager()
    task = task_manager.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    return task.to_dict()


@router.get("/scrapers/download/current", summary="获取当前下载任务")
async def get_current_download(
    current_user: models.User = Depends(get_current_user),
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
    current_user: models.User = Depends(get_current_user),
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
    current_user: models.User = Depends(get_current_user),
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
                    yield f"data: {json.dumps({'type': 'done', 'status': 'completed', 'need_restart': False}, ensure_ascii=False)}\n\n"
                logger.info(f"[SSE] 任务 {task_id} done 消息已发送，退出 SSE 流")
                break

            # 任务完成则退出（备用逻辑，正常情况下应该通过 restart_pending 退出）
            if current_task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                logger.info(f"[SSE] 任务 {task_id} 状态为 {current_task.status.value}，准备发送 done 消息")
                # 等待一小段时间，确保前端有时间处理最后的 progress 消息
                await asyncio.sleep(0.1)
                logger.info(f"[SSE] 任务 {task_id} 发送 done 消息")
                yield f"data: {json.dumps({'type': 'done', 'status': current_task.status.value, 'need_restart': current_task.need_restart}, ensure_ascii=False)}\n\n"
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
    current_user: models.User = Depends(get_current_user),  # noqa: ARG001
    session: AsyncSession = Depends(get_db_session),
):
    """
    查询持久化到数据库的任务状态

    用于容器重启后前端查询之前任务的完成状态
    缓存有效期为1小时
    """
    from src.db import CacheManager
    from src.db.database import get_db_session_factory
    from src.utils.scraper_download_executor import SCRAPER_DOWNLOAD_TASK_CACHE_PREFIX

    cache_manager = CacheManager(get_db_session_factory())
    cached_data = await cache_manager.get(SCRAPER_DOWNLOAD_TASK_CACHE_PREFIX, task_id, session)

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
    current_user: models.User = Depends(get_current_user),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """获取弹幕源自动更新配置"""
    enabled = await config_manager.get("scraperAutoUpdateEnabled", "false")
    interval = await config_manager.get("scraperAutoUpdateInterval", "30")
    return {
        "enabled": enabled.lower() == "true",
        "interval": int(interval)
    }


@router.put("/scrapers/auto-update", status_code=status.HTTP_204_NO_CONTENT, summary="保存自动更新配置")
async def save_auto_update_config(
    payload: Dict[str, Any],
    current_user: models.User = Depends(get_current_user),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """保存弹幕源自动更新配置"""
    enabled = payload.get("enabled", False)
    interval = payload.get("interval", 15)

    await config_manager.setValue("scraperAutoUpdateEnabled", str(enabled).lower())
    await config_manager.setValue("scraperAutoUpdateInterval", str(interval))

    logger.info(f"用户 '{current_user.username}' 更新了自动更新配置: enabled={enabled}, interval={interval}分钟")


@router.get("/scrapers/full-replace", summary="获取全量替换配置")
async def get_full_replace_config(
    current_user: models.User = Depends(get_current_user),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """获取弹幕源全量替换配置

    全量替换模式：从 GitHub Releases 下载压缩包进行全量替换，
    而不是逐个文件对比哈希值下载。适用于 .so 文件更新不生效的情况。
    """
    enabled = await config_manager.get("scraperFullReplaceEnabled", "false")
    return {
        "enabled": enabled.lower() == "true"
    }


@router.put("/scrapers/full-replace", status_code=status.HTTP_204_NO_CONTENT, summary="保存全量替换配置")
async def save_full_replace_config(
    payload: Dict[str, Any],
    current_user: models.User = Depends(get_current_user),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """保存弹幕源全量替换配置"""
    enabled = payload.get("enabled", False)
    await config_manager.setValue("scraperFullReplaceEnabled", str(enabled).lower())
    logger.info(f"用户 '{current_user.username}' 更新了全量替换配置: enabled={enabled}")


async def _fetch_github_release_asset(
    repo_info: Dict[str, str],
    platform_key: str,
    headers: Dict[str, str],
    proxy: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    从 GitHub Releases 获取最新版本的压缩包资产信息

    Args:
        repo_info: 仓库信息 (owner, repo, proxy, proxy_type)
        platform_key: 平台标识 (如 linux-x86, windows-amd64)
        headers: HTTP 请求头
        proxy: 代理URL

    Returns:
        包含 download_url, filename, version 的字典，失败返回 None
    """
    owner = repo_info['owner']
    repo = repo_info['repo']
    github_proxy = repo_info.get('proxy')  # 用户配置的 GitHub 加速链接
    proxy_type = repo_info.get('proxy_type')

    # GitHub Releases API - 原始 URL
    original_api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"

    # 构建要尝试的 API URL 列表
    api_urls_to_try = []

    # 如果用户配置了加速链接（非 jsDelivr），优先尝试加速地址
    if github_proxy and proxy_type != 'jsdelivr':
        # 通用代理格式: https://代理域名/https://api.github.com/...
        proxied_api_url = f"{github_proxy}/https://api.github.com/repos/{owner}/{repo}/releases/latest"
        api_urls_to_try.append(('proxied', proxied_api_url))
        logger.info(f"将尝试通过加速链接获取 Release 信息: {proxied_api_url}")

    # 原始 API 作为回退
    api_urls_to_try.append(('original', original_api_url))

    timeout = httpx.Timeout(60.0, read=60.0)  # 连接60秒，读取60秒

    release_data = None
    used_proxy = False

    for url_type, api_url in api_urls_to_try:
        try:
            async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True, proxy=proxy) as client:
                logger.info(f"正在请求 Release API ({url_type}): {api_url}")
                response = await client.get(api_url)
                if response.status_code == 200:
                    release_data = response.json()
                    used_proxy = (url_type == 'proxied')
                    logger.info(f"成功获取 Release 信息 (通过 {url_type})")
                    break
                else:
                    logger.warning(f"获取 GitHub Releases 失败 ({url_type}): HTTP {response.status_code}")
        except Exception as e:
            logger.warning(f"请求 Release API 失败 ({url_type}): {e}")
            continue

    if not release_data:
        logger.error("获取 GitHub Releases 信息失败：所有尝试均失败")
        return None

    try:
        version = release_data.get('tag_name', 'unknown')
        assets = release_data.get('assets', [])

        # 查找匹配当前平台的压缩包
        asset_info = _find_matching_asset(assets, platform_key, version, 'github')
        if asset_info:
            # 如果用户配置了 GitHub 加速链接，替换下载 URL
            if github_proxy and asset_info.get('download_url') and proxy_type != 'jsdelivr':
                original_url = asset_info['download_url']
                # 通用代理格式: https://代理域名/https://github.com/...
                proxied_url = f"{github_proxy}/{original_url}"
                logger.info(f"应用 GitHub 加速链接: {original_url} -> {proxied_url}")
                asset_info['download_url'] = proxied_url
                asset_info['original_url'] = original_url  # 保留原始 URL 用于回退
            return asset_info

        logger.warning(f"未找到匹配平台 {platform_key} 的压缩包资产")
        return None

    except Exception as e:
        logger.error(f"解析 GitHub Releases 信息失败: {e}")
        return None


async def _fetch_gitee_release_asset(
    gitee_info: Dict[str, str],
    platform_key: str,
    headers: Dict[str, str],
    proxy: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    从 Gitee Releases 获取最新版本的压缩包资产信息

    Args:
        gitee_info: Gitee 仓库信息 (owner, repo)
        platform_key: 平台标识 (如 linux-x86, windows-amd64)
        headers: HTTP 请求头
        proxy: 代理URL

    Returns:
        包含 download_url, filename, version 的字典，失败返回 None
    """
    owner = gitee_info['owner']
    repo = gitee_info['repo']

    # Gitee Releases API - 获取最新发行版
    # Gitee API: https://gitee.com/api/v5/repos/{owner}/{repo}/releases/latest
    api_url = f"https://gitee.com/api/v5/repos/{owner}/{repo}/releases/latest"

    timeout = httpx.Timeout(60.0, read=60.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True, proxy=proxy) as client:
            response = await client.get(api_url)
            if response.status_code != 200:
                logger.warning(f"获取 Gitee Releases 失败: HTTP {response.status_code}")
                return None

            release_data = response.json()
            version = release_data.get('tag_name', 'unknown')
            # Gitee API 返回的是 'assets' 字段（和 GitHub 类似）
            assets = release_data.get('assets', [])

            # 调试日志：打印 Gitee 返回的资产信息
            logger.info(f"Gitee Release 版本: {version}, 资产数量: {len(assets)}")
            for asset in assets:
                asset_name = asset.get('name', asset.get('browser_download_url', 'unknown'))
                logger.debug(f"  - Gitee 资产: {asset_name}")

            # 查找匹配当前平台的压缩包
            asset_info = _find_matching_asset(assets, platform_key, version, 'gitee')
            if asset_info:
                return asset_info

            logger.warning(f"Gitee: 未找到匹配平台 {platform_key} 的压缩包资产，目标模式: {platform_key}.tar.gz 或 {platform_key}.zip")
            return None

    except Exception as e:
        logger.error(f"获取 Gitee Releases 信息失败: {e}")
        return None


def _find_matching_asset(
    assets: list,
    platform_key: str,
    version: str,
    platform_type: str = 'github'
) -> Optional[Dict[str, Any]]:
    """
    从资产列表中查找匹配当前平台的压缩包

    Args:
        assets: 资产列表
        platform_key: 平台标识 (如 linux-x86, windows-amd64)
        version: 版本号
        platform_type: 平台类型 ('github' 或 'gitee')

    Returns:
        包含 download_url, filename, version 的字典，未找到返回 None
    """
    # 支持的命名格式:
    # - scrapers-{platform_key}.zip / .tar.gz
    # - {platform_key}.zip / .tar.gz
    # - scrapers_{platform_key}.zip / .tar.gz
    target_patterns = [
        f"scrapers-{platform_key}.zip",
        f"scrapers-{platform_key}.tar.gz",
        f"{platform_key}.zip",
        f"{platform_key}.tar.gz",
        f"scrapers_{platform_key}.zip",
        f"scrapers_{platform_key}.tar.gz",
    ]

    for asset in assets:
        asset_name = asset.get('name', '').lower()
        for pattern in target_patterns:
            if pattern.lower() in asset_name or asset_name == pattern.lower():
                # GitHub 和 Gitee 的下载 URL 字段不同
                if platform_type == 'gitee':
                    # Gitee 使用 cli_download_url 作为完整下载链接
                    download_url = asset.get('cli_download_url') or asset.get('browser_download_url')
                else:
                    download_url = asset.get('browser_download_url')

                if download_url:
                    logger.info(f"找到匹配的 Release 资产: {asset.get('name')} (版本: {version}, 平台: {platform_type})")
                    return {
                        'download_url': download_url,
                        'filename': asset.get('name'),
                        'version': version,
                        'size': asset.get('size', 0),
                        'platform_type': platform_type
                    }

    return None


async def _download_and_extract_release(
    asset_info: Dict[str, Any],
    scrapers_dir: Path,
    headers: Dict[str, str],
    proxy: Optional[str] = None,
    progress_callback = None
) -> bool:
    """
    下载并解压 Release 压缩包（支持 .zip 和 .tar.gz）

    Args:
        asset_info: 资产信息 (download_url, filename, version)
        scrapers_dir: 目标目录
        headers: HTTP 请求头
        proxy: 代理URL
        progress_callback: 进度回调函数

    Returns:
        是否成功
    """
    import zipfile
    import tarfile
    import io

    download_url = asset_info['download_url']
    filename = asset_info.get('filename', '').lower()

    timeout = httpx.Timeout(180.0, read=180.0)  # 下载大文件需要更长超时
    max_retries = 3  # 最大重试次数
    archive_content = None

    # 带重试的下载逻辑
    for retry_count in range(max_retries + 1):
        try:
            if retry_count > 0:
                # 指数退避：2秒, 4秒, 8秒
                wait_time = min(2 ** retry_count, 10)
                logger.warning(f"下载压缩包重试 {retry_count}/{max_retries}，等待 {wait_time} 秒...")
                if progress_callback:
                    await progress_callback(f"下载失败，正在重试 ({retry_count}/{max_retries})...")
                await asyncio.sleep(wait_time)
            else:
                if progress_callback:
                    await progress_callback("正在下载压缩包...")

            async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True, proxy=proxy) as client:
                response = await client.get(download_url)
                if response.status_code == 200:
                    archive_content = response.content
                    logger.info(f"压缩包下载完成: {len(archive_content)} 字节")
                    break  # 下载成功，跳出重试循环
                else:
                    logger.warning(f"下载压缩包失败: HTTP {response.status_code} (重试 {retry_count}/{max_retries})")
                    if retry_count == max_retries:
                        logger.error(f"下载压缩包失败，已重试 {max_retries} 次: HTTP {response.status_code}")
                        return False

        except (httpx.TimeoutException, asyncio.TimeoutError) as e:
            logger.warning(f"下载压缩包超时 (重试 {retry_count}/{max_retries}): {e}")
            if retry_count == max_retries:
                logger.error(f"下载压缩包超时，已重试 {max_retries} 次")
                return False
        except httpx.ConnectError as e:
            logger.warning(f"连接失败 (重试 {retry_count}/{max_retries}): {e}")
            if retry_count == max_retries:
                logger.error(f"连接失败，已重试 {max_retries} 次")
                return False
        except Exception as e:
            logger.warning(f"下载异常 (重试 {retry_count}/{max_retries}): {e}")
            if retry_count == max_retries:
                logger.error(f"下载异常，已重试 {max_retries} 次: {e}")
                return False

    if not archive_content:
        logger.error("下载压缩包失败：未获取到内容")
        return False

    try:

        if progress_callback:
            await progress_callback("正在解压文件...")

        # 先清理旧的 .so/.pyd 文件
        deleted_count = 0
        for file in scrapers_dir.glob("*"):
            if file.suffix in ['.so', '.pyd']:
                try:
                    file.unlink()
                    deleted_count += 1
                except Exception as e:
                    logger.warning(f"删除旧文件 {file.name} 失败: {e}")

        if deleted_count > 0:
            logger.info(f"已删除 {deleted_count} 个旧的编译文件")

        # 解压新文件
        extracted_count = 0

        # 判断压缩包类型
        if filename.endswith('.tar.gz') or filename.endswith('.tgz'):
            # 处理 tar.gz 格式
            with tarfile.open(fileobj=io.BytesIO(archive_content), mode='r:gz') as tar_ref:
                for member in tar_ref.getmembers():
                    # 安全检查：防止符号链接和路径穿越
                    if member.issym() or member.islnk():
                        logger.warning(f"跳过符号链接: {member.name}")
                        continue

                    if member.isfile() and member.name.endswith(('.so', '.pyd', '.json')):
                        # 获取文件名（去掉路径前缀）
                        base_name = Path(member.name).name
                        if not base_name or ".." in member.name:
                            logger.warning(f"跳过可疑文件名: {member.name}")
                            continue

                        target_path = scrapers_dir / base_name

                        # 安全检查：确保目标路径在 scrapers_dir 内
                        try:
                            target_path.resolve().relative_to(scrapers_dir.resolve())
                        except ValueError:
                            logger.warning(f"检测到路径穿越尝试: {member.name}")
                            continue

                        # 读取并写入文件
                        file_obj = tar_ref.extractfile(member)
                        if file_obj:
                            file_content = file_obj.read()
                            await asyncio.to_thread(target_path.write_bytes, file_content)
                            extracted_count += 1
                            logger.debug(f"解压: {base_name}")
        else:
            # 处理 zip 格式
            with zipfile.ZipFile(io.BytesIO(archive_content), 'r') as zip_ref:
                for zip_info in zip_ref.infolist():
                    # 只解压 .so, .pyd, .json 文件
                    if zip_info.filename.endswith(('.so', '.pyd', '.json')):
                        # 获取文件名（去掉路径前缀）
                        base_name = Path(zip_info.filename).name
                        if not base_name or ".." in zip_info.filename:
                            logger.warning(f"跳过可疑文件名: {zip_info.filename}")
                            continue

                        target_path = scrapers_dir / base_name

                        # 安全检查：确保目标路径在 scrapers_dir 内
                        try:
                            target_path.resolve().relative_to(scrapers_dir.resolve())
                        except ValueError:
                            logger.warning(f"检测到路径穿越尝试: {zip_info.filename}")
                            continue

                        # 读取并写入文件
                        file_content = zip_ref.read(zip_info.filename)
                        await asyncio.to_thread(target_path.write_bytes, file_content)
                        eracted_count += 1
                        logger.debug(f"解压: {base_name}")

        logger.info(f"解压完成: 共 {extracted_count} 个文件")

        if progress_callback:
            await progress_callback(f"解压完成: {extracted_count} 个文件")

        return extracted_count > 0

    except zipfile.BadZipFile:
        logger.error("ZIP 压缩包格式错误")
        return False
    except tarfile.TarError as e:
        logger.error(f"TAR 压缩包格式错误: {e}")
        return False
    except Exception as e:
        logger.error(f"下载或解压失败: {e}", exc_info=True)
        return False


@router.delete("/scrapers/backup", summary="删除弹幕源备份")
async def delete_backup(
    current_user: models.User = Depends(get_current_user)
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
    current_user: models.User = Depends(get_current_user),
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

        # 删除 package.json 和 versions.json
        if SCRAPERS_PACKAGE_FILE.exists():
            SCRAPERS_PACKAGE_FILE.unlink()
            logger.info("已删除 package.json")

        if SCRAPERS_VERSIONS_FILE.exists():
            SCRAPERS_VERSIONS_FILE.unlink()
            logger.info("已删除 versions.json")

        # 清除版本缓存
        global _version_cache, _version_cache_time
        _version_cache = None
        _version_cache_time = None

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
    current_user: models.User = Depends(get_current_user),
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

            # 删除 package.json 和 versions.json
            if SCRAPERS_PACKAGE_FILE.exists():
                SCRAPERS_PACKAGE_FILE.unlink()
                logger.info("已删除 package.json")

            if SCRAPERS_VERSIONS_FILE.exists():
                SCRAPERS_VERSIONS_FILE.unlink()
                logger.info("已删除 versions.json")

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
        global _version_cache, _version_cache_time
        _version_cache = None
        _version_cache_time = None

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
