import asyncio
import importlib
import re
import pkgutil
import inspect
import logging
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from pathlib import Path
from typing import Dict, List, Optional, Any, Type, Tuple, TYPE_CHECKING
from urllib.parse import urlparse


from src.scrapers.base import BaseScraper
from src.utils import TransportManager
from src.db import models, crud, ConfigManager

# 从 models 导入需要的类
ProviderSearchInfo = models.ProviderSearchInfo
ScraperSetting = models.ScraperSetting

if TYPE_CHECKING:
    from .metadata_manager import MetadataSourceManager

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
        def _is_docker_environment():
            """检测是否在Docker容器中运行"""
            import os
            # 方法1: 检查 /.dockerenv 文件（Docker标准做法）
            if Path("/.dockerenv").exists():
                return True
            # 方法2: 检查环境变量
            if os.getenv("DOCKER_CONTAINER") == "true" or os.getenv("IN_DOCKER") == "true":
                return True
            # 方法3: 检查当前工作目录是否为 /app
            if Path.cwd() == Path("/app"):
                return True
            return False

        if _is_docker_environment():
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
        versions_json_path = scrapers_dir / "versions.json"
        if versions_json_path.exists():
            try:
                import json
                versions_data = json.loads(versions_json_path.read_text())
                # versions.json 中的 scrapers 字段存储各个源的版本号
                versions_from_file = versions_data.get('scrapers', {})
                logging.getLogger(__name__).debug(f"从 versions.json 读取到 {len(versions_from_file)} 个源的版本信息")
            except Exception as e:
                logging.getLogger(__name__).warning(f"读取 versions.json 失败: {e}")

        # 使用 pkgutil 发现模块，这对于 .py, .pyc, .so 文件都有效。
        # 我们需要同时处理源码和编译后的情况。
        # 对文件列表排序以确保每次发现的顺序一致
        for file_path in sorted(scrapers_dir.iterdir()):
            # 我们只关心 .py 文件或已知的二进制扩展名
            if not (file_path.name.endswith(".py") or file_path.name.endswith(".so") or file_path.name.endswith(".pyd")):
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
            except Exception as e:
                # 使用标准日志记录器
                logging.getLogger(__name__).error(f"加载搜索源模块 {module_name} 失败，已跳过。错误: {e}", exc_info=True)
        
        # 在同步数据库之前，注册所有发现的默认配置
        if default_configs_to_register:
            await self.config_manager.register_defaults(default_configs_to_register)
            logging.getLogger(__name__).info(f"已为 {len(default_configs_to_register)} 个搜索源注册默认分集黑名单。")

        # 修正：重构同步逻辑以确保 'custom' 源始终存在，并防止意外删除。
        async with self._session_factory() as session:
            # 1. 仅当发现基于文件的搜索源时，才清理过时的条目。
            #    这是一个安全措施，防止在发现过程失败时意外清空数据库。
            #    我们总是将 'custom' 添加到要保留的列表中。
            if discovered_providers:
                providers_to_keep = discovered_providers + ['custom']
                await crud.remove_stale_scrapers(session, providers_to_keep)
            
            # 2. 确保所有发现的搜索源和 'custom' 源都存在于数据库中。
            #    这会添加任何新的搜索源，包括首次添加 'custom'。
            providers_to_sync = discovered_providers + ['custom']
            await crud.sync_scrapers_to_db(session, providers_to_sync)

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
        async def timed_search(scraper, keyword):
            task_id = id(asyncio.current_task())  # 获取当前任务ID
            try:
                result = await scraper.search(keyword, episode_info=episode_info)
                # 从装饰器存储的 _task_timings 中读取耗时（并发安全）
                duration_ms = scraper._task_timings.pop(task_id, 0) if hasattr(scraper, '_task_timings') else 0
                return (scraper.provider_name, result, duration_ms, None)
            except Exception as e:
                duration_ms = scraper._task_timings.pop(task_id, 0) if hasattr(scraper, '_task_timings') else 0
                return (scraper.provider_name, None, duration_ms, e)

        tasks = []
        for keyword in keywords:
            for scraper in enabled_scrapers:
                tasks.append(timed_search(scraper, keyword))

        timed_results = await asyncio.gather(*tasks)

        # 聚合每个源的耗时和结果数（同一个源可能搜索多个关键词）
        provider_timing: Dict[str, Tuple[float, int]] = {}  # {provider: (max_duration, total_count)}

        all_results = []
        seen_results = set()

        for provider_name, result, duration_ms, error in timed_results:
            if error:
                logging.getLogger(__name__).error(f"搜索源 '{provider_name}' 的搜索子任务失败: {error}", exc_info=True)
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

        # 保存耗时信息供计时报告使用
        self.last_search_timing = [
            (name, dur, cnt) for name, (dur, cnt) in sorted(provider_timing.items(), key=lambda x: -x[1][0])
        ]

        # 新增：在此处应用全局标题过滤
        cn_pattern_str = await self.config_manager.get("search_result_global_blacklist_cn", "")
        eng_pattern_str = await self.config_manager.get("search_result_global_blacklist_eng", "")

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
        return filtered_results

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


