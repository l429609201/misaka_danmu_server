"""
弹幕源资源自动更新处理器

用于后台自动检查并更新弹幕源资源。
"""
import json
import asyncio
import logging
import hashlib
from pathlib import Path
from typing import Optional, Dict, Any

import httpx
from fastapi import FastAPI

from .base import BasePollingTask

# 复用 scraper_resources 中的工具函数
from ..api.ui.scraper_resources import (
    parse_github_url,
    parse_gitee_url,
    _build_base_url,
    get_platform_key,
    get_platform_info,
    _get_scrapers_dir,
    SCRAPERS_VERSIONS_FILE,
    SCRAPERS_PACKAGE_FILE,
    _download_lock,
    backup_scrapers,
    _fetch_github_release_asset,
    _download_and_extract_release,
)

logger = logging.getLogger("ScraperAutoUpdate")


class SystemUser:
    """用于自动更新时的虚拟用户对象"""
    username = "system_auto_update"


class ScraperAutoUpdateTask(BasePollingTask):
    """弹幕源自动更新轮询任务"""
    name = "scraper_auto_update"
    enabled_key = "scraperAutoUpdateEnabled"
    interval_key = "scraperAutoUpdateInterval"
    default_interval = 30   # 30分钟
    min_interval = 15       # 最小15分钟
    startup_delay = 60      # 启动后60秒开始

    @staticmethod
    async def handler(app: FastAPI) -> None:
        """弹幕源自动更新处理器"""
        await _scraper_auto_update_handler(app)


async def _scraper_auto_update_handler(app: FastAPI) -> None:
    """
    弹幕源自动更新处理器

    检查是否有新版本，如果有则自动下载更新。
    """
    config_manager = app.state.config_manager
    scraper_manager = app.state.scraper_manager

    # 获取资源仓库URL
    repo_url = await config_manager.get("scraper_resource_repo", "")
    if not repo_url:
        logger.debug("未配置资源仓库URL，跳过自动更新")
        return

    logger.info("开始检查弹幕源更新...")

    # 获取本地版本
    local_version = await _get_local_version()

    # 获取代理配置
    proxy_to_use = await _get_proxy_config(config_manager)

    # 解析仓库URL并获取headers
    headers, repo_info, gitee_info = await _get_repo_headers(config_manager, repo_url)
    base_url = _build_base_url(repo_info, repo_url, gitee_info)

    # 获取远程版本和包数据
    package_data = await _fetch_remote_package(base_url, headers, proxy_to_use)
    if not package_data:
        logger.debug("无法获取远程版本信息，跳过更新")
        return

    remote_version = package_data.get("version")
    if not remote_version:
        logger.debug("远程 package.json 中没有版本号")
        return

    # 比较版本
    if local_version == remote_version:
        logger.debug(f"弹幕源已是最新版本 ({local_version})")
        return

    logger.info(f"检测到新版本: {local_version} -> {remote_version}，开始自动更新...")

    # 执行更新
    await _perform_update(
        app=app,
        package_data=package_data,
        base_url=base_url,
        headers=headers,
        proxy_to_use=proxy_to_use,
        local_version=local_version,
        remote_version=remote_version,
        repo_info=repo_info
    )


async def _get_local_version() -> str:
    """获取本地版本号"""
    local_package_file = _get_scrapers_dir() / "package.json"
    if local_package_file.exists():
        try:
            local_package = json.loads(await asyncio.to_thread(local_package_file.read_text))
            return local_package.get("version", "unknown")
        except Exception as e:
            logger.warning(f"读取本地 package.json 失败: {e}")
    return "unknown"


async def _get_proxy_config(config_manager) -> Optional[str]:
    """获取代理配置"""
    proxy_url = await config_manager.get("proxyUrl", "")
    proxy_enabled_str = await config_manager.get("proxyEnabled", "false")
    proxy_enabled = proxy_enabled_str.lower() == 'true'
    return proxy_url if proxy_enabled and proxy_url else None


async def _get_repo_headers(config_manager, repo_url: str) -> tuple:
    """获取仓库请求头和解析信息

    Returns:
        tuple: (headers, repo_info, gitee_info)
    """
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

    return headers, repo_info, gitee_info


async def _fetch_remote_package(base_url: str, headers: Dict, proxy: Optional[str]) -> Optional[Dict]:
    """获取远程 package.json"""
    package_url = f"{base_url}/package.json"
    timeout = httpx.Timeout(30.0, read=30.0)

    try:
        async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True, proxy=proxy) as client:
            response = await client.get(package_url)
            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"获取远程 package.json 失败: HTTP {response.status_code}")
    except Exception as e:
        logger.warning(f"获取远程版本失败: {e}")

    return None


