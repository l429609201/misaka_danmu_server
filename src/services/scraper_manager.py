import asyncio
import importlib
import inspect
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Type
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src._version import APP_VERSION, MIN_SCRAPER_VERSION
from src.core.env import is_docker_environment
from src.core.timezone import get_now
from src.services.config_service import ConfigService
from src.schemas.search import ProviderSearchInfo
from src.schemas.ui_models import ScraperSetting
from src.schemas.common import SCRAPER_API_VERSION
from src.scrapers.base import BaseScraper
from src.services.service_container import get_database_service
from src.utils.runtime.transport_manager import TransportManager
from src.services.file_storage_service import get_file_storage_service


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
        # 本地来源目录必须锚定当前代码位置，不依赖 IDE/重载进程的工作目录。
        project_root = Path(__file__).resolve().parents[2]
        return cls(
            scrapers_dir=project_root / "src" / "scrapers",
            backup_dir=project_root / "config" / "scrapers_backup",
        )


@dataclass
class ModuleDiscoveryResult:
    """模块发现结果"""
    discovered_providers: List[str]
    failed_providers: List[str]
    default_configs: Dict[str, Tuple[Any, str]]


def _version_satisfies(current: str, minimum: str) -> bool:
    """比较严格的三段数字版本号，非法值明确不兼容。"""
    version_pattern = re.compile(r"^\d+\.\d+\.\d+$")
    if not isinstance(current, str) or not isinstance(minimum, str):
        logging.getLogger(__name__).warning("非法版本类型: current=%r minimum=%r", current, minimum)
        return False
    current = current.strip()
    minimum = minimum.strip()
    if not version_pattern.fullmatch(current) or not version_pattern.fullmatch(minimum):
        logging.getLogger(__name__).warning("非法版本格式: current=%r minimum=%r", current, minimum)
        return False
    return tuple(int(x) for x in current.split('.')) >= tuple(int(x) for x in minimum.split('.'))


