"""弹幕源资源下载、文件部署与包校验的共享服务。"""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import io
import json
import logging
import platform
import re
import shutil
import sys
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from src._version import APP_VERSION
from src.core.env import is_docker_environment as _is_docker_environment
from src.scrapers.base import BaseScraper
from src.services.scraper_manager import _version_satisfies
from src.utils.scraper_ops.scraper_version_manager import ScraperVersionManager, is_semantic_version

logger = logging.getLogger(__name__)

# 下载状态区域由读写双方共享，不再从执行器反向取常量。
SCRAPER_DOWNLOAD_TASK_CACHE_PREFIX = "scraper_download_task_"
SCRAPER_DOWNLOAD_TASK_CACHE_TTL = 3600
_download_lock = asyncio.Lock()


class ResourceVersionCache:
    """共享版本缓存状态，避免执行器依赖 HTTP 模块。"""

    def __init__(self) -> None:
        self.value: Optional[Dict[str, Any]] = None
        self.updated_at: Optional[datetime] = None

    def clear(self) -> None:
        """使下载后的版本查询重新读取文件。"""
        self.value = None
        self.updated_at = None


resource_version_cache = ResourceVersionCache()


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


async def check_scraper_compat_in_dir(check_dir: Path) -> dict:
    """从目录中逐个 import .so/.pyd，检查 min_server_version 类属性。
    与 scraper_manager.load_and_sync_scrapers 使用相同机制，是部署前最可靠的校验点。
    返回不兼容的 {provider_name: required_version} 字典。

    why：提到模块级供手动下载与自动更新两条链路共用——此前只有手动路径做预检，
    自动更新直接下载后重启，导致"重启后才发现全部源不满足版本"（源全废）。
    """

    def _probe_single(file_path: Path):
        """在线程中同步加载单个 .so，返回 (provider_name, min_ver) 或 None。
        why：exec_module 是同步阻塞调用，直接在事件循环中执行会阻塞所有 SSE 推送，
        导致前端始终收到 status='运行中' 的旧消息，无法感知任务结束。
        """
        module_stem = file_path.stem.split('.')[0]
        spec = importlib.util.spec_from_file_location(
            f"_compat_probe_{module_stem}", file_path
        )
        if not spec or not spec.loader:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            if not (issubclass(obj, BaseScraper) and obj is not BaseScraper):
                continue
            provider_name = getattr(obj, 'provider_name', None)
            source_min_ver = getattr(obj, 'min_server_version', None) or ''
            if source_min_ver and not _version_satisfies(APP_VERSION, source_min_ver):
                if provider_name:
                    return (provider_name, source_min_ver)
        return None

    incompatible: dict = {}
    for file_path in sorted(check_dir.iterdir()):
        if not (file_path.name.endswith(".so") or file_path.name.endswith(".pyd")):
            continue
        module_stem = file_path.stem.split('.')[0]
        if module_stem.startswith("_") or module_stem == "base":
            continue
        if file_path.stat().st_size == 0:
            continue
        try:
            result = await asyncio.to_thread(_probe_single, file_path)
            if result:
                provider_name, source_min_ver = result
                incompatible[provider_name] = source_min_ver
        except Exception as e:
            # 无法 import 的模块跳过，不阻断整体校验
            logger.debug(f"check_scraper_compat_in_dir 跳过 {file_path.name}: {e}")
    return incompatible


async def ensure_manifest_in_dir(check_dir: Path) -> Optional[Dict[str, Any]]:
    """确保目录内存在权威文件；缺失时从 legacy 两份配置整合生成并落盘。

    生成即整合完整数据（版本/哈希/架构/大小），之后该目录的一切操作只读权威文件。
    """
    manifest = await asyncio.to_thread(ScraperVersionManager.load_manifest, check_dir)
    if manifest and ScraperVersionManager.validate_manifest(manifest):
        return manifest

    logger.info(f"{check_dir.name} 内无有效权威文件，从 legacy 配置整合生成")
    manifest = await asyncio.to_thread(
        ScraperVersionManager.extract_manifest_from_legacy,
        check_dir / "package.json",
        check_dir / "versions.json",
        check_dir,
    )
    if manifest and manifest.get("sources"):
        await asyncio.to_thread(ScraperVersionManager.save_manifest, manifest, check_dir)
        return manifest

    logger.warning(f"{check_dir.name} 无法生成权威文件（无源文件或 legacy 配置）")
    return None