async def _perform_update(
    app: FastAPI,
    package_data: Dict[str, Any],
    base_url: str,
    headers: Dict,
    proxy_to_use: Optional[str],
    local_version: str,
    remote_version: str,
    repo_info: Optional[Dict] = None
) -> None:
    """执行实际的更新操作"""
    config_manager = app.state.config_manager
    scraper_manager = app.state.scraper_manager

    # 检查下载锁
    if _download_lock.locked():
        logger.info("另一个下载任务正在进行中，跳过本次更新")
        return

    async with _download_lock:
        # 备份当前文件
        try:
            await backup_scrapers(SystemUser())
            logger.info("备份当前弹幕源成功")
        except Exception as e:
            logger.error(f"备份失败，取消更新: {e}")
            return

        # 获取平台信息
        platform_key = get_platform_key()
        platform_info = get_platform_info()
        scrapers_dir = _get_scrapers_dir()

        # 检查是否启用全量替换模式
        full_replace_enabled = await config_manager.get("scraperFullReplaceEnabled", "false")
        use_full_replace = full_replace_enabled.lower() == "true"

        # ========== 全量替换模式 ==========
        if use_full_replace and repo_info:
            logger.info("使用全量替换模式，从 GitHub Releases 下载压缩包")

            asset_info = await _fetch_github_release_asset(
                repo_info=repo_info,
                platform_key=platform_key,
                headers=headers,
                proxy=proxy_to_use
            )

            if asset_info:
                success = await _download_and_extract_release(
                    asset_info=asset_info,
                    scrapers_dir=scrapers_dir,
                    headers=headers,
                    proxy=proxy_to_use
                )

                if success:
                    # 更新 versions.json
                    from datetime import datetime
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

                    # 从解压后的 versions.json 读取全局版本限制字段（覆盖前读取）
                    min_server_version = None
                    existing_versions_file = scrapers_dir / "versions.json"
                    if existing_versions_file.exists():
                        try:
                            existing_ver_data = json.loads(await asyncio.to_thread(existing_versions_file.read_text))
                            min_server_version = existing_ver_data.get('min_server_version')
                        except Exception:
                            pass
                    # package.json 也可能携带版本限制字段
                    if local_package_file.exists() and not min_server_version:
                        try:
                            pkg = json.loads(await asyncio.to_thread(local_package_file.read_text))
                            min_server_version = pkg.get('min_server_version')
                        except Exception:
                            pass

                    versions_data = {
                        "platform": platform_info['platform'],
                        "type": platform_info['arch'],
                        "version": release_version,
                        "scrapers": scrapers_versions,
                        "hashes": scrapers_hashes,
                        "full_replace": True,
                        "update_time": datetime.now().isoformat()
                    }
                    if min_server_version:
                        versions_data['min_server_version'] = min_server_version
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

                    # 全量替换模式：一定是更新已有源，需要重启容器
                    # 先备份新下载的资源到持久化目录
                    try:
                        logger.info("正在备份全量替换的资源到持久化目录...")
                        await backup_scrapers(SystemUser())
                        logger.info("全量替换资源备份完成")
                    except Exception as backup_error:
                        logger.warning(f"备份资源失败: {backup_error}")

                    # 检查是否有 Docker socket
                    from src.utils.docker_utils import is_docker_socket_available, restart_container
                    import sys
                    docker_available = is_docker_socket_available()

                    # 判断是否是首次下载（本地没有任何弹幕源）
                    existing_scrapers = set(scraper_manager.scrapers.keys())
                    is_first_download = len(existing_scrapers) == 0

                    if is_first_download:
                        # 首次下载：执行热加载
                        try:
                            await scraper_manager.load_and_sync_scrapers()
                            logger.info(f"弹幕源首次下载完成（热加载）: {release_version}")
                        except Exception as e:
                            logger.error(f"热加载失败: {e}")
                    elif docker_available:
                        # 非首次下载且有 Docker socket：重启容器
                        logger.info("全量替换完成，准备重启容器...")

                        # 刷新日志缓冲，确保日志输出
                        for handler in logging.getLogger().handlers:
                            handler.flush()
                        sys.stdout.flush()
                        sys.stderr.flush()

                        # 等待日志写入完成
                        await asyncio.sleep(1.0)

                        container_name = await config_manager.get("containerName", "misaka-danmu-server")
                        result = await restart_container(container_name)
                        if result.get("success"):
                            logger.info(f"弹幕源全量替换完成: {local_version} -> {release_version}，已向容器 '{container_name}' 发送重启指令")
                        else:
                            logger.warning(f"重启容器失败: {result.get('message')}")
                            logger.warning("⚠️ 请手动重启容器以加载新的弹幕源")
                    else:
                        # 非首次下载且没有 Docker socket：仅提示手动重启，不执行热加载
                        logger.info(f"弹幕源全量替换下载完成: {local_version} -> {release_version}")
                        logger.warning("⚠️ 未检测到 Docker 套接字，请手动重启容器以加载新的弹幕源（.so 文件需要重启才能生效）")

                    # 清除版本缓存
                    import src.api.ui.scraper_resources as sr
                    sr._version_cache = None
                    sr._version_cache_time = None
                    return
                else:
                    logger.warning("全量替换失败，回退到逐文件下载模式")
            else:
                logger.warning("未找到匹配的 Release 压缩包，回退到逐文件下载模式")

        # ========== 逐文件下载模式（默认）==========
        # 获取资源列表
        resources = package_data.get('resources', {})
        if not resources:
            logger.warning("资源包中未找到弹幕源文件")
            return

        total_count = len(resources)
        download_count = 0
        skip_count = 0
        failed_downloads = []
        versions_data = {}
        hashes_data = {}

        # 保存 package.json
        local_package_file = scrapers_dir / "package.json"
        package_json_str = json.dumps(package_data, indent=2, ensure_ascii=False)
        await asyncio.to_thread(local_package_file.write_text, package_json_str)

        # 下载文件（增加超时时间：连接30秒，读取60秒）
        download_timeout = httpx.Timeout(30.0, read=60.0)
        async with httpx.AsyncClient(timeout=download_timeout, headers=headers, follow_redirects=True, proxy=proxy_to_use) as client:
            for scraper_name, scraper_info in resources.items():
                result = await _download_single_scraper(
                    client=client,
                    scraper_name=scraper_name,
                    scraper_info=scraper_info,
                    platform_key=platform_key,
                    base_url=base_url,
                    scrapers_dir=scrapers_dir,
                    versions_data=versions_data,
                    hashes_data=hashes_data
                )

                if result == "downloaded":
                    download_count += 1
                elif result == "skipped":
                    skip_count += 1
                elif result == "failed":
                    failed_downloads.append(scraper_name)

        # 检查是否有下载失败的文件
        if failed_downloads:
            logger.warning(f"有 {len(failed_downloads)} 个文件下载失败: {failed_downloads}")
            logger.warning("由于存在下载失败，不更新版本信息，不执行重启")
            # 清除版本缓存
            import src.api.ui.scraper_resources as sr
            sr._version_cache = None
            sr._version_cache_time = None
            return  # 有失败则不继续执行

        # 如果没有成功下载任何文件，直接返回
        if download_count == 0:
            logger.info(f"没有新文件需要下载 (跳过: {skip_count})")
            # 清除版本缓存
            import src.api.ui.scraper_resources as sr
            sr._version_cache = None
            sr._version_cache_time = None
            return

        # 判断是否是首次下载（本地没有任何弹幕源）
        existing_scrapers = set(scraper_manager.scrapers.keys())
        is_first_download = len(existing_scrapers) == 0

        logger.info(f"下载完成: 下载 {download_count} 个, 跳过 {skip_count} 个")

        # 先备份新下载的资源到持久化目录（包括版本信息）
        try:
            logger.info("正在备份新下载的资源到持久化目录...")
            # 非首次下载时，传入新版本信息以保存到备份目录
            if not is_first_download:
                await backup_scrapers(
                    SystemUser(),
                    new_versions_data=versions_data,
                    new_hashes_data=hashes_data,
                    package_data=package_data
                )
            else:
                await backup_scrapers(SystemUser())
            logger.info("新资源备份完成")
        except Exception as backup_error:
            logger.warning(f"备份新资源失败: {backup_error}")

        # 只有首次下载时才保存版本信息到 scrapers 目录并执行热加载
        if is_first_download:
            # 保存版本信息
            await _save_versions(versions_data, hashes_data, platform_info, package_data, failed_downloads)
            # 首次下载：执行热加载
            try:
                await scraper_manager.load_and_sync_scrapers()
                logger.info(f"弹幕源首次下载完成（热加载）: {remote_version} (下载: {download_count})")
            except Exception as e:
                logger.error(f"热加载失败: {e}")
        else:
            # 非首次下载：不保存版本信息到 scrapers 目录，版本信息只在备份中
            # 根据是否有 Docker socket 决定重启方式
            from src.utils.docker_utils import is_docker_socket_available, restart_container
            import sys
            docker_available = is_docker_socket_available()

            if docker_available:
                # 有 Docker socket：重启容器
                logger.info("检测到弹幕源更新，准备重启容器...")

                # 刷新日志缓冲，确保日志输出
                for handler in logging.getLogger().handlers:
                    handler.flush()
                sys.stdout.flush()
                sys.stderr.flush()

                # 等待日志写入完成
                await asyncio.sleep(1.0)

                container_name = await config_manager.get("containerName", "misaka-danmu-server")
                result = await restart_container(container_name)
                if result.get("success"):
                    logger.info(f"弹幕源自动更新完成: {local_version} -> {remote_version}，已向容器 '{container_name}' 发送重启指令")
                else:
                    logger.warning(f"重启容器失败: {result.get('message')}")
                    logger.warning("⚠️ 请手动重启容器以加载新的弹幕源")
            else:
                # 没有 Docker socket：仅提示手动重启，不执行热加载
                logger.info(f"弹幕源自动更新下载完成: {local_version} -> {remote_version} (下载: {download_count}, 跳过: {skip_count})")
                logger.warning("⚠️ 未检测到 Docker 套接字，请手动重启容器以加载新的弹幕源（.so 文件需要重启才能生效）")

        # 清除版本缓存
        import src.api.ui.scraper_resources as sr
        sr._version_cache = None
        sr._version_cache_time = None



