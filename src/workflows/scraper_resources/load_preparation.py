"""弹幕源加载前的备份恢复、版本整合与旧文件清理流程。"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from src.services.file_storage_service import get_file_storage_service, wait_for_settlement
from src.services.scraper_manager import ScraperPaths
from src.workflows.scraper_resources.version_manager import ScraperVersionManager


class ScraperLoadPreparation:
    """组装加载前资源操作；不承担来源实例或数据库生命周期。"""

    def __init__(self) -> None:
        self._fs = get_file_storage_service()

    async def prepare(self, paths: ScraperPaths, skip_backup_restore: bool = False) -> Optional[Dict[str, Any]]:
        """在线程中完成资源准备，热加载时禁止从备份覆盖已部署文件。"""
        return await wait_for_settlement(asyncio.to_thread(self._prepare_sync, paths, skip_backup_restore))

    def _prepare_sync(self, paths: ScraperPaths, skip_backup_restore: bool) -> Optional[Dict[str, Any]]:
        """整段在线程内顺序执行，取消后也等待文件操作收尾。"""
        self._fs.resource_mkdir(paths.scrapers_dir, parents=True, exist_ok=True)
        if not skip_backup_restore:
            self._restore_from_backup_if_needed(paths)
        else:
            logging.getLogger(__name__).debug("跳过备份恢复检查（热加载模式）")
        return self._finish_preparation(paths.scrapers_dir)

    def _finish_preparation(self, scrapers_dir: Path) -> Optional[Dict[str, Any]]:
        self._ensure_manifest_exists(scrapers_dir)
        # 版本整合失败时保留旧清单，下一轮仍可恢复而不丢失版本信息。
        manifest = ScraperVersionManager.load_manifest(scrapers_dir)
        if ScraperVersionManager.validate_manifest(manifest):
            self._cleanup_legacy_version_files(scrapers_dir)
        self._check_version_file_integrity(scrapers_dir)
        return manifest

    def _restore_from_backup_if_needed(self, paths: ScraperPaths) -> None:
        """检查并从备份恢复爬虫文件（如果需要）"""
        scrapers_dir = paths.scrapers_dir
        backup_dir = paths.backup_dir

        # 检查 scrapers 目录是否为空(没有 .so/.pyd 文件)
        has_scrapers = any(
            f.suffix in ['.so', '.pyd']
            for f in self._fs.resource_iterdir(scrapers_dir)
            if self._fs.resource_is_file(f)
        )

        # 判断是否需要恢复
        should_restore = False
        restore_reason = ""

        if not has_scrapers and self._fs.resource_exists(backup_dir):
            # 情况1: scrapers 目录为空但有备份
            backup_files = list(self._fs.resource_glob(backup_dir, "*.so")) + list(self._fs.resource_glob(backup_dir, "*.pyd"))
            if backup_files:
                should_restore = True
                restore_reason = f"scrapers 目录为空但存在备份 ({len(backup_files)} 个文件)"
        elif has_scrapers and self._fs.resource_exists(backup_dir):
            # 情况2: 备份目录有更新的版本（通过比较 manifest）
            scrapers_manifest = ScraperVersionManager.load_manifest(scrapers_dir)
            backup_manifest = ScraperVersionManager.load_manifest(backup_dir)

            if backup_manifest and scrapers_manifest:
                result, reason = ScraperVersionManager.compare_manifests(
                    scrapers_manifest,
                    backup_manifest
                )
                if result < 0:  # 备份更新
                    should_restore = True
                    restore_reason = f"备份目录版本更新: {reason}"

        if should_restore:
            self._perform_backup_restore(backup_dir, scrapers_dir, restore_reason)

    def _perform_backup_restore(self, backup_dir: Path, scrapers_dir: Path, reason: str) -> None:
        """执行备份恢复操作

        使用 ScraperVersionManager.copy_scraper_files 统一搬运，**只搬 manifest + *.so/.pyd**。
        不再搬 legacy 文件（package.json / versions.json），也不再反向修改备份目录的 manifest。
        """
        # 预检：备份目录必须有二进制文件
        backup_binaries = [
            f for f in self._fs.resource_iterdir(backup_dir)
            if self._fs.resource_is_file(f) and f.suffix in ScraperVersionManager._BINARY_SUFFIXES
        ] if self._fs.resource_exists(backup_dir) else []
        if not backup_binaries:
            return

        logger = logging.getLogger(__name__)

        # 从 manifest 读取版本号用于日志对比
        backup_version = ScraperVersionManager.get_version_from_manifest(
            ScraperVersionManager.load_manifest(backup_dir)
        )
        scrapers_version = ScraperVersionManager.get_version_from_manifest(
            ScraperVersionManager.load_manifest(scrapers_dir)
        )

        logger.info(f"检测到需要从备份恢复: {reason}")
        logger.info(
            f"备份恢复详情:\n"
            f"  备份版本: {backup_version}\n"
            f"  运行版本: {scrapers_version}\n"
            f"  备份二进制数: {len(backup_binaries)}"
        )

        # 使用统一搬运工具：只搬 manifest + 二进制，不搬 legacy 文件，不反向写源目录
        copied = ScraperVersionManager.copy_scraper_files(backup_dir, scrapers_dir)

        logger.info(f"备份恢复完成 - 已复制 {copied} 个文件，当前版本: {backup_version}")

    def _ensure_manifest_exists(self, scrapers_dir: Path) -> None:
        """确保 manifest 文件存在且格式正确，如不存在或格式错误则从 legacy 文件提取生成"""
        logger = logging.getLogger(__name__)
        manifest_path = scrapers_dir / ScraperVersionManager.MANIFEST_FILENAME

        # 空目录不生成 manifest
        # why：删除源接口会先删掉 .so 与 manifest，随后调用 load_and_sync_scrapers。
        # 若此处无条件重建，会在空目录上产出一份没有 sources 的空壳 manifest，
        # 表现为"源已删除但 scraper_manifest.json 还在、本地版本显示 unknown"。
        has_binary = self._fs.resource_exists(scrapers_dir) and any(
            ScraperVersionManager.is_scraper_binary(p) for p in self._fs.resource_iterdir(scrapers_dir)
        )
        if not has_binary:
            if self._fs.resource_exists(manifest_path):
                logger.debug("运行目录无弹幕源二进制，跳过 manifest 重建")
            return

        # 检查是否存在且格式正确
        need_regenerate = False
        if self._fs.resource_exists(manifest_path):
            manifest = ScraperVersionManager.load_manifest(scrapers_dir)
            if not manifest or not ScraperVersionManager.validate_manifest(manifest):
                logger.warning("现有 manifest 格式不正确，将重新生成")
                need_regenerate = True
        else:
            need_regenerate = True

        if not need_regenerate:
            return

        try:
            manifest = ScraperVersionManager.extract_manifest_from_legacy(
                scrapers_dir / "package.json",
                scrapers_dir / "versions.json",
                scrapers_dir
            )
            if not ScraperVersionManager.save_manifest(manifest, scrapers_dir):
                logger.warning("生成 manifest 失败，保留 legacy 文件供下次恢复")
                return

            # 不同步到备份目录
            # why：备份目录的 manifest 必须与其自身的 .so 保持一致。运行目录重建出的
            # manifest 反映的是运行目录状态，写进备份会造成"备份 .so 与 manifest 错配"，
            # 之后从备份还原会拿到错误的版本与哈希信息。
            logger.info("已生成/更新 scraper_manifest.json")
        except Exception as e:
            logger.warning(f"生成 manifest 失败: {e}")

    def _cleanup_legacy_version_files(self, scrapers_dir: Path) -> None:
        """
        清理 scrapers 目录中的 legacy 版本文件（package.json 和 versions.json）

        这些文件已被 scraper_manifest.json 取代，不再需要保留在运行目录中。
        """
        logger = logging.getLogger(__name__)
        legacy_files = ["package.json", "versions.json"]

        for filename in legacy_files:
            file_path = scrapers_dir / filename
            if self._fs.resource_exists(file_path):
                try:
                    self._fs.resource_unlink(file_path)
                    logger.info(f"✓ 已清理 legacy 文件: {filename}")
                except Exception as e:
                    logger.warning(f"清理 {filename} 失败: {e}")

    def _check_version_file_integrity(self, scrapers_dir: Path) -> None:
        """检查 manifest 文件完整性

        使用统一的 scraper_manifest.json 进行版本检查。

        Args:
            scrapers_dir: scrapers 目录路径
        """
        logger = logging.getLogger(__name__)

        # 检查 manifest 是否存在
        manifest = ScraperVersionManager.load_manifest(scrapers_dir)
        if manifest is None:
            logger.warning(
                "启动检查: scraper_manifest.json 不存在。"
                "将在后续步骤中自动生成。"
            )
            return

        # 验证 manifest 格式
        if not ScraperVersionManager.validate_manifest(manifest):
            logger.warning("启动检查: scraper_manifest.json 格式不完整或不正确")
            return

        # 完整性检查通过
        version = manifest.get("version", "unknown")
        updated_at = manifest.get("updated_at", "N/A")
        source_count = len(manifest.get("sources", {}))

        logger.info(
            f"启动检查: 版本文件完整性正常\n"
            f"  全局版本: {version}\n"
            f"  更新时间: {updated_at}\n"
            f"  弹幕源数量: {source_count}"
        )

