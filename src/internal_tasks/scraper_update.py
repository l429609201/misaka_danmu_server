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
from src.core.env import is_docker_environment
from src.utils.scraper_version_manager import ScraperVersionManager

# 复用 scraper_resources 中的工具函数
from ..api.ui.scraper_resources import (
    parse_github_url,
    parse_gitee_url,
    _build_base_url,
    get_platform_key,
    get_platform_info,
    _get_scrapers_dir,
    _download_lock,
    backup_scrapers,
    _fetch_github_release_asset,
    _download_and_extract_release,
)

logger = logging.getLogger("ScraperAutoUpdate")


class SystemUser:
    """用于自动更新时的虚拟用户对象"""
    username = "system_auto_update"


def _get_backup_dir_path() -> Path:
    """获取持久化备份目录路径（与 scraper_resources.BACKUP_DIR 一致）。"""
    return Path("/app/config/scrapers_backup") if is_docker_environment() else Path("config/scrapers_backup")


def _get_backup_version() -> Optional[str]:
    """从备份目录 manifest 读取版本号（用于版本状态校验）。无则返回 None。"""
    try:
        backup_dir = _get_backup_dir_path()
        manifest = ScraperVersionManager.load_manifest(backup_dir)
        return ScraperVersionManager.get_version_from_manifest(manifest)
    except Exception:
        pass
    return None


def _backup_has_binaries() -> bool:
    """备份目录是否含可恢复的 .so/.pyd 文件。"""
    try:
        backup_dir = _get_backup_dir_path()
        if not backup_dir.exists():
            return False
        return any(
            f.suffix in (".so", ".pyd") for f in backup_dir.iterdir() if f.is_file()
        )
    except Exception:
        return False


def _verify_backup_version(remote_version: str) -> bool:
    """校验备份目录是否已成功落盘为指定的目标版本。

    判据（必须同时满足）：
    - 备份目录含 .so/.pyd 文件（保证重启后有可恢复的源）；
    - 备份目录 manifest 的 version == remote_version（严格版本匹配）。

    why：以“版本状态”而非“时间冷却”决策——仅在确认目标版本已真正持久化到备份目录后，
    才允许重启，避免重启后版本回退导致无限循环。
    """
    try:
        if not _backup_has_binaries():
            return False
        return _get_backup_version() == remote_version
    except Exception as e:
        logger.debug(f"校验备份版本失败（视为未就绪）: {e}")
        return False


async def _restart_to_apply_backup(config_manager, target_version: str) -> None:
    """备份目录已是目标版本、只差重启生效时，触发容器重启（不重复下载）。

    why：以版本状态决策——当上一轮已把新版本下载/上传到备份目录、但因重启失败等原因
    未生效时，无需重新下载，只要重启让 scraper_manager 从备份恢复即可。
    """
    from src.utils.docker_utils import is_docker_socket_available, is_running_in_docker, restart_container
    import sys

    docker_available = is_docker_socket_available() and is_running_in_docker()
    if not docker_available:
        logger.warning(
            f"备份目录已是目标版本 {target_version}，但未检测到 Docker 套接字，"
            f"无法自动重启。请手动重启容器以加载新弹幕源（.so 需重启生效）。"
        )
        return

    logger.info(f"备份目录已是目标版本 {target_version}，无需重复下载，准备重启容器使其生效...")
    for handler in logging.getLogger().handlers:
        handler.flush()
    sys.stdout.flush()
    sys.stderr.flush()
    await asyncio.sleep(1.0)

    container_name = await config_manager.get("containerName", "misaka-danmu-server")
    result = await restart_container(container_name)
    if result.get("success"):
        logger.info(f"已向容器 '{container_name}' 发送重启指令，重启后将从备份恢复到 {target_version}")
    else:
        logger.warning(f"重启容器失败: {result.get('message')}，请手动重启容器")




def _write_full_replace_fail_flag(flag_path: Path, error: str, version: str) -> None:
    """写入全量替换失败标志，冷却期内自动降级为增量更新。

    why：所有失败退出点都必须刷新标志时间戳。此前仅异常分支写入，
    备份校验失败等提前 return 的分支不写，导致旧标志的时间戳一直不更新，
    冷却判断失准（可能提前失效，每轮轮询都重复下载）。
    """
    try:
        from datetime import datetime
        fail_info = {
            "time": datetime.now().isoformat(),
            "error": str(error)[:200],
            "version": version or "unknown"
        }
        flag_path.parent.mkdir(parents=True, exist_ok=True)
        flag_path.write_text(json.dumps(fail_info, ensure_ascii=False))
        logger.info("已写入全量替换失败标志，冷却期内将自动降级为增量更新")
    except Exception as e:
        logger.debug(f"写入全量替换失败标志失败: {e}")


