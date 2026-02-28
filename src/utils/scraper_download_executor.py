"""
弹幕源下载执行器

将下载逻辑从 SSE 连接中解耦，实现后台独立运行
"""

import asyncio
import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from src.services import DownloadTaskManager, DownloadTask, TaskStatus, get_download_task_manager

logger = logging.getLogger(__name__)

# 下载任务状态缓存前缀和TTL
SCRAPER_DOWNLOAD_TASK_CACHE_PREFIX = "scraper_download_task_"
SCRAPER_DOWNLOAD_TASK_CACHE_TTL = 3600  # 1小时

# 临时下载目录前缀和TTL（用于部分成功时保存已下载的文件）
TEMP_DOWNLOAD_DIR_PREFIX = "temp_download_"
TEMP_DOWNLOAD_TTL_SECONDS = 3600  # 1小时

# 导入需要的工具函数（稍后从 scraper_resources.py 中提取）
SCRAPERS_DIR = Path("/app/scrapers")
SCRAPERS_VERSIONS_FILE = SCRAPERS_DIR / "versions.json"


def _get_temp_download_base_dir() -> Path:
    """获取临时下载目录的基础路径"""
    from src.api.ui.scraper_resources import _is_docker_environment
    if _is_docker_environment():
        return Path("/app/config/temp_downloads")
    else:
        return Path("config/temp_downloads")


def _get_scrapers_dir() -> Path:
    """获取弹幕源目录"""
    from src.api.ui.scraper_resources import _get_scrapers_dir as get_dir
    return get_dir()


def get_platform_key() -> str:
    """获取平台标识"""
    from src.api.ui.scraper_resources import get_platform_key as get_key
    return get_key()


def get_platform_info() -> Dict[str, str]:
    """获取平台信息"""
    from src.api.ui.scraper_resources import get_platform_info as get_info
    return get_info()


def parse_github_url(url: str):
    """解析 GitHub URL"""
    from src.api.ui.scraper_resources import parse_github_url as parse_gh
    return parse_gh(url)


def parse_gitee_url(url: str):
    """解析 Gitee URL"""
    from src.api.ui.scraper_resources import parse_gitee_url as parse_gt
    return parse_gt(url)


def _build_base_url(repo_info, repo_url: str, gitee_info, branch: str = "main") -> str:
    """构建基础 URL"""
    from src.api.ui.scraper_resources import _build_base_url as build_url
    return build_url(repo_info, repo_url, gitee_info, branch)