async def verify_scraper_package(
    check_dir: Path,
    expected_version: Optional[str] = None,
) -> Tuple[bool, List[str]]:
    """对目录（通常是解压出的临时目录）做部署前四项校验。

    校验项：架构 / 各源版本 / 最低可用版本 / 哈希。
    全部通过才应继续备份与部署流程。

    Returns:
        (是否通过, 失败原因列表)
    """
    errors: List[str] = []

    if not check_dir.exists():
        return False, [f"目录不存在: {check_dir}"]

    manifest = await ensure_manifest_in_dir(check_dir)
    if not manifest:
        return False, ["缺少权威文件且无法生成"]

    platform_key = ScraperVersionManager.get_platform_key()
    expected_arch = ScraperVersionManager.normalize_arch(platform_key)
    sources = manifest.get("sources") or {}

    # ── 1. 包版本与远程声明一致 ──
    # why：expected_version 可能来自 Release 的 tag_name。测试通道用固定标签（如 test），
    #      标签名并非语义版本，与包内真实版本（2.3.0）比对必然失败，会误判为"包版本不符"
    #      而拒绝部署。因此仅当声明值本身是语义版本时才做一致性校验。
    pkg_version = ScraperVersionManager.get_version_from_manifest(manifest)
    if expected_version and pkg_version and is_semantic_version(expected_version):
        if pkg_version.lstrip('v') != expected_version.lstrip('v'):
            errors.append(
                f"包版本不符：声明 {expected_version}，实际 {pkg_version}"
            )
    elif expected_version and not is_semantic_version(expected_version):
        logger.info(
            f"声明版本 '{expected_version}' 非语义版本（通常是测试通道标签），"
            f"跳过版本一致性校验，实际包版本: {pkg_version or '未知'}"
        )

    # ── 2. 包级最低可用版本 ──
    min_server = manifest.get("min_server_version")
    if min_server and not _version_satisfies(APP_VERSION, min_server):
        errors.append(
            f"服务器版本不足：当前 {APP_VERSION}，包要求 >= {min_server}"
        )

    binaries = [p for p in sorted(check_dir.iterdir())
                if ScraperVersionManager.is_scraper_binary(p)]
    if not binaries:
        return False, ["目录内无弹幕源二进制文件"]

    # ── 3. 架构与哈希（逐文件，均以权威文件为基准） ──
    def _verify_files() -> List[str]:
        problems: List[str] = []
        for file_path in binaries:
            name = file_path.name.split('.')[0]
            entry = sources.get(name) or {}

            actual_arch = ScraperVersionManager.detect_binary_arch(file_path)
            if actual_arch and expected_arch:
                if ScraperVersionManager.normalize_arch(actual_arch) != expected_arch:
                    problems.append(
                        f"{name} 架构不符：本机 {expected_arch}，文件 {actual_arch}"
                    )
                    continue

            expected_hash = None
            hashes = entry.get("hashes")
            if isinstance(hashes, dict):
                expected_hash = hashes.get(platform_key)
            if not expected_hash:
                expected_hash = entry.get("hash")

            if expected_hash:
                actual_hash = ScraperVersionManager.calculate_file_hash(file_path)
                if actual_hash != expected_hash:
                    problems.append(
                        f"{name} 哈希不符：期望 {expected_hash[:12]}…，实际 {actual_hash[:12]}…"
                    )
        return problems

    errors.extend(await asyncio.to_thread(_verify_files))

    # ── 4. 各源 min_server_version（import 探测，最可靠） ──
    incompatible = await check_scraper_compat_in_dir(check_dir)
    if incompatible:
        detail = ", ".join(f"{k} 需要 >= {v}" for k, v in sorted(incompatible.items()))
        errors.append(f"{len(incompatible)} 个源版本要求不满足（{detail}）")

    return (not errors), errors