async def _download_single_scraper(
    client: httpx.AsyncClient,
    scraper_name: str,
    scraper_info: Dict,
    platform_key: str,
    base_url: str,
    scrapers_dir: Path,
    versions_data: Dict,
    hashes_data: Dict
) -> str:
    """
    下载单个弹幕源文件

    Returns:
        "downloaded" - 下载成功
        "skipped" - 跳过（哈希值相同）
        "failed" - 下载失败
    """
    try:
        # 获取当前平台的文件路径
        files = scraper_info.get('files', {})
        file_path = files.get(platform_key)

        if not file_path:
            return "failed"

        filename = Path(file_path).name
        target_path = scrapers_dir / filename

        # 获取远程文件的哈希值
        remote_hashes = scraper_info.get('hashes', {})
        remote_hash = remote_hashes.get(platform_key)

        # 检查是否需要下载
        if remote_hash and SCRAPERS_VERSIONS_FILE.exists():
            try:
                local_versions = json.loads(await asyncio.to_thread(SCRAPERS_VERSIONS_FILE.read_text))
                local_hash = local_versions.get('hashes', {}).get(scraper_name)
                if local_hash and local_hash == remote_hash:
                    versions_data[scraper_name] = scraper_info.get('version', 'unknown')
                    hashes_data[scraper_name] = remote_hash
                    return "skipped"
            except Exception:
                pass

        # 下载文件
        file_url = f"{base_url}/{file_path}"
        max_retries = 3

        for retry in range(max_retries):
            try:
                response = await asyncio.wait_for(client.get(file_url), timeout=60.0)
                if response.status_code == 200:
                    file_content = response.content

                    # 让出控制权
                    await asyncio.sleep(0)

                    # 验证哈希值（异步方式，防止阻塞事件循环）
                    if remote_hash:
                        # 将哈希计算放到线程池中执行
                        local_hash = await asyncio.to_thread(
                            lambda data: hashlib.sha256(data).hexdigest(),
                            file_content
                        )
                        if local_hash != remote_hash:
                            logger.warning(f"\t哈希验证失败 {scraper_name} (重试 {retry + 1}/{max_retries})")
                            if retry == max_retries - 1:
                                return "failed"
                            await asyncio.sleep(0)
                            continue
                        hashes_data[scraper_name] = remote_hash
                        logger.debug(f"\t哈希验证通过: {scraper_name}")

                    # 写入文件（异步方式）
                    logger.debug(f"\t写入文件: {scraper_name} ({len(file_content)} 字节)")
                    await asyncio.to_thread(target_path.write_bytes, file_content)

                    versions_data[scraper_name] = scraper_info.get('version', 'unknown')
                    logger.debug(f"✓ 成功下载: {scraper_name}")

                    # 让出控制权
                    await asyncio.sleep(0)
                    return "downloaded"
                elif retry == max_retries - 1:
                    logger.warning(f"\t下载失败 {scraper_name}: HTTP {response.status_code}")
                    return "failed"
                # 让出控制权
                await asyncio.sleep(0)
            except Exception as e:
                if retry == max_retries - 1:
                    logger.warning(f"下载 {scraper_name} 失败: {e}", exc_info=True)
                    return "failed"
                # 重试前让出控制权
                await asyncio.sleep(0.5)

        return "failed"
    except Exception as e:
        logger.error(f"处理 {scraper_name} 时出错: {e}")
        return "failed"


