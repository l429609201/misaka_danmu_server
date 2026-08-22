import asyncio
import importlib
import inspect
import json
import logging
import pkgutil
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type, TYPE_CHECKING
from urllib.parse import urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.env import is_docker_environment
from src.db import ConfigManager, crud, models, orm_models
from src.scrapers.base import BaseScraper
from src.utils import TransportManager
from src.utils.buffered_logging import BufferedLogHandler, create_buffered_logger, flush_buffered_logs
from src.utils.scraper_version_manager import ScraperVersionManager

# 从 models 导入需要的类
ProviderSearchInfo = models.ProviderSearchInfo
ScraperSetting = models.ScraperSetting

if TYPE_CHECKING:
    from .metadata_manager import MetadataSourceManager


@dataclass
class ScraperPaths:
    """爬虫目录路径配置"""
    scrapers_dir: Path
    backup_dir: Path

    @classmethod
    def from_environment(cls) -> 'ScraperPaths':
        """根据运行环境返回相应的路径配置"""
        if is_docker_environment():
            return cls(
                scrapers_dir=Path("/app/src/scrapers"),
                backup_dir=Path("/app/config/scrapers_backup")
            )
        return cls(
            scrapers_dir=Path("src/scrapers"),
            backup_dir=Path("config/scrapers_backup")
        )


@dataclass
class ModuleDiscoveryResult:
    """模块发现结果"""
    discovered_providers: List[str]
    failed_providers: List[str]
    default_configs: Dict[str, Tuple[Any, str]]


def _version_satisfies(current: str, minimum: str) -> bool:
    """比较语义版本号，返回 current >= minimum。解析失败时默认放行。"""
    try:
        cur = tuple(int(x) for x in current.strip().split('.')[:3])
        min_ = tuple(int(x) for x in minimum.strip().split('.')[:3])
        return cur >= min_
    except Exception:
        return True