async def _restore_from_backup(scrapers_dir: Path) -> None:
    """从持久化备份目录恢复 .so/.pyd，用于全量替换失败后回滚被覆盖的运行目录。"""
    try:
        backup_dir = _get_backup_dir_path()
        if not backup_dir.exists():
            return
        import shutil
        backup_files = list(backup_dir.glob("*.so")) + list(backup_dir.glob("*.pyd"))
        if not backup_files:
            return
        for f in backup_files:
            await asyncio.to_thread(shutil.copy2, f, scrapers_dir / f.name)
        logger.info(f"已从备份恢复 {len(backup_files)} 个弹幕源文件")
    except Exception as restore_err:
        logger.error(f"从备份恢复失败: {restore_err}")


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

    # 获取远程版本和 manifest 数据
    manifest_data = await _fetch_remote_manifest(base_url, headers, proxy_to_use)
    if not manifest_data:
        logger.debug("无法获取远程 manifest 信息，跳过更新")
        return

    remote_version = ScraperVersionManager.get_version_from_manifest(manifest_data)
    if not remote_version:
        logger.debug("远程 manifest 中没有版本号")
        return

    # 前置检查：远程弹幕源包是否要求更高的服务器版本
    # why：两个字段语义相同，远端包可能只写其中之一。手动下载路径已做双字段兜底，
    # 此处若只读 min_server_version，包只写 min_fetchable_version 时校验会误放行，
    # 一路下载备份重启后才发现所有源都不满足版本 → 源全部加载失败。
    remote_min_server = manifest_data.get("min_server_version") or manifest_data.get("min_fetchable_version")
    if remote_min_server:
        from src._version import APP_VERSION
        from src.services.scraper_manager import _version_satisfies
        if not _version_satisfies(APP_VERSION, remote_min_server):
            logger.warning(
                f"远程弹幕源包要求服务器版本 >= {remote_min_server}，"
                f"当前版本 {APP_VERSION}，跳过自动更新"
            )
            return

    # 比较版本
    if local_version == remote_version:
        logger.debug(f"弹幕源已是最新版本 ({local_version})")
        return

    logger.info(f"检测到新版本: {local_version} -> {remote_version}，开始自动更新...")

    # 版本状态预校（代替时间冷却）：若备份目录已经是目标版本，说明上一轮已下载/上传好，
    # 只是尚未重启生效（如上次重启失败）。此时不重复下载，直接触发重启让备份生效即可。
    # why：以“版本状态”而非“时间”决策，既避免重复下载重启循环，又不会误伤正常更新。
    if _verify_backup_version(remote_version):
        await _restart_to_apply_backup(config_manager, remote_version)
        return

    # 执行更新
    await _perform_update(
        app=app,
        manifest_data=manifest_data,
        base_url=base_url,
        headers=headers,
        proxy_to_use=proxy_to_use,
        local_version=local_version,
        remote_version=remote_version,
        repo_info=repo_info
    )


async def _get_local_version() -> str:
    """从本地 scrapers 目录的 manifest 获取版本号"""
    try:
        scrapers_dir = _get_scrapers_dir()
        manifest = await asyncio.to_thread(
            ScraperVersionManager.load_manifest,
            scrapers_dir
        )
        version = ScraperVersionManager.get_version_from_manifest(manifest)
        return version or "unknown"
    except Exception as e:
        logger.warning(f"读取本地 manifest 版本号失败: {e}")
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


async def _fetch_remote_manifest(base_url: str, headers: Dict, proxy: Optional[str]) -> Optional[Dict]:
    """
    获取远程 manifest 信息（已废弃，使用 remote_manifest_fetcher 模块）

    为保持向后兼容，保留此函数作为适配器。
    """
    from src.utils.remote_manifest_fetcher import fetch_remote_manifest_dict

    return await fetch_remote_manifest_dict(
        base_url=base_url,
        headers=headers,
        max_retries=1,
        proxy=proxy,
        timeout_seconds=30.0,
        read_timeout_seconds=30.0
    )