class ScraperManager:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        config_service: ConfigService,
        metadata_manager: Any,
        transport_manager: TransportManager,
        *,
        prepare_load: Optional[Callable[[ScraperPaths, bool], Awaitable[Optional[Dict[str, Any]]]]] = None,
    ) -> None:
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
        self.last_search_errors: List[str] = []
        # 编辑导入展示用：分集列表命中源缓存时，仍保留最近一次黑名单过滤明细。
        self._episode_filtered_details: Dict[Tuple[str, str], list] = {}
        self._webhook_search_locks: set[str] = set()  # Webhook 搜索锁（基于 animeTitle-season）
        self._lock = asyncio.Lock()
        self.config_service = config_service
        self.offline_bangumi_service = None
        self.transport_manager = transport_manager
        # 组合根注入资源准备流程，服务层不反向依赖 workflow。
        self._prepare_load = prepare_load
        self._load_manifest: Optional[Dict[str, Any]] = None

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

    async def load_and_sync_scrapers(self, skip_backup_restore: bool = False) -> None:
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

        # 每轮获取独立快照，避免热加载继续读取上次的版本信息。
        if self._prepare_load is not None:
            self._load_manifest = await self._prepare_load(paths, skip_backup_restore)
        else:
            # 未组装资源流程的独立调用方只读取现有清单，不执行迁移或恢复。
            content = await get_file_storage_service().read_text(paths.scrapers_dir / "scraper_manifest.json")
            try:
                manifest = json.loads(content) if content else None
                self._load_manifest = manifest if isinstance(manifest, dict) else None
            except (TypeError, ValueError):
                logging.getLogger(__name__).warning("加载 manifest 失败，清单不是合法 JSON")
                self._load_manifest = None

        # 全局版本检查
        if not await self._check_global_version_compatibility(paths.scrapers_dir):
            return

        # 发现并加载模块
        result = await self._discover_and_load_modules(paths.scrapers_dir)

        # 注册默认配置
        if result.default_configs:
            await self._register_default_configs(result.default_configs)


        # 同步到数据库
        await self._sync_to_database(result.discovered_providers, result.failed_providers)

        # 实例化爬虫
        await self._instantiate_scrapers()

        # 初始化信息增强开关（为所有源注册默认值 false）
        await self._init_enrich_defaults()

    async def _init_enrich_defaults(self):
        """为所有已加载的源初始化信息增强配置默认值（避免升级后全部开启）"""
        if not self.scrapers:
            return

        # 使用标准日志记录器（mgr_logger 仅为 search_all 内的局部变量，此处不可用）
        logger = logging.getLogger(__name__)

        for provider_name in self.scrapers.keys():
            # 迁移旧配置：如果存在旧的 fetch_episode_count 配置，迁移到新的 enrich_enabled
            old_key = f"scraper_{provider_name}_fetch_episode_count"
            new_key = f"scraper_{provider_name}_enrich_enabled"

            old_value = await self.config_service.get(old_key, None)
            existing = await self.config_service.get(new_key, None)

            if existing is None:
                # 如果旧配置存在，迁移过来；否则默认 false
                # 仅初始化缺失值，已有用户选择在 reload 时必须保留。
                await self.config_service.set(new_key, old_value if old_value is not None else "false")
                logger.info(f"已初始化 {provider_name} 的增强配置 {new_key}")

        logger.info(f"已为 {len(self.scrapers)} 个源初始化信息增强配置默认值（false）")

    async def _check_global_version_compatibility(self, scrapers_dir: Path) -> bool:
        """
        检查全局版本兼容性。

        Returns:
            bool: True 表示版本兼容可以继续，False 表示版本不兼容需要跳过加载
        """
        manifest = self._load_manifest
        if not manifest:
            return True

        global_min_version = manifest.get("min_server_version")
        if not global_min_version:
            return True

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
            await self.config_service.register_defaults(default_configs)
            logging.getLogger(__name__).info(
                f"已为 {len(default_configs)} 个搜索源注册默认配置。"
            )
        except Exception as e:
            logging.getLogger(__name__).error(
                f"注册弹幕源默认配置时出错（已跳过，不影响启动）: {e}", exc_info=True
            )

    async def _sync_to_database(self, discovered_providers: List[str], failed_providers: List[str]):
        """将发现的爬虫同步到数据库"""
        # why: 清理、同步、重排三步必须原子完成，中途失败会留下不一致的源列表。
        db = get_database_service()
        async with db.transaction():
            # 失败/不兼容的来源仍保留数据库设置，避免临时加载失败被误判为删除。
            existing_settings = await db.scraper.get_all_scraper_settings()
            existing_providers = [s.get('providerName') for s in existing_settings if s.get('providerName')]
            providers_to_keep = discovered_providers + ['custom']
            if failed_providers:
                providers_to_keep.extend(existing_providers)
            await db.scraper.remove_stale_scrapers(providers_to_keep)

            # 确保所有发现的搜索源和 'custom' 源都存在于数据库中
            providers_to_sync = discovered_providers + ['custom']
            await db.scraper.sync_scrapers_to_db(providers_to_sync)

            # 按用户保存的顺序快照重排 display_order
            await db.scraper.apply_scraper_order_from_snapshot()

            # 重新加载所有设置
            settings_list = await db.scraper.get_all_scraper_settings()

        self.scraper_settings = {s['providerName']: s for s in settings_list}

    async def _instantiate_scrapers(self):
        """实例化所有已发现的爬虫类"""
        enabled_count = 0
        disabled_count = 0
        scraper_items = []  # (order, name)

        for provider_name, scraper_class in list(self._scraper_classes.items()):
            try:
                scraper_instance = self._create_scraper_instance(scraper_class)
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

    def _create_scraper_instance(self, scraper_class: Type[BaseScraper]) -> BaseScraper:
        """按新来源契约创建实例，统一首次加载与 reload 的构造路径。"""
        # 来源只接收基础设施服务；旧的三参数构造不再兼容，也不捕获 TypeError 重试。
        instance = scraper_class(self.config_service, self.transport_manager)
        instance._scraper_manager_ref = self
        instance._bangumi_data = self.offline_bangumi_service
        return instance

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

        # 扫描信息与加载失败分开记录，便于识别空目录、工作目录偏移和契约拒绝。
        module_files = sorted(get_file_storage_service().resource_iterdir(scrapers_dir))
        candidates = [
            path for path in module_files
            if self._is_valid_module_file(path)
            and not path.stem.startswith("_") and path.stem != "base"
        ]
        logging.getLogger(__name__).info(
            "搜索源扫描目录: %s，候选模块: %d", scrapers_dir.resolve(), len(candidates),
        )
        if not candidates:
            logging.getLogger(__name__).warning("搜索源目录未发现 .py/.so/.pyd 实现: %s", scrapers_dir.resolve())
        for file_path in candidates:
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

    def _load_versions_from_manifest(self, scrapers_dir: Path) -> Dict[str, Dict[str, Any]]:
        """从 manifest 读取版本信息"""
        versions = {}
        manifest = self._load_manifest
        if manifest:
            for provider, info in manifest.get("sources", {}).items():
                if isinstance(info, dict):
                    versions[provider] = info
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
            fsize = get_file_storage_service().resource_stat(file_path).st_size
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
        versions_from_file: Dict[str, Dict[str, Any]],
        default_configs: Dict[str, Tuple[Any, str]]
    ) -> Optional[str]:
        """
        加载单个爬虫模块。

        Returns:
            str: 成功加载的 provider_name，失败返回 None
        """
        module_name = f"src.scrapers.{module_name_stem}"

        try:
            manifest_info = versions_from_file.get(module_name_stem, {})
            if not isinstance(manifest_info, dict):
                raise ValueError(f"manifest 来源信息必须为对象，实际为 {type(manifest_info).__name__}")
            manifest_min_version = manifest_info.get("min_server_version")
            if "min_server_version" in manifest_info:
                if not isinstance(manifest_min_version, str) or not manifest_min_version.strip():
                    raise ValueError("manifest min_server_version 必须为非空字符串")
                if not _version_satisfies(APP_VERSION, manifest_min_version):
                    self._version_skipped[module_name_stem] = f"要求服务器 >= {manifest_min_version}"
                    logging.getLogger(__name__).warning(
                        "跳过 %s：manifest 要求服务器版本 >= %s，当前 %s",
                        module_name_stem, manifest_min_version, APP_VERSION,
                    )
                    return None

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

                if vars(obj).get("scraper_api_version") != SCRAPER_API_VERSION:
                    self._version_skipped[provider_name] = (
                        f"来源 API 契约不匹配 (当前 {vars(obj).get('scraper_api_version')!r}, 要求 {SCRAPER_API_VERSION})"
                    )
                    logging.getLogger(__name__).warning(
                        "跳过 %s：来源 API 契约不匹配（需要 %s）",
                        provider_name, SCRAPER_API_VERSION,
                    )
                    return None

                # 版本兼容性检查
                if not self._check_version_compatibility(
                    provider_name, package_version, obj, versions_from_file.get(provider_name, {})
                ):
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
        scraper_class: Type,
        manifest_info: Dict[str, Any],
    ) -> bool:
        """
        检查版本兼容性（双向检查）。

        Returns:
            bool: True 表示兼容，False 表示不兼容需要跳过
        """

        if not isinstance(manifest_info, dict):
            self._version_skipped[provider_name] = "manifest 来源信息非法"
            return False

        # manifest 缺失该字段时兼容历史包；字段存在则必须是非空字符串。
        manifest_min_ver = manifest_info.get("min_server_version")
        if manifest_min_ver is not None:
            if not isinstance(manifest_min_ver, str) or not manifest_min_ver.strip():
                self._version_skipped[provider_name] = "manifest min_server_version 非法"
                return False
            if not _version_satisfies(APP_VERSION, manifest_min_ver):
                self._version_skipped[provider_name] = f"要求服务器 >= {manifest_min_ver}"
                return False

        # 来源类声明同样单独校验，不能被 manifest 的较低门槛覆盖。
        class_min_ver = getattr(scraper_class, "min_server_version", None)
        if class_min_ver:
            if not isinstance(class_min_ver, str) or not _version_satisfies(APP_VERSION, class_min_ver):
                self._version_skipped[provider_name] = f"要求服务器 >= {class_min_ver}"
                return False

        # 服务器要求最低弹幕源总版本号（服务器 → 弹幕源）
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
        versions_from_file: Dict[str, Dict[str, Any]],
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
            self._scraper_versions[provider_name] = versions_from_file[provider_name].get("version")
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
        db = get_database_service()
        async with db.transaction():
            await db.scraper.update_scrapers_settings(settings)

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
            self.scrapers[provider_name] = self._create_scraper_instance(scraper_class)
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

    async def _update_health_stats(self, timed_results: List[tuple]) -> bool:
        """通过统一服务事务更新统计，返回成败以避免托管任务误报成功。"""
        try:
            db = get_database_service()
            async with db.transaction():
                await db.scraper_crud.record_search_health(timed_results, get_now())
            return True
        except Exception as exc:
            logging.getLogger(__name__).debug("更新弹幕源健康统计失败: %s", exc)
            return False

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
        在指定的搜索源上搜索，错误保持空结果契约。
        """
        scraper = self.get_scraper(provider)
        try:
            results = await scraper.search(keyword, episode_info)
        except Exception as e:
            logging.getLogger(__name__).error(f"主搜索源 '{provider}' 搜索时发生错误: {e}", exc_info=True)
            results = []

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


