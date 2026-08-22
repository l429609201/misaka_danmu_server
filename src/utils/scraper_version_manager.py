"""
弹幕源版本管理统一工具

负责管理唯一权威版本文件 scraper_manifest.json，从现有 package.json 和 versions.json 提取信息。
"""
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


class ScraperVersionManager:
    """弹幕源版本管理器

    核心职责：
    - 从 package.json + versions.json 提取并合并为统一的 manifest
    - 保存/加载 scraper_manifest.json
    - 版本比较和校验
    - 随 .so 文件同步复制 manifest
    """

    MANIFEST_FILENAME = "scraper_manifest.json"

    @staticmethod
    def extract_manifest_from_legacy(
        package_json_path: Path,
        versions_json_path: Path,
        scrapers_dir: Path
    ) -> Dict[str, Any]:
        """从现有 package.json + versions.json 提取信息构建完整的 manifest

        Args:
            package_json_path: package.json 文件路径
            versions_json_path: versions.json 文件路径
            scrapers_dir: 弹幕源 .so/.pyd 文件所在目录

        Returns:
            完整的 manifest 字典，包含所有必要信息
        """
        manifest: Dict[str, Any] = {
            "version": "unknown",
            "updated_at": datetime.now().isoformat(),
            "min_server_version": None,
            "platform": None,
            "branch": "main",  # 默认分支
            "sources": {}
        }

        # 从 package.json 提取全局版本号和各源的完整信息
        if package_json_path.exists():
            try:
                package_data = json.loads(package_json_path.read_text(encoding='utf-8'))
                manifest["version"] = package_data.get("version", "unknown")
                manifest["min_server_version"] = package_data.get("min_server_version") or package_data.get("min_fetchable_version")

                # 从 resources 提取各源详情
                resources = package_data.get("resources", {})
                for scraper_name, scraper_info in resources.items():
                    if isinstance(scraper_info, dict):
                        source_entry: Dict[str, Any] = {
                            "version": scraper_info.get("version"),
                        }

                        # 提取多架构哈希值
                        hashes = scraper_info.get("hashes", {})
                        if hashes and isinstance(hashes, dict):
                            source_entry["hashes"] = hashes

                        # 提取多架构文件路径信息
                        files = scraper_info.get("files", {})
                        if files and isinstance(files, dict):
                            source_entry["files"] = files

                        manifest["sources"][scraper_name] = source_entry

            except Exception as e:
                logger.warning(f"从 package.json 提取信息失败: {e}")

        # 从 versions.json 提取平台信息、分支信息和当前平台的哈希值
        if versions_json_path.exists():
            try:
                versions_data = json.loads(versions_json_path.read_text(encoding='utf-8'))

                # 优先使用 versions.json 的 updated_at（更准确）
                if "updated_at" in versions_data:
                    manifest["updated_at"] = versions_data["updated_at"]

                # 提取平台信息
                platform = versions_data.get("platform", "unknown")
                arch = versions_data.get("type", "unknown")
                if platform != "unknown" and arch != "unknown":
                    manifest["platform"] = f"{platform}-{arch}"
                elif platform != "unknown":
                    manifest["platform"] = platform
                elif arch != "unknown":
                    manifest["platform"] = arch

                # 提取分支信息
                if "branch" in versions_data:
                    manifest["branch"] = versions_data["branch"]

                # 补充 min_server_version（如果 package.json 没有）
                if not manifest["min_server_version"]:
                    manifest["min_server_version"] = versions_data.get("min_server_version")

                # 补充各源的版本号
                scrapers_versions = versions_data.get("scrapers", {})
                for scraper_name, version in scrapers_versions.items():
                    if scraper_name not in manifest["sources"]:
                        manifest["sources"][scraper_name] = {}
                    # 优先使用 package.json 的版本，如果没有则使用 versions.json 的
                    if not manifest["sources"][scraper_name].get("version"):
                        manifest["sources"][scraper_name]["version"] = version

                # 补充当前平台的哈希值（合并到 hashes 字典中）
                current_platform_key = manifest.get("platform", "unknown")
                hashes = versions_data.get("hashes", {})
                for scraper_name, hash_value in hashes.items():
                    if scraper_name not in manifest["sources"]:
                        manifest["sources"][scraper_name] = {}

                    # 确保 hashes 字段存在
                    if "hashes" not in manifest["sources"][scraper_name]:
                        manifest["sources"][scraper_name]["hashes"] = {}

                    # 添加当前平台的哈希值
                    manifest["sources"][scraper_name]["hashes"][current_platform_key] = hash_value

            except Exception as e:
                logger.warning(f"从 versions.json 提取信息失败: {e}")

        # 扫描实际 .so/.pyd 文件，补充文件名和文件大小信息
        if scrapers_dir.exists():
            for file_path in scrapers_dir.glob("*"):
                if file_path.suffix not in ['.so', '.pyd']:
                    continue

                # 提取弹幕源名称（文件名第一个点之前的部分）
                scraper_name = file_path.name.split('.')[0]

                # 跳过内部文件
                if scraper_name.startswith('_') or scraper_name == 'base':
                    continue

                if scraper_name not in manifest["sources"]:
                    manifest["sources"][scraper_name] = {}

                manifest["sources"][scraper_name]["filename"] = file_path.name
                manifest["sources"][scraper_name]["size"] = file_path.stat().st_size

        return manifest

    @staticmethod
    def save_manifest(manifest: Dict[str, Any], target_dir: Path) -> bool:
        """保存 manifest 到指定目录

        Args:
            manifest: manifest 字典
            target_dir: 目标目录

        Returns:
            是否保存成功
        """
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            manifest_file = target_dir / ScraperVersionManager.MANIFEST_FILENAME

            manifest_file.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False),
                encoding='utf-8'
            )

            logger.info(f"已保存 manifest 到: {manifest_file}")
            return True

        except Exception as e:
            logger.error(f"保存 manifest 失败: {e}", exc_info=True)
            return False

    @staticmethod
    def load_manifest(source_dir: Path) -> Optional[Dict[str, Any]]:
        """从目录加载 manifest

        Args:
            source_dir: 源目录

        Returns:
            manifest 字典，如果不存在或加载失败返回 None
        """
        manifest_file = source_dir / ScraperVersionManager.MANIFEST_FILENAME

        if not manifest_file.exists():
            return None

        try:
            manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
            return manifest
        except Exception as e:
            logger.warning(f"加载 manifest 失败: {e}")
            return None

    @staticmethod
    def compare_manifests(
        manifest_a: Optional[Dict[str, Any]],
        manifest_b: Optional[Dict[str, Any]]
    ) -> Tuple[int, str]:
        """比较两个 manifest 的版本

        **版本号是唯一权威依据**。updated_at 仅在版本号相同时用作辅助区分。

        why：此前优先比 updated_at。但 updated_at 是"文件被写入的时间"，不是"版本发布时间"。
        启动流程中 _ensure_manifest_exists / sync_manifest_with_binaries 会刷新运行目录的
        updated_at 为当前时刻，导致旧版运行目录的时间戳比新版备份目录更晚 → 恢复判定为
        "运行目录更新、无需恢复"，新版永远不生效。把版本号提到最高优先级彻底根治此问题。

        Args:
            manifest_a: 第一个 manifest
            manifest_b: 第二个 manifest

        Returns:
            (比较结果, 原因描述)
            比较结果: 1 表示 a 更新, -1 表示 b 更新, 0 表示相同或无法比较
        """
        if manifest_a is None and manifest_b is None:
            return (0, "两个 manifest 都不存在")

        if manifest_a is None:
            return (-1, "manifest_a 不存在")

        if manifest_b is None:
            return (1, "manifest_b 不存在")

        # 优先比较版本号（权威依据）
        version_a = manifest_a.get("version", "")
        version_b = manifest_b.get("version", "")

        if version_a and version_b and version_a != version_b:
            # 使用语义化版本比较
            try:
                parts_a = [int(x) for x in version_a.lstrip('v').split('.')]
                parts_b = [int(x) for x in version_b.lstrip('v').split('.')]
                max_len = max(len(parts_a), len(parts_b))
                parts_a.extend([0] * (max_len - len(parts_a)))
                parts_b.extend([0] * (max_len - len(parts_b)))
                for a, b in zip(parts_a, parts_b):
                    if a > b:
                        return (1, f"manifest_a 版本更高 ({version_a} > {version_b})")
                    elif a < b:
                        return (-1, f"manifest_b 版本更高 ({version_b} > {version_a})")
            except (ValueError, AttributeError):
                # 无法解析为语义化版本，回退字符串比较
                if version_a > version_b:
                    return (1, f"manifest_a 版本更高 ({version_a} > {version_b})")
                else:
                    return (-1, f"manifest_b 版本更高 ({version_b} > {version_a})")

        # 版本号相同时，比较 updated_at 作为辅助区分
        updated_at_a = manifest_a.get("updated_at", "")
        updated_at_b = manifest_b.get("updated_at", "")

        if updated_at_a and updated_at_b:
            if updated_at_a > updated_at_b:
                return (1, f"版本相同({version_a})，manifest_a 更新时间更晚")
            elif updated_at_a < updated_at_b:
                return (-1, f"版本相同({version_a})，manifest_b 更新时间更晚")

        return (0, "版本和时间戳相同")

    @staticmethod
    def sync_manifest_with_binaries(
        scrapers_dir: Path,
        backup_dir: Path,
        force_extract: bool = False
    ) -> bool:
        """同步 manifest 到 scrapers 和 backup 目录

        确保 manifest 随 .so/.pyd 文件一起被复制和同步。

        Args:
            scrapers_dir: scrapers 目录
            backup_dir: backup 目录
            force_extract: 是否强制从 legacy 文件重新提取（默认 False）

        Returns:
            是否同步成功
        """
        try:
            # 检查 scrapers 目录是否存在 manifest
            scrapers_manifest = ScraperVersionManager.load_manifest(scrapers_dir)

            # 如果不存在或需要强制提取，从 legacy 文件提取
            if scrapers_manifest is None or force_extract:
                logger.info("从 legacy 文件提取 manifest...")
                package_json = scrapers_dir / "package.json"
                versions_json = scrapers_dir / "versions.json"

                scrapers_manifest = ScraperVersionManager.extract_manifest_from_legacy(
                    package_json,
                    versions_json,
                    scrapers_dir
                )

                # 保存到 scrapers 目录
                ScraperVersionManager.save_manifest(scrapers_manifest, scrapers_dir)

            # 同步到 backup 目录
            if scrapers_manifest:
                ScraperVersionManager.save_manifest(scrapers_manifest, backup_dir)
                logger.info("已同步 manifest 到 backup 目录")
                return True

            return False

        except Exception as e:
            logger.error(f"同步 manifest 失败: {e}", exc_info=True)
            return False

    @staticmethod
    def get_version_from_manifest(manifest: Optional[Dict[str, Any]]) -> str:
        """从 manifest 获取全局版本号

        Args:
            manifest: manifest 字典

        Returns:
            版本号字符串，如果无法获取返回 "unknown"
        """
        if manifest is None:
            return "unknown"

        return manifest.get("version", "unknown")

    @staticmethod
    def has_binaries(directory: Path) -> bool:
        """检查目录是否包含弹幕源二进制文件

        Args:
            directory: 要检查的目录

        Returns:
            是否包含 .so 或 .pyd 文件
        """
        if not directory.exists():
            return False

        for file_path in directory.iterdir():
            if file_path.suffix in ['.so', '.pyd']:
                # 跳过内部文件
                if not file_path.name.startswith('_') and file_path.name.split('.')[0] != 'base':
                    return True

        return False

    @staticmethod
    def get_local_version(scrapers_dir: Path) -> str:
        """获取本地弹幕源的版本号

        Args:
            scrapers_dir: scrapers 目录路径

        Returns:
            版本号字符串，如果无法获取返回 "unknown"
        """
        manifest = ScraperVersionManager.load_manifest(scrapers_dir)
        return ScraperVersionManager.get_version_from_manifest(manifest)

    @staticmethod
    def get_min_server_version(scrapers_dir: Path) -> Optional[str]:
        """获取本地弹幕源要求的最低服务端版本

        Args:
            scrapers_dir: scrapers 目录路径

        Returns:
            最低服务端版本字符串，如果无法获取返回 None
        """
        manifest = ScraperVersionManager.load_manifest(scrapers_dir)
        if manifest is None:
            return None
        return manifest.get("min_server_version")

    @staticmethod
    def get_source_version(scrapers_dir: Path, scraper_name: str) -> str:
        """获取指定弹幕源的版本号

        Args:
            scrapers_dir: scrapers 目录路径
            scraper_name: 弹幕源名称

        Returns:
            版本号字符串，如果无法获取返回 "unknown"
        """
        manifest = ScraperVersionManager.load_manifest(scrapers_dir)
        if manifest is None:
            return "unknown"

        sources = manifest.get("sources", {})
        if scraper_name not in sources:
            return "unknown"

        return sources[scraper_name].get("version", "unknown")

    @staticmethod
    def get_all_sources(scrapers_dir: Path) -> Dict[str, Dict[str, Any]]:
        """获取所有弹幕源的信息

        Args:
            scrapers_dir: scrapers 目录路径

        Returns:
            弹幕源信息字典，格式: {scraper_name: {version, hash, filename, ...}}
        """
        manifest = ScraperVersionManager.load_manifest(scrapers_dir)
        if manifest is None:
            return {}

        return manifest.get("sources", {})

    @staticmethod
    def update_manifest_version(scrapers_dir: Path, version: str, min_server_version: Optional[str] = None) -> bool:
        """更新 manifest 的全局版本号和最低服务端版本

        Args:
            scrapers_dir: scrapers 目录路径
            version: 新的版本号
            min_server_version: 新的最低服务端版本（可选）

        Returns:
            是否更新成功
        """
        manifest = ScraperVersionManager.load_manifest(scrapers_dir)
        if manifest is None:
            logger.warning(f"无法加载 manifest，无法更新版本")
            return False

        manifest["version"] = version
        manifest["updated_at"] = datetime.now().isoformat()

        if min_server_version is not None:
            manifest["min_server_version"] = min_server_version

        return ScraperVersionManager.save_manifest(manifest, scrapers_dir)

    @staticmethod
    def update_source_info(
        scrapers_dir: Path,
        scraper_name: str,
        version: Optional[str] = None,
        hash_value: Optional[str] = None
    ) -> bool:
        """更新指定弹幕源的信息

        Args:
            scrapers_dir: scrapers 目录路径
            scraper_name: 弹幕源名称
            version: 新的版本号（可选）
            hash_value: 新的哈希值（可选）

        Returns:
            是否更新成功
        """
        manifest = ScraperVersionManager.load_manifest(scrapers_dir)
        if manifest is None:
            logger.warning(f"无法加载 manifest，无法更新弹幕源 {scraper_name} 的信息")
            return False

        if scraper_name not in manifest["sources"]:
            manifest["sources"][scraper_name] = {}

        if version is not None:
            manifest["sources"][scraper_name]["version"] = version

        if hash_value is not None:
            manifest["sources"][scraper_name]["hash"] = hash_value

        manifest["updated_at"] = datetime.now().isoformat()

        return ScraperVersionManager.save_manifest(manifest, scrapers_dir)

    @staticmethod
    def validate_manifest(manifest: Optional[Dict[str, Any]]) -> bool:
        """验证 manifest 的有效性

        Args:
            manifest: 要验证的 manifest 字典

        Returns:
            manifest 是否有效
        """
        if manifest is None:
            return False

        # 检查必需字段
        if "version" not in manifest:
            return False

        if "sources" not in manifest or not isinstance(manifest["sources"], dict):
            return False

        return True


    # ─── 统一文件搬运工具（只搬 manifest + 二进制） ───

    # 弹幕源二进制扩展名（跨平台）
    _BINARY_SUFFIXES = frozenset((".so", ".pyd"))

    @staticmethod
    def copy_scraper_files(
        src_dir: Path,
        dst_dir: Path,
        *,
        clear_dst: bool = False,
    ) -> int:
        """在两个目录之间搬运弹幕源文件。

        **只搬两类**：
        1. ``scraper_manifest.json``（权威版本文件）
        2. ``*.so`` / ``*.pyd``（编译后的弹幕源二进制）

        其余文件（``package.json``、``versions.json``、``backup_metadata.json`` 等）
        **不搬**。所有调用方应使用本方法替代各自实现的复制循环，确保"搬什么"的定义
        全局唯一、不再散落多处。

        Args:
            src_dir:   来源目录（运行目录、备份目录或临时目录均可）
            dst_dir:   目标目录（会自动 mkdir -p）
            clear_dst: True = 复制前先清空目标目录中的同类文件
                       （用于备份场景，保证目标只含最新版本；
                        恢复场景不建议开启，以免清空后复制中途失败导致两头都空）。

        Returns:
            实际复制的文件数
        """
        import shutil as _shutil

        dst_dir.mkdir(parents=True, exist_ok=True)

        manifest_name = ScraperVersionManager.MANIFEST_FILENAME

        if clear_dst:
            for f in dst_dir.iterdir():
                if f.is_file() and (
                    f.name == manifest_name
                    or f.suffix in ScraperVersionManager._BINARY_SUFFIXES
                ):
                    f.unlink(missing_ok=True)

        copied = 0
        for f in src_dir.iterdir():
            if not f.is_file():
                continue
            if f.name == manifest_name or f.suffix in ScraperVersionManager._BINARY_SUFFIXES:
                _shutil.copy2(f, dst_dir / f.name)
                copied += 1

        return copied