async def _perform_update(
    app: FastAPI,
    manifest_data: Dict[str, Any],
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

        # 全量替换防御：检查最近是否失败过（防止 native crash 导致无限重启循环）
        FULL_REPLACE_FAIL_FLAG = Path("/app/config/full_replace_failed") if is_docker_environment() else Path("config/full_replace_failed")
        if use_full_replace and FULL_REPLACE_FAIL_FLAG.exists():
            try:
                from datetime import datetime
                fail_data = json.loads(FULL_REPLACE_FAIL_FLAG.read_text())
                fail_time = datetime.fromisoformat(fail_data.get("time", ""))
                cooldown_minutes = 60
                elapsed = (datetime.now() - fail_time).total_seconds() / 60
                if elapsed < cooldown_minutes:
                    logger.warning(
                        f"全量替换在 {int(elapsed)} 分钟前失败过，冷却期 {cooldown_minutes} 分钟内跳过本次更新。"
                        f"上次失败原因: {fail_data.get('error', '未知')}"
                    )
                    return
                else:
                    # 冷却期已过，清除标志文件
                    FULL_REPLACE_FAIL_FLAG.unlink(missing_ok=True)
                    logger.info("全量替换冷却期已过，清除失败标志")
            except Exception:
                # 标志文件格式异常，清除并继续
                FULL_REPLACE_FAIL_FLAG.unlink(missing_ok=True)

        # ========== 全量替换模式 ==========
        if use_full_replace and repo_info:
            logger.info("使用全量替换模式，从 GitHub Releases 下载压缩包")

            try:
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
                        # 更新 manifest
                        from datetime import datetime
                        release_version = asset_info['version'].lstrip('v')

                        # 部署前预检：逐个 import 解压出的 .so，确认各源 min_server_version 均满足
                        # why：包级 min_server_version 只是声明值，可能缺失或与单源类属性不一致。
                        # 此前自动更新完全没有这一步，下载解压后直接备份并重启，重启时
                        # scraper_manager 才逐个加载失败 → 所有源全废（前端弹"需要服务器版本≥x"）。
                        # 与手动下载路径共用同一探测实现，改为"预检不通过就不重启"。
                        from src.utils.scraper_download_executor import check_scraper_compat_in_dir
                        incompatible = await check_scraper_compat_in_dir(scrapers_dir)
                        if incompatible:
                            from src._version import APP_VERSION
                            detail = ", ".join(f"{k} 需要 >= {v}" for k, v in sorted(incompatible.items()))
                            logger.error(
                                f"全量替换预检失败：当前服务器版本 {APP_VERSION}，"
                                f"有 {len(incompatible)} 个弹幕源版本要求不满足（{detail}）。"
                                "为避免重启后源全部加载失败，本次不写入版本信息、不备份、不重启。"
                            )
                            # 写入失败标志，冷却期内降级为增量更新，避免每轮轮询重复下载
                            _write_full_replace_fail_flag(
                                FULL_REPLACE_FAIL_FLAG,
                                f"预检失败：{len(incompatible)} 个源要求更高服务器版本（{detail}）",
                                release_version
                            )
                            # 从备份恢复被解压覆盖掉的旧版 .so，保证当前运行的源不被破坏
                            await _restore_from_backup(scrapers_dir)
                            import src.api.ui.scraper_resources as sr
                            sr._version_cache = None
                            sr._version_cache_time = None
                            return

                        # 优先从解压后的 scraper_manifest.json 读取，回退到 package.json
                        scrapers_versions = {}
                        scrapers_hashes = {}

                        # 尝试从 manifest 读取（新架构）
                        manifest = await asyncio.to_thread(ScraperVersionManager.load_manifest, scrapers_dir)
                        if manifest and manifest.get("sources"):
                            sources = manifest.get("sources", {})
                            for scraper_name, info in sources.items():
                                if isinstance(info, dict):
                                    version = info.get("version")
                                    if version:
                                        scrapers_versions[scraper_name] = version
                                    hash_value = info.get("hash")
                                    if hash_value:
                                        scrapers_hashes[scraper_name] = hash_value
                            logger.info(f"从 scraper_manifest.json 读取: {len(scrapers_versions)} 个源版本")
                        else:
                            # 回退：从 package.json 读取（兼容旧格式的压缩包）
                            local_package_file = scrapers_dir / "package.json"
                            try:
                                if local_package_file.exists():
                                    package_content = json.loads(await asyncio.to_thread(local_package_file.read_text))
                                    resources = package_content.get('resources', {})
                                    for scraper_name, scraper_info in resources.items():
                                        if isinstance(scraper_info, dict):
                                            version = scraper_info.get('version')
                                            if version:
                                                scrapers_versions[scraper_name] = version
                                            hashes = scraper_info.get('hashes', {})
                                            if platform_key in hashes:
                                                scrapers_hashes[scraper_name] = hashes[platform_key]
                                    logger.info(f"从 package.json 读取（兼容模式）: {len(scrapers_versions)} 个源版本")
                            except Exception as e:
                                logger.warning(f"读取 package.json 中的源版本信息失败: {e}")

                        # 从解压后的 manifest 读取全局版本限制字段（覆盖前读取）
                        min_server_version = None
                        try:
                            existing_manifest = await asyncio.to_thread(
                                ScraperVersionManager.load_manifest,
                                scrapers_dir
                            )
                            if existing_manifest:
                                min_server_version = existing_manifest.get('min_server_version')
                        except Exception:
                            pass

                        # package.json 也可能携带版本限制字段作为兜底
                        if local_package_file.exists() and not min_server_version:
                            try:
                                pkg = json.loads(await asyncio.to_thread(local_package_file.read_text))
                                min_server_version = pkg.get('min_server_version') or pkg.get('min_fetchable_version')
                            except Exception:
                                pass

                        # 构建 manifest 数据
                        manifest_data = {
                            "version": release_version,
                            "platform": platform_info['platform'],
                            "arch": platform_info['arch'],
                            "resources": {},  # 全量替换模式不需要详细的 resources
                            "updated_at": datetime.now().isoformat()
                        }

                        # 添加各源的版本和哈希
                        for scraper_name, version in scrapers_versions.items():
                            manifest_data["resources"][scraper_name] = {
                                "version": version,
                                "hashes": {
                                    platform_key: scrapers_hashes.get(scraper_name, "")
                                }
                            }

                        if min_server_version:
                            manifest_data['min_server_version'] = min_server_version

                        # 保存 manifest
                        scrapers_dir = _get_scrapers_dir()
                        await asyncio.to_thread(
                            ScraperVersionManager.save_manifest,
                            scrapers_dir,
                            manifest_data
                        )
                        logger.info(f"已更新 manifest: {len(scrapers_versions)} 个源版本, {len(scrapers_hashes)} 个哈希值")

                        # 全量替换模式：一定是更新已有源，需要重启容器
                        # 先备份新下载的资源到持久化目录
                        try:
                            logger.info("正在备份全量替换的资源到持久化目录...")
                            await backup_scrapers(SystemUser())
                            logger.info("全量替换资源备份完成")
                        except Exception as backup_error:
                            logger.warning(f"备份资源失败: {backup_error}")

                        # 关键防护(重启循环)：校验备份目录是否已落盘为目标版本。
                        # 全量替换已先更新 scrapers/manifest.json(含 updated_at 与新 version)，
                        # backup_scrapers 无参复制即把新版本写入持久化备份目录；此处再校验一次，
                        # 若备份未成功落盘则绝不重启——否则重启后 scrapers 回退镜像旧版、备份也无新版，
                        # 轮询又检测到新版 → 无限下载重启循环。
                        full_replace_backup_ok = _verify_backup_version(release_version)
                        if not full_replace_backup_ok:
                            logger.error(
                                f"全量替换备份校验失败：备份目录版本未更新为 {release_version}，"
                                "为避免版本回退导致无限重启循环，本次不重启容器。请检查备份目录权限或磁盘空间。"
                            )
                            # 刷新失败标志，让冷却期从本次失败重新计时
                            _write_full_replace_fail_flag(
                                FULL_REPLACE_FAIL_FLAG,
                                f"备份校验失败：备份目录版本未更新为 {release_version}",
                                release_version
                            )
                            import src.api.ui.scraper_resources as sr
                            sr._version_cache = None
                            sr._version_cache_time = None
                            return

                        # 检查是否在 Docker 容器内且有 Docker socket
                        from src.utils.docker_utils import is_docker_socket_available, is_running_in_docker, restart_container
                        import sys
                        docker_available = is_docker_socket_available() and is_running_in_docker()

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

                        # 全量替换成功，清除失败标志
                        FULL_REPLACE_FAIL_FLAG.unlink(missing_ok=True)
                        return
                    else:
                        logger.warning("全量替换失败，回退到逐文件下载模式")
                else:
                    logger.warning("未找到匹配的 Release 压缩包，回退到逐文件下载模式")

            except Exception as full_replace_error:
                # 全量替换过程中发生异常（包括可能的 native crash 前的 Python 异常）
                logger.error(f"全量替换异常: {full_replace_error}", exc_info=True)
                # 写入失败标志文件，防止重启后立即重试导致无限重启
                _write_full_replace_fail_flag(
                    FULL_REPLACE_FAIL_FLAG,
                    str(full_replace_error),
                    asset_info.get('version', 'unknown') if isinstance(asset_info, dict) else 'unknown'
                )
                # 尝试从备份恢复
                await _restore_from_backup(_get_scrapers_dir())
                logger.warning("全量替换异常，回退到逐文件下载模式")

        # ========== 逐文件下载模式（默认）==========
        # 获取资源列表
        resources = manifest_data.get('resources', {})
        if not resources:
            logger.warning("manifest 中未找到弹幕源文件")
            return

        total_count = len(resources)
        download_count = 0
        skip_count = 0
        failed_downloads = []
        versions_data = {}
        hashes_data = {}

        # 保存 manifest 到本地
        scrapers_dir = _get_scrapers_dir()
        await asyncio.to_thread(
            ScraperVersionManager.save_manifest,
            scrapers_dir,
            manifest_data
        )

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
        # why：scrapers 目录不持久化（compose 只挂 ./config），重启后 .so 会回退到镜像旧版，
        # 只有备份目录（/app/config/scrapers_backup）持久化。必须确认新版本已落盘到备份目录，
        # 否则重启后版本回退，轮询又检测到“新版本”→再次下载重启，形成无限循环。
        backup_ok = False
        if not is_first_download:
            try:
                logger.info("正在备份新下载的资源到持久化目录...")
                # 非首次下载时，传入新版本信息以保存到备份目录
                await backup_scrapers(
                    SystemUser(),
                    new_versions_data=versions_data,
                    new_hashes_data=hashes_data,
                    manifest_data=manifest_data
                )
                # 校验备份目录 package.json 版本号是否已更新为远程版本（确认落盘成功）
                backup_ok = _verify_backup_version(remote_version)
                if backup_ok:
                    logger.info("新资源备份完成并校验通过")
                else:
                    logger.error(
                        "备份校验失败：备份目录版本未更新为远程版本，"
                        "为避免版本回退导致无限重启循环，本次不重启容器"
                    )
            except Exception as backup_error:
                logger.error(f"备份新资源失败: {backup_error}，为避免版本回退循环，本次不重启容器")
                backup_ok = False
        else:
            try:
                logger.info("正在备份新下载的资源到持久化目录...")
                await backup_scrapers(SystemUser())
                logger.info("新资源备份完成")
            except Exception as backup_error:
                logger.warning(f"备份新资源失败: {backup_error}")

        # 只有首次下载时才执行热加载
        if is_first_download:
            # 首次下载：执行热加载
            try:
                await scraper_manager.load_and_sync_scrapers()
                logger.info(f"弹幕源首次下载完成（热加载）: {remote_version} (下载: {download_count})")
            except Exception as e:
                logger.error(f"热加载失败: {e}")
        else:
            # 非首次下载：不保存版本信息到 scrapers 目录，版本信息只在备份中
            # 关键防护：备份未成功落盘时，绝不重启（否则重启后回退旧版 → 无限循环）
            if not backup_ok:
                logger.warning(
                    "⚠️ 新版本未能成功持久化到备份目录，已中止自动重启。"
                    "冷却期内不会重复尝试同版本，请检查备份目录权限或磁盘空间。"
                )
                # 清除版本缓存后直接返回，不重启
                import src.api.ui.scraper_resources as sr
                sr._version_cache = None
                sr._version_cache_time = None
                return

            # 根据是否在 Docker 容器内且有 Docker socket 决定重启方式
            from src.utils.docker_utils import is_docker_socket_available, is_running_in_docker, restart_container
            import sys
            docker_available = is_docker_socket_available() and is_running_in_docker()

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

        # 检查是否需要下载（从 manifest 读取本地哈希）
        if remote_hash:
            try:
                manifest = await asyncio.to_thread(
                    ScraperVersionManager.load_manifest,
                    scrapers_dir
                )
                local_hashes = ScraperVersionManager.get_hashes_from_manifest(manifest)
                local_hash = local_hashes.get(scraper_name)
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
    验证本地源文件与 manifest 中记录的哈希值是否一致

    Returns:
        True: 一致或无法验证（manifest 不存在等情况）
        False: 不一致
    """
    scrapers_dir = _get_scrapers_dir()

    try:
        manifest = await asyncio.to_thread(
            ScraperVersionManager.load_manifest,
            scrapers_dir
        )

        existing_hashes = ScraperVersionManager.get_hashes_from_manifest(manifest)

        if not existing_hashes:
            logger.debug("manifest 中没有哈希值记录，跳过一致性检查")
            return True

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
            logger.warning(f"发现 {len(inconsistent_files)} 个源文件与 manifest 记录不一致: {inconsistent_files}")
            return False

        logger.debug("本地源文件与 manifest 记录一致")
        return True

    except Exception as e:
        logger.warning(f"验证本地文件一致性失败: {e}")
        # 验证失败时不阻止更新
        return True




