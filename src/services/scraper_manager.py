import asyncio
import importlib
import json
import re
import pkgutil
import inspect
import logging
import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from pathlib import Path
from typing import Dict, List, Optional, Any, Type, Tuple, TYPE_CHECKING
from urllib.parse import urlparse


from src.scrapers.base import BaseScraper
from src.utils import TransportManager
from src.utils.buffered_logging import BufferedLogHandler, create_buffered_logger, flush_buffered_logs
from src.db import models, crud, ConfigManager, orm_models
from src.core.env import is_docker_environment

# 从 models 导入需要的类
ProviderSearchInfo = models.ProviderSearchInfo
ScraperSetting = models.ScraperSetting

if TYPE_CHECKING:
    from .metadata_manager import MetadataSourceManager


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

    async def release_webhook_search_lock(self, lock_key: str):
        """释放 Webhook 搜索锁。"""
        async with self._lock:
            self._webhook_search_locks.discard(lock_key)
            logging.getLogger(__name__).info(f"Webhook 搜索锁已释放: '{lock_key}'。")


    
    async def load_and_sync_scrapers(self):
        """
        动态发现、同步到数据库并根据数据库设置加载搜索源。
        此方法可以被再次调用以重新加载搜索源。
        """
        # 清理现有爬虫以确保全新加载
        await self.close_all()
        self.scrapers.clear()
        self._scraper_classes.clear()
        self._scraper_versions.clear()  # 清理版本号缓存
        self.scraper_settings.clear()

        # 检查是否需要从备份恢复
        if is_docker_environment():
            scrapers_dir = Path("/app/src/scrapers")
            backup_dir = Path("/app/config/scrapers_backup")
        else:
            scrapers_dir = Path("src/scrapers")
            backup_dir = Path("config/scrapers_backup")

        # 检查 scrapers 目录是否为空(没有 .so/.pyd 文件)
        has_scrapers = any(
            f.suffix in ['.so', '.pyd']
            for f in scrapers_dir.iterdir()
            if f.is_file()
        )

        # 检查是否需要从备份恢复
        # 情况1: scrapers 目录为空但有备份
        # 情况2: 备份目录有更新的文件（通过比较 versions.json 的 updated_at 时间戳）
        should_restore = False
        restore_reason = ""

        if not has_scrapers and backup_dir.exists():
            backup_files = list(backup_dir.glob("*.so")) + list(backup_dir.glob("*.pyd"))
            if backup_files:
                should_restore = True
                restore_reason = f"scrapers 目录为空但存在备份 ({len(backup_files)} 个文件)"
        elif has_scrapers and backup_dir.exists():
            # 检查备份是否比当前更新（通过 versions.json 的 updated_at 字段）
            import json
            scrapers_versions_file = scrapers_dir / "versions.json"
            backup_versions_file = backup_dir / "versions.json"

            if backup_versions_file.exists():
                try:
                    backup_data = json.loads(backup_versions_file.read_text())
                    backup_updated_at = backup_data.get("updated_at", "")

                    scrapers_updated_at = ""
                    if scrapers_versions_file.exists():
                        scrapers_data = json.loads(scrapers_versions_file.read_text())
                        scrapers_updated_at = scrapers_data.get("updated_at", "")

                    # 如果备份的更新时间比 scrapers 的更新时间新，则恢复
                    if backup_updated_at and backup_updated_at > scrapers_updated_at:
                        should_restore = True
                        restore_reason = f"备份目录有更新 (backup: {backup_updated_at}, scrapers: {scrapers_updated_at or 'N/A'})"
                except Exception as e:
                    logging.getLogger(__name__).debug(f"比较版本信息失败: {e}")

        if should_restore:
            backup_files = list(backup_dir.glob("*.so")) + list(backup_dir.glob("*.pyd"))
            if backup_files:
                import shutil
                logging.getLogger(__name__).info(f"检测到需要从备份恢复: {restore_reason}")
                logging.getLogger(__name__).info(f"正在恢复 {len(backup_files)} 个弹幕源文件...")
                for file in backup_files:
                    shutil.copy2(file, scrapers_dir / file.name)

                # 恢复 package.json
                backup_package_file = backup_dir / "package.json"
                if backup_package_file.exists():
                    shutil.copy2(backup_package_file, scrapers_dir / "package.json")
                    logging.getLogger(__name__).info("已恢复 package.json")

                # 恢复 versions.json
                backup_versions_file = backup_dir / "versions.json"
                if backup_versions_file.exists():
                    shutil.copy2(backup_versions_file, scrapers_dir / "versions.json")
                    logging.getLogger(__name__).info("已恢复 versions.json")

                logging.getLogger(__name__).info("备份恢复完成")

        self._domain_map.clear()
        discovered_providers = []
        default_configs_to_register: Dict[str, Tuple[Any, str]] = {}

        # 从 versions.json 读取各个源的版本号（优先使用，因为 .so 模块无法热更新）
        versions_from_file: Dict[str, str] = {}
        global_min_version: Optional[str] = None
        versions_json_path = scrapers_dir / "versions.json"
        if versions_json_path.exists():
            try:
                import json
                versions_data = json.loads(versions_json_path.read_text())
                # versions.json 中的 scrapers 字段存储各个源的版本号
                versions_from_file = versions_data.get('scrapers', {})
                # 读取全局版本限制字段
                global_min_version = versions_data.get('min_server_version')
                logging.getLogger(__name__).debug(f"从 versions.json 读取到 {len(versions_from_file)} 个源的版本信息")
            except Exception as e:
                logging.getLogger(__name__).warning(f"读取 versions.json 失败: {e}")

        # 全局版本检查：若弹幕源包要求的最低服务器版本高于当前版本，跳过全部加载
        if global_min_version:
            from src._version import APP_VERSION
            if not _version_satisfies(APP_VERSION, global_min_version):
                logging.getLogger(__name__).warning(
                    f"弹幕源包要求服务器版本 >= {global_min_version}，"
                    f"当前版本 {APP_VERSION}，跳过全部弹幕源加载"
                )
                return

        # 使用 pkgutil 发现模块，这对于 .py, .pyc, .so 文件都有效。
        # 我们需要同时处理源码和编译后的情况。
        # 对文件列表排序以确保每次发现的顺序一致
        failed_providers: list = []  # import 失败的源，同步数据库时保留，不删除
        for file_path in sorted(scrapers_dir.iterdir()):
            # 我们只关心 .py 文件或已知的二进制扩展名
            if not (file_path.name.endswith(".py") or file_path.name.endswith(".so") or file_path.name.endswith(".pyd")):
                continue

            # 防御性检查：跳过 0 字节的二进制文件（损坏/不完整的 .so/.pyd）
            if file_path.name.endswith((".so", ".pyd")):
                try:
                    fsize = file_path.stat().st_size
                    if fsize == 0:
                        logging.getLogger(__name__).warning(
                            f"跳过 0 字节文件: {file_path.name}（文件损坏或下载不完整）"
                        )
                        failed_providers.append(file_path.stem.split('.')[0])
                        continue
                except OSError as e:
                    logging.getLogger(__name__).warning(f"无法读取文件信息 {file_path.name}: {e}")
                    failed_providers.append(file_path.stem.split('.')[0])
                    continue


            module_name_stem = file_path.stem.split('.')[0] # e.g., 'bilibili.cpython-311-x86_64-linux-gnu' -> 'bilibili'
            if module_name_stem.startswith("_") or module_name_stem == "base":
                continue
            try:


                module_name = f"src.scrapers.{module_name_stem}"
                module = importlib.import_module(module_name)

                # 提取模块级别的 __version__ 属性
                module_version = getattr(module, '__version__', None)

                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, BaseScraper) and obj is not BaseScraper:
                        provider_name = obj.provider_name # 直接访问类属性，避免实例化

                        # 单源最低服务器版本检查：类属性 min_server_version（空字符串或未定义则不限制）
                        source_min_ver = getattr(obj, 'min_server_version', None) or ''
                        if source_min_ver:
                            from src._version import APP_VERSION
                            if _version_satisfies(APP_VERSION, source_min_ver):
                                logging.getLogger(__name__).info(
                                    f"✓ {provider_name} 版本检查通过 (要求 >= {source_min_ver}, 当前 {APP_VERSION})"
                                )
                            else:
                                logging.getLogger(__name__).warning(
                                    f"✗ 跳过 {provider_name}: 要求服务器版本 >= {source_min_ver}，当前 {APP_VERSION}"
                                )
                                failed_providers.append(module_name_stem)
                                continue

                        discovered_providers.append(provider_name)
                        # (新增) 注册该刮削器能处理的域名
                        for domain in getattr(obj, 'handled_domains', []):
                            self._domain_map[domain] = provider_name

                        # 在加载时直接发现并收集提供商特定的默认配置
                        if hasattr(obj, '_PROVIDER_SPECIFIC_BLACKLIST_DEFAULT'):
                            config_key = f"{provider_name}_episode_blacklist_regex"
                            default_value = getattr(obj, '_PROVIDER_SPECIFIC_BLACKLIST_DEFAULT')
                            description = f"{provider_name.capitalize()} 源的特定分集标题黑名单 (正则表达式)。"
                            default_configs_to_register[config_key] = (default_value, description)

                        # 收集 scraper 声明的其他默认配置（如 dandanplay 的跨域代理配置）
                        if hasattr(obj, '_DEFAULT_CONFIGS'):
                            scraper_default_configs = getattr(obj, '_DEFAULT_CONFIGS', {})
                            for config_key, config_tuple in scraper_default_configs.items():
                                default_configs_to_register[config_key] = config_tuple
                                logging.getLogger(__name__).debug(f"发现 {provider_name} 的默认配置: {config_key}")

                        self._scraper_classes[provider_name] = obj
                        # 存储版本号：优先使用 versions.json 中的版本（因为 .so 模块无法热更新）
                        if provider_name in versions_from_file:
                            self._scraper_versions[provider_name] = versions_from_file[provider_name]
                        elif module_version:
                            self._scraper_versions[provider_name] = module_version

            except TypeError as e:
                if "couldn't parse file content" in str(e).lower():
                    # 这是一个针对 protobuf 版本不兼容的特殊情况。
                    error_msg = (
                        f"加载搜索源模块 {module_name} 失败，疑似 protobuf 版本不兼容。 "
                        f"请确保已将 'protobuf' 版本固定为 '3.20.3' (在 requirements.txt 中), "
                        f"并且已经通过 'docker-compose build' 命令重新构建了您的 Docker 镜像。"
                    )
                    logging.getLogger(__name__).error(error_msg)
                else:
                    # 正常处理其他 TypeError
                    logging.getLogger(__name__).error(f"加载搜索源模块 {module_name} 失败，已跳过。错误: {e}", exc_info=True)
                failed_providers.append(module_name_stem)
            except Exception as e:
                # 使用标准日志记录器
                logging.getLogger(__name__).error(f"加载搜索源模块 {module_name} 失败，已跳过。错误: {e}", exc_info=True)
                failed_providers.append(module_name_stem)
        
        # 在同步数据库之前，注册所有发现的默认配置
        if default_configs_to_register:
            await self.config_manager.register_defaults(default_configs_to_register)
            logging.getLogger(__name__).info(f"已为 {len(default_configs_to_register)} 个搜索源注册默认分集黑名单。")

        # ── 远程版本校验：拉取公共仓库 package.json，比较全局最低版本要求 ──
        if await self._check_remote_min_version():
            # 当前服务器版本不满足远程弹幕源包的最低版本要求，跳过全部加载
            return

        # 同步数据库：清理不可用的源，确保 'custom' 源始终存在。
        async with self._session_factory() as session:
            # 1. 清理数据库中不再存在的源（只保留成功加载的 + custom）。
            #    加载失败的源也会被移除，避免前端显示不可用的源。
            providers_to_keep = discovered_providers + ['custom']
            await crud.remove_stale_scrapers(session, providers_to_keep)
            
            # 2. 确保所有发现的搜索源和 'custom' 源都存在于数据库中。
            #    这会添加任何新的搜索源，包括首次添加 'custom'。
            providers_to_sync = discovered_providers + ['custom']
            await crud.sync_scrapers_to_db(session, providers_to_sync)

            # 2.5 按用户保存的顺序快照重排 display_order。
            #    why：弹幕源更新/重启时源可能短暂缺失被删、回归后被当新源追加到末尾，
            #    用 config 表的顺序快照恢复用户调好的顺序，避免顺序反复丢失。
            await crud.apply_scraper_order_from_snapshot(session)

            # 3. 重新加载所有设置。
            settings_list = await crud.get_all_scraper_settings(session)
        self.scraper_settings = {s['providerName']: s for s in settings_list}

        # Instantiate all discovered scrapers
        enabled_count = 0
        disabled_count = 0
        scraper_items = []  # (order, name, status)

        for provider_name, scraper_class in self._scraper_classes.items():
            scraper_instance = scraper_class(self._session_factory, self.config_manager, self.transport_manager)
            # 【优化】设置 scraper_manager 引用,以便使用缓存的配置
            scraper_instance._scraper_manager_ref = self
            self.scrapers[provider_name] = scraper_instance
            setting = self.scraper_settings.get(provider_name, {})

            is_enabled = setting.get('isEnabled', True)
            order = setting.get('displayOrder', 999)
            if is_enabled:
                enabled_count += 1
            else:
                disabled_count += 1
            scraper_items.append((order, provider_name))

            if not setting:
                logging.getLogger(__name__).warning(f"已加载搜索源 '{provider_name}'，但在数据库中未找到其设置。")

        # 汇总输出（按顺序排列）
        scraper_items.sort(key=lambda x: x[0])
        _P = "  - "
        total = enabled_count + disabled_count
        log_lines = [f"已加载 {total} 个搜索源 (已启用: {enabled_count}, 已禁用: {disabled_count})"]
        for order, name in scraper_items:
            log_lines.append(f"{_P}(顺序: {order:02d}) {name}")
        logging.getLogger(__name__).info("\n".join(log_lines))

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

        async def _async_flush_logs():
            """异步日志 flush，不阻塞 search_all 的返回"""
            for pn, buffers in provider_buffers.items():
                total_count = provider_timing.get(pn, (0, 0))[1]
                total_dur = provider_timing.get(pn, (0, 0))[0]
                first_error = next((e for _, _, _, e in buffers if e), None)
                merged_handler = BufferedLogHandler()
                for bh, _, _, _ in buffers:
                    merged_handler._records.extend(bh.records)
                    bh.clear()
                flush_buffered_logs(mgr_logger, pn, merged_handler, total_count, total_dur, first_error)
                await asyncio.sleep(0)  # 让出事件循环，避免长时间阻塞

        asyncio.create_task(_async_flush_logs())

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
            episodes = []
            for idx, url in episode_urls:
                try:
                    if scraper:
                        episode_id = await scraper.get_id_from_url(url)
                    else:
                        episode_id = None
                    episodes.append(ProviderEpisodeInfo(
                        provider=provider,
                        episodeId=episode_id or url,
                        title=f"第{idx}集",
                        episodeIndex=idx,
                        url=url
                    ))
                except Exception as e:
                    logger.debug(f"补充源分集URL解析失败 (第{idx}集): {e}")
                    episodes.append(ProviderEpisodeInfo(
                        provider=provider,
                        episodeId=url,
                        title=f"第{idx}集",
                        episodeIndex=idx,
                        url=url
                    ))
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
        拉取远程公共仓库的 package.json，比较全局 min_server_version。
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

            package_url = f"{base_url}/package.json"

            # 获取代理和 Token
            headers = {}
            if repo_info:
                github_token = await self.config_manager.get("github_token", "")
                if github_token:
                    headers["Authorization"] = f"Bearer {github_token}"

            proxy_url = await self.config_manager.get("proxyUrl", "")
            proxy_enabled_str = await self.config_manager.get("proxyEnabled", "false")
            proxy = proxy_url if proxy_enabled_str.lower() == "true" and proxy_url else None

            # 拉取远程 package.json（超时 5 秒，不阻塞启动）
            timeout = httpx.Timeout(5.0, read=5.0)
            async with httpx.AsyncClient(
                timeout=timeout, headers=headers, follow_redirects=True, proxy=proxy
            ) as client:
                resp = await client.get(package_url)
                if resp.status_code != 200:
                    logging.getLogger(__name__).debug(
                        f"拉取远程 package.json 失败: HTTP {resp.status_code}，跳过版本校验"
                    )
                    return False
                package_data = resp.json()

            min_ver = package_data.get("min_server_version")
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