async def backup_scrapers(
    current_user: Any,
    new_versions_data: Optional[Dict[str, str]] = None,
    new_hashes_data: Optional[Dict[str, str]] = None,
    package_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """备份当前 scrapers 目录下的编译文件到持久化目录

    直接从 scrapers 目录复制所有 .so/.pyd 文件和 versions.json 到备份目录。

    自动更新（非首次下载）场景说明：
    逐文件自动更新只把新 .so 下到 scrapers 目录，并不会更新 scrapers/versions.json。
    若此时仍直接复制旧的 scrapers/versions.json 到备份目录，备份目录的 updated_at
    不会比 scrapers 目录新，重启后 scraper_manager 便不会从备份恢复新版本，从而导致
    “下载新版→重启→版本回退→再下载”的无限重启循环。
    因此这里允许调用方传入 new_versions_data / new_hashes_data / package_data，
    直接用新版本信息构建备份目录的 versions.json（含 updated_at），确保新版本被正确持久化。
    """
    try:
        scrapers_dir = _get_scrapers_dir()

        # 创建备份目录
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        # 读取版本信息（使用 ScraperVersionManager 统一管理）
        manifest = ScraperVersionManager.load_manifest(_get_scrapers_dir())
        if manifest is None:
            manifest = {"sources": {}}

        # 搬运权威文件与二进制到备份目录（clear_dst 先清空同类旧文件，保留 backup_metadata.json）
        # 使用统一搬运工具，只搬 scraper_manifest.json + *.so/*.pyd，不搬 legacy 文件
        backup_count = ScraperVersionManager.copy_scraper_files(
            scrapers_dir, BACKUP_DIR, clear_dst=True
        )

        # 收集已备份二进制的元数据（供接口返回）
        backed_files = []
        sources = manifest.get("sources", {})
        for file in BACKUP_DIR.iterdir():
            if not file.is_file() or file.suffix not in ['.so', '.pyd']:
                continue

            # 从文件名提取弹幕源名称
            scraper_name = file.name.split('.')[0]

            file_info = {
                "name": file.name,
                "scraper": scraper_name,
                "size": file.stat().st_size,
                "modified": datetime.fromtimestamp(file.stat().st_mtime).isoformat()
            }

            # 添加版本号（从 manifest 的 sources 中查找）
            if scraper_name in sources:
                file_info["version"] = sources[scraper_name].get("version", "unknown")

            backed_files.append(file_info)

        # 备份 scraper_manifest.json（使用 ScraperVersionManager）
        if manifest:
            # 如果有新的 package_data，更新 manifest
            if package_data is not None:
                manifest["version"] = package_data.get("version", manifest.get("version", "unknown"))
                if package_data.get("min_server_version"):
                    manifest["min_server_version"] = package_data["min_server_version"]
                manifest["updated_at"] = datetime.now().isoformat()

            # 保存到备份目录
            ScraperVersionManager.save_manifest(manifest, BACKUP_DIR)
            logger.info("已备份 scraper_manifest.json")
        else:
            logger.warning("无 manifest 数据，无法备份版本信息")

        # 读取 manifest 的版本号（用于元数据）
        package_version = manifest.get("version", "unknown") if manifest else None
        if not package_version and package_data is not None:
            package_version = package_data.get("version")

        logger.info(f"用户 '{current_user.username}' 备份了 {backup_count} 个弹幕源文件到 {BACKUP_DIR}")
        return {"message": f"成功备份 {backup_count} 个文件", "count": backup_count}

    except Exception as e:
        logger.error(f"备份弹幕源失败: {e}", exc_info=True)
        raise RuntimeError(f"备份失败: {e}") from e


async def restore_scrapers(
    current_user: Any,
    manager: Any = None
) -> Dict[str, Any]:
    """从持久化备份目录还原弹幕源文件"""
    try:
        scrapers_dir = _get_scrapers_dir()

        if not BACKUP_DIR.exists():
            raise FileNotFoundError("未找到备份目录")

        # 检查备份的 manifest 文件
        backup_manifest_file = BACKUP_DIR / "scraper_manifest.json"
        if not backup_manifest_file.exists():
            raise FileNotFoundError("备份目录中未找到 scraper_manifest.json")

        # 读取备份的 manifest
        manifest = json.loads(backup_manifest_file.read_text(encoding="utf-8"))
        logger.info(f"备份信息: 版本 {manifest.get('version')}, 平台 {manifest.get('platform')}, {len(manifest.get('sources', {}))} 个源")

        # 还原文件（使用统一搬运工具）
        # 原来通配 .json 会把 backup_metadata.json 一并还原到运行目录，造成污染
        restore_count = ScraperVersionManager.copy_scraper_files(BACKUP_DIR, scrapers_dir)

        if restore_count == 0:
            raise FileNotFoundError("备份目录为空")

        logger.info(f"用户 '{current_user.username}' 从备份还原了 {restore_count} 个文件")

        result = {
            "message": f"成功还原 {restore_count} 个文件，正在后台重载...",
            "count": restore_count,
            "manifestInfo": {
                "version": manifest.get("version"),
                "platform": manifest.get("platform"),
                "sourceCount": len(manifest.get("sources", {})),
                "updatedAt": manifest.get("updated_at")
            }
        }

        # 文件还原与重载分离，由调用方决定何时加载二进制。
        return result

    except FileNotFoundError:
        raise
    except Exception as e:
        logger.error(f"还原弹幕源失败: {e}", exc_info=True)
        raise RuntimeError(f"还原失败: {e}") from e


async def _fetch_github_release_asset(
    repo_info: Dict[str, str],
    platform_key: str,
    headers: Dict[str, str],
    proxy: Optional[str] = None,
    tag_or_branch: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    从 GitHub Releases 获取压缩包资产信息

    Args:
        repo_info: 仓库信息 (owner, repo, proxy, proxy_type)
        platform_key: 平台标识 (如 linux-x86, windows-amd64)
        headers: HTTP 请求头
        proxy: 代理URL
        tag_or_branch: 标签或分支名（如 "v2.2.8" 或 "main"）。为 None 或 "main"/"master" 时使用 latest

    Returns:
        包含 download_url, filename, version 的字典，失败返回 None
    """
    owner = repo_info['owner']
    repo = repo_info['repo']
    github_proxy = repo_info.get('proxy')  # 用户配置的 GitHub 加速链接
    proxy_type = repo_info.get('proxy_type')

    # 判断是使用特定标签还是最新版本
    # 如果 tag_or_branch 是 None、空字符串、"main" 或 "master"，使用 latest
    # 否则使用指定的标签
    use_latest = not tag_or_branch or tag_or_branch.strip() in ("", "main", "master")

    # GitHub Releases API - 原始 URL
    if use_latest:
        original_api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        logger.info(f"使用 GitHub Releases latest API")
    else:
        # 确保标签名带 v 前缀（如果用户输入的是纯数字版本）
        tag = tag_or_branch.strip()
        if not tag.startswith('v') and tag[0].isdigit():
            tag = f"v{tag}"
        original_api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}"
        logger.info(f"使用 GitHub Releases 指定标签: {tag}")

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
    proxy: Optional[str] = None,
    tag_or_branch: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    从 Gitee Releases 获取压缩包资产信息

    Args:
        gitee_info: Gitee 仓库信息 (owner, repo)
        platform_key: 平台标识 (如 linux-x86, windows-amd64)
        headers: HTTP 请求头
        proxy: 代理URL
        tag_or_branch: 标签或分支名（如 "v2.2.8" 或 "main"）。为 None 或 "main"/"master" 时使用 latest

    Returns:
        包含 download_url, filename, version 的字典，失败返回 None
    """
    owner = gitee_info['owner']
    repo = gitee_info['repo']

    # 判断是使用特定标签还是最新版本
    use_latest = not tag_or_branch or tag_or_branch.strip() in ("", "main", "master")

    # Gitee Releases API
    if use_latest:
        api_url = f"https://gitee.com/api/v5/repos/{owner}/{repo}/releases/latest"
        logger.info(f"使用 Gitee Releases latest API")
    else:
        # 确保标签名带 v 前缀（如果用户输入的是纯数字版本）
        tag = tag_or_branch.strip()
        if not tag.startswith('v') and tag[0].isdigit():
            tag = f"v{tag}"
        api_url = f"https://gitee.com/api/v5/repos/{owner}/{repo}/releases/tags/{tag}"
        logger.info(f"使用 Gitee Releases 指定标签: {tag}")

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


def _purge_legacy_version_files(target_dir: Path) -> None:
    """清除目录中的 legacy 版本文件（package.json / versions.json）。

    why: 新架构只以 scraper_manifest.json 为权威。历史版本或旧代码路径可能在备份目录
    留下这两个文件，它们不属于搬运范围、也不会被同名覆盖，滞留后会被误当作版本依据，
    造成备份目录显示的版本与实际 .so 不一致。
    """
    for name in ("package.json", "versions.json"):
        stale = target_dir / name
        if stale.exists():
            try:
                stale.unlink()
                logger.info(f"已清除备份目录的 legacy 文件: {name}")
            except OSError as e:
                logger.warning(f"清除 legacy 文件 {name} 失败: {e}")


def _persist_new_version_to_backup(
    extract_dir: Path,
    release_version: str,
    remote_package_json: Optional[Dict] = None,
) -> None:
    """将临时目录中解压好的新版弹幕源持久化到备份目录（覆盖运行 .so 之前调用）。

    why(断无限重启循环)：备份目录是唯一持久化的位置，且重启恢复逻辑依据
    backup/scraper_manifest.json 的 updated_at 判定是否需要恢复。必须在覆盖运行中的 .so
    （可能 native crash）之前，就把新版 .so + scraper_manifest.json 落盘到备份目录；
    否则一旦覆盖时崩溃，backup 仍是旧版 → 重启后回退 → 轮询又发现新版 → 无限循环。

    scraper_manifest.json 的 updated_at 必须写为当前时间且版本号为新版，确保重启后
    backup.updated_at > scrapers.updated_at 时恢复到的是新版本。

    Args:
        remote_package_json: 下载前从远端仓库预拉取的 package.json 内容（dict）。
            why：全量包（tar.gz）通常不内置 package.json 和 versions.json，
            此时 scrapers_versions/scrapers_hashes 全为空，_verify_backup_version
            校验失败 → 循环重启。用远端数据兜底可确保写出完整的 backup/scraper_manifest.json。
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    # 1) 从临时目录的 package.json/versions.json 生成 scraper_manifest.json
    # why: 新架构下，只保留 scraper_manifest.json 作为唯一权威文件
    tmp_package_file = extract_dir / "package.json"
    tmp_versions_file = extract_dir / "versions.json"

    # 生成 manifest（优先使用临时目录的文件，远端 package.json 作为兜底）
    try:
        manifest = ScraperVersionManager.extract_manifest_from_legacy(
            tmp_package_file,
            tmp_versions_file,
            extract_dir
        )

        # 更新全局版本号
        # why: release_version 来自 asset_info['version']，实际是 Release 的 tag_name。
        #  1) 按 tag 下载时可能为空字符串；
        #  2) 测试通道使用固定标签（如 test / nightly），标签名不是版本号。
        # 这两种情况下直接赋值都会抹掉 extract_manifest_from_legacy 从包内 versions.json
        # 提取到的真实版本（如 2.3.0），导致权威文件 version 写成 "test"、前端"本地版本"
        # 显示为分支名，且后续所有版本比较失效。因此仅当标签本身是语义版本时才覆盖。
        if release_version and is_semantic_version(release_version):
            manifest["version"] = release_version
        elif release_version:
            logger.info(
                f"标签 '{release_version}' 非语义版本（通常是测试通道标签），"
                f"保留包内真实版本: {manifest.get('version') or '未知'}"
            )
        manifest["updated_at"] = datetime.now().isoformat()

        # 如果临时目录没有版本信息，使用远端 package.json 兜底
        if remote_package_json and (not manifest.get("sources") or not manifest.get("min_server_version")):
            if not manifest.get("min_server_version"):
                manifest["min_server_version"] = remote_package_json.get("min_server_version")

            # 从远端 package.json 提取各源版本信息
            platform_key = get_platform_key()
            for scraper_name, scraper_info in (remote_package_json.get("resources", {}) or {}).items():
                if isinstance(scraper_info, dict):
                    if scraper_name not in manifest["sources"]:
                        manifest["sources"][scraper_name] = {}

                    manifest["sources"][scraper_name]["version"] = scraper_info.get("version")

                    # 提取哈希
                    hashes = scraper_info.get("hashes", {})
                    if platform_key in hashes:
                        manifest["sources"][scraper_name]["hash"] = hashes[platform_key]

            logger.info(f"全量包内无版本文件，已用远端 package.json 兜底生成 manifest（{len(manifest['sources'])} 个源）")

        # 保存 manifest 到临时目录（后续会被复制）
        ScraperVersionManager.save_manifest(manifest, extract_dir)

        # 删除临时目录中的 legacy 文件
        # why: 新架构只保留 scraper_manifest.json，package.json 和 versions.json 仅用于生成 manifest
        if tmp_package_file.exists():
            tmp_package_file.unlink()
            logger.info("已删除临时目录的 package.json")
        if tmp_versions_file.exists():
            tmp_versions_file.unlink()
            logger.info("已删除临时目录的 versions.json")

    except Exception as e:
        logger.error(f"生成 manifest 失败: {e}", exc_info=True)
        raise

    # 2) 搬运临时目录的权威文件与二进制到备份目录
    # 使用统一搬运工具，不再依赖"legacy 文件已被删除"这一前置条件
    # clear_dst=True: 复制前先清空备份目录的同类旧文件。
    # why: 覆盖式写入只能盖住同名文件，历史遗留的 package.json / versions.json
    # 不在搬运范围内，会永久滞留在备份目录并被误当作版本依据。
    backup_count = ScraperVersionManager.copy_scraper_files(
        extract_dir, BACKUP_DIR, clear_dst=True
    )
    _purge_legacy_version_files(BACKUP_DIR)

    logger.info(f"已将新版 {release_version} 持久化到备份目录: {backup_count} 个文件, {len(manifest.get('sources', {}))} 个源")


def _get_deferred_overlay_dir(scrapers_dir: Optional[Path] = None) -> Path:
    """推迟覆盖时使用的临时目录（存放已解压待生效的新版文件）"""
    base = scrapers_dir if scrapers_dir is not None else _get_scrapers_dir()
    return base / ".tmp_update"


def _overlay_extract_dir_to_scrapers(
    extract_dir: Path,
    scrapers_dir: Path,
    old_files: Optional[set] = None,
    new_files: Optional[set] = None,
) -> int:
    """把临时目录里的新版文件覆盖到运行目录，并清理不再存在于新包中的旧 .so/.pyd

    危险操作：覆盖后进程内存中的旧模块与磁盘新二进制不一致，调用方必须紧接着重启，
    中间不要再执行业务代码。

    注意：临时目录中应该只包含 scraper_manifest.json 和 .so/.pyd 文件，
    package.json 和 versions.json 已在生成 manifest 后被删除。
    """
    # 使用统一搬运工具：只搬 manifest + 二进制
    try:
        overlay_count = ScraperVersionManager.copy_scraper_files(extract_dir, scrapers_dir)
    except Exception as e:
        logger.warning(f"覆盖运行目录失败: {e}")
        overlay_count = 0

    # 覆盖成功后，清理不再存在于新包中的旧文件
    if old_files and overlay_count > 0:
        stale_files = old_files - (new_files or set())
        for stale_name in stale_files:
            try:
                (scrapers_dir / stale_name).unlink(missing_ok=True)
                logger.info(f"清理旧文件: {stale_name}")
            except Exception as e:
                logger.warning(f"清理旧文件 {stale_name} 失败: {e}")

    # 清理运行目录中的 legacy 文件（如果存在）
    # why: 新架构只保留 scraper_manifest.json 作为唯一权威文件
    try:
        legacy_files = ["package.json", "versions.json"]
        for legacy_file in legacy_files:
            legacy_path = scrapers_dir / legacy_file
            if legacy_path.exists():
                legacy_path.unlink()
                logger.info(f"已删除运行目录的 legacy 文件: {legacy_file}")
    except Exception as e:
        logger.warning(f"清理运行目录 legacy 文件失败: {e}")

    # 清理临时目录
    shutil.rmtree(extract_dir, ignore_errors=True)
    return overlay_count


def apply_deferred_overlay(scrapers_dir: Optional[Path] = None) -> int:
    """应用被推迟的覆盖操作（供 executor 在「SSE 终态已发送 + 即将重启」时调用）

    Returns:
        覆盖的文件数；无待应用内容时返回 0
    """
    target_dir = scrapers_dir if scrapers_dir is not None else _get_scrapers_dir()
    extract_dir = _get_deferred_overlay_dir(target_dir)
    if not extract_dir.is_dir():
        logger.warning("没有待应用的更新（临时目录不存在），跳过覆盖")
        return 0

    # 运行目录里现存的 .so/.pyd，用于覆盖后清理已从新包中移除的旧文件
    old_files = {
        f.name for f in target_dir.glob("*")
        if f.is_file() and f.suffix in ['.so', '.pyd']
    }
    new_files = {
        f.name for f in extract_dir.glob("*")
        if f.is_file() and f.suffix in ['.so', '.pyd']
    }
    overlay_count = _overlay_extract_dir_to_scrapers(extract_dir, target_dir, old_files, new_files)
    logger.info(f"已应用推迟的更新: {overlay_count} 个文件")
    return overlay_count


async def _download_and_extract_release(
    asset_info: Dict[str, Any],
    scrapers_dir: Path,
    headers: Dict[str, str],
    proxy: Optional[str] = None,
    progress_callback = None,
    defer_overlay: bool = False,
    remote_package_json: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    下载并解压 Release 压缩包（支持 .zip 和 .tar.gz）

    Args:
        asset_info: 资产信息 (download_url, filename, version)
        scrapers_dir: 目标目录
        headers: HTTP 请求头
        proxy: 代理URL
        progress_callback: 进度回调函数
        defer_overlay: 为 True 时只解压到临时目录并完成持久化，不覆盖运行目录的 .so。
            why: 覆盖正在被加载的 .so 后，进程内存中是旧模块而磁盘已是新二进制，
            此后任何延迟 import / 未加载符号的访问都可能 segfault（表现为 SSE 心跳
            永久消失、前端卡住）。因此对齐逐文件更新路径的做法——把覆盖动作推迟到
            最后，等 SSE 终态消息发完，紧邻重启时再执行。
        remote_package_json: 下载前从远端预拉取的 package.json 内容，透传给
            _persist_new_version_to_backup 作为生成权威文件时的兜底数据源。

    Returns:
        是否成功
    """

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
                # 流式下载并周期性回报进度
                # why: 原先用 client.get() 一次性读完整个包，期间无任何进度反馈。
                # 大包 + GitHub 直连较慢时，前端会长时间停在"正在下载压缩包..."看起来像卡死
                # （最坏 4 次尝试 x 180s 超时 ≈ 12 分钟无变化）。改为流式下载，按进度推送文案，
                # 让用户能看到实际下载速度与百分比。
                async with client.stream("GET", download_url) as response:
                    if response.status_code == 200:
                        total_size = int(response.headers.get("content-length") or 0)
                        chunks = []
                        downloaded = 0
                        last_report = 0.0
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            chunks.append(chunk)
                            downloaded += len(chunk)
                            # 每累计 512KB 或每 1% 回报一次，避免刷屏
                            if progress_callback and (downloaded - last_report >= 512 * 1024):
                                last_report = downloaded
                                mb = downloaded / 1024 / 1024
                                if total_size > 0:
                                    pct = downloaded * 100 // total_size
                                    total_mb = total_size / 1024 / 1024
                                    await progress_callback(
                                        f"正在下载压缩包... {pct}% ({mb:.1f}/{total_mb:.1f} MB)"
                                    )
                                else:
                                    await progress_callback(f"正在下载压缩包... 已下载 {mb:.1f} MB")
                        archive_content = b"".join(chunks)
                        logger.info(f"压缩包下载完成: {len(archive_content)} 字节")
                        if progress_callback:
                            await progress_callback(
                                f"下载完成 ({len(archive_content) / 1024 / 1024:.1f} MB)，准备解压..."
                            )
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

        # ── 解压前版本检查：从包内读取 versions.json 的 min_server_version ──
        try:
            min_server_version = None
            if filename.endswith('.tar.gz') or filename.endswith('.tgz'):
                with tarfile.open(fileobj=io.BytesIO(archive_content), mode='r:gz') as pre_tar:
                    for m in pre_tar.getmembers():
                        if m.isfile() and Path(m.name).name == 'versions.json':
                            fo = pre_tar.extractfile(m)
                            if fo:
                                min_server_version = json.loads(fo.read()).get('min_server_version')
                            break
            else:
                with zipfile.ZipFile(io.BytesIO(archive_content), 'r') as pre_zip:
                    for zi in pre_zip.infolist():
                        if Path(zi.filename).name == 'versions.json':
                            min_server_version = json.loads(pre_zip.read(zi.filename)).get('min_server_version')
                            break

            if min_server_version:
                if not _version_satisfies(APP_VERSION, min_server_version):
                    logger.error(
                        f"全量替换中止：弹幕源包要求服务器版本 >= {min_server_version}，"
                        f"当前版本 {APP_VERSION}"
                    )
                    if progress_callback:
                        await progress_callback(f"版本不满足：需要 >= {min_server_version}，当前 {APP_VERSION}")
                    return False
                logger.info(f"版本检查通过: 服务器 {APP_VERSION} >= 弹幕源包要求 {min_server_version}")
        except Exception as e:
            logger.warning(f"解压前版本检查失败（宽松放行）: {e}")

        # 记录旧文件列表（解压完成后清理多余的旧文件）
        old_files = {
            file.name for file in scrapers_dir.glob("*")
            if file.suffix in ['.so', '.pyd']
        }

        # 方案A(断无限重启循环)：先解压到临时目录，持久化 backup 落盘新版后，
        # 才覆盖运行目录里正在被加载的 .so。
        # why: 直接 write_bytes 覆盖运行中的 .so 在 ARM64/uvloop 下易触发 native crash，
        # 崩溃点若发生在"写 versions.json + 备份到持久化目录"之前，会导致 scrapers 已是
        # 新版 .so 但 versions.json/backup 仍是旧版 → 重启后从旧 backup 恢复 → 轮询又发现
        # 新版 → 无限下载重启循环。将危险的覆盖操作放到持久化之后，即使覆盖时崩溃，重启后
        # backup 已是新版，恢复的就是新版，循环终结。
        extract_dir = _get_deferred_overlay_dir(scrapers_dir)
        try:
            if extract_dir.exists():
                shutil.rmtree(extract_dir, ignore_errors=True)
            extract_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"创建临时解压目录失败: {e}")
            return False

        # 解压新文件到临时目录（不碰运行中的 .so）
        extracted_count = 0
        new_files = set()

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

                        # 先写入临时目录（extract_dir），持久化后再覆盖运行目录
                        target_path = extract_dir / base_name

                        # 安全检查：确保目标路径在 extract_dir 内
                        try:
                            target_path.resolve().relative_to(extract_dir.resolve())
                        except ValueError:
                            logger.warning(f"检测到路径穿越尝试: {member.name}")
                            continue

                        # 读取并写入文件（同步写入，避免 asyncio.to_thread 在 ARM64+uvloop 下触发 native crash）
                        file_obj = tar_ref.extractfile(member)
                        if file_obj:
                            file_content = file_obj.read()
                            if len(file_content) == 0 and base_name.endswith(('.so', '.pyd')):
                                logger.warning(f"跳过 0 字节文件: {base_name}")
                                continue
                            target_path.write_bytes(file_content)
                            extracted_count += 1
                            if base_name.endswith(('.so', '.pyd')):
                                new_files.add(base_name)
                            logger.debug(f"解压: {base_name} ({len(file_content)} 字节)")
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

                        # 先写入临时目录（extract_dir），持久化后再覆盖运行目录
                        target_path = extract_dir / base_name

                        # 安全检查：确保目标路径在 extract_dir 内
                        try:
                            target_path.resolve().relative_to(extract_dir.resolve())
                        except ValueError:
                            logger.warning(f"检测到路径穿越尝试: {zip_info.filename}")
                            continue

                        # 读取并写入文件（同步写入，避免 asyncio.to_thread 在 ARM64+uvloop 下触发 native crash）
                        file_content = zip_ref.read(zip_info.filename)
                        if len(file_content) == 0 and base_name.endswith(('.so', '.pyd')):
                            logger.warning(f"跳过 0 字节文件: {base_name}")
                            continue
                        target_path.write_bytes(file_content)
                        extracted_count += 1
                        if base_name.endswith(('.so', '.pyd')):
                            new_files.add(base_name)
                        logger.debug(f"解压: {base_name} ({len(file_content)} 字节)")

        logger.info(f"解压完成（临时目录）: 共 {extracted_count} 个文件")

        if extracted_count <= 0:
            shutil.rmtree(extract_dir, ignore_errors=True)
            logger.error("解压结果为空，取消更新")
            return False

        # ========== 备份前校验：架构 / 版本 / 最低可用版本 / 哈希 ==========
        # why：备份目录是重启后恢复的唯一依据，一旦写入损坏或架构不符的包，
        # 重启后会从备份恢复出坏包，且轮询又判定需要更新 → 循环。因此必须在
        # 持久化之前校验临时目录，不通过就地清理、不污染备份。
        # 临时目录若无权威文件，会先从 package.json + versions.json 整合生成。
        if progress_callback:
            await progress_callback("正在校验新版本文件...")

        # 校验与解压共享服务实现，避免反向导入执行器。

        expected_version = str(asset_info.get('version', '')).lstrip('v')
        verify_passed, verify_errors = await verify_scraper_package(
            extract_dir,
            expected_version=expected_version or None
        )
        if not verify_passed:
            detail = "；".join(verify_errors)
            logger.error(f"新版本文件校验失败，取消更新以避免污染备份目录：{detail}")
            if progress_callback:
                await progress_callback(f"校验失败: {detail}")
            shutil.rmtree(extract_dir, ignore_errors=True)
            return False

        logger.info(f"✓ 新版本文件校验通过（{extracted_count} 个文件，版本 {expected_version or '未知'}）")

        # ========== 关键顺序（断循环）：先把新版持久化到 backup 目录，再覆盖运行目录 ==========
        # why: 只有 backup 目录（/app/config/scrapers_backup）是持久化的。必须保证在覆盖
        # 运行中的 .so（可能 native crash）之前，backup 已是新版；这样即便覆盖时崩溃，重启后
        # 恢复逻辑读到的 backup 就是新版，不会回退到旧版触发无限重启循环。
        if progress_callback:
            await progress_callback("正在备份新版本到持久化目录...")
        try:
            release_version = str(asset_info.get('version', '')).lstrip('v')
            _persist_new_version_to_backup(
                extract_dir, release_version, remote_package_json
            )
        except Exception as persist_err:
            logger.error(f"持久化新版到备份目录失败，取消覆盖运行目录以避免版本回退循环: {persist_err}", exc_info=True)
            shutil.rmtree(extract_dir, ignore_errors=True)
            return False

        # defer_overlay: 不在此处覆盖运行目录，交由调用方在「SSE 终态已发送 + 即将重启」时执行。
        # why: 覆盖正在加载的 .so 之后再跑任何业务代码都有 segfault 风险，会导致 SSE 心跳
        # 永久消失、前端卡在中间状态。此处保留临时目录供后续 _apply_deferred_overlay 使用。
        if defer_overlay:
            logger.info(f"已解压并持久化新版（{extracted_count} 个文件），覆盖运行目录已推迟至重启前")
            if progress_callback:
                await progress_callback(f"新版本已就绪: {extracted_count} 个文件")
            return True

        # 持久化完成后，才覆盖运行目录里正在被加载的 .so（危险操作放最后）
        if progress_callback:
            await progress_callback("正在应用更新...")
        overlay_count = _overlay_extract_dir_to_scrapers(extract_dir, scrapers_dir, old_files, new_files)

        # 清理临时目录
        shutil.rmtree(extract_dir, ignore_errors=True)

        logger.info(f"更新已应用到运行目录: {overlay_count} 个文件")
        if progress_callback:
            await progress_callback(f"解压完成: {extracted_count} 个文件")

        return overlay_count > 0

    except zipfile.BadZipFile:
        logger.error("ZIP 压缩包格式错误")
        return False
    except tarfile.TarError as e:
        logger.error(f"TAR 压缩包格式错误: {e}")
        return False
    except Exception as e:
        logger.error(f"下载或解压失败: {e}", exc_info=True)
        return False