class ScraperDownloadExecutor:
    """弹幕源下载执行器"""

    def __init__(
        self,
        task: DownloadTask,
        config_manager,
        scraper_manager,
        current_user,
    ):
        self.task = task
        self.config_manager = config_manager
        self.scraper_manager = scraper_manager
        self.current_user = current_user
        self._task_manager = get_download_task_manager()

    def _log(self, message: str, level: str = "info"):
        """记录日志并添加到任务消息"""
        self.task.add_message(message)
        log_func = getattr(logger, level, logger.info)
        log_func(f"[任务 {self.task.task_id}] {message}")

    async def _log_async(self, message: str, level: str = "info"):
        """异步版本的日志记录（用于 progress_callback）"""
        self._log(message, level)

    async def _persist_task_status(self, status: str, need_restart: bool = False, extra_info: Optional[Dict] = None):
        """
        持久化任务状态到数据库缓存，用于容器重启后前端查询

        Args:
            status: 任务状态 (completed, failed, cancelled)
            need_restart: 是否需要重启容器
            extra_info: 额外信息
        """
        from src.db import CacheManager
        from src.db import get_db_session_factory

        try:
            cache_manager = CacheManager(get_db_session_factory())
            cache_data = {
                "task_id": self.task.task_id,
                "status": status,
                "need_restart": need_restart,
                "downloaded_count": len(self.task.progress.downloaded),
                "skipped_count": len(self.task.progress.skipped),
                "failed_count": len(self.task.progress.failed),
                "error_message": self.task.error_message,
                "completed_at": datetime.now().isoformat(),
                "extra_info": extra_info or {}
            }
            await cache_manager.set(
                SCRAPER_DOWNLOAD_TASK_CACHE_PREFIX,
                self.task.task_id,
                cache_data,
                SCRAPER_DOWNLOAD_TASK_CACHE_TTL
            )
            logger.info(f"[任务 {self.task.task_id}] 已持久化任务状态到缓存: {status}")
        except Exception as e:
            logger.warning(f"[任务 {self.task.task_id}] 持久化任务状态失败: {e}")

    async def _save_to_temp_dir(self, downloaded_files: list) -> Optional[str]:
        """
        将已下载成功的文件保存到临时目录

        Args:
            downloaded_files: 已下载成功的文件名列表

        Returns:
            临时目录路径，失败返回 None
        """
        import shutil
        from src.db import CacheManager
        from src.db import get_db_session_factory

        if not downloaded_files:
            return None

        try:
            temp_base_dir = _get_temp_download_base_dir()
            temp_base_dir.mkdir(parents=True, exist_ok=True)

            # 创建以任务ID命名的临时目录
            temp_dir = temp_base_dir / f"{TEMP_DOWNLOAD_DIR_PREFIX}{self.task.task_id}"
            temp_dir.mkdir(parents=True, exist_ok=True)

            scrapers_dir = _get_scrapers_dir()

            # 复制已下载成功的文件到临时目录
            for scraper_name in downloaded_files:
                # 查找该 scraper 的所有相关文件
                for file_path in scrapers_dir.iterdir():
                    if file_path.stem == scraper_name or file_path.name.startswith(f"{scraper_name}."):
                        dest_path = temp_dir / file_path.name
                        await asyncio.to_thread(shutil.copy2, file_path, dest_path)
                        logger.debug(f"已复制 {file_path.name} 到临时目录")

            # 在缓存中记录临时目录信息（用于后续清理和查找）
            cache_manager = CacheManager(get_db_session_factory())
            temp_info = {
                "task_id": self.task.task_id,
                "temp_dir": str(temp_dir),
                "downloaded_files": downloaded_files,
                "created_at": datetime.now().isoformat()
            }
            await cache_manager.set(
                TEMP_DOWNLOAD_DIR_PREFIX,
                self.task.task_id,
                temp_info,
                TEMP_DOWNLOAD_TTL_SECONDS
            )

            self._log(f"已将 {len(downloaded_files)} 个成功下载的文件保存到临时目录")
            logger.info(f"[任务 {self.task.task_id}] 临时目录: {temp_dir}")
            return str(temp_dir)

        except Exception as e:
            logger.warning(f"[任务 {self.task.task_id}] 保存到临时目录失败: {e}")
            return None

    async def _check_and_use_temp_files(self, to_download: list) -> list:
        """
        检查临时目录中是否有可复用的已下载文件

        Args:
            to_download: 待下载的文件列表 [(scraper_name, scraper_info, file_path, filename, remote_hash), ...]

        Returns:
            过滤后仍需下载的文件列表
        """
        import shutil
        from src.db import CacheManager
        from src.db import get_db_session_factory

        try:
            cache_manager = CacheManager(get_db_session_factory())
            temp_base_dir = _get_temp_download_base_dir()

            if not temp_base_dir.exists():
                return to_download

            # 查找所有有效的临时目录缓存
            remaining_to_download = []
            reused_count = 0
            scrapers_dir = _get_scrapers_dir()

            for item in to_download:
                scraper_name = item[0]
                remote_hash = item[4]
                reused = False

                # 遍历临时目录查找可复用的文件
                for temp_dir in temp_base_dir.iterdir():
                    if not temp_dir.is_dir() or not temp_dir.name.startswith(TEMP_DOWNLOAD_DIR_PREFIX):
                        continue

                    task_id = temp_dir.name[len(TEMP_DOWNLOAD_DIR_PREFIX):]

                    # 检查缓存是否还有效
                    temp_info = await cache_manager.get(TEMP_DOWNLOAD_DIR_PREFIX, task_id)
                    if not temp_info:
                        # 缓存已过期，清理目录
                        try:
                            await asyncio.to_thread(shutil.rmtree, temp_dir)
                            logger.debug(f"清理过期临时目录: {temp_dir}")
                        except Exception:
                            pass
                        continue

                    # 检查是否有该文件
                    if scraper_name in temp_info.get("downloaded_files", []):
                        # 查找临时目录中的文件
                        for temp_file in temp_dir.iterdir():
                            if temp_file.stem == scraper_name:
                                # 验证哈希值
                                file_hash = await self._calculate_file_hash(temp_file)
                                if file_hash == remote_hash:
                                    # 哈希匹配，复制到 scrapers 目录
                                    dest_path = scrapers_dir / temp_file.name
                                    await asyncio.to_thread(shutil.copy2, temp_file, dest_path)
                                    self.task.progress.downloaded.append(scraper_name)
                                    reused = True
                                    reused_count += 1
                                    self._log(f"复用临时文件: {scraper_name}")
                                    break
                        if reused:
                            break

                if not reused:
                    remaining_to_download.append(item)

            if reused_count > 0:
                self._log(f"从临时目录复用了 {reused_count} 个文件")

            return remaining_to_download

        except Exception as e:
            logger.warning(f"[任务 {self.task.task_id}] 检查临时文件失败: {e}")
            return to_download

    async def _calculate_file_hash(self, file_path: Path) -> str:
        """计算文件的 SHA256 哈希值"""
        def _hash():
            sha256 = hashlib.sha256()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    sha256.update(chunk)
            return sha256.hexdigest()
        return await asyncio.to_thread(_hash)

    async def _cleanup_temp_dir(self, task_id: str):
        """清理指定任务的临时目录"""
        import shutil
        from src.db import CacheManager
        from src.db import get_db_session_factory

        try:
            temp_base_dir = _get_temp_download_base_dir()
            temp_dir = temp_base_dir / f"{TEMP_DOWNLOAD_DIR_PREFIX}{task_id}"

            if temp_dir.exists():
                await asyncio.to_thread(shutil.rmtree, temp_dir)
                logger.info(f"已清理临时目录: {temp_dir}")

            # 删除缓存记录
            cache_manager = CacheManager(get_db_session_factory())
            await cache_manager.delete(TEMP_DOWNLOAD_DIR_PREFIX, task_id)

        except Exception as e:
            logger.warning(f"清理临时目录失败: {e}")

    async def execute(self):
        """执行下载任务"""
        self.task.status = TaskStatus.RUNNING
        self.task.started_at = datetime.now()
        self._task_manager.set_current_task(self.task.task_id)

        try:
            await self._do_download()
        except asyncio.CancelledError:
            self._log("任务被取消", "warning")
            self.task.status = TaskStatus.CANCELLED
            raise
        except Exception as e:
            self._log(f"任务执行失败: {e}", "error")
            self.task.status = TaskStatus.FAILED
            self.task.error_message = str(e)
        finally:
            self.task.completed_at = datetime.now()
            self._task_manager.clear_current_task()

    async def _do_download(self):
        """执行实际的下载逻辑"""
        repo_url = self.task.repo_url
        if not repo_url:
            repo_url = await self.config_manager.get("scraper_resource_repo", "")

        if not repo_url:
            raise ValueError("未配置资源仓库链接")

        branch = self.task.branch  # 获取分支
        self._log(f"开始下载，仓库: {repo_url}, 分支: {branch}")

        # 获取平台信息
        platform_key = get_platform_key()
        platform_info = get_platform_info()
        self._log(f"当前平台: {platform_key}")

        # 解析仓库 URL
        headers = {}
        repo_info = None
        gitee_info = parse_gitee_url(repo_url)

        if not gitee_info:
            try:
                repo_info = parse_github_url(repo_url)
            except ValueError:
                pass

        # GitHub Token
        if repo_info:
            github_token = await self.config_manager.get("github_token", "")
            if github_token:
                headers["Authorization"] = f"Bearer {github_token}"

        base_url = _build_base_url(repo_info, repo_url, gitee_info, branch)  # 传递分支参数

        # 代理配置
        proxy_mode = await self.config_manager.get("proxyMode", "none")

        # 兼容旧配置：如果 proxyMode 为 none 但 proxyEnabled 为 true，则使用 http_socks 模式
        if proxy_mode == "none":
            proxy_enabled_str = await self.config_manager.get("proxyEnabled", "false")
            if proxy_enabled_str.lower() == "true":
                proxy_mode = "http_socks"

        proxy_to_use = None

        # 只有 http_socks 模式才需要设置 httpx 的 proxy 参数
        if proxy_mode == "http_socks":
            proxy_url = await self.config_manager.get("proxyUrl", "")
            proxy_to_use = proxy_url if proxy_url else None

        if proxy_to_use:
            self._log(f"使用代理: {proxy_to_use}")

        # 检查是否使用全量替换
        if self.task.use_full_replace:
            await self._do_full_replace(repo_info, gitee_info, headers, proxy_to_use, platform_key)
        else:
            await self._do_incremental_download(base_url, headers, proxy_to_use, platform_key, platform_info)

    async def _do_full_replace(self, repo_info, gitee_info, headers, proxy_to_use, platform_key):
        """全量替换模式"""
        from src.api.ui.scraper_resources import (
            _fetch_github_release_asset,
            _fetch_gitee_release_asset,
            _download_and_extract_release,
            backup_scrapers,
            restore_scrapers,
        )

        self._log("使用全量替换模式")

        # 获取 Release 资产信息
        asset_info = None
        if gitee_info:
            self._log("正在从 Gitee Releases 获取压缩包...")
            asset_info = await _fetch_gitee_release_asset(
                gitee_info=gitee_info,
                platform_key=platform_key,
                headers=headers,
                proxy=proxy_to_use
            )
        elif repo_info:
            self._log("正在从 GitHub Releases 获取压缩包...")
            asset_info = await _fetch_github_release_asset(
                repo_info=repo_info,
                platform_key=platform_key,
                headers=headers,
                proxy=proxy_to_use
            )

        if not asset_info:
            raise ValueError("未找到匹配的 Release 压缩包")

        self._log(f"找到压缩包: {asset_info['filename']} (版本: {asset_info['version']})")

        # 备份当前文件
        self._log("正在备份当前弹幕源...")
        await backup_scrapers(self.current_user)
        self._log("备份完成")

        # 下载并解压
        scrapers_dir = _get_scrapers_dir()
        self._log("正在下载压缩包...")

        success = await _download_and_extract_release(
            asset_info=asset_info,
            scrapers_dir=scrapers_dir,
            headers=headers,
            proxy=proxy_to_use,
            progress_callback=self._log_async
        )

        if not success:
            # 下载失败，还原备份
            self._log("全量替换失败，正在还原备份...", "error")
            await restore_scrapers(self.current_user, self.scraper_manager)
            self._log("已还原备份")
            raise ValueError("全量替换失败")

        # 下载成功，更新 versions.json
        self._log("正在更新版本信息...")
        await self._update_versions_json(asset_info, scrapers_dir, platform_key)

        # 清除版本缓存，让前端能获取到最新版本号
        self._clear_version_cache()

        self.task.progress.current = 1
        self.task.progress.total = 1
        self.task.progress.downloaded.append("full_replace")
        self._log("全量替换完成")

        # 备份新下载的资源
        self._log("正在备份新下载的资源...")
        await backup_scrapers(self.current_user)
        self._log("✓ 新资源备份完成")

        # 判断是否是首次下载（本地没有任何弹幕源）
        existing_scrapers = set(self.scraper_manager.scrapers.keys())
        is_first_download = len(existing_scrapers) == 0

        if is_first_download:
            # 首次下载：执行热加载
            self._log("检测到首次下载弹幕源，正在热加载...")
            logger.info(f"用户 '{self.current_user.username}' 首次通过全量替换模式下载了弹幕源，正在热加载")
            await self.scraper_manager.load_and_sync_scrapers()
            self._log("✓ 弹幕源加载完成")
        else:
            # 非首次下载：检查是否有 Docker socket，决定重启方式
            from src.utils.docker_utils import is_docker_socket_available, restart_container
            docker_available = is_docker_socket_available()

            if docker_available:
                # 有 Docker socket，执行容器级别重启
                from src.utils.docker_utils import get_current_container_id
                detected_id = get_current_container_id()

                self._log("⚠️ 全量替换后需要重启容器以加载新的 .so 文件")
                if detected_id:
                    self._log(f"检测到当前容器 ID: {detected_id}")
                    logger.info(f"自动检测到当前容器 ID: {detected_id}")
                else:
                    fallback_name = await self.config_manager.get("containerName", "misaka_danmu_server")
                    self._log(f"未能自动检测容器 ID，将使用兜底名称: {fallback_name}")
                    logger.info(f"未能自动检测容器 ID，将使用兜底名称: {fallback_name}")

                self._log("将在 3 秒后重启容器...")
                logger.info(f"用户 '{self.current_user.username}' 通过全量替换模式更新了弹幕源，即将重启容器")

                # 先设置任务状态为完成（但不设置 restart_pending，让 SSE 继续发送日志）
                self.task.need_restart = True
                self.task.status = TaskStatus.COMPLETED

                # 持久化任务状态到缓存（容器重启后前端可查询）
                await self._persist_task_status("completed", need_restart=True)

                # 等待 1 秒让 SSE 发送最新的日志消息（SSE 每 0.5 秒轮询一次）
                await asyncio.sleep(1.0)

                # 现在设置 restart_pending，让 SSE 发送终止消息并退出
                self.task.restart_pending = True

                # 刷新日志缓冲区，确保日志输出
                import sys
                for handler in logging.getLogger().handlers:
                    handler.flush()
                sys.stdout.flush()
                sys.stderr.flush()

                # 等待 SSE 发送 done 消息（再等 2 秒）
                logger.info(f"[任务 {self.task.task_id}] 等待 SSE 发送终止消息...")
                for handler in logging.getLogger().handlers:
                    handler.flush()
                await asyncio.sleep(2.0)
                logger.info(f"[任务 {self.task.task_id}] SSE 终止消息已发送，准备重启容器")
                for handler in logging.getLogger().handlers:
                    handler.flush()

                fallback_name = await self.config_manager.get("containerName", "misaka_danmu_server")

                # 在重启前再次刷新所有日志
                logger.info(f"[任务 {self.task.task_id}] 正在发送容器重启指令...")
                for handler in logging.getLogger().handlers:
                    handler.flush()
                sys.stdout.flush()
                sys.stderr.flush()

                result = await restart_container(fallback_name)
                # 注意：如果重启成功，下面的代码可能不会执行（进程被杀死）
                if result.get("success"):
                    container_id = result.get("container_id", "unknown")
                    logger.info(f"✓ 已向容器发送重启指令 (ID: {container_id})")
                    # 刷新日志
                    for handler in logging.getLogger().handlers:
                        handler.flush()
                    sys.stdout.flush()
                    sys.stderr.flush()
                else:
                    self._log(f"重启容器失败: {result.get('message')}")
                    logger.warning(f"重启容器失败: {result.get('message')}")
                    # 重启失败时提示用户手动重启
                    self._log("⚠️ 请手动重启容器以加载新的弹幕源")
                    # 清除重启标记
                    self.task.restart_pending = False

                # 任务状态已在上面设置，直接返回
                return
            else:
                # 非首次下载且没有 Docker socket：提示手动重启，不执行热加载
                self._log("⚠️ 未检测到 Docker 套接字，无法自动重启容器")
                self._log("⚠️ 请手动重启容器以加载新的弹幕源（.so 文件需要重启才能生效）")
                logger.info(f"用户 '{self.current_user.username}' 通过全量替换模式更新了弹幕源，需要手动重启容器")

        self.task.status = TaskStatus.COMPLETED

    async def _do_incremental_download(self, base_url, headers, proxy_to_use, platform_key, platform_info):
        """增量下载模式"""
        from src.api.ui.scraper_resources import backup_scrapers, restore_scrapers, BACKUP_DIR

        # 下载 package.json
        package_url = f"{base_url}/package.json"
        self._log("正在获取资源包信息...")

        timeout_config = httpx.Timeout(30.0, read=30.0)
        package_data = await self._fetch_package_json(package_url, headers, proxy_to_use, timeout_config)

        if not package_data:
            raise ValueError("获取资源包信息失败")

        # 获取资源列表
        resources = package_data.get('resources', {})
        if not resources:
            raise ValueError("资源包中未找到弹幕源文件")

        total_count = len(resources)
        self._log(f"检测到 {total_count} 个弹幕源，正在比对哈希值...")

        # 比对哈希值
        scrapers_dir = _get_scrapers_dir()
        to_download, to_skip, unsupported, versions_data, hashes_data = await self._compare_hashes(
            resources, platform_key, scrapers_dir
        )

        skip_count = len(to_skip)
        need_download_count = len(to_download)
        self._log(f"比对完成: 需要下载 {need_download_count} 个，跳过 {skip_count} 个，不支持 {len(unsupported)} 个")

        self.task.progress.total = need_download_count
        self.task.progress.skipped = to_skip

        # 如果没有需要下载的文件
        if need_download_count == 0:
            self._log("所有弹幕源都是最新的，无需下载")
            self.task.need_restart = False
            self.task.status = TaskStatus.COMPLETED

            # 等待 SSE 发送最新的进度消息
            await asyncio.sleep(1.0)

            # 设置 restart_pending 让 SSE 发送 done 消息并退出
            self.task.restart_pending = True
            logger.info(f"[任务 {self.task.task_id}] 所有弹幕源已是最新，设置 restart_pending=True，等待 SSE 发送 done 消息")

            # 等待 SSE 发送 done 消息
            await asyncio.sleep(2.0)
            return

        # 创建临时下载目录
        import shutil
        temp_dir = _get_temp_download_base_dir() / f"download_{self.task.task_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        self._log(f"创建临时下载目录: {temp_dir}")

        try:
            # 先备份当前文件（在修改任何文件之前备份，以便失败时恢复）
            self._log("正在备份当前弹幕源...")
            await backup_scrapers(self.current_user)
            self._log("备份完成")

            self._log(f"开始下载 {need_download_count} 个文件到临时目录...")

            # 下载文件到临时目录
            download_timeout = httpx.Timeout(30.0, read=60.0)
            failed_downloads = []

            for index, (scraper_name, scraper_info, file_path, filename, remote_hash) in enumerate(to_download, 1):
                # 检查是否被取消
                if self.task.is_cancelled():
                    self._log("任务被取消，停止下载")
                    break

                self.task.progress.current = index
                self.task.progress.current_file = scraper_name
                self._log(f"正在下载 [{index}/{need_download_count}]: {scraper_name}")

                try:
                    success = await self._download_single_file(
                        scraper_name, scraper_info, file_path, filename, remote_hash,
                        base_url, headers, proxy_to_use, download_timeout, temp_dir,
                        versions_data, hashes_data
                    )
                    if success:
                        self.task.progress.downloaded.append(scraper_name)
                    else:
                        failed_downloads.append(scraper_name)
                        self.task.progress.failed.append(scraper_name)
                except Exception as e:
                    self._log(f"下载 {scraper_name} 失败: {e}", "error")
                    failed_downloads.append(scraper_name)
                    self.task.progress.failed.append(scraper_name)

            download_count = len(self.task.progress.downloaded)
            self._log(f"下载完成: 成功 {download_count}/{need_download_count} 个，跳过 {skip_count} 个，失败 {len(failed_downloads)} 个")

            # 检查下载结果：有失败时还原备份
            if failed_downloads:
                self._log(f"有 {len(failed_downloads)} 个弹幕源下载失败: {', '.join(failed_downloads)}", "error")
                self._log("正在还原备份...")
                await restore_scrapers(self.current_user, self.scraper_manager)
                self._log("已还原备份")
                self.task.status = TaskStatus.FAILED
                self.task.error_message = f"下载失败: {', '.join(failed_downloads)}"
                return

            if download_count == 0:
                self._log("没有成功下载的弹幕源", "warning")
                self.task.need_restart = False
                self.task.status = TaskStatus.COMPLETED

                # 等待 SSE 发送最新的进度消息
                await asyncio.sleep(1.0)

                # 设置 restart_pending 让 SSE 发送 done 消息并退出
                self.task.restart_pending = True
                logger.info(f"[任务 {self.task.task_id}] 无成功下载，设置 restart_pending=True，等待 SSE 发送 done 消息")

                # 等待 SSE 发送 done 消息
                await asyncio.sleep(2.0)
                return

            # 判断是否是首次下载（本地没有任何弹幕源）
            existing_scrapers = set(self.scraper_manager.scrapers.keys())
            is_first_download = len(existing_scrapers) == 0

            # 检查是否有 Docker socket
            from src.utils.docker_utils import is_docker_socket_available, restart_container
            docker_available = is_docker_socket_available()

            if is_first_download:
                # 首次下载（本地没有弹幕源）：部署到 scrapers 和 backup 目录，然后热加载
                self._log("正在部署下载的文件...")
                deployed, deploy_failed = await self._deploy_downloaded_files(
                    temp_dir, scrapers_dir, BACKUP_DIR,
                    self.task.progress.downloaded, hashes_data
                )

                if deploy_failed:
                    self._log(f"部署失败: {', '.join(deploy_failed)}", "error")
                    self._log("正在还原备份...")
                    await restore_scrapers(self.current_user, self.scraper_manager)
                    self._log("已还原备份")
                    self.task.status = TaskStatus.FAILED
                    self.task.error_message = f"部署失败: {', '.join(deploy_failed)}"
                    return

                deploy_count = len(deployed)
                self._log(f"✓ 成功部署 {deploy_count} 个弹幕源")

                # 更新版本信息
                await self._update_version_files(scrapers_dir, BACKUP_DIR, package_data, versions_data, hashes_data, platform_info)

                # 清除版本缓存
                self._clear_version_cache()

                # 执行热加载
                self._log("检测到首次下载弹幕源，正在热加载...")
                logger.info(f"用户 '{self.current_user.username}' 首次下载了 {deploy_count} 个弹幕源，正在热加载")
                await self.scraper_manager.load_and_sync_scrapers()
                self._log(f"✓ 成功加载了 {deploy_count} 个弹幕源")

                # 先清理临时目录（在设置 COMPLETED 之前，确保 SSE 发送的最后消息是完成消息）
                if temp_dir.exists():
                    try:
                        import shutil
                        shutil.rmtree(temp_dir)
                        self._log("✓ 已清理临时下载目录")
                    except Exception as e:
                        logger.warning(f"清理临时目录失败: {e}")

                # 发送完成消息
                self._log("✓ 弹幕源热加载完成，正在刷新页面...")

                # 首次下载完成，设置任务状态为完成
                # 注意：need_restart = False 表示不需要重启容器
                self.task.need_restart = False
                self.task.status = TaskStatus.COMPLETED

                # 等待 1 秒让 SSE 发送最新的日志消息
                await asyncio.sleep(1.0)

                # 设置 restart_pending，让 SSE 发送终止消息并退出
                # 这里复用 restart_pending 标志，但 need_restart = False
                # SSE 流会检测到 restart_pending 并发送 done 消息
                self.task.restart_pending = True
                logger.info(f"[任务 {self.task.task_id}] 热加载完成，设置 restart_pending=True，等待 SSE 发送 done 消息")

                # 等待 SSE 发送 done 消息
                await asyncio.sleep(2.0)
                logger.info(f"[任务 {self.task.task_id}] SSE done 消息应该已发送")

            else:
                # 非首次下载（已有弹幕源）：只部署到 backup 目录，然后重启容器
                # 这样可以避免在运行时替换 .so 文件导致的冲突
                self._log("检测到已有弹幕源，只部署到备份目录...")
                deployed, deploy_failed = await self._deploy_to_backup_only(
                    temp_dir, BACKUP_DIR,
                    self.task.progress.downloaded, hashes_data
                )

                if deploy_failed:
                    self._log(f"部署到备份目录失败: {', '.join(deploy_failed)}", "error")
                    self.task.status = TaskStatus.FAILED
                    self.task.error_message = f"部署失败: {', '.join(deploy_failed)}"
                    return

                deploy_count = len(deployed)
                self._log(f"✓ 成功部署 {deploy_count} 个弹幕源到备份目录")

                # 更新版本信息到 backup 目录（不更新 scrapers 目录，重启后会从 backup 恢复）
                await self._update_version_files_backup_only(BACKUP_DIR, package_data, versions_data, hashes_data, platform_info)

                # 清除版本缓存
                self._clear_version_cache()

                if docker_available:
                    # 有 Docker socket，执行容器级别重启
                    from src.utils.docker_utils import get_current_container_id
                    detected_id = get_current_container_id()

                    self._log("⚠️ 检测到弹幕源更新，需要重启容器以加载新的 .so 文件")
                    if detected_id:
                        self._log(f"检测到当前容器 ID: {detected_id}")
                        logger.info(f"自动检测到当前容器 ID: {detected_id}")
                    else:
                        fallback_name = await self.config_manager.get("containerName", "misaka_danmu_server")
                        self._log(f"未能自动检测容器 ID，将使用兜底名称: {fallback_name}")
                        logger.info(f"未能自动检测容器 ID，将使用兜底名称: {fallback_name}")

                    self._log("将在 3 秒后重启容器...")
                    logger.info(f"用户 '{self.current_user.username}' 增量更新了 {deploy_count} 个弹幕源，即将重启容器")

                    # 先设置任务状态为完成
                    self.task.need_restart = True
                    self.task.status = TaskStatus.COMPLETED

                    # 持久化任务状态到缓存
                    await self._persist_task_status("completed", need_restart=True)

                    # 等待 1 秒让 SSE 发送最新的日志消息
                    await asyncio.sleep(1.0)

                    # 设置 restart_pending，让 SSE 发送终止消息并退出
                    self.task.restart_pending = True

                    # 刷新日志缓冲区
                    import sys
                    for handler in logging.getLogger().handlers:
                        handler.flush()
                    sys.stdout.flush()
                    sys.stderr.flush()

                    # 等待 SSE 发送 done 消息
                    logger.info(f"[任务 {self.task.task_id}] 等待 SSE 发送终止消息...")
                    for handler in logging.getLogger().handlers:
                        handler.flush()
                    await asyncio.sleep(2.0)
                    logger.info(f"[任务 {self.task.task_id}] SSE 终止消息已发送，准备重启容器")
                    for handler in logging.getLogger().handlers:
                        handler.flush()

                    fallback_name = await self.config_manager.get("containerName", "misaka_danmu_server")

                    # 在重启前再次刷新所有日志
                    logger.info(f"[任务 {self.task.task_id}] 正在发送容器重启指令...")
                    for handler in logging.getLogger().handlers:
                        handler.flush()
                    sys.stdout.flush()
                    sys.stderr.flush()

                    result = await restart_container(fallback_name)
                    # 注意：如果重启成功，下面的代码可能不会执行（进程被杀死）
                    if result.get("success"):
                        container_id = result.get("container_id", "unknown")
                        logger.info(f"✓ 已向容器发送重启指令 (ID: {container_id})")
                        for handler in logging.getLogger().handlers:
                            handler.flush()
                        sys.stdout.flush()
                        sys.stderr.flush()
                    else:
                        self._log(f"重启容器失败: {result.get('message')}")
                        logger.warning(f"重启容器失败: {result.get('message')}")
                        self._log("⚠️ 请手动重启容器以加载新的弹幕源")
                        self.task.restart_pending = False

                    # 任务状态已在上面设置，直接返回
                    return
                else:
                    # 没有 Docker socket：提示手动重启
                    self._log("⚠️ 未检测到 Docker 套接字，无法自动重启容器")
                    self._log("⚠️ 请手动重启容器以加载新的弹幕源（.so 文件需要重启才能生效）")
                    logger.info(f"用户 '{self.current_user.username}' 更新了 {deploy_count} 个弹幕源，需要手动重启容器")

            self.task.need_restart = False
            self.task.status = TaskStatus.COMPLETED

            # 等待 SSE 发送最新的进度消息
            await asyncio.sleep(1.0)

            # 设置 restart_pending 让 SSE 发送 done 消息并退出
            self.task.restart_pending = True
            logger.info(f"[任务 {self.task.task_id}] 任务完成，设置 restart_pending=True，等待 SSE 发送 done 消息")

            # 等待 SSE 发送 done 消息
            await asyncio.sleep(2.0)

        finally:
            # 清理临时下载目录（如果还存在的话）
            # 注意：热加载场景下，临时目录已在设置 COMPLETED 之前清理，这里不会重复清理
            if temp_dir.exists():
                try:
                    import shutil
                    shutil.rmtree(temp_dir)
                    # 不发送日志消息，避免在 COMPLETED 状态后添加新消息影响 SSE 流
                    logger.info(f"[任务 {self.task.task_id}] 已清理临时下载目录")
                except Exception as e:
                    logger.warning(f"清理临时目录失败: {e}")

    async def _fetch_package_json(self, package_url, headers, proxy_to_use, timeout_config):
        """获取 package.json"""
        max_retries = 3
        self._log(f"正在访问: {package_url}")  # 添加 URL 日志

        for retry in range(max_retries + 1):
            try:
                if retry > 0:
                    wait_time = min(2 ** retry, 8)
                    self._log(f"获取资源包信息重试 {retry}/{max_retries}，等待 {wait_time} 秒...")
                    await asyncio.sleep(wait_time)

                async with httpx.AsyncClient(timeout=timeout_config, headers=headers, follow_redirects=True, proxy=proxy_to_use) as client:
                    response = await client.get(package_url)
                    if response.status_code == 200:
                        self._log("成功获取资源包信息")
                        return response.json()
                    else:
                        self._log(f"获取资源包信息失败: HTTP {response.status_code}", "warning")
                        # 添加响应内容日志
                        try:
                            error_text = response.text[:500]  # 只记录前500字符
                            self._log(f"响应内容: {error_text}", "warning")
                        except:
                            pass
            except Exception as e:
                self._log(f"获取资源包信息异常: {e}", "warning")
                logger.error(f"获取 package.json 异常 (URL: {package_url}): {e}", exc_info=True)

        self._log(f"获取资源包信息失败，已重试 {max_retries} 次", "error")
        return None

    async def _compare_hashes(self, resources, platform_key, scrapers_dir):
        """比对哈希值，确定需要下载的文件"""
        to_download = []
        to_skip = []
        unsupported = []
        versions_data = {}
        hashes_data = {}

        # 读取本地 versions.json
        # 优先从 backup 目录读取（因为非首次下载时只更新 backup 目录）
        # 如果 backup 目录没有，再从 scrapers 目录读取
        local_hashes = {}
        from src.api.ui.scraper_resources import BACKUP_DIR
        backup_versions_file = BACKUP_DIR / "versions.json"
        scrapers_versions_file = scrapers_dir / "versions.json"

        # 选择更新的 versions.json 文件
        versions_file = None
        if backup_versions_file.exists() and scrapers_versions_file.exists():
            # 两个都存在，比较 updated_at 时间戳，选择更新的
            try:
                backup_data = json.loads(await asyncio.to_thread(backup_versions_file.read_text))
                scrapers_data = json.loads(await asyncio.to_thread(scrapers_versions_file.read_text))
                backup_time = backup_data.get('updated_at', '')
                scrapers_time = scrapers_data.get('updated_at', '')
                if backup_time >= scrapers_time:
                    versions_file = backup_versions_file
                    self._log("使用备份目录的版本信息（更新）")
                else:
                    versions_file = scrapers_versions_file
                    self._log("使用 scrapers 目录的版本信息")
            except Exception:
                versions_file = backup_versions_file if backup_versions_file.exists() else scrapers_versions_file
        elif backup_versions_file.exists():
            versions_file = backup_versions_file
            self._log("使用备份目录的版本信息")
        elif scrapers_versions_file.exists():
            versions_file = scrapers_versions_file

        if versions_file and versions_file.exists():
            try:
                local_versions = json.loads(await asyncio.to_thread(versions_file.read_text))
                local_hashes = local_versions.get('hashes', {})
                self._log(f"已读取本地版本信息，包含 {len(local_hashes)} 个哈希值")
                # 调试：显示本地哈希值的 key
                if local_hashes:
                    self._log(f"本地哈希值 keys: {list(local_hashes.keys())[:5]}...", "debug")
            except Exception as e:
                self._log(f"读取本地版本文件失败: {e}", "warning")

        for scraper_name, scraper_info in resources.items():
            files = scraper_info.get('files', {})
            file_path = files.get(platform_key)

            if not file_path:
                unsupported.append(scraper_name)
                continue

            filename = Path(file_path).name
            remote_hashes = scraper_info.get('hashes', {})
            remote_hash = remote_hashes.get(platform_key)
            local_hash = local_hashes.get(scraper_name)
            version = scraper_info.get('version', 'unknown')

            if remote_hash and local_hash and local_hash == remote_hash:
                to_skip.append(scraper_name)
                versions_data[scraper_name] = version
                hashes_data[scraper_name] = remote_hash
            else:
                to_download.append((scraper_name, scraper_info, file_path, filename, remote_hash))
                # 调试日志：显示哈希不匹配的原因
                if not remote_hash:
                    self._log(f"  {scraper_name}: 远程无哈希值", "debug")
                elif not local_hash:
                    self._log(f"  {scraper_name}: 本地无哈希值", "debug")
                else:
                    self._log(f"  {scraper_name}: 哈希不匹配 (本地: {local_hash[:16]}..., 远程: {remote_hash[:16]}...)", "debug")

        return to_download, to_skip, unsupported, versions_data, hashes_data

    async def _download_single_file(
        self, scraper_name, scraper_info, file_path, filename, remote_hash,
        base_url, headers, proxy_to_use, timeout_config, temp_dir,
        versions_data, hashes_data
    ):
        """下载单个文件到临时目录"""
        file_url = f"{base_url}/{file_path}"
        target_path = temp_dir / filename
        max_retries = 3

        for retry in range(max_retries + 1):
            try:
                if retry > 0:
                    wait_time = min(2 ** (retry - 1), 10)
                    self._log(f"重试下载 {scraper_name} ({retry}/{max_retries})，等待 {wait_time} 秒...")
                    await asyncio.sleep(wait_time)

                async with httpx.AsyncClient(timeout=timeout_config, headers=headers, follow_redirects=True, proxy=proxy_to_use) as client:
                    response = await asyncio.wait_for(client.get(file_url), timeout=60.0)

                if response.status_code == 200:
                    file_content = response.content

                    # 验证哈希值
                    if remote_hash:
                        local_hash = await asyncio.to_thread(
                            lambda data: hashlib.sha256(data).hexdigest(),
                            file_content
                        )
                        if local_hash != remote_hash:
                            self._log(f"{scraper_name} 哈希验证失败", "warning")
                            if retry == max_retries:
                                return False
                            continue
                        hashes_data[scraper_name] = remote_hash

                    # 写入临时目录
                    await asyncio.to_thread(target_path.write_bytes, file_content)

                    version = scraper_info.get('version', 'unknown')
                    versions_data[scraper_name] = version
                    self._log(f"✓ 成功下载: {filename} (版本: {version}, 大小: {len(file_content)} 字节)")
                    return True
                else:
                    self._log(f"下载 {scraper_name} 返回 HTTP {response.status_code}", "warning")

            except (httpx.TimeoutException, asyncio.TimeoutError) as e:
                self._log(f"下载 {scraper_name} 超时", "warning")
            except Exception as e:
                self._log(f"下载 {scraper_name} 异常: {e}", "warning")

        self._log(f"✗ 下载失败: {scraper_name} (已重试 {max_retries} 次)", "error")
        return False

    async def _verify_file_hash(self, file_path: Path, expected_hash: str) -> bool:
        """校验文件哈希值"""
        if not file_path.exists():
            return False
        try:
            content = await asyncio.to_thread(file_path.read_bytes)
            actual_hash = hashlib.sha256(content).hexdigest()
            return actual_hash == expected_hash
        except Exception as e:
            logger.warning(f"校验文件哈希失败 {file_path}: {e}")
            return False

    async def _copy_and_verify(self, src_path: Path, dst_path: Path, expected_hash: str, scraper_name: str) -> bool:
        """复制文件并校验哈希值"""
        import shutil
        try:
            # 复制文件
            await asyncio.to_thread(shutil.copy2, src_path, dst_path)

            # 校验哈希
            if not await self._verify_file_hash(dst_path, expected_hash):
                self._log(f"复制后校验失败: {scraper_name} -> {dst_path}", "error")
                # 删除损坏的文件
                if dst_path.exists():
                    dst_path.unlink()
                return False
            return True
        except Exception as e:
            self._log(f"复制文件失败 {scraper_name}: {e}", "error")
            return False

    async def _deploy_downloaded_files(
        self,
        temp_dir: Path,
        scrapers_dir: Path,
        backup_dir: Path,
        downloaded_scrapers: list,
        hashes_data: dict
    ) -> tuple[list, list]:
        """
        将临时目录中的文件部署到 scrapers 和 backup 目录

        Returns:
            (成功部署的列表, 部署失败的列表)
        """
        import shutil

        deployed = []
        failed = []

        # 确保目录存在
        scrapers_dir.mkdir(parents=True, exist_ok=True)
        backup_dir.mkdir(parents=True, exist_ok=True)

        for scraper_name in downloaded_scrapers:
            # 查找临时目录中的文件
            temp_files = list(temp_dir.glob(f"{scraper_name}.*"))
            if not temp_files:
                self._log(f"临时目录中未找到 {scraper_name} 的文件", "warning")
                failed.append(scraper_name)
                continue

            temp_file = temp_files[0]
            filename = temp_file.name
            expected_hash = hashes_data.get(scraper_name)

            if not expected_hash:
                self._log(f"{scraper_name} 没有哈希值，跳过校验", "warning")
                failed.append(scraper_name)
                continue

            # 1. 校验临时文件哈希
            if not await self._verify_file_hash(temp_file, expected_hash):
                self._log(f"临时文件校验失败: {scraper_name}", "error")
                failed.append(scraper_name)
                continue

            # 2. 复制到 scrapers 目录并校验
            scrapers_target = scrapers_dir / filename
            if not await self._copy_and_verify(temp_file, scrapers_target, expected_hash, scraper_name):
                failed.append(scraper_name)
                continue

            # 3. 复制到 backup 目录并校验
            backup_target = backup_dir / filename
            if not await self._copy_and_verify(temp_file, backup_target, expected_hash, scraper_name):
                # 回滚 scrapers 目录的文件
                if scrapers_target.exists():
                    scrapers_target.unlink()
                failed.append(scraper_name)
                continue

            deployed.append(scraper_name)
            self._log(f"✓ 已部署: {scraper_name}")

        # 刷新日志缓冲区，确保部署日志输出
        import sys
        for handler in logging.getLogger().handlers:
            handler.flush()
        sys.stdout.flush()
        sys.stderr.flush()

        return deployed, failed

    async def _deploy_to_backup_only(
        self,
        temp_dir: Path,
        backup_dir: Path,
        downloaded_scrapers: list,
        hashes_data: dict
    ) -> tuple[list, list]:
        """
        只将临时目录中的文件部署到 backup 目录（不部署到 scrapers 目录）
        用于非首次下载时，避免在运行时替换 .so 文件导致的冲突

        Returns:
            (成功部署的列表, 部署失败的列表)
        """
        deployed = []
        failed = []

        # 确保目录存在
        backup_dir.mkdir(parents=True, exist_ok=True)

        for scraper_name in downloaded_scrapers:
            # 查找临时目录中的文件
            temp_files = list(temp_dir.glob(f"{scraper_name}.*"))
            if not temp_files:
                self._log(f"临时目录中未找到 {scraper_name} 的文件", "warning")
                failed.append(scraper_name)
                continue

            temp_file = temp_files[0]
            filename = temp_file.name
            expected_hash = hashes_data.get(scraper_name)

            if not expected_hash:
                self._log(f"{scraper_name} 没有哈希值，跳过校验", "warning")
                failed.append(scraper_name)
                continue

            # 1. 校验临时文件哈希
            if not await self._verify_file_hash(temp_file, expected_hash):
                self._log(f"临时文件校验失败: {scraper_name}", "error")
                failed.append(scraper_name)
                continue

            # 2. 只复制到 backup 目录并校验
            backup_target = backup_dir / filename
            if not await self._copy_and_verify(temp_file, backup_target, expected_hash, scraper_name):
                failed.append(scraper_name)
                continue

            deployed.append(scraper_name)
            self._log(f"✓ 已部署到备份目录: {scraper_name}")

        # 刷新日志缓冲区
        import sys
        for handler in logging.getLogger().handlers:
            handler.flush()
        sys.stdout.flush()
        sys.stderr.flush()

        return deployed, failed

    async def _update_version_files(
        self,
        scrapers_dir: Path,
        backup_dir: Path,
        package_data: dict,
        versions_data: dict,
        hashes_data: dict,
        platform_info: dict
    ):
        """更新版本信息文件到 scrapers 和 backup 目录"""
        import shutil

        self._log("正在更新版本信息...")

        # 1. 保存新的 package.json 到 scrapers 目录
        scrapers_package_file = scrapers_dir / "package.json"
        package_json_str = json.dumps(package_data, indent=2, ensure_ascii=False)
        await asyncio.to_thread(scrapers_package_file.write_text, package_json_str)

        # 2. 保存 versions.json 到 scrapers 目录
        await self._save_versions(versions_data, hashes_data, platform_info, package_data, [])

        # 3. 同步 package.json 和 versions.json 到 backup 目录
        scrapers_versions_file = scrapers_dir / "versions.json"
        backup_versions_file = backup_dir / "versions.json"
        backup_package_file = backup_dir / "package.json"

        if scrapers_versions_file.exists():
            shutil.copy2(scrapers_versions_file, backup_versions_file)

        if scrapers_package_file.exists():
            shutil.copy2(scrapers_package_file, backup_package_file)

        self._log("✓ 版本信息已更新并同步到备份目录")

    async def _update_version_files_backup_only(
        self,
        backup_dir: Path,
        package_data: dict,
        versions_data: dict,
        hashes_data: dict,
        platform_info: dict
    ):
        """只更新版本信息文件到 backup 目录（不更新 scrapers 目录）"""
        self._log("正在更新备份目录的版本信息...")

        # 确保目录存在
        backup_dir.mkdir(parents=True, exist_ok=True)

        # 1. 保存 package.json 到 backup 目录
        backup_package_file = backup_dir / "package.json"
        package_json_str = json.dumps(package_data, indent=2, ensure_ascii=False)
        await asyncio.to_thread(backup_package_file.write_text, package_json_str)

        # 2. 构建并保存 versions.json 到 backup 目录
        backup_versions_file = backup_dir / "versions.json"

        # 读取现有的 versions.json（如果存在）
        existing_scrapers = {}
        existing_hashes = {}
        if backup_versions_file.exists():
            try:
                existing_data = json.loads(await asyncio.to_thread(backup_versions_file.read_text))
                existing_scrapers = existing_data.get("scrapers", {})
                existing_hashes = existing_data.get("hashes", {})
            except Exception:
                pass

        # 合并版本信息
        existing_scrapers.update(versions_data)
        existing_hashes.update(hashes_data)

        # 从 package_data 读取全局版本限制字段
        min_server_version = package_data.get('min_server_version')

        # 构建完整的 versions.json
        versions_json = {
            "platform": platform_info.get('platform', 'unknown'),
            "type": platform_info.get('arch', 'unknown'),
            "scrapers": existing_scrapers,
            "hashes": existing_hashes,
            "updated_at": datetime.now().isoformat()
        }
        if min_server_version:
            versions_json['min_server_version'] = min_server_version

        versions_json_str = json.dumps(versions_json, indent=2, ensure_ascii=False)
        await asyncio.to_thread(backup_versions_file.write_text, versions_json_str)

        self._log("✓ 备份目录版本信息已更新")

    async def _save_versions(self, versions_data, hashes_data, platform_info, package_data, failed_downloads):
        """保存版本信息"""
        if not versions_data:
            return

        try:
            scrapers_dir = _get_scrapers_dir()
            versions_file = scrapers_dir / "versions.json"

            # 合并旧版本信息
            existing_scrapers = {}
            existing_hashes = {}
            if failed_downloads and versions_file.exists():
                try:
                    existing_versions = json.loads(await asyncio.to_thread(versions_file.read_text))
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
                "scrapers": merged_scrapers,
                "updated_at": datetime.now().isoformat()
            }

            if merged_hashes:
                full_versions_data["hashes"] = merged_hashes
            if min_server_version:
                full_versions_data['min_server_version'] = min_server_version

            versions_json_str = json.dumps(full_versions_data, indent=2, ensure_ascii=False)
            await asyncio.to_thread(versions_file.write_text, versions_json_str)
            self._log(f"已保存 {len(merged_scrapers)} 个弹幕源的版本信息")
        except Exception as e:
            self._log(f"保存版本信息失败: {e}", "warning")

    def _clear_version_cache(self):
        """清除版本缓存，让前端能获取到最新版本号"""
        try:
            import src.api.ui.scraper_resources as scraper_resources_module
            scraper_resources_module._version_cache = None
            scraper_resources_module._version_cache_time = None
            logger.info("已清除版本缓存")
        except Exception as e:
            logger.warning(f"清除版本缓存失败: {e}")

    async def _update_versions_json(self, asset_info: Dict[str, Any], scrapers_dir: Path, platform_key: str):
        """全量替换后更新 versions.json"""
        try:
            platform_info = get_platform_info()
            release_version = asset_info['version'].lstrip('v')

            # 从解压后的 package.json 读取各个源的版本信息
            scrapers_versions = {}
            scrapers_hashes = {}
            local_package_file = scrapers_dir / "package.json"

            # 从解压后的 versions.json 读取全局版本限制字段（覆盖前读取）
            min_server_version = None
            existing_versions_file = scrapers_dir / "versions.json"
            if existing_versions_file.exists():
                try:
                    existing_ver_data = json.loads(await asyncio.to_thread(existing_versions_file.read_text))
                    min_server_version = existing_ver_data.get('min_server_version')
                except Exception:
                    pass

            if local_package_file.exists():
                try:
                    package_content = json.loads(await asyncio.to_thread(local_package_file.read_text))
                    # 从 resources 字段提取各个源的版本号和哈希值
                    resources = package_content.get('resources', {})
                    for scraper_name, scraper_info in resources.items():
                        if isinstance(scraper_info, dict):
                            version = scraper_info.get('version')
                            if version:
                                scrapers_versions[scraper_name] = version
                            # 提取哈希值 - 使用 platform_key (如 linux-x86) 而不是 platform_arch 组合
                            hashes = scraper_info.get('hashes', {})
                            if platform_key in hashes:
                                scrapers_hashes[scraper_name] = hashes[platform_key]
                    logger.info(f"从 package.json 读取到 {len(scrapers_versions)} 个源的版本信息, {len(scrapers_hashes)} 个哈希值")
                    # package.json 也可能携带版本限制字段
                    if not min_server_version:
                        min_server_version = package_content.get('min_server_version')
                except Exception as e:
                    logger.warning(f"读取 package.json 中的源版本信息失败: {e}")

            # 构建 versions.json 数据
            versions_data = {
                "platform": platform_info['platform'],
                "type": platform_info['arch'],
                "version": release_version,
                "scrapers": scrapers_versions,
                "hashes": scrapers_hashes,
                "full_replace": True,
                "updated_at": datetime.now().isoformat()  # 使用 updated_at 与其他地方保持一致
            }
            if min_server_version:
                versions_data['min_server_version'] = min_server_version

            # 写入 versions.json 到 scrapers 目录
            versions_file = scrapers_dir / "versions.json"
            versions_json_str = json.dumps(versions_data, indent=2, ensure_ascii=False)
            await asyncio.to_thread(versions_file.write_text, versions_json_str)
            logger.info(f"已更新 versions.json: {len(scrapers_versions)} 个源版本, {len(scrapers_hashes)} 个哈希值")

            # 同步到 backup 目录
            from src.api.ui.scraper_resources import BACKUP_DIR
            import shutil
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            backup_versions_file = BACKUP_DIR / "versions.json"
            backup_package_file = BACKUP_DIR / "package.json"
            shutil.copy2(versions_file, backup_versions_file)
            if local_package_file.exists():
                shutil.copy2(local_package_file, backup_package_file)
            logger.info("已同步版本信息到备份目录")

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

        except Exception as e:
            logger.error(f"更新版本信息失败: {e}", exc_info=True)
            self._log(f"更新版本信息失败: {e}", "warning")


async def start_download_task(
    repo_url: str,
    use_full_replace: bool,
    branch: str,  # 添加分支参数
    config_manager,
    scraper_manager,
    current_user,
) -> DownloadTask:
    """启动下载任务"""
    task_manager = get_download_task_manager()

    # 检查是否有任务正在运行
    if task_manager.is_running():
        raise ValueError("已有下载任务正在进行，请稍后再试")

    # 创建任务
    task = task_manager.create_task(repo_url, use_full_replace, branch)  # 传递分支参数

    # 创建执行器
    executor = ScraperDownloadExecutor(
        task=task,
        config_manager=config_manager,
        scraper_manager=scraper_manager,
        current_user=current_user,
    )

    # 启动后台任务
    async def run_task():
        try:
            await executor.execute()
        except asyncio.CancelledError:
            logger.info(f"任务 {task.task_id} 被取消")
        except Exception as e:
            logger.error(f"任务 {task.task_id} 执行失败: {e}", exc_info=True)

    task._asyncio_task = asyncio.create_task(run_task())
    logger.info(f"已启动下载任务: {task.task_id} (分支: {branch})")

    return task

