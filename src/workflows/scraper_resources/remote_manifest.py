"""
远程 Manifest 信息获取工具

统一管理从远程仓库获取版本信息的逻辑，支持多个备用文件名。
优先尝试 scraper_manifest.json，如果失败则尝试 package.json。
"""

import asyncio
import logging
from typing import Dict, Optional

import httpx

logger = logging.getLogger(__name__)


async def fetch_remote_manifest_info(
    base_url: str,
    headers: Dict[str, str],
    max_retries: int = 1,
    proxy: Optional[str] = None,
    timeout_seconds: float = 15.0,
    read_timeout_seconds: float = 8.0
) -> Optional[Dict[str, Optional[str]]]:
    """
    获取远程 manifest 信息，支持多个备用文件名
    
    优先尝试 scraper_manifest.json，如果失败则尝试 package.json。
    返回统一格式的版本信息。
    
    Args:
        base_url: 基础 URL（不含文件名），如 https://raw.githubusercontent.com/owner/repo/branch
        headers: HTTP 请求头
        max_retries: 每个文件的最大重试次数（默认1次，即只尝试一次）
        proxy: 代理 URL（可选）
        timeout_seconds: 连接超时时间（秒）
        read_timeout_seconds: 读取超时时间（秒）
    
    Returns:
        包含 version 和 minServerVersion 的字典，失败返回 None
        格式：{"version": "2.2.9", "minServerVersion": "1.0.0"}
    """
    timeout_config = httpx.Timeout(timeout_seconds, read=read_timeout_seconds)
    
    # 定义要尝试的文件名，按优先级排序
    filenames_to_try = ["scraper_manifest.json", "package.json"]
    
    # 依次尝试每个文件名
    for file_index, filename in enumerate(filenames_to_try):
        # 构建完整 URL
        manifest_url = f"{base_url.rstrip('/')}/{filename}"
        logger.debug(f"[远程版本] 尝试文件 {file_index + 1}/{len(filenames_to_try)}: {filename}")
        
        # 对当前文件进行重试
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(
                    timeout=timeout_config,
                    headers=headers,
                    follow_redirects=True,
                    proxy=proxy
                ) as client:
                    response = await client.get(manifest_url)
                    
                    if response.status_code == 200:
                        manifest_data = response.json()
                        version = manifest_data.get("version", "unknown")
                        min_server_version = manifest_data.get("min_server_version") or manifest_data.get("minServerVersion")
                        
                        logger.info(f"✓ 成功获取远程版本: {version} (文件: {filename}, 尝试 {attempt + 1}/{max_retries})")
                        
                        return {
                            "version": version,
                            "minServerVersion": min_server_version
                        }
                    else:
                        logger.debug(f"获取版本失败 HTTP {response.status_code} (文件: {filename}, 尝试 {attempt + 1}/{max_retries})")
                        
            except httpx.TimeoutException:
                logger.debug(f"连接超时 (文件: {filename}, 尝试 {attempt + 1}/{max_retries})")
            except httpx.ConnectError as e:
                logger.debug(f"连接失败: {e} (文件: {filename}, 尝试 {attempt + 1}/{max_retries})")
            except Exception as e:
                logger.debug(f"获取版本异常: {e} (文件: {filename}, 尝试 {attempt + 1}/{max_retries})")
            
            # 如果不是最后一次尝试，等待一小段时间再重试
            if attempt < max_retries - 1:
                await asyncio.sleep(0.5)
        
        # 当前文件的所有重试都失败了，记录并尝试下一个文件
        logger.debug(f"✗ 文件 {filename} 失败，已重试 {max_retries} 次")
    
    logger.warning(f"获取远程版本失败，已尝试 {len(filenames_to_try)} 个文件，每个文件重试 {max_retries} 次")
    return None


async def fetch_remote_manifest_dict(
    base_url: str,
    headers: Dict[str, str],
    max_retries: int = 1,
    proxy: Optional[str] = None,
    timeout_seconds: float = 30.0,
    read_timeout_seconds: float = 30.0
) -> Optional[Dict]:
    """
    获取远程 manifest 完整字典（用于自动更新任务）
    
    与 fetch_remote_manifest_info 类似，但返回完整的 JSON 数据而不是提取版本字段。
    
    Args:
        base_url: 基础 URL（不含文件名）
        headers: HTTP 请求头
        max_retries: 每个文件的最大重试次数
        proxy: 代理 URL（可选）
        timeout_seconds: 连接超时时间（秒）
        read_timeout_seconds: 读取超时时间（秒）
    
    Returns:
        完整的 manifest 字典，失败返回 None
    """
    timeout_config = httpx.Timeout(timeout_seconds, read=read_timeout_seconds)
    
    # 定义要尝试的文件名，按优先级排序
    filenames_to_try = ["scraper_manifest.json", "package.json"]
    
    for file_index, filename in enumerate(filenames_to_try):
        manifest_url = f"{base_url.rstrip('/')}/{filename}"
        logger.debug(f"[远程Manifest] 尝试文件 {file_index + 1}/{len(filenames_to_try)}: {filename}")
        
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(
                    timeout=timeout_config,
                    headers=headers,
                    follow_redirects=True,
                    proxy=proxy
                ) as client:
                    response = await client.get(manifest_url)
                    
                    if response.status_code == 200:
                        logger.info(f"✓ 成功获取远程 manifest (文件: {filename})")
                        return response.json()
                    else:
                        logger.debug(f"获取 manifest 失败: HTTP {response.status_code} (文件: {filename})")
                        
            except Exception as e:
                logger.debug(f"获取 manifest 失败: {e} (文件: {filename})")
            
            if attempt < max_retries - 1:
                await asyncio.sleep(0.5)
        
        logger.debug(f"✗ 文件 {filename} 失败")
    
    logger.warning(f"获取远程 manifest 失败，已尝试 {len(filenames_to_try)} 个文件")
    return None