async def _verify_local_files_consistency() -> bool:
    """
    验证本地源文件与 versions.json 中记录的哈希值是否一致

    Returns:
        True: 一致或无法验证（versions.json 不存在等情况）
        False: 不一致
    """
    if not SCRAPERS_VERSIONS_FILE.exists():
        logger.debug("versions.json 不存在，跳过一致性检查")
        return True

    try:
        existing_versions = json.loads(await asyncio.to_thread(SCRAPERS_VERSIONS_FILE.read_text))
        existing_hashes = existing_versions.get('hashes', {})

        if not existing_hashes:
            logger.debug("versions.json 中没有哈希值记录，跳过一致性检查")
            return True

        scrapers_dir = _get_scrapers_dir()

        # 确定文件扩展名
        import platform as plat
        system = plat.system().lower()
        if system == 'windows':
            ext = '.pyd'
        else:
            ext = '.so'

        inconsistent_files = []

        for scraper_name, expected_hash in existing_hashes.items():
            # 查找对应的源文件
            # 文件名格式可能是: scraper_name.cpython-3xx-xxx.pyd/so 或 scraper_name.pyd/so
            possible_files = list(scrapers_dir.glob(f"{scraper_name}*{ext}"))

            if not possible_files:
                # 文件不存在，可能已被删除，这种情况允许更新
                logger.debug(f"源文件 {scraper_name} 不存在，跳过检查")
                continue

            # 取第一个匹配的文件
            local_file = possible_files[0]

            try:
                # 计算本地文件的哈希值
                file_content = await asyncio.to_thread(local_file.read_bytes)
                local_hash = hashlib.sha256(file_content).hexdigest()

                if local_hash != expected_hash:
                    inconsistent_files.append(scraper_name)
                    logger.warning(f"源文件 {scraper_name} 哈希值不一致: 期望 {expected_hash[:16]}..., 实际 {local_hash[:16]}...")
            except Exception as e:
                logger.warning(f"计算 {scraper_name} 哈希值失败: {e}")
                # 计算失败时不阻止更新
                continue

        if inconsistent_files:
            logger.warning(f"发现 {len(inconsistent_files)} 个源文件与 versions.json 记录不一致: {inconsistent_files}")
            return False

        logger.debug("本地源文件与 versions.json 记录一致")
        return True

    except Exception as e:
        logger.warning(f"验证本地文件一致性失败: {e}")
        # 验证失败时不阻止更新
        return True


