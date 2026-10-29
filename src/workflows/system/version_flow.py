"""
版本检查相关的业务编排层
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

import httpx

from src._version import APP_VERSION, DOCS_URL, GITHUB_OWNER, GITHUB_REPO
from src.services.config_service import ConfigService

logger = logging.getLogger(__name__)

# 版本检查缓存
_app_version_cache: Optional[Dict[str, Any]] = None
_app_version_cache_time: Optional[datetime] = None
_APP_VERSION_CACHE_DURATION = timedelta(minutes=30)


async def workflow_check_version(
    config_service: ConfigService,
    force_refresh: bool = False
) -> Dict[str, Any]:
    """
    检查是否有新版本可用
    
    从 GitHub Releases 获取最新版本信息和更新日志
    """
    global _app_version_cache, _app_version_cache_time

    # 检查缓存
    if not force_refresh and _app_version_cache and _app_version_cache_time:
        cache_age = datetime.now() - _app_version_cache_time
        if cache_age < _APP_VERSION_CACHE_DURATION:
            logger.debug(f"使用缓存的版本检查结果 (缓存时间: {cache_age.total_seconds():.1f}秒)")
            return _app_version_cache

    result = {
        "currentVersion": APP_VERSION,
        "latestVersion": None,
        "hasUpdate": False,
        "releaseUrl": None,
        "changelog": None,
        "publishedAt": None,
    }

    try:
        # 使用 _version.py 中的 GitHub 仓库信息
        api_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

        # 获取代理配置
        proxy_enabled = (await config_service.get("proxyEnabled", "false")).lower() == "true"
        proxy_url = await config_service.get("proxyUrl", "") if proxy_enabled else None

        # 获取 GitHub Token
        github_token = await config_service.get("github_token", "")
        headers = {"Accept": "application/vnd.github.v3+json"}
        if github_token:
            headers["Authorization"] = f"Bearer {github_token}"

        timeout = httpx.Timeout(10.0, connect=5.0)

        async with httpx.AsyncClient(timeout=timeout, proxy=proxy_url) as client:
            response = await client.get(api_url, headers=headers)

            if response.status_code == 200:
                release_data = response.json()

                latest_version = release_data.get("tag_name", "").lstrip("v")
                result["latestVersion"] = latest_version
                result["releaseUrl"] = release_data.get("html_url")
                result["changelog"] = release_data.get("body", "")
                result["publishedAt"] = release_data.get("published_at")

                # 比较版本号
                if latest_version and latest_version != APP_VERSION:
                    try:
                        current_parts = [int(x) for x in APP_VERSION.split(".")]
                        latest_parts = [int(x) for x in latest_version.split(".")]
                        result["hasUpdate"] = latest_parts > current_parts
                    except ValueError:
                        result["hasUpdate"] = latest_version != APP_VERSION
            else:
                logger.warning(f"获取 GitHub Release 失败: HTTP {response.status_code}")

    except Exception as e:
        logger.warning(f"检查更新失败: {e}")

    # 更新缓存
    _app_version_cache = result
    _app_version_cache_time = datetime.now()

    return result


async def workflow_get_release_history(
    config_service: ConfigService,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    获取历史版本列表和更新日志
    
    从 GitHub Releases 获取最近的版本信息
    """
    releases = []

    try:
        api_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases?per_page={limit}"

        # 获取代理配置
        proxy_enabled = (await config_service.get("proxyEnabled", "false")).lower() == "true"
        proxy_url = await config_service.get("proxyUrl", "") if proxy_enabled else None

        # 获取 GitHub Token
        github_token = await config_service.get("github_token", "")
        headers = {"Accept": "application/vnd.github.v3+json"}
        if github_token:
            headers["Authorization"] = f"Bearer {github_token}"

        timeout = httpx.Timeout(10.0, connect=5.0)

        async with httpx.AsyncClient(timeout=timeout, proxy=proxy_url) as client:
            response = await client.get(api_url, headers=headers)

            if response.status_code == 200:
                releases_data = response.json()

                for release in releases_data:
                    version = release.get("tag_name", "").lstrip("v")
                    releases.append({
                        "version": version,
                        "changelog": release.get("body", ""),
                        "publishedAt": release.get("published_at"),
                        "releaseUrl": release.get("html_url")
                    })
            else:
                logger.warning(f"获取 GitHub Releases 列表失败: HTTP {response.status_code}")

    except Exception as e:
        logger.warning(f"获取历史版本失败: {e}")

    return releases