class ScraperManager:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], config_manager: ConfigManager, metadata_manager: "MetadataSourceManager", transport_manager: TransportManager):
        self.scrapers: Dict[str, BaseScraper] = {}
        self._scraper_classes: Dict[str, Type[BaseScraper]] = {}
        self._scraper_versions: Dict[str, str] = {}  # 存储每个源的版本号
        # why：版本不兼容被跳过的源只打 WARNING 日志，前端完全不感知；
        #      用独立的内存属性记录，通过专门的 /load-check 接口暴露给前端展示 Alert，
        #      不污染现有 ScraperSettingWithConfig 响应模型，也不修改任何 DB 表结构。
        self._version_skipped: Dict[str, str] = {}   # {provider_name: required_version}
        self._global_version_skip: Optional[str] = None  # 全局版本不满足时记录要求版本
        self.scraper_settings: Dict[str, Dict[str, Any]] = {}
        self._session_factory = session_factory
        self._domain_map: Dict[str, str] = {}
        self._search_locks: set[str] = set()
        # 存储最后一次 search_all 的单源耗时信息: [(provider_name, duration_ms, result_count), ...]
        self.last_search_timing: List[Tuple[str, float, int]] = []
        # 编辑导入展示用：分集列表命中源缓存时，仍保留最近一次黑名单过滤明细。
        self._episode_filtered_details: Dict[Tuple[str, str], list] = {}
        self._webhook_search_locks: set[str] = set()  # Webhook 搜索锁（基于 animeTitle-season）
        self._lock = asyncio.Lock()
        self.config_manager = config_manager
        self.metadata_manager = metadata_manager
        self.transport_manager = transport_manager

    async def acquire_search_lock(self, api_key: str) -> bool:
        """Acquires a search lock for a given API key. Returns False if already locked."""
        async with self._lock:
            if api_key in self._search_locks:
                logging.getLogger(__name__).warning(f"API key '{api_key[:8]}...' tried to start a new search while another was running.")
                return False
            self._search_locks.add(api_key)
            logging.getLogger(__name__).info(f"Search lock acquired for API key '{api_key[:8]}...'.")
            return True

    async def release_search_lock(self, api_key: str):
        """Releases the search lock for a given API key."""
        async with self._lock:
            self._search_locks.discard(api_key)
            logging.getLogger(__name__).info(f"Search lock released for API key '{api_key[:8]}...'.")

    async def acquire_webhook_search_lock(self, lock_key: str) -> bool:
        """获取 Webhook 搜索锁。基于 animeTitle-season 的锁，防止同一作品同季的多个请求同时搜索。"""
        async with self._lock:
            if lock_key in self._webhook_search_locks:
                logging.getLogger(__name__).info(f"Webhook 搜索锁已被占用: '{lock_key}'，跳过重复搜索。")
                return False
            self._webhook_search_locks.add(lock_key)
            logging.getLogger(__name__).info(f"Webhook 搜索锁已获取: '{lock_key}'。")
            return True

    def _check_version_file_integrity(self, scrapers_dir: Path):
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

    async def release_webhook_search_lock(self, lock_key: str):
        """释放 Webhook 搜索锁。"""
        async with self._lock:
            self._webhook_search_locks.discard(lock_key)
            logging.getLogger(__name__).info(f"Webhook 搜索锁已释放: '{lock_key}'。")

    def _cleanup_existing_state(self):
        """清理现有爬虫状态，为重新加载做准备"""
        self.scrapers.clear()
        self._scraper_classes.clear()
        self._scraper_versions.clear()
        self._version_skipped.clear()
        self._global_version_skip = None
        self.scraper_settings.clear()

    def _get_scraper_paths(self) -> ScraperPaths:
        """获取爬虫目录路径配置"""
        return ScraperPaths.from_environment()

    async def _restore_from_backup_if_needed(self, paths: ScraperPaths):
        """检查并从备份恢复爬虫文件（如果需要）"""
        scrapers_dir = paths.scrapers_dir
        backup_dir = paths.backup_dir

        # 检查 scrapers 目录是否为空(没有 .so/.pyd 文件)
        has_scrapers = any(
            f.suffix in ['.so', '.pyd']
            for f in scrapers_dir.iterdir()
            if f.is_file()
        )

        # 判断是否需要恢复
        should_restore = False
        restore_reason = ""

        if not has_scrapers and backup_dir.exists():
            # 情况1: scrapers 目录为空但有备份
            backup_files = list(backup_dir.glob("*.so")) + list(backup_dir.glob("*.pyd"))
            if backup_files:
                should_restore = True
                restore_reason = f"scrapers 目录为空但存在备份 ({len(backup_files)} 个文件)"
        elif has_scrapers and backup_dir.exists():
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
            await self._perform_backup_restore(backup_dir, scrapers_dir, restore_reason)

    async def _perform_backup_restore(self, backup_dir: Path, scrapers_dir: Path, reason: str):
        """执行备份恢复操作

        使用 ScraperVersionManager.copy_scraper_files 统一搬运，**只搬 manifest + *.so/.pyd**。
        不再搬 legacy 文件（package.json / versions.json），也不再反向修改备份目录的 manifest。
        """
        # 预检：备份目录必须有二进制文件
        backup_binaries = [
            f for f in backup_dir.iterdir()
            if f.is_file() and f.suffix in ScraperVersionManager._BINARY_SUFFIXES
        ] if backup_dir.exists() else []
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

    def _ensure_manifest_exists(self, scrapers_dir: Path):
        """确保 manifest 文件存在且格式正确，如不存在或格式错误则从 legacy 文件提取生成"""
        logger = logging.getLogger(__name__)
        manifest_path = scrapers_dir / ScraperVersionManager.MANIFEST_FILENAME

        # 检查是否存在且格式正确
        need_regenerate = False
        if manifest_path.exists():
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
            ScraperVersionManager.save_manifest(manifest, scrapers_dir)

            # 同步到备份目录
            backup_dir = self._get_scraper_paths().backup_dir
            if backup_dir.exists():
                ScraperVersionManager.save_manifest(manifest, backup_dir)

            logger.info("已生成/更新 scraper_manifest.json")
        except Exception as e:
            logger.warning(f"生成 manifest 失败: {e}")

    def _cleanup_legacy_version_files(self, scrapers_dir: Path):
        """
        清理 scrapers 目录中的 legacy 版本文件（package.json 和 versions.json）

        这些文件已被 scraper_manifest.json 取代，不再需要保留在运行目录中。
        """
        logger = logging.getLogger(__name__)
        legacy_files = ["package.json", "versions.json"]

        for filename in legacy_files:
            file_path = scrapers_dir / filename
            if file_path.exists():
                try:
                    file_path.unlink()
                    logger.info(f"✓ 已清理 legacy 文件: {filename}")
                except Exception as e:
                    logger.warning(f"清理 {filename} 失败: {e}")

    async def load_and_sync_scrapers(self, skip_backup_restore: bool = False):
        """
        动态发现、同步到数据库并根据数据库设置加载搜索源。
        此方法可以被再次调用以重新加载搜索源。

        Args:
            skip_backup_restore: True = 跳过备份恢复检查。
                用于 executor 热加载场景——文件已经由 apply_deferred_overlay 就位。
        """
        # 清理现有爬虫以确保全新加载
        await self.close_all()
        self._cleanup_existing_state()

        # 获取路径配置
        paths = self._get_scraper_paths()

        # 检查是否需要从备份恢复
        if not skip_backup_restore:
            await self._restore_from_backup_if_needed(paths)
        else:
            logging.getLogger(__name__).debug("跳过备份恢复检查（热加载模式）")

        # 确保 manifest 文件存在
        self._ensure_manifest_exists(paths.scrapers_dir)

        # 清理 legacy 版本文件（package.json 和 versions.json）
        self._cleanup_legacy_version_files(paths.scrapers_dir)

        # 版本文件完整性检查
        self._check_version_file_integrity(paths.scrapers_dir)

        # 全局版本检查
        if not await self._check_global_version_compatibility(paths.scrapers_dir):
            return

        # 发现并加载模块
        result = await self._discover_and_load_modules(paths.scrapers_dir)

        # 注册默认配置
        if result.default_configs:
            await self._register_default_configs(result.default_configs)

        # 远程版本校验
        if await self._check_remote_min_version():
            return

        # 同步到数据库
        await self._sync_to_database(result.discovered_providers, result.failed_providers)

        # 实例化爬虫
        await self._instantiate_scrapers()

    async def _check_global_version_compatibility(self, scrapers_dir: Path) -> bool:
        """
        检查全局版本兼容性。

        Returns:
            bool: True 表示版本兼容可以继续，False 表示版本不兼容需要跳过加载
        """
        manifest = ScraperVersionManager.load_manifest(scrapers_dir)
        if not manifest:
            return True

        global_min_version = manifest.get("min_server_version")
        if not global_min_version:
            return True

        from src._version import APP_VERSION
        if not _version_satisfies(APP_VERSION, global_min_version):
            logging.getLogger(__name__).warning(
                f"弹幕源包要求服务器版本 >= {global_min_version}，"
                f"当前版本 {APP_VERSION}，跳过全部弹幕源加载"
            )
            self._global_version_skip = global_min_version
            return False

        return True

    async def _register_default_configs(self, default_configs: Dict[str, Tuple[Any, str]]):
        """注册爬虫的默认配置"""
        try:
            await self.config_manager.register_defaults(default_configs)
            logging.getLogger(__name__).info(
                f"已为 {len(default_configs)} 个搜索源注册默认配置。"
            )
        except Exception as e:
            logging.getLogger(__name__).error(
                f"注册弹幕源默认配置时出错（已跳过，不影响启动）: {e}", exc_info=True
            )

    async def _sync_to_database(self, discovered_providers: List[str], failed_providers: List[str]):
        """将发现的爬虫同步到数据库"""
        async with self._session_factory() as session:
            # 清理数据库中不再存在的源（只保留成功加载的 + custom）
            providers_to_keep = discovered_providers + ['custom']
            await crud.remove_stale_scrapers(session, providers_to_keep)

            # 确保所有发现的搜索源和 'custom' 源都存在于数据库中
            providers_to_sync = discovered_providers + ['custom']
            await crud.sync_scrapers_to_db(session, providers_to_sync)

            # 按用户保存的顺序快照重排 display_order
            await crud.apply_scraper_order_from_snapshot(session)

            # 重新加载所有设置
            settings_list = await crud.get_all_scraper_settings(session)

        self.scraper_settings = {s['providerName']: s for s in settings_list}

    async def _instantiate_scrapers(self):
        """实例化所有已发现的爬虫类"""
        enabled_count = 0
        disabled_count = 0
        scraper_items = []  # (order, name)

        for provider_name, scraper_class in list(self._scraper_classes.items()):
            try:
                scraper_instance = scraper_class(
                    self._session_factory,
                    self.config_manager,
                    self.transport_manager
                )
                # 设置 scraper_manager 引用，以便使用缓存的配置
                scraper_instance._scraper_manager_ref = self
            except Exception as e:
                logging.getLogger(__name__).error(
                    f"实例化搜索源 '{provider_name}' 失败，已跳过该源: {e}", exc_info=True
                )
                self._scraper_classes.pop(provider_name, None)
                self._scraper_versions.pop(provider_name, None)
                # 同步清理域名映射，避免 URL 路由到一个不存在的实例
                for domain in [d for d, p in self._domain_map.items() if p == provider_name]:
                    self._domain_map.pop(domain, None)
                continue

            self.scrapers[provider_name] = scraper_instance
            setting = self.scraper_settings.get(provider_name, {})

            is_enabled = setting.get('isEnabled', True)
            try:
                order = int(setting.get('displayOrder', 999))
            except (TypeError, ValueError):
                order = 999

            if is_enabled:
                enabled_count += 1
            else:
                disabled_count += 1
            scraper_items.append((order, provider_name))

            if not setting:
                logging.getLogger(__name__).warning(
                    f"已加载搜索源 '{provider_name}'，但在数据库中未找到其设置。"
                )

        # 汇总输出（按顺序排列）
        scraper_items.sort(key=lambda x: (x[0], x[1]))
        total = enabled_count + disabled_count
        log_lines = [f"已加载 {total} 个搜索源 (已启用: {enabled_count}, 已禁用: {disabled_count})"]
        for order, name in scraper_items:
            log_lines.append(f"  - (顺序: {order:02d}) {name}")
        logging.getLogger(__name__).info("\n".join(log_lines))

    async def _discover_and_load_modules(self, scrapers_dir: Path) -> ModuleDiscoveryResult:
        """
        发现并加载爬虫模块。

        Returns:
            ModuleDiscoveryResult: 包含发现的提供者、失败的提供者和默认配置
        """
        self._domain_map.clear()
        discovered_providers = []
        failed_providers = []
        default_configs = {}

        # 从 manifest 读取各源版本号
        versions_from_file = self._load_versions_from_manifest(scrapers_dir)

        # 遍历所有模块文件
        for file_path in sorted(scrapers_dir.iterdir()):
            if not self._is_valid_module_file(file_path):
                continue

            # 防御性检查：跳过损坏的二进制文件
            if file_path.name.endswith((".so", ".pyd")):
                if not self._check_binary_file_integrity(file_path, failed_providers):
                    continue

            module_name_stem = file_path.stem.split('.')[0]
            if module_name_stem.startswith("_") or module_name_stem == "base":
                continue

            # 加载单个模块
            result = await self._load_single_module(
                module_name_stem,
                versions_from_file,
                default_configs
            )

            if result:
                discovered_providers.append(result)
            else:
                failed_providers.append(module_name_stem)

        return ModuleDiscoveryResult(
            discovered_providers=discovered_providers,
            failed_providers=failed_providers,
            default_configs=default_configs
        )

    def _load_versions_from_manifest(self, scrapers_dir: Path) -> Dict[str, str]:
        """从 manifest 读取版本信息"""
        versions = {}
        manifest = ScraperVersionManager.load_manifest(scrapers_dir)
        if manifest:
            for provider, info in manifest.get("sources", {}).items():
                ver = info.get("version")
                if ver:
                    versions[provider] = ver
            logging.getLogger(__name__).debug(
                f"从 manifest 读取到 {len(versions)} 个源的版本信息"
            )
        return versions

    def _is_valid_module_file(self, file_path: Path) -> bool:
        """检查文件是否是有效的模块文件"""
        return (
            file_path.name.endswith(".py") or
            file_path.name.endswith(".so") or
            file_path.name.endswith(".pyd")
        )

    def _check_binary_file_integrity(self, file_path: Path, failed_providers: List[str]) -> bool:
        """检查二进制文件完整性，返回 True 表示文件正常"""
        try:
            fsize = file_path.stat().st_size
            if fsize == 0:
                logging.getLogger(__name__).warning(
                    f"跳过 0 字节文件: {file_path.name}（文件损坏或下载不完整）"
                )
                failed_providers.append(file_path.stem.split('.')[0])
                return False
        except OSError as e:
            logging.getLogger(__name__).warning(f"无法读取文件信息 {file_path.name}: {e}")
            failed_providers.append(file_path.stem.split('.')[0])
            return False
        return True

    async def _load_single_module(
        self,
        module_name_stem: str,
        versions_from_file: Dict[str, str],
        default_configs: Dict[str, Tuple[Any, str]]
    ) -> Optional[str]:
        """
        加载单个爬虫模块。

        Returns:
            str: 成功加载的 provider_name，失败返回 None
        """
        module_name = f"src.scrapers.{module_name_stem}"

        try:
            module = importlib.import_module(module_name)
            module_version = getattr(module, '__version__', None)
            package_version = getattr(module, 'PACKAGE_VERSION', None)

            # 查找 BaseScraper 子类
            for name, obj in inspect.getmembers(module, inspect.isclass):
                if not (issubclass(obj, BaseScraper) and obj is not BaseScraper):
                    continue

                provider_name = self._validate_provider_name(obj, module_name_stem, name)
                if not provider_name:
                    continue

                # 版本兼容性检查
                if not self._check_version_compatibility(provider_name, package_version, obj):
                    return None

                # 注册提供者
                self._register_provider(
                    provider_name,
                    obj,
                    module_version,
                    versions_from_file,
                    default_configs
                )

                return provider_name

        except TypeError as e:
            self._handle_module_load_error(module_name, module_name_stem, e, is_type_error=True)
        except Exception as e:
            self._handle_module_load_error(module_name, module_name_stem, e, is_type_error=False)

        return None

    def _validate_provider_name(self, obj: Type, module_name_stem: str, class_name: str) -> Optional[str]:
        """验证并返回 provider_name"""
        provider_name = getattr(obj, 'provider_name', None)
        if not provider_name or not isinstance(provider_name, str):
            logging.getLogger(__name__).warning(
                f"跳过 {module_name_stem} 中的类 {class_name}："
                f"provider_name 缺失或非字符串（值={provider_name!r}）"
            )
            return None
        return provider_name

    def _check_version_compatibility(
        self,
        provider_name: str,
        package_version: Optional[str],
        scraper_class: Type
    ) -> bool:
        """
        检查版本兼容性（双向检查）。

        Returns:
            bool: True 表示兼容，False 表示不兼容需要跳过
        """
        from src._version import APP_VERSION, MIN_SCRAPER_VERSION

        # 1. 单源要求最低服务器版本（弹幕源 → 服务器）
        source_min_ver = getattr(scraper_class, 'min_server_version', None) or ''
        if source_min_ver:
            if _version_satisfies(APP_VERSION, source_min_ver):
                logging.getLogger(__name__).info(
                    f"✓ {provider_name} 服务器版本检查通过 "
                    f"(要求 >= {source_min_ver}, 当前 {APP_VERSION})"
                )
            else:
                logging.getLogger(__name__).warning(
                    f"✗ 跳过 {provider_name}: "
                    f"要求服务器版本 >= {source_min_ver}，当前 {APP_VERSION}"
                )
                self._version_skipped[provider_name] = f"要求服务器 >= {source_min_ver}"
                return False

        # 2. 服务器要求最低弹幕源总版本号（服务器 → 弹幕源）
        if package_version:
            if not _version_satisfies(package_version, MIN_SCRAPER_VERSION):
                logging.getLogger(__name__).warning(
                    f"✗ 跳过 {provider_name}: 弹幕源版本过旧 "
                    f"(弹幕源总版本号 {package_version}, 要求 >= {MIN_SCRAPER_VERSION})"
                )
                self._version_skipped[provider_name] = (
                    f"弹幕源总版本号过旧 (当前 {package_version}, 要求 >= {MIN_SCRAPER_VERSION})"
                )
                return False
        else:
            # 未注入 PACKAGE_VERSION，允许加载但记录警告
            logging.getLogger(__name__).warning(
                f"⚠ {provider_name}: 未声明 PACKAGE_VERSION，跳过弹幕源总版本号检查"
            )

        return True

    def _register_provider(
        self,
        provider_name: str,
        scraper_class: Type,
        module_version: Optional[str],
        versions_from_file: Dict[str, str],
        default_configs: Dict[str, Tuple[Any, str]]
    ):
        """注册一个已验证的爬虫提供者"""
        # 注册域名映射
        self._register_domains(provider_name, scraper_class)

        # 收集默认配置
        self._collect_default_configs(provider_name, scraper_class, default_configs)

        # 注册爬虫类
        self._scraper_classes[provider_name] = scraper_class

        # 版本号优先从 manifest 读取（因为 .so 模块无法热更新）
        if provider_name in versions_from_file:
            self._scraper_versions[provider_name] = versions_from_file[provider_name]
        elif module_version:
            self._scraper_versions[provider_name] = module_version

    def _register_domains(self, provider_name: str, scraper_class: Type):
        """注册爬虫处理的域名"""
        raw_domains = getattr(scraper_class, 'handled_domains', [])

        # 防御：裸字符串会被逐字符迭代
        if isinstance(raw_domains, str):
            logging.getLogger(__name__).warning(
                f"{provider_name}.handled_domains 是裸字符串 {raw_domains!r}，"
                f"应为列表；已自动包装为单元素列表。"
            )
            raw_domains = [raw_domains]

        for domain in raw_domains:
            if isinstance(domain, str) and domain:
                self._domain_map[domain] = provider_name

    def _collect_default_configs(
        self,
        provider_name: str,
        scraper_class: Type,
        default_configs: Dict[str, Tuple[Any, str]]
    ):
        """收集爬虫的默认配置"""
        # 1. 收集特定的黑名单配置
        if hasattr(scraper_class, '_PROVIDER_SPECIFIC_BLACKLIST_DEFAULT'):
            config_key = f"{provider_name}_episode_blacklist_regex"
            default_value = getattr(scraper_class, '_PROVIDER_SPECIFIC_BLACKLIST_DEFAULT', '')

            if not isinstance(default_value, str):
                default_value = str(default_value)

            description = f"{provider_name.capitalize()} 源的特定分集标题黑名单 (正则表达式)。"
            default_configs[config_key] = (default_value, description)

        # 2. 收集其他默认配置
        if hasattr(scraper_class, '_DEFAULT_CONFIGS'):
            scraper_default_configs = getattr(scraper_class, '_DEFAULT_CONFIGS', None)

            if not isinstance(scraper_default_configs, dict):
                if scraper_default_configs is not None:
                    logging.getLogger(__name__).warning(
                        f"跳过 {provider_name}._DEFAULT_CONFIGS：应为 dict，"
                        f"实际类型为 {type(scraper_default_configs).__name__}。"
                    )
                return

            for config_key, config_tuple in scraper_default_configs.items():
                if not (isinstance(config_tuple, (tuple, list)) and len(config_tuple) == 2):
                    logging.getLogger(__name__).warning(
                        f"跳过 {provider_name}._DEFAULT_CONFIGS[{config_key!r}]："
                        f"值应为 (默认值, 描述) 二元组，"
                        f"实际类型为 {type(config_tuple).__name__}={config_tuple!r}。"
                    )
                    continue

                default_configs[config_key] = config_tuple
                logging.getLogger(__name__).debug(f"发现 {provider_name} 的默认配置: {config_key}")

    def _handle_module_load_error(
        self,
        module_name: str,
        module_name_stem: str,
        error: Exception,
        is_type_error: bool
    ):
        """统一处理模块加载错误"""
        if is_type_error and "couldn't parse file content" in str(error).lower():
            # protobuf 版本不兼容的特殊情况
            error_msg = (
                f"加载搜索源模块 {module_name} 失败，疑似 protobuf 版本不兼容。 "
                f"请确保已将 'protobuf' 版本固定为 '3.20.3' (在 requirements.txt 中), "
                f"并且已经通过 'docker-compose build' 命令重新构建了您的 Docker 镜像。"
            )
            logging.getLogger(__name__).error(error_msg)
        else:
            logging.getLogger(__name__).error(
                f"加载搜索源模块 {module_name} 失败，已跳过。错误: {error}",
                exc_info=True
            )

    async def initialize(self):
        """
        初始化管理器，同步搜索源。
        """
        await self.load_and_sync_scrapers()

    async def update_settings(self, settings: List[ScraperSetting]):
        """
        更新多个搜索源的设置，并立即重新加载以使更改生效。
        这是更新设置的正确方式，因为它能确保内存中的缓存失效。
        """
        async with self._session_factory() as session:
            # CRUD函数负责处理更新逻辑并提交事务。
            await crud.update_scrapers_settings(session, settings)

        # 更新数据库后，重新加载所有搜索源以应用新设置。
        # 这能确保启用/禁用、代理设置等立即生效。
        await self.load_and_sync_scrapers()
        # 使用标准日志记录器
        logging.getLogger(__name__).info("搜索源设置已更新并重新加载。")

    async def reload_scraper(self, provider_name: str):
        """
        重新加载单个搜索源实例。
        当配置更新时调用此方法以使更改生效。
        """
        # 关闭现有实例
        if provider_name in self.scrapers:
            try:
                await self.scrapers[provider_name].close()
            except Exception as e:
                logging.getLogger(__name__).warning(f"关闭搜索源 '{provider_name}' 时出错: {e}")

        # 重新创建实例
        if provider_name in self._scraper_classes:
            scraper_class = self._scraper_classes[provider_name]
            self.scrapers[provider_name] = scraper_class(self._session_factory, self.config_manager, self.transport_manager)
            logging.getLogger(__name__).info(f"搜索源 '{provider_name}' 已重新加载。")
        else:
            logging.getLogger(__name__).warning(f"未找到搜索源类 '{provider_name}'，无法重新加载。")

    @property
    def has_enabled_scrapers(self) -> bool:
        """检查是否有任何已启用的弹幕搜索源(排除虚拟的custom源,且必须实际加载了对应的scraper实例)。"""
        return any(
            s.get('isEnabled')
            for provider_name, s in self.scraper_settings.items()
            if provider_name != 'custom' and provider_name in self.scrapers
        )

    async def search_all(self, keywords: List[str], episode_info: Optional[Dict[str, Any]] = None, max_results_per_source: Optional[int] = None) -> List[ProviderSearchInfo]:
        """
        在所有已启用的搜索源上并发搜索关键词列表。

        Args:
            keywords: 搜索关键词列表
            episode_info: 分集信息
            max_results_per_source: 每个源最多返回的结果数量（None表示不限制）
        """
        enabled_scrapers = [
            scraper for name, scraper in self.scrapers.items()
            if self.scraper_settings.get(name, {}).get('isEnabled')
        ]

        if not enabled_scrapers:
            self.last_search_timing = []
            return []

        # 包装搜索任务，从 @track_performance 装饰器存储的 _task_timings 中读取耗时
        # 使用缓冲 logger 避免并发搜索日志交叉

        # 预加载所有启用源的超时配置并注入到 scraper 实例
        timeout_tasks = {
            scraper.provider_name: self.config_manager.get(
                f"scraper_{scraper.provider_name}_search_timeout", "15"
            )
            for scraper in enabled_scrapers
        }
        timeout_raw = await asyncio.gather(*timeout_tasks.values())
        for scraper in enabled_scrapers:
            raw_val = timeout_raw[list(timeout_tasks.keys()).index(scraper.provider_name)]
            try:
                scraper._search_timeout = max(5.0, min(100.0, float(raw_val)))
            except (ValueError, TypeError):
                scraper._search_timeout = 15.0

        async def timed_search(scraper, keyword):
            task_id = id(asyncio.current_task())  # 获取当前任务ID

            # 安装缓冲 logger，替换 scraper.logger
            original_logger = scraper.logger
            temp_logger, buffer_handler = create_buffered_logger(scraper.provider_name, task_id)
            scraper.logger = temp_logger

            # 单源总搜索超时熔断：「搜索超时」配置语义为单个源的整体搜索时长上限，
            # 而非单次 HTTP 请求超时。源内部可能并行多请求/降级/限流，任一源卡住
            # 都会拖垮 gather 等待所有源完成，故在此用 wait_for 按配置值强制熔断。
            source_total_timeout = getattr(scraper, "_search_timeout", 15.0) or 15.0
            try:
                result = await asyncio.wait_for(
                    scraper.search(keyword, episode_info=episode_info),
                    timeout=source_total_timeout,
                )
                # 从装饰器存储的 _task_timings 中读取耗时（并发安全）
                duration_ms = scraper._task_timings.pop(task_id, 0) if hasattr(scraper, '_task_timings') else 0
                return (scraper.provider_name, result, duration_ms, None, buffer_handler)
            except asyncio.TimeoutError:
                # 源整体搜索超时：熔断该源，返回空结果，不拖垮其它源
                duration_ms = scraper._task_timings.pop(task_id, 0) if hasattr(scraper, '_task_timings') else 0
                scraper.logger.warning(
                    f"{scraper.provider_name}: 搜索超过单源总超时 {source_total_timeout:.0f}s，已熔断跳过"
                )
                return (scraper.provider_name, None, duration_ms,
                        TimeoutError(f"单源搜索超时 ({source_total_timeout:.0f}s)"), buffer_handler)
            except Exception as e:
                duration_ms = scraper._task_timings.pop(task_id, 0) if hasattr(scraper, '_task_timings') else 0
                return (scraper.provider_name, None, duration_ms, e, buffer_handler)
            finally:
                # 恢复原始 logger
                scraper.logger = original_logger

        # 分发策略：每个源自行决定要搜哪些关键词（BaseScraper 默认只用主搜索词 keywords[0]，
        # gamer 等源覆写 select_search_keywords 按语言挑别名），不再「全量别名 × 全部源」笛卡尔积。
        tasks = []
        for scraper in enabled_scrapers:
            try:
                scraper_keywords = scraper.select_search_keywords(keywords)
            except Exception:
                # 挑词异常不影响搜索：回退主搜索词
                scraper_keywords = [keywords[0]] if keywords else []
            for keyword in scraper_keywords:
                tasks.append(timed_search(scraper, keyword))

        # 并行启动补充源搜索（乐观策略：先搜所有可映射平台，后续再过滤）
        supplement_task = None
        if self.metadata_manager:
            all_possible_empty = {
                name for name in self.scrapers if name != 'custom'
            }
            if all_possible_empty:
                primary_keyword = keywords[0] if keywords else ""

                async def _run_supplement():
                    import time as _time
                    _start = _time.monotonic()
                    results = await self.metadata_manager.supplement_empty_search_results(
                        primary_keyword, all_possible_empty
                    )
                    _dur = (_time.monotonic() - _start) * 1000
                    return results, _dur

                supplement_task = asyncio.create_task(_run_supplement())

        # 预加载全局过滤配置（与弹幕源搜索并行，避免搜索完成后串行读取）
        async def _preload_filter_config():
            cn = await self.config_manager.get("search_result_global_blacklist_cn", "")
            eng = await self.config_manager.get("search_result_global_blacklist_eng", "")
            return cn, eng

        filter_config_task = asyncio.create_task(_preload_filter_config())

        timed_results = await asyncio.gather(*tasks)

        # 聚合每个源的耗时和结果数（同一个源可能搜索多个关键词）
        provider_timing: Dict[str, Tuple[float, int]] = {}  # {provider: (max_duration, total_count)}
        # 收集每个源的缓冲日志，按完成顺序记录
        provider_buffers: Dict[str, List[Tuple[BufferedLogHandler, int, float, Exception]]] = {}

        all_results = []
        seen_results = set()

        for provider_name, result, duration_ms, error, buffer_handler in timed_results:
            result_count = 0
            if error:
                # 记录失败的耗时
                if provider_name not in provider_timing:
                    provider_timing[provider_name] = (duration_ms, 0)
                else:
                    old_dur, old_cnt = provider_timing[provider_name]
                    provider_timing[provider_name] = (max(old_dur, duration_ms), old_cnt)
            elif result:
                # 优化5: 限制每个源的结果数量
                limited_result = result[:max_results_per_source] if max_results_per_source else result
                result_count = len(limited_result)

                # 更新耗时统计
                if provider_name not in provider_timing:
                    provider_timing[provider_name] = (duration_ms, result_count)
                else:
                    old_dur, old_cnt = provider_timing[provider_name]
                    provider_timing[provider_name] = (max(old_dur, duration_ms), old_cnt + result_count)

                for item in limited_result:
                    unique_id = (item.provider, item.mediaId)
                    if unique_id not in seen_results:
                        all_results.append(item)
                        seen_results.add(unique_id)
            else:
                # 空结果
                if provider_name not in provider_timing:
                    provider_timing[provider_name] = (duration_ms, 0)
                else:
                    old_dur, old_cnt = provider_timing[provider_name]
                    provider_timing[provider_name] = (max(old_dur, duration_ms), old_cnt)

            # 收集缓冲日志
            if provider_name not in provider_buffers:
                provider_buffers[provider_name] = []
            provider_buffers[provider_name].append((buffer_handler, result_count, duration_ms, error))

        # 按源分组输出缓冲的日志（消除交叉）- 使用 create_task 异步执行，不阻塞事件循环
        mgr_logger = logging.getLogger(__name__)

        def _do_flush_logs():
            """在线程池中执行日志 flush，避免占用事件循环。
            why：flush_buffered_logs 只是纯 Python 日志写入（无 I/O 等待），
            用 run_in_executor 推给线程池，主协程可以并行继续处理补充源、过滤等逻辑，
            最后在 return 前 await 确保日志块在计时报告之前全部输出完毕，
            同时不阻塞事件循环。
            """
            for pn, buffers in provider_buffers.items():
                total_count = provider_timing.get(pn, (0, 0))[1]
                total_dur = provider_timing.get(pn, (0, 0))[0]
                first_error = next((e for _, _, _, e in buffers if e), None)
                merged_handler = BufferedLogHandler()
                for bh, _, _, _ in buffers:
                    merged_handler._records.extend(bh.records)
                    bh.clear()
                flush_buffered_logs(mgr_logger, pn, merged_handler, total_count, total_dur, first_error)

        # 提交到线程池并行执行，主协程继续处理补充源/过滤等逻辑
        flush_task = asyncio.get_event_loop().run_in_executor(None, _do_flush_logs)

        # 保存耗时信息供计时报告使用
        self.last_search_timing = [
            (name, dur, cnt) for name, (dur, cnt) in sorted(provider_timing.items(), key=lambda x: -x[1][0])
        ]

        # 收集补充源结果（已在弹幕源搜索开始时并行启动，现在 await 获取结果）
        try:
            if supplement_task:
                supplement_results, _supp_dur = await supplement_task

                # 完全无结果的弹幕源（含被禁用的），这些源的补充项无条件合并（零结果兜底）
                empty_providers = {
                    name for name, (_, cnt) in provider_timing.items()
                    if cnt == 0 and name != 'custom'
                }
                disabled_providers = {
                    name for name in self.scrapers
                    if not self.scraper_settings.get(name, {}).get('isEnabled')
                    and name != 'custom'
                }
                empty_providers |= disabled_providers

                # why(方案1-结果增补)：不再只对空结果源补充。对"非空"源，也允许把
                # 该源自身没搜到的条目（如综艺往季）合并进来，用「标题归一化+年份」防止
                # 与自身已有结果重复。被禁用/零结果的源仍按原逻辑无条件兜底。
                def _norm_title(t: str) -> str:
                    # 归一化标题用于跨源去重：去除空白与常见分隔符，转小写
                    if not t:
                        return ""
                    return re.sub(r'[\s:：·\-—_、,，.。]+', '', str(t)).lower()

                # 建立"每个 provider 已存在结果"的标题+年份索引，用于识别补充项是否重复
                existing_title_year: set = set()
                for r in all_results:
                    existing_title_year.add((r.provider, _norm_title(r.title), r.year))

                # 去重并合并
                added_count = 0
                supplemented_providers = set()
                merged_supp = []  # 实际参与合并的补充项（用于日志）
                for supp_item in supplement_results:
                    prov = supp_item.provider
                    is_empty_provider = prov in empty_providers
                    unique_id = (prov, supp_item.mediaId)
                    title_year_key = (prov, _norm_title(supp_item.title), supp_item.year)

                    if unique_id in seen_results:
                        continue  # mediaId 完全重复，跳过
                    if not is_empty_provider and title_year_key in existing_title_year:
                        continue  # 非空源：该季已被自身结果覆盖，避免重复

                    all_results.append(supp_item)
                    seen_results.add(unique_id)
                    existing_title_year.add(title_year_key)
                    added_count += 1
                    supplemented_providers.add(prov)
                    merged_supp.append(supp_item)
                # 日志只展示实际合并进结果的补充项
                filtered_supp = merged_supp

                # 使用框框格式输出日志
                _lines = ["-", f"┌─── 搜索补充源 ({added_count}个补充, {_supp_dur:.0f}ms) ───"]
                _lines.append(f"  无结果的弹幕源: {sorted(empty_providers)}")
                if filtered_supp:
                    for supp_item in filtered_supp:
                        _lines.append(f"  + [{supp_item.provider}] {supp_item.title}")
                else:
                    _lines.append(f"  (未获得任何补充结果)")
                _lines.append(f"└─── 搜索补充源 ───")
                mgr_logger.info("\n".join(_lines))

                # 将补充源各项耗时追加到计时报告
                if hasattr(self.metadata_manager, 'last_supplement_timing') and self.metadata_manager.last_supplement_timing:
                    for s_name, s_dur, s_cnt in self.metadata_manager.last_supplement_timing:
                        self.last_search_timing.append((f"补充:{s_name}", s_dur, s_cnt))
                else:
                    self.last_search_timing.append(("搜索补充源", _supp_dur, added_count))
        except Exception as e:
            mgr_logger.warning(f"搜索补充源调用失败: {e}", exc_info=True)

        # 使用预加载的全局过滤配置（已与弹幕源并行加载完成）
        cn_pattern_str, eng_pattern_str = await filter_config_task

        cn_pattern = re.compile(cn_pattern_str, re.IGNORECASE) if cn_pattern_str else None
        eng_pattern = re.compile(r'(\[|\【|\b)(' + eng_pattern_str + r')(\d{1,2})?(\s|_ALL)?(\]|\】|\b)', re.IGNORECASE) if eng_pattern_str else None

        if not cn_pattern and not eng_pattern:
            return all_results

        filtered_results = []
        for item in all_results:
            is_junk = False
            if cn_pattern and cn_pattern.search(item.title):
                is_junk = True
            if not is_junk and eng_pattern and eng_pattern.search(item.title):
                is_junk = True

            if not is_junk:
                filtered_results.append(item)

        logging.getLogger(__name__).info(f"全局标题过滤: 从 {len(all_results)} 个结果中保留了 {len(filtered_results)} 个。")

        # 确保各源日志块在返回结果（进而触发计时报告）之前全部输出完毕
        # why：flush_task 在线程池里并行执行，此处 await 不阻塞事件循环，
        # 仅等待线程写完日志，保证日志顺序：各源日志块 → 补充源日志 → 计时报告
        await flush_task

        # 异步更新弹幕源健康度统计
        asyncio.create_task(self._update_health_stats(timed_results))

        return filtered_results

    async def _update_health_stats(self, timed_results):
        """异步更新弹幕源健康度统计到 scrapers 表"""
        from src.core import get_now
        now = get_now()

        try:
            async with self._session_factory() as session:
                for provider_name, result, duration_ms, error, _ in timed_results:
                    scraper_row = await session.get(orm_models.Scraper, provider_name)
                    if not scraper_row:
                        continue
                    scraper_row.totalSearches = (scraper_row.totalSearches or 0) + 1
                    scraper_row.totalDurationMs = (scraper_row.totalDurationMs or 0) + duration_ms
                    if error:
                        scraper_row.failCount = (scraper_row.failCount or 0) + 1
                        err_str = str(error)[:500]
                        scraper_row.lastError = err_str
                        if "timeout" in err_str.lower() or "timed out" in err_str.lower():
                            scraper_row.timeoutCount = (scraper_row.timeoutCount or 0) + 1
                    elif result:
                        scraper_row.successCount = (scraper_row.successCount or 0) + 1
                        scraper_row.totalResultCount = (scraper_row.totalResultCount or 0) + len(result)
                    else:
                        scraper_row.emptyCount = (scraper_row.emptyCount or 0) + 1
                    scraper_row.lastSearchAt = now
                await session.commit()
        except Exception as e:
            logging.getLogger(__name__).debug(f"更新弹幕源健康统计失败: {e}")

    @staticmethod
    def parse_supplement_media_id(media_id: str) -> Optional[tuple]:
        """解析补充源 mediaId 格式: sup_{补充源名}_{媒体ID}_{平台key}

        Returns:
            (supplement_source_name, original_media_id, platform_key) 或 None
        """
        if not media_id or not media_id.startswith("sup_"):
            return None
        parts = media_id.split("_", 3)
        if len(parts) >= 4:
            return (parts[1], parts[2], parts[3])
        return None

    async def get_episodes_routed(
        self,
        provider: str,
        media_id: str,
        db_media_type: Optional[str] = None,
        target_episode_index: Optional[int] = None,
        return_filtered: bool = False,
    ):
        """统一分集获取路由：自动识别补充源 mediaId 并路由到正确的数据源。

        如果 media_id 以 'sup_' 开头，转给对应补充源的 get_episode_urls()；
        否则走弹幕源的 get_episodes()。
        """
        logger = logging.getLogger(__name__)
        parsed = self.parse_supplement_media_id(media_id)

        if parsed and self.metadata_manager:
            supplement_source_name, original_media_id, platform_key = parsed
            logger.info(f"补充源路由: {media_id} -> 补充源={supplement_source_name}, 媒体ID={original_media_id}, 平台={platform_key}")

            source = self.metadata_manager.sources.get(supplement_source_name)
            if not source:
                logger.warning(f"补充源 '{supplement_source_name}' 不可用")
                return []

            # 调用补充源获取分集URL
            episode_urls = await source.get_episode_urls(original_media_id, target_provider=provider)
            if not episode_urls:
                logger.warning(f"补充源 '{supplement_source_name}' 未返回分集URL")
                return []

            logger.info(f"补充源 '{supplement_source_name}' 返回 {len(episode_urls)} 个分集URL")

            # 尝试用弹幕源解析URL为分集信息
            from src.db.models import ProviderEpisodeInfo
            scraper = self.get_scraper(provider)
            if not scraper:
                logger.warning(f"补充源路由: 弹幕源 '{provider}' 不可用，无法将分集URL解析为原生ID")
                return []
            episodes = []
            for idx, url in episode_urls:
                try:
                    raw_id = await scraper.get_id_from_url(url)
                except Exception as e:
                    # 解析异常：跳过该集，不塞 URL 制造必然取不到弹幕的假分集
                    logger.warning(f"补充源分集URL解析异常，跳过第{idx}集: url={url}, 原因={e}")
                    continue

                if not raw_id:
                    # 无法从 URL 解析出弹幕源原生ID：该集在此源下无法取弹幕，跳过
                    logger.warning(f"补充源无法从URL解析出 {provider} 原生分集ID，跳过第{idx}集: url={url}")
                    continue

                # 归一化为 get_comments 可解析的字符串（如 mgtv 的 "cid,vid"）。
                # get_id_from_url 可能返回 dict（mgtv={cid,vid}、bilibili={aid,cid}）或字符串，
                # 统一经 format_episode_id_for_comments 转成 get_comments 期望的字符串格式。
                episode_id = scraper.format_episode_id_for_comments(raw_id)
                episodes.append(ProviderEpisodeInfo(
                    provider=provider,
                    episodeId=episode_id,
                    title=f"第{idx}集",
                    episodeIndex=idx,
                    url=url
                ))
            # 汇总日志（单条解析细节已降级为 DEBUG，此处统一打印一条 INFO 汇总，避免刷屏）
            logger.info(f"补充源URL解析完成: {provider} 成功解析 {len(episodes)}/{len(episode_urls)} 个分集为原生ID")
            # 兜底全局分集标题过滤（统一收口，对所有调用路径生效）
            from src.utils.episode_filter import apply_global_episode_title_filter
            return await apply_global_episode_title_filter(
                episodes, self.config_manager, provider, media_id,
                return_filtered=return_filtered,
            )
        else:
            # 普通弹幕源路径
            scraper = self.get_scraper(provider)
            if not scraper:
                raise ValueError(f"弹幕源 '{provider}' 不可用")
            episodes = await scraper.get_episodes(
                media_id,
                target_episode_index=target_episode_index,
                db_media_type=db_media_type
            )
            # 弹幕源内部已完成自身黑名单过滤；编辑导入需要取回其过滤明细。
            filtered_key = (provider, str(media_id))
            source_filtered = list(getattr(scraper, '_last_logged_filtered_out', []))
            scraper._last_logged_filtered_out = []
            if source_filtered:
                self._episode_filtered_details[filtered_key] = source_filtered
            elif return_filtered:
                source_filtered = list(self._episode_filtered_details.get(filtered_key, []))
            # 兜底全局分集标题过滤（统一收口，对所有调用路径生效）
            from src.utils.episode_filter import apply_global_episode_title_filter
            global_result = await apply_global_episode_title_filter(
                episodes, self.config_manager, provider, media_id,
                return_filtered=return_filtered,
            )
            if not return_filtered:
                return global_result
            kept, global_filtered = global_result
            return kept, [*source_filtered, *global_filtered]

    async def search_sequentially(self, keyword: str, episode_info: Optional[Dict[str, Any]] = None) -> Optional[tuple[str, List[ProviderSearchInfo]]]:
        """
        按用户定义的顺序，在已启用的搜索源上顺序搜索。
        一旦找到任何结果，立即停止并返回提供方名称和结果列表。
        """
        if not self.scrapers:
            return None, None

        # 使用缓存的设置来获取有序且已启用的搜索源列表
        ordered_providers = sorted(
            [p for p, s in self.scraper_settings.items() if s.get('isEnabled')],
            key=lambda p: self.scraper_settings[p].get('displayOrder', 99)
        )

        for provider_name in ordered_providers:
            scraper = self.scrapers.get(provider_name)
            if not scraper: continue

            try:
                results = await scraper.search(keyword, episode_info=episode_info)
                if results:
                    return provider_name, results
            except Exception as e:
                logging.getLogger(__name__).error(f"顺序搜索时，提供方 '{provider_name}' 发生错误: {e}", exc_info=True)

        return None, None

    async def search(self, provider: str, keyword: str, episode_info: Optional[Dict[str, Any]] = None) -> List[ProviderSearchInfo]:
        """
        在指定的搜索源上搜索，如果失败则尝试故障转移。
        """
        scraper = self.get_scraper(provider)
        try:
            results = await scraper.search(keyword, episode_info)
        except Exception as e:
            logging.getLogger(__name__).error(f"主搜索源 '{provider}' 搜索时发生错误: {e}", exc_info=True)
            results = []

        # 如果主搜索源没有结果，则尝试故障转移
        if not results and self.metadata_manager:
            try:
                failover_results = await self.metadata_manager.supplement_search_result(provider, keyword, episode_info)
                if failover_results:
                    return failover_results
            except Exception as e:
                logging.getLogger(__name__).error(f"搜索故障转移过程中发生错误: {e}", exc_info=True)

        return results

    async def close_all(self):
        """关闭所有搜索源的客户端。"""
        tasks = [scraper.close() for scraper in self.scrapers.values()]
        await asyncio.gather(*tasks, return_exceptions=True)

    def get_scraper(self, provider: str) -> BaseScraper:
        """通过名称获取指定的搜索源实例。"""
        scraper = self.scrapers.get(provider)
        if not scraper:
            raise ValueError(f"未找到提供方为 '{provider}' 的搜索源")
        return scraper

    def get_scraper_class(self, provider_name: str) -> Optional[Type[BaseScraper]]:
        """获取刮削器的类，而不实例化它。"""
        return self._scraper_classes.get(provider_name)

    def get_scraper_version(self, provider_name: str) -> Optional[str]:
        """获取刮削器的版本号。"""
        return self._scraper_versions.get(provider_name)

    def get_scraper_by_domain(self, url: str) -> Optional[BaseScraper]:
        """
        (新增) 通过URL的域名查找合适的刮削器实例。
        """
        try:
            domain = urlparse(url).netloc
            provider_name = self._domain_map.get(domain)
            return self.get_scraper(provider_name) if provider_name else None
        except Exception:
            return None

    async def _check_remote_min_version(self) -> bool:
        """
        拉取远程公共仓库的 manifest，比较全局 min_server_version。
        如果当前服务器版本低于远程要求的最低版本，则不允许加载弹幕源。

        Returns:
            True = 版本不满足，应跳过加载
            False = 版本满足或无法校验，正常加载
        """
        try:
            repo_url = await self.config_manager.get("scraper_resource_repo", "")
            if not repo_url:
                return False

            from src.api.ui.scraper_resources import parse_github_url, parse_gitee_url, _build_base_url

            gitee_info = parse_gitee_url(repo_url)
            repo_info = None
            if not gitee_info:
                try:
                    repo_info = parse_github_url(repo_url)
                except ValueError:
                    pass

            base_url = _build_base_url(repo_info, repo_url, gitee_info)
            if not base_url:
                return False

            manifest_url = f"{base_url}/{ScraperVersionManager.MANIFEST_FILENAME}"

            # 获取代理和 Token
            headers = {}
            if repo_info:
                github_token = await self.config_manager.get("github_token", "")
                if github_token:
                    headers["Authorization"] = f"Bearer {github_token}"

            proxy_url = await self.config_manager.get("proxyUrl", "")
            proxy_enabled_str = await self.config_manager.get("proxyEnabled", "false")
            proxy = proxy_url if proxy_enabled_str.lower() == "true" and proxy_url else None

            # 拉取远程 manifest（超时 5 秒，不阻塞启动）
            timeout = httpx.Timeout(5.0, read=5.0)
            async with httpx.AsyncClient(
                timeout=timeout, headers=headers, follow_redirects=True, proxy=proxy
            ) as client:
                resp = await client.get(manifest_url)
                if resp.status_code != 200:
                    logging.getLogger(__name__).debug(
                        f"拉取远程 manifest 失败: HTTP {resp.status_code}，跳过版本校验"
                    )
                    return False
                manifest_data = resp.json()

            min_ver = manifest_data.get("min_server_version")
            if not min_ver:
                return False

            from src._version import APP_VERSION

            if not _version_satisfies(APP_VERSION, min_ver):
                logging.getLogger(__name__).warning(
                    f"远程弹幕源包要求服务器版本 >= {min_ver}，"
                    f"当前版本 {APP_VERSION}，跳过全部弹幕源加载"
                )
                return True

            return False

        except Exception as e:
            # 拉取失败不影响正常加载（宽松策略）
            logging.getLogger(__name__).debug(f"远程版本校验失败，跳过: {e}")
            return False


