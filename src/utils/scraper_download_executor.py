"""
弹幕源下载执行器

将下载逻辑从 SSE 连接中解耦，实现后台独立运行
"""

import asyncio
import hashlib
import importlib.util
import inspect
import json
import logging
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from src._version import APP_VERSION
from src.api.ui.scraper_resources import (
    BACKUP_DIR,
    _build_base_url as build_url,
    _download_and_extract_release,
    _fetch_github_release_asset,
    _fetch_gitee_release_asset,
    _get_deferred_overlay_dir,
    _get_scrapers_dir as get_dir,
    _is_docker_environment,
    apply_deferred_overlay,
    backup_scrapers,
    get_platform_info as get_info,
    get_platform_key as get_key,
    parse_gitee_url as parse_gt,
    parse_github_url as parse_gh,
    restore_scrapers,
)
import src.api.ui.scraper_resources as scraper_resources_module
from src.db import CacheManager, get_db_session_factory
from src.scrapers.base import BaseScraper
from src.services import DownloadTask, DownloadTaskManager, get_download_task_manager
# why：src.services 顶层的 TaskStatus 是主任务管理器的中文枚举（失败/已完成/运行中，且无 CANCELLED），
#      下载任务用的是 download_task_manager 里的英文枚举（failed/completed/cancelled）。
#      从顶层导入会写入中文状态，导致 SSE 终态判断（比对英文值）永不命中而无限推送 progress。
#      必须直接从 download_task_manager 导入，禁止改回 from src.services import TaskStatus。
from src.services.download_task_manager import TaskStatus
from src.services.scraper_manager import _version_satisfies
from src.utils.docker_utils import (
    get_current_container_id,
    is_docker_socket_available,
    is_running_in_docker,
    restart_container,
)
from src.utils.scraper_version_manager import ScraperVersionManager
from src.utils.version_comparator import VersionComparator

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
    if _is_docker_environment():
        return Path("/app/config/temp_downloads")
    else:
        return Path("config/temp_downloads")


def _get_scrapers_dir() -> Path:
    """获取弹幕源目录"""
    return get_dir()


def get_platform_key() -> str:
    """获取平台标识"""
    return get_key()


def get_platform_info() -> Dict[str, str]:
    """获取平台信息"""
    return get_info()


def parse_github_url(url: str):
    """解析 GitHub URL"""
    return parse_gh(url)


def parse_gitee_url(url: str):
    """解析 Gitee URL"""
    return parse_gt(url)


