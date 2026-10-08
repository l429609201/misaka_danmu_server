"""
弹幕源更新工具模块

负责弹幕源的自动更新和全量更新逻辑。
"""
import asyncio
import hashlib
import json
import logging
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, Any, Optional, Callable
from datetime import datetime

import httpx

from .scraper_version_manager import ScraperVersionManager

logger = logging.getLogger(__name__)


class ScraperUpdateManager:
    """弹幕源更新管理器

    核心职责：
    - 从远程下载弹幕源资源包
    - 执行全量更新（下载完整 ZIP 并解压）
    - 执行增量更新（仅下载变更的 .so/.pyd 文件）
    - 更新后同步 manifest 和 legacy 文件
    """

    def __init__(self, scrapers_dir: Path, backup_dir: Path):
        """初始化更新管理器

        Args:
            scrapers_dir: 弹幕源目录
            backup_dir: 备份目录
        """
        self.scrapers_dir = scrapers_dir
        self.backup_dir = backup_dir

    async def download_full_package(
        self,
        download_url: str,
        progress_callback: Optional[Callable[[str], None]] = None,
        timeout: int = 300
    ) -> Path:
        """下载完整的弹幕源资源包

        Args:
            download_url: 下载 URL
            progress_callback: 进度回调函数
            timeout: 超时时间（秒）

        Returns:
            下载的临时文件路径

        Raises:
            Exception: 下载失败时抛出异常
        """
        if progress_callback:
            progress_callback("开始下载资源包...")

        temp_file = Path(tempfile.mktemp(suffix=".zip"))

        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                async with client.stream("GET", download_url) as response:
                    response.raise_for_status()

                    total_size = int(response.headers.get("content-length", 0))
                    downloaded = 0

                    with temp_file.open("wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            f.write(chunk)
                            downloaded += len(chunk)

                            if progress_callback and total_size > 0:
                                percent = (downloaded / total_size) * 100
                                progress_callback(f"下载中: {percent:.1f}% ({downloaded}/{total_size})")

            if progress_callback:
                progress_callback("下载完成")

            logger.info(f"资源包下载完成: {temp_file}, 大小: {temp_file.stat().st_size} bytes")
            return temp_file

        except Exception as e:
            if temp_file.exists():
                temp_file.unlink()
            logger.error(f"下载资源包失败: {e}")
            raise

    async def extract_and_install(
        self,
        zip_path: Path,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> Dict[str, Any]:
        """解压并安装弹幕源资源包

        Args:
            zip_path: ZIP 文件路径
            progress_callback: 进度回调函数

        Returns:
            安装结果统计信息

        Raises:
            Exception: 解压或安装失败时抛出异常
        """
        if progress_callback:
            progress_callback("开始解压资源包...")

        temp_dir = Path(tempfile.mkdtemp())
        installed_count = 0

        try:
            # 解压到临时目录
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)

            if progress_callback:
                progress_callback("解压完成，开始安装...")

            # 确保目标目录存在
            self.scrapers_dir.mkdir(parents=True, exist_ok=True)

            # 复制 .so/.pyd 文件
            for file_path in temp_dir.rglob("*"):
                if file_path.suffix in ['.so', '.pyd']:
                    target_path = self.scrapers_dir / file_path.name
                    shutil.copy2(file_path, target_path)
                    installed_count += 1
                    logger.debug(f"已安装: {file_path.name}")

            # 复制 package.json
            package_json = temp_dir / "package.json"
            if package_json.exists():
                shutil.copy2(package_json, self.scrapers_dir / "package.json")
                logger.info("已安装 package.json")

            # 复制 versions.json
            versions_json = temp_dir / "versions.json"
            if versions_json.exists():
                shutil.copy2(versions_json, self.scrapers_dir / "versions.json")
                logger.info("已安装 versions.json")

            # 复制 manifest（如果存在）
            manifest_json = temp_dir / ScraperVersionManager.MANIFEST_FILENAME
            if manifest_json.exists():
                shutil.copy2(manifest_json, self.scrapers_dir / ScraperVersionManager.MANIFEST_FILENAME)
                logger.info("已安装 scraper_manifest.json")
            else:
                # 如果 ZIP 中没有 manifest，生成一个
                try:
                    manifest = ScraperVersionManager.extract_manifest_from_legacy(
                        self.scrapers_dir / "package.json",
                        self.scrapers_dir / "versions.json",
                        self.scrapers_dir
                    )
                    ScraperVersionManager.save_manifest(manifest, self.scrapers_dir)
                    logger.info("已生成 scraper_manifest.json")
                except Exception as e:
                    logger.warning(f"生成 manifest 失败: {e}")

            if progress_callback:
                progress_callback(f"安装完成: {installed_count} 个文件")

            return {
                "installed_count": installed_count,
                "has_package_json": package_json.exists(),
                "has_versions_json": versions_json.exists()
            }

        finally:
            # 清理临时目录
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def download_single_file(
        self,
        file_url: str,
        target_filename: str,
        expected_hash: Optional[str] = None,
        timeout: int = 60
    ) -> bool:
        """下载单个弹幕源文件



        Args:
            file_url: 文件下载 URL
            target_filename: 目标文件名
            expected_hash: 期望的文件哈希值（用于校验）
            timeout: 超时时间（秒）

        Returns:
            是否下载成功
        """
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(file_url)
                response.raise_for_status()

                content = response.content

                # 如果提供了哈希值，进行校验
                if expected_hash:
                    actual_hash = hashlib.sha256(content).hexdigest()
                    if actual_hash != expected_hash:
                        logger.error(
                            f"文件哈希校验失败: {target_filename}\n"
                            f"  期望: {expected_hash}\n"
                            f"  实际: {actual_hash}"
                        )
                        return False

                # 写入文件
                target_path = self.scrapers_dir / target_filename
                target_path.write_bytes(content)

                logger.info(f"已下载: {target_filename} ({len(content)} bytes)")
                return True

        except Exception as e:
            logger.error(f"下载文件失败 {target_filename}: {e}")
            return False

    async def incremental_update(
        self,
        remote_manifest: Dict[str, Any],
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> Dict[str, Any]:
        """增量更新弹幕源

        对比本地和远程 manifest，只下载变更的文件。

        Args:
            remote_manifest: 远程 manifest 数据
            progress_callback: 进度回调函数

        Returns:
            更新结果统计信息
        """
        if progress_callback:
            progress_callback("检查本地版本...")

        # 加载本地 manifest
        local_manifest = ScraperVersionManager.load_manifest(self.scrapers_dir)

        # 找出需要更新的文件
        to_update = []
        to_add = []

        remote_sources = remote_manifest.get("sources", {})
        local_sources = local_manifest.get("sources", {}) if local_manifest else {}

        for scraper_name, remote_info in remote_sources.items():
            remote_version = remote_info.get("version")
            remote_hash = remote_info.get("hash")
            remote_filename = remote_info.get("filename")

            if not remote_filename:
                logger.warning(f"远程 manifest 缺少 {scraper_name} 的 filename 信息")
                continue

            # 检查本地是否存在
            if scraper_name not in local_sources:
                to_add.append({
                    "name": scraper_name,
                    "filename": remote_filename,
                    "version": remote_version,
                    "hash": remote_hash
                })
            else:
                local_info = local_sources[scraper_name]
                local_version = local_info.get("version")
                local_hash = local_info.get("hash")

                # 版本号或哈希值不同，需要更新
                if remote_version != local_version or remote_hash != local_hash:
                    to_update.append({
                        "name": scraper_name,
                        "filename": remote_filename,
                        "version": remote_version,
                        "hash": remote_hash,
                        "old_version": local_version
                    })

        total_count = len(to_update) + len(to_add)

        if total_count == 0:
            if progress_callback:
                progress_callback("所有弹幕源已是最新版本")
            return {
                "updated_count": 0,
                "added_count": 0,
                "failed_count": 0
            }

        if progress_callback:
            progress_callback(f"发现 {len(to_update)} 个更新, {len(to_add)} 个新增")

        # 执行下载
        updated_count = 0
        added_count = 0
        failed_count = 0

        # 构造下载 URL（需要从 remote_manifest 获取 base_url）
        base_url = remote_manifest.get("base_url", "")

        for item in to_update:
            if progress_callback:
                progress_callback(f"更新 {item['name']} ({item['old_version']} -> {item['version']})...")

            file_url = f"{base_url}/{item['filename']}"
            success = await self.download_single_file(
                file_url,
                item['filename'],
                item.get('hash')
            )

            if success:
                updated_count += 1
            else:
                failed_count += 1

        for item in to_add:
            if progress_callback:
                progress_callback(f"新增 {item['name']} ({item['version']})...")

            file_url = f"{base_url}/{item['filename']}"
            success = await self.download_single_file(
                file_url,
                item['filename'],
                item.get('hash')
            )

            if success:
                added_count += 1
            else:
                failed_count += 1

        # 更新本地 manifest
        try:
            if local_manifest is None:
                local_manifest = remote_manifest
            else:
                local_manifest["version"] = remote_manifest.get("version", local_manifest["version"])
                local_manifest["updated_at"] = datetime.now().isoformat()
                local_manifest["min_server_version"] = remote_manifest.get(
                    "min_server_version",
                    local_manifest.get("min_server_version")
                )

                # 更新各源信息
                for scraper_name, remote_info in remote_sources.items():
                    local_manifest["sources"][scraper_name] = remote_info

            ScraperVersionManager.save_manifest(local_manifest, self.scrapers_dir)

            if progress_callback:
                progress_callback("已更新 manifest")

        except Exception as e:
            logger.error(f"更新 manifest 失败: {e}")

        if progress_callback:
            msg = f"更新完成: 更新 {updated_count} 个, 新增 {added_count} 个, 失败 {failed_count} 个"
            progress_callback(msg)

        return {
            "updated_count": updated_count,
            "added_count": added_count,
            "failed_count": failed_count
        }