async def _save_versions(
    versions_data: Dict,
    hashes_data: Dict,
    platform_info: Dict,
    package_data: Dict,
    failed_downloads: list
) -> None:
    """保存版本信息"""
    if not versions_data:
        return

    try:
        # 在更新前，检查本地源文件与当前 versions.json 是否一致
        is_consistent = await _verify_local_files_consistency()
        if not is_consistent:
            logger.warning("本地源文件与 versions.json 记录不一致，跳过更新版本信息文件")
            return

        # 如果有下载失败的文件，合并旧版本信息
        existing_scrapers = {}
        existing_hashes = {}
        if failed_downloads and SCRAPERS_VERSIONS_FILE.exists():
            try:
                existing_versions = json.loads(await asyncio.to_thread(SCRAPERS_VERSIONS_FILE.read_text))
                existing_scrapers = existing_versions.get('scrapers', {})
                existing_hashes = existing_versions.get('hashes', {})
            except Exception:
                pass

        merged_scrapers = {**existing_scrapers, **versions_data}
        merged_hashes = {**existing_hashes, **hashes_data}

        # 从 package_data 读取全局版本限制字段
        min_server_version = package_data.get('min_server_version')

        full_versions_data = {
            "platform": platform_info['platform'],
            "type": platform_info['arch'],
            "version": package_data.get("version", "unknown"),
            "scrapers": merged_scrapers
        }

        if merged_hashes:
            full_versions_data["hashes"] = merged_hashes
        if min_server_version:
            full_versions_data['min_server_version'] = min_server_version

        versions_json_str = json.dumps(full_versions_data, indent=2, ensure_ascii=False)
        await asyncio.to_thread(SCRAPERS_VERSIONS_FILE.write_text, versions_json_str)
        logger.debug(f"已保存 {len(merged_scrapers)} 个弹幕源的版本信息")
    except Exception as e:
        logger.warning(f"保存版本信息失败: {e}")