def _build_base_url(repo_info, repo_url: str, gitee_info, branch: str = "main") -> str:
    """构建基础 URL"""
    return build_url(repo_info, repo_url, gitee_info, branch)


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
        self._log("使用全量替换模式")

        # 获取 Release 资产信息
        asset_info = None
        if gitee_info:
            self._log("正在从 Gitee Releases 获取压缩包...")
            asset_info = await _fetch_gitee_release_asset(
                gitee_info=gitee_info,
                platform_key=platform_key,
                headers=headers,
                proxy=proxy_to_use,
                tag_or_branch=self.task.branch  # 传递用户选择的版本/分支
            )
        elif repo_info:
            self._log("正在从 GitHub Releases 获取压缩包...")
            asset_info = await _fetch_github_release_asset(
                repo_info=repo_info,
                platform_key=platform_key,
                headers=headers,
                proxy=proxy_to_use,
                tag_or_branch=self.task.branch  # 传递用户选择的版本/分支
            )

        if not asset_info:
            raise ValueError("未找到匹配的 Release 压缩包")

        self._log(f"找到压缩包: {asset_info['filename']} (版本: {asset_info['version']})")

        # 前置版本校验：在备份/下载之前先取 {base_url}/package.json（几KB）核验 min_server_version。
        # why：全量路径仅先拿到 asset_info（文件名/版本），版本约束在 package.json 里；
        #      下载整包+备份耗时可达数分钟，若版本不满足应在任何磁盘写入之前快速报错。
        #      与 _do_incremental_download 中第一步就校验 min_server_version 的做法对齐。
        # 额外用途：全量包（tar.gz）本身不内置 package.json，把这里拉到的内容保存为
        # remote_pre_pkg，后续传给 _update_versions_json 作兜底，确保 backup/versions.json
        # 和 backup/package.json 即使在包内缺文件时也能正确写入，从根本上避免循环重启。
        self._log("正在预检弹幕源包服务器版本要求...")
        remote_pre_pkg = None  # 保存网络拉取的 package.json，供后续兜底
        try:
            pre_base_url = _build_base_url(repo_info, self.task.repo_url or "", gitee_info, self.task.branch)
            remote_pre_pkg = await self._fetch_package_json(
                f"{pre_base_url}/package.json", headers, proxy_to_use,
                httpx.Timeout(15.0, read=15.0),
            )
            if remote_pre_pkg:
                # min_server_version 与 min_fetchable_version 语义相同：
                # 都表示"服务器版本必须 >= 该值才能使用本弹幕源包"。
                # 两者取其一即可阻断下载（优先 min_server_version，回退 min_fetchable_version）。
                _pre_min = remote_pre_pkg.get("min_server_version") or remote_pre_pkg.get("min_fetchable_version")
                if _pre_min:
                    if not _version_satisfies(APP_VERSION, _pre_min):
                        raise ValueError(
                            f"弹幕源包要求服务器版本 >= {_pre_min}，"
                            f"当前版本 {APP_VERSION}，请先升级服务器再下载"
                        )
                    self._log(f"✓ 版本预检通过（要求 >= {_pre_min}，当前 {APP_VERSION}）")
                else:
                    self._log("✓ 版本预检通过（无最低版本要求）")
        except ValueError:
            raise  # 版本不满足直接上抛，不做备份/下载
        except Exception as _pre_err:
            # 预检网络失败不阻断：解压后的后置校验（_update_versions_json 段）仍会兜底
            self._log(f"版本预检跳过（网络异常: {_pre_err}）", "debug")

        # 下载并解压（先不备份，等版本校验通过后再备份）
        scrapers_dir = _get_scrapers_dir()
        self._log("正在下载压缩包...")

        success = await _download_and_extract_release(
            asset_info=asset_info,
            scrapers_dir=scrapers_dir,
            headers=headers,
            proxy=proxy_to_use,
            progress_callback=self._log_async,
            # 只解压到临时目录 + 持久化到 backup，不覆盖运行中的 .so。
            # why: 对齐逐文件更新路径的做法——覆盖 .so 之后进程随时可能 segfault，
            # 必须等版本信息写完、SSE 终态发完，才在紧邻重启处执行覆盖。
            defer_overlay=True
        )

        if not success:
            # 下载失败，直接返回（此时还未备份，无需还原）
            self._log("全量替换失败", "error")
            raise ValueError("全量替换失败")

        # 下载成功（新版已解压到临时目录并持久化到 backup，运行目录尚未被覆盖）
        pending_dir = _get_deferred_overlay_dir(scrapers_dir)

        # 更新 versions.json（从临时目录读取新包的 package.json；包内若无则用网络预检拿到的兜底）
        self._log("正在更新版本信息...")
        full_replace_min_ver = await self._update_versions_json(
            asset_info, scrapers_dir, platform_key,
            source_dir=pending_dir,
            remote_package_json=remote_pre_pkg,
        )

        # 全量替换后检查：解压出的弹幕源包是否要求更高的服务器版本
        if full_replace_min_ver:
            if not _version_satisfies(APP_VERSION, full_replace_min_ver):
                msg = (
                    f"远程弹幕源包要求服务器版本 >= {full_replace_min_ver}，"
                    f"当前版本 {APP_VERSION}，已取消本次更新"
                )
                self._log(f"⚠️ {msg}", "warning")
                # 运行目录的 .so 还没被覆盖，只需丢弃临时目录并还原备份中的版本信息
                await asyncio.to_thread(shutil.rmtree, pending_dir, True)
                await restore_scrapers(self.current_user, self.scraper_manager)
                self._log("已还原备份，请先升级服务器版本")
                raise ValueError(msg)

        # 清除版本缓存，让前端能获取到最新版本号
        self._clear_version_cache()

        # 注：新版本已由解压流程持久化到 backup 目录，无需再次 backup_scrapers

        # .so 版本兼容性预检（首次/非首次均需校验）
        # why：package.json 的 min_fetchable_version / min_server_version 是全局字段，
        #      而每个 .so 的类属性 min_server_version 才是真正的细粒度约束，两者可以不一致。
        #      必须在覆盖运行目录（或重启）之前做后置校验，否则部署后重启才报错，
        #      整次更新白费且弹幕源全部跳过。
        self._log("正在校验临时目录中弹幕源的服务器版本要求...")
        compat_errors = await self._check_scraper_compat_in_dir(pending_dir)
        if compat_errors:
            detail = "、".join(f"{n}(要求 >= {v})" for n, v in compat_errors.items())
            msg = (
                f"弹幕源版本不兼容，当前服务器 {APP_VERSION} 不满足：{detail}，"
                "已取消部署，请先升级服务器"
            )
            self._log(f"⚠️ {msg}", "warning")
            await asyncio.to_thread(shutil.rmtree, pending_dir, True)
            await restore_scrapers(self.current_user, self.scraper_manager)
            self._log("已还原备份")
            raise ValueError(msg)

        # 校验通过后才设进度为完成，避免校验失败时前端进度条误显示绿色满格
        self.task.progress.current = 1
        self.task.progress.total = 1
        self.task.progress.downloaded.append("full_replace")
        self._log("全量替换完成")

        # 最终版本验证：解压后的文件是否与本地不同
        # why: 用户可能下载了相同版本的压缩包，在这里做最后检查，避免不必要的部署和重启
        if remote_pre_pkg:

            remote_version = remote_pre_pkg.get("version", "unknown")
            remote_branch = self.task.branch if hasattr(self.task, 'branch') else None

            should_update, reason = VersionComparator.should_update(
                local_dir=scrapers_dir,
                remote_version=remote_version,
                remote_branch=remote_branch
            )

            if not should_update:
                self._log(f"✓ 最终版本验证: {reason}，跳过部署")
                # 清理临时目录
                await asyncio.to_thread(shutil.rmtree, pending_dir, True)
                self._log("✓ 已清理临时目录")

                # 清除版本缓存
                self._clear_version_cache()

                # why: 版本相同不需要还原备份（备份的就是当前版本），直接标记完成即可
                # 避免触发无意义的文件复制和容器重启
                self.task.status = TaskStatus.COMPLETED
                self.task.need_restart = False
                self.task.success_message = f"当前弹幕源版本与所选加载版本（{remote_version}）相同，无需重载"
                return

        # 版本不同，需要部署，先备份当前版本
        self._log("正在备份当前弹幕源...")
        await backup_scrapers(self.current_user)
        self._log("备份完成")

        # 判断是否是首次下载（本地没有任何弹幕源）
        existing_scrapers = set(self.scraper_manager.scrapers.keys())
        is_first_download = len(existing_scrapers) == 0

        if is_first_download:
            # 版本兼容，正式应用覆盖并热加载
            self._log("检测到首次下载弹幕源，正在应用更新...")
            overlay_count = await asyncio.to_thread(apply_deferred_overlay, scrapers_dir)
            self._log(f"✓ 已应用 {overlay_count} 个文件")
            logger.info(f"用户 '{self.current_user.username}' 首次通过全量替换模式下载了弹幕源，正在热加载")
            # why：传 skip_backup_restore=True，避免 apply_deferred_overlay 将
            #      临时目录中没有 updated_at 的 versions.json 覆盖运行目录后，
            #      load_and_sync_scrapers 误判"备份更新 > scrapers"而触发不必要的备份还原，
            #      进而在热加载过程中重复 import .so 导致 native crash / 容器重启。
            await self.scraper_manager.load_and_sync_scrapers(skip_backup_restore=True)
            self._log("✓ 弹幕源加载完成")
        else:
            # 非首次下载：检查是否在 Docker 容器内且有 Docker socket，决定重启方式
            # 同时满足两个条件才走自动重启路径：socket 可用 + 确实在 Docker 容器内
            docker_available = is_docker_socket_available() and is_running_in_docker()

            if docker_available:
                # 有 Docker socket，执行容器级别重启
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

                # ========== 最后一步：覆盖运行目录里正在被加载的 .so，然后立即重启 ==========
                # why: 覆盖后进程内存中是旧模块而磁盘已是新二进制，此后任何延迟 import 或
                # 未加载符号的访问都可能 segfault（旧实现就是在覆盖后继续执行
                # _update_versions_json / backup_scrapers 而崩溃，导致 SSE 心跳永久消失、
                # 前端一直卡在中间状态）。这里对齐逐文件更新路径：SSE 终态消息已发送完毕，
                # 覆盖完只做重启，中间不执行任何业务代码。
                logger.info(f"[任务 {self.task.task_id}] 正在应用新版 .so 到运行目录...")
                for handler in logging.getLogger().handlers:
                    handler.flush()
                try:
                    overlay_count = apply_deferred_overlay(scrapers_dir)
                    logger.info(f"[任务 {self.task.task_id}] 已应用 {overlay_count} 个文件，立即重启")
                except Exception as overlay_err:
                    # 覆盖失败不影响重启：backup 目录已是新版，重启后会从备份恢复
                    logger.error(f"[任务 {self.task.task_id}] 应用新版文件失败: {overlay_err}")
                for handler in logging.getLogger().handlers:
                    handler.flush()
                sys.stdout.flush()
                sys.stderr.flush()

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
                # 非首次下载且没有 Docker socket：应用更新后提示手动重启，不执行热加载
                # 覆盖放在最后并紧跟返回，覆盖后不再执行业务代码（见上方 segfault 说明）
                self._log("⚠️ 未检测到 Docker 套接字，无法自动重启容器")
                self.task.status = TaskStatus.COMPLETED
                await self._persist_task_status("completed", need_restart=True)
                self._log("正在应用新版文件...")
                self.task.need_restart = True
                # 等 SSE 把上面的消息推送出去，再执行危险的覆盖操作
                await asyncio.sleep(1.5)
                try:
                    overlay_count = apply_deferred_overlay(scrapers_dir)
                    logger.info(f"已应用 {overlay_count} 个文件（等待手动重启生效）")
                except Exception as overlay_err:
                    logger.error(f"应用新版文件失败: {overlay_err}")
                logger.info(f"用户 '{self.current_user.username}' 通过全量替换模式更新了弹幕源，需要手动重启容器")
                logger.warning("⚠️ 请手动重启容器以加载新的弹幕源（.so 文件需要重启才能生效）")
                self.task.restart_pending = True
                return

        self.task.status = TaskStatus.COMPLETED

    async def _do_incremental_download(self, base_url, headers, proxy_to_use, platform_key, platform_info):
        """增量下载模式"""

        # 下载 package.json
        package_url = f"{base_url}/package.json"
        self._log("正在获取资源包信息...")

        timeout_config = httpx.Timeout(30.0, read=30.0)
        package_data = await self._fetch_package_json(package_url, headers, proxy_to_use, timeout_config)

        if not package_data:
            raise ValueError("获取资源包信息失败")

        # 前置检查：远程弹幕源包是否要求更高的服务器版本
        # min_server_version 与 min_fetchable_version 语义相同，两者取其一即可阻断下载
        remote_min_server = package_data.get('min_server_version') or package_data.get('min_fetchable_version')
        if remote_min_server:
            if not _version_satisfies(APP_VERSION, remote_min_server):
                msg = (
                    f"远程弹幕源包要求服务器版本 >= {remote_min_server}，"
                    f"当前版本 {APP_VERSION}，请先升级服务器"
                )
                self._log(f"⚠️ {msg}", "warning")
                raise ValueError(msg)

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
            self.task.success_message = f"所有弹幕源文件哈希值与远程版本（{package_data.get('version', 'unknown')}）一致，无需下载"
            # why: 所有文件都是最新，不需要重启
            # 直接返回即可，SSE会因为status=COMPLETED自动发送done消息
            return

        # 创建临时下载目录
        temp_dir = _get_temp_download_base_dir() / f"download_{self.task.task_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        self._log(f"创建临时下载目录: {temp_dir}")

        try:
            # 先下载到临时目录（不备份，等版本校验通过后再备份）
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

            # 检查下载结果：有失败时直接返回（此时还未备份，无需还原）
            if failed_downloads:
                self._log(f"有 {len(failed_downloads)} 个弹幕源下载失败: {', '.join(failed_downloads)}", "error")
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

            # 最终版本验证：临时目录的文件是否与本地不同
            # why: 下载过程中可能有网络问题或其他原因导致下载的版本实际上和本地一样
            # 在这里做最后检查，避免不必要的部署和重启

            remote_version = package_data.get("version", "unknown")
            remote_branch = self.task.branch if hasattr(self.task, 'branch') else None

            should_update, reason = VersionComparator.should_update(
                local_dir=scrapers_dir,
                remote_version=remote_version,
                remote_branch=remote_branch
            )

            if not should_update:
                self._log(f"✓ 最终版本验证: {reason}，跳过部署")
                # 清理临时目录
                if temp_dir.exists():
                    shutil.rmtree(temp_dir)
                    self._log("✓ 已清理临时目录")

                # 清除版本缓存
                self._clear_version_cache()

                # why: 版本相同不需要还原备份（备份的就是当前版本），直接标记完成即可
                # 避免触发无意义的文件复制和容器重启
                self.task.status = TaskStatus.COMPLETED
                self.task.need_restart = False
                self.task.success_message = f"当前弹幕源版本与所选加载版本（{remote_version}）相同，无需重载"
                return

            # 版本不同，需要部署，先备份当前版本
            self._log("正在备份当前弹幕源...")
            await backup_scrapers(self.current_user)
            self._log("备份完成")

            # 判断是否是首次下载（本地没有任何弹幕源）
            existing_scrapers = set(self.scraper_manager.scrapers.keys())
            is_first_download = len(existing_scrapers) == 0

            # 检查是否在 Docker 容器内且有 Docker socket
            docker_available = is_docker_socket_available() and is_running_in_docker()

            # why：预检 package.json 只能拦截全局 min_server_version，
            # 但 .so 内部的类属性 min_server_version 才是实际生效的约束；
            # 两者可能不一致，如果不在这里做后置校验，下载+部署后重启才报错，
            # 导致所有弹幕源全部跳过、整次更新白费。
            # 在部署前从已下载到临时目录的 .so 文件中读取实际版本约束，
            # 不通过则立即还原备份并以 ValueError 结束任务。
            self._log("正在校验临时目录中弹幕源的服务器版本要求...")
            compat_errors = await self._check_scraper_compat_in_dir(temp_dir)
            if compat_errors:
                detail = "、".join(
                    f"{n}(要求 >= {v})" for n, v in compat_errors.items()
                )
                msg = (
                    f"弹幕源版本不兼容，当前服务器 {APP_VERSION} 不满足：{detail}，"
                    "已取消部署，请先升级服务器"
                )
                self._log(f"⚠️ {msg}", "warning")
                await restore_scrapers(self.current_user, self.scraper_manager)
                self._log("已还原备份")
                raise ValueError(msg)

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
        backup_versions_file = BACKUP_DIR / "versions.json"
        scrapers_versions_file = scrapers_dir / "versions.json"

        # 获取当前任务的分支信息
        current_branch = self.task.branch if hasattr(self.task, 'branch') else 'main'

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

        # 检查分支是否匹配
        branch_mismatch = False
        if versions_file and versions_file.exists():
            try:
                local_versions = json.loads(await asyncio.to_thread(versions_file.read_text))
                local_branch = local_versions.get('branch', 'main')

                # 检查分支是否一致
                if local_branch != current_branch:
                    branch_mismatch = True
                    self._log(f"⚠ 分支不匹配: 本地版本来自分支 '{local_branch}'，当前下载分支 '{current_branch}'，将忽略本地哈希值", "warning")
                else:
                    local_hashes = local_versions.get('hashes', {})
                    self._log(f"已读取本地版本信息，包含 {len(local_hashes)} 个哈希值（分支: {local_branch}）")
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

    async def _check_scraper_compat_in_dir(self, check_dir: Path) -> dict:
        """薄封装，实际实现见模块级 check_scraper_compat_in_dir（与自动更新链路共用）。"""
        return await check_scraper_compat_in_dir(check_dir)

    async def _copy_and_verify(self, src_path: Path, dst_path: Path, expected_hash: str, scraper_name: str) -> bool:
        """复制文件并校验哈希值"""
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
        """更新版本信息文件（只生成 scraper_manifest.json，不再保存 package.json 和 versions.json 到 scrapers）"""

        self._log("正在更新版本信息...")

        # 1. 在 backup 目录保存 package.json 和 versions.json（作为中间文件）
        backup_package_file = backup_dir / "package.json"
        backup_versions_file = backup_dir / "versions.json"

        package_json_str = json.dumps(package_data, indent=2, ensure_ascii=False)
        await asyncio.to_thread(backup_package_file.write_text, package_json_str)

        # 保存 versions.json 到 backup
        await self._save_versions(versions_data, hashes_data, platform_info, package_data, [])

        # 2. 从 backup 目录的两个文件提取信息，生成完整的 scraper_manifest.json
        try:
            manifest = await asyncio.to_thread(
                ScraperVersionManager.extract_manifest_from_legacy,
                backup_package_file,
                backup_versions_file,
                scrapers_dir
            )

            # 保存到 scrapers 目录（权威文件）
            await asyncio.to_thread(ScraperVersionManager.save_manifest, manifest, scrapers_dir)
            self._log(f"✓ 已生成权威文件 scraper_manifest.json: {len(manifest.get('sources', {}))} 个源")

            # 同时保存到 backup 目录
            await asyncio.to_thread(ScraperVersionManager.save_manifest, manifest, backup_dir)

        except Exception as e:
            self._log(f"生成 scraper_manifest.json 失败: {e}", "warning")

        self._log("✓ 版本信息已更新（scrapers 目录仅保留 scraper_manifest.json）")

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
            "branch": self.task.branch if hasattr(self.task, 'branch') else 'main',  # 记录分支信息
            "updated_at": datetime.now().isoformat()
        }
        if min_server_version:
            versions_json['min_server_version'] = min_server_version

        versions_json_str = json.dumps(versions_json, indent=2, ensure_ascii=False)
        await asyncio.to_thread(backup_versions_file.write_text, versions_json_str)

        self._log("✓ 备份目录版本信息已更新")

        # 同时生成/更新 backup 目录的 scraper_manifest.json
        # why: 重启后恢复逻辑依赖 manifest 的 updated_at 比较，必须同步更新
        try:

            # 直接使用已有数据构造 manifest，避免重新读取文件
            manifest = {
                "version": package_data.get("version", versions_json.get("version", "unknown")),
                "updated_at": versions_json["updated_at"],
                "platform": versions_json["platform"],
                "branch": versions_json.get("branch", "main"),
                "sources": {}
            }

            # 添加 min_server_version（如果存在）
            min_server_version = package_data.get("min_server_version") or package_data.get("min_fetchable_version")
            if min_server_version:
                manifest["min_server_version"] = min_server_version

            # 构造 sources
            for scraper_name, version in existing_scrapers.items():
                source_entry = {
                    "version": version
                }

                # 添加哈希值（如果存在）
                if scraper_name in existing_hashes:
                    source_entry["hashes"] = {
                        platform_info.get('platform', 'unknown'): existing_hashes[scraper_name]
                    }

                manifest["sources"][scraper_name] = source_entry

            # 保存到 backup 目录
            await asyncio.to_thread(
                ScraperVersionManager.save_manifest,
                manifest,
                BACKUP_DIR
            )

            self._log(f"✓ 已更新 backup 目录的 scraper_manifest.json: {len(manifest.get('sources', {}))} 个源")
        except Exception as e:
            self._log(f"更新 backup manifest 失败: {e}", "warning")
            logger.warning(f"更新 backup manifest 详细错误: {traceback.format_exc()}")

    async def _save_versions(self, versions_data, hashes_data, platform_info, package_data, failed_downloads):
        """保存版本信息到 backup 目录（不再保存到 scrapers 目录）"""
        if not versions_data:
            return

        try:
            versions_file = BACKUP_DIR / "versions.json"

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

            # versions.json 只作为中间文件，保存到 backup 目录
            full_versions_data = {
                "platform": platform_info['platform'],
                "type": platform_info['arch'],
                "scrapers": merged_scrapers,
                "branch": self.task.branch if hasattr(self.task, 'branch') else 'main',
                "updated_at": datetime.now().isoformat()
            }

            if merged_hashes:
                full_versions_data["hashes"] = merged_hashes
            if min_server_version:
                full_versions_data['min_server_version'] = min_server_version

            versions_json_str = json.dumps(full_versions_data, indent=2, ensure_ascii=False)
            await asyncio.to_thread(versions_file.write_text, versions_json_str)
            self._log(f"已保存 {len(merged_scrapers)} 个弹幕源的版本信息到 backup 目录")

        except Exception as e:
            self._log(f"保存版本信息失败: {e}", "warning")

    def _clear_version_cache(self):
        """清除版本缓存，让前端能获取到最新版本号"""
        try:
            scraper_resources_module._version_cache = None
            scraper_resources_module._version_cache_time = None
            logger.info("已清除版本缓存")
        except Exception as e:
            logger.warning(f"清除版本缓存失败: {e}")

    async def _update_versions_json(
        self,
        asset_info: Dict[str, Any],
        scrapers_dir: Path,
        platform_key: str,
        source_dir: Optional[Path] = None,
        remote_package_json: Optional[Dict] = None,
    ) -> Optional[str]:
        """全量替换后更新 versions.json，返回 min_server_version（如有）

        Args:
            source_dir: 新包内 package.json / versions.json 的所在目录。
                推迟覆盖模式下新文件还在临时目录里，需从那里读取；默认取 scrapers_dir。
            remote_package_json: 下载前从远端仓库预拉取的 package.json 内容（dict）。
                why：全量包（tar.gz）本身不内置 package.json，当包内既无 package.json
                也无 versions.json 时，直接用此兜底，确保 backup/package.json 和
                backup/versions.json 能正确写入版本信息，避免循环重启。
        """
        try:
            platform_info = get_platform_info()
            release_version = asset_info['version'].lstrip('v')

            # 从新包读取各源版本号与哈希
            # why：全量包（tar.gz 单平台格式）不含 package.json，版本/哈希在 versions.json；
            #      多平台 zip 包含 package.json，哈希格式 {platform_key: hash}；
            #      优先 package.json，缺失时从 versions.json 回退。
            read_dir = source_dir if source_dir is not None else scrapers_dir
            scrapers_versions = {}
            scrapers_hashes = {}
            min_server_version = None

            # 步骤1：尝试从 package.json 读（多平台包格式）
            local_package_file = read_dir / "package.json"
            if local_package_file.exists():
                try:
                    package_content = json.loads(await asyncio.to_thread(local_package_file.read_text))
                    resources = package_content.get('resources', {})
                    for scraper_name, scraper_info in resources.items():
                        if isinstance(scraper_info, dict):
                            version = scraper_info.get('version')
                            if version:
                                scrapers_versions[scraper_name] = version
                            # package.json 里哈希键是连字符格式 platform_key（如 linux-x86）
                            hashes = scraper_info.get('hashes', {})
                            if platform_key in hashes:
                                scrapers_hashes[scraper_name] = hashes[platform_key]
                    logger.info(f"从 package.json 读取到 {len(scrapers_versions)} 个源的版本信息, {len(scrapers_hashes)} 个哈希值")
                    if not min_server_version:
                        min_server_version = package_content.get('min_server_version')
                except Exception as e:
                    logger.warning(f"读取 package.json 中的源版本信息失败: {e}")

            # 步骤2：从 versions.json 补充/回退（单平台包主流路径）
            # why：单平台 tar.gz 里 versions.json 的 scrapers={name:ver}, hashes={name:hash}，
            #      直接就是所需结构，不需要 platform_key 查找。
            existing_versions_file = read_dir / "versions.json"
            if existing_versions_file.exists():
                try:
                    existing_ver_data = json.loads(await asyncio.to_thread(existing_versions_file.read_text))
                    if not min_server_version:
                        min_server_version = existing_ver_data.get('min_server_version')
                    # 若 package.json 未读到版本/哈希，从 versions.json 补充
                    if not scrapers_versions:
                        scrapers_versions = existing_ver_data.get('scrapers', {}) or {}
                    if not scrapers_hashes:
                        scrapers_hashes = existing_ver_data.get('hashes', {}) or {}
                    logger.info(f"从 versions.json 补充读取: {len(scrapers_versions)} 个源版本, {len(scrapers_hashes)} 个哈希值")
                except Exception as e:
                    logger.warning(f"读取 versions.json 版本信息失败: {e}")

            # 步骤3：远端 package.json 兜底
            # why：全量包（tar.gz）通常不内置 package.json，且上游可能也不内置 versions.json，
            #      导致步骤1/2 均为空，_persist_new_version_to_backup 写出的 backup/versions.json
            #      里 scrapers={} → _verify_backup_version 永远校验失败 → 循环重启。
            #      _do_full_replace 在下包前已从远端仓库拉取 package.json（remote_package_json），
            #      这里作为最后一道兜底：若包内完全没有版本信息，就用远端数据补全；
            #      同时把它写入临时目录，让后续 _persist_new_version_to_backup 复制时自然包含。
            if remote_package_json and (not scrapers_versions or not min_server_version):
                try:
                    if not min_server_version:
                        min_server_version = (
                            remote_package_json.get('min_server_version')
                            or remote_package_json.get('min_fetchable_version')
                        )
                    if not scrapers_versions:
                        for scraper_name, scraper_info in (remote_package_json.get('resources', {}) or {}).items():
                            if isinstance(scraper_info, dict):
                                ver = scraper_info.get('version')
                                if ver:
                                    scrapers_versions[scraper_name] = ver
                                hashes = scraper_info.get('hashes', {}) or {}
                                if platform_key in hashes:
                                    scrapers_hashes[scraper_name] = hashes[platform_key]
                    # 把远端 package.json 写入临时目录，确保后续持久化到 backup 时有完整文件
                    if not local_package_file.exists():
                        remote_pkg_with_ver = dict(remote_package_json)
                        remote_pkg_with_ver['version'] = release_version
                        await asyncio.to_thread(
                            local_package_file.write_text,
                            json.dumps(remote_pkg_with_ver, indent=2, ensure_ascii=False)
                        )
                        logger.info(f"全量包内无 package.json，已用远端内容写入临时目录作兜底（版本 {release_version}）")
                    logger.info(f"远端兜底后: {len(scrapers_versions)} 个源版本, min_server_version={min_server_version}")
                except Exception as e:
                    logger.warning(f"远端 package.json 兜底失败: {e}")

            # P1-1: 统一版本号权威源 - versions.json 不再存储全局 version 字段
            # package.json 是唯一权威版本源，versions.json 只保留 updated_at 和各源详情
            versions_data = {
                "platform": platform_info['platform'],
                "type": platform_info['arch'],
                # "version": release_version,  # ❌ 已移除：统一使用 package.json
                "scrapers": scrapers_versions,
                "hashes": scrapers_hashes,
                "full_replace": True,
                "branch": self.task.branch if hasattr(self.task, 'branch') else 'main',  # 记录分支信息
                "updated_at": datetime.now().isoformat()  # 使用 updated_at 与其他地方保持一致
            }
            if min_server_version:
                versions_data['min_server_version'] = min_server_version

            # 写入 versions.json 到 scrapers 目录
            versions_file = scrapers_dir / "versions.json"
            versions_json_str = json.dumps(versions_data, indent=2, ensure_ascii=False)
            await asyncio.to_thread(versions_file.write_text, versions_json_str)
            logger.info(f"已更新 versions.json: {len(scrapers_versions)} 个源版本, {len(scrapers_hashes)} 个哈希值")

            # 先更新 package.json 的版本号（前端从这里读取整体版本），再同步到备份目录
            # why: 原先先同步 backup 再改版本号，导致 backup 里的 package.json 版本号仍是旧值
            package_written_to: Optional[Path] = None
            try:
                if local_package_file.exists():
                    package_content = json.loads(await asyncio.to_thread(local_package_file.read_text))
                    package_content['version'] = release_version
                else:
                    package_content = {"version": release_version}
                package_json_str = json.dumps(package_content, indent=2, ensure_ascii=False)
                # 写回新包所在目录（推迟覆盖模式下即临时目录，后续会被覆盖到运行目录）
                await asyncio.to_thread(local_package_file.write_text, package_json_str)
                package_written_to = local_package_file
                # 同时写入运行目录，保证前端立即能读到新版本号
                scrapers_package_file = scrapers_dir / "package.json"
                if scrapers_package_file != local_package_file:
                    await asyncio.to_thread(scrapers_package_file.write_text, package_json_str)
                logger.info(f"已更新 package.json 版本号为: {release_version}")
            except Exception as pkg_err:
                logger.warning(f"更新 package.json 失败: {pkg_err}")

            # 同步到 backup 目录
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            backup_versions_file = BACKUP_DIR / "versions.json"
            backup_package_file = BACKUP_DIR / "package.json"
            shutil.copy2(versions_file, backup_versions_file)
            if package_written_to and package_written_to.exists():
                shutil.copy2(package_written_to, backup_package_file)
            logger.info("已同步版本信息到备份目录")

            return min_server_version

        except Exception as e:
            logger.error(f"更新版本信息失败: {e}", exc_info=True)
            self._log(f"更新版本信息失败: {e}", "warning")
            return None


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

