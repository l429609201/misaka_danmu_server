import asyncio
import importlib
import inspect
import logging
import aiomysql
from pathlib import Path
from typing import Dict, List, Optional, Any, Type

from .scrapers.base import BaseScraper
from .config_manager import ConfigManager
from .models import ProviderSearchInfo
from . import crud


class ScraperManager:
    def __init__(self, pool: aiomysql.Pool, config_manager: ConfigManager):
        self.scrapers: Dict[str, BaseScraper] = {}
        self._scraper_classes: Dict[str, Type[BaseScraper]] = {}
        self.scraper_settings: Dict[str, Dict[str, Any]] = {}
        self.pool = pool
        self.config_manager = config_manager
        # 注意：加载逻辑现在是异步的，将在应用启动时调用

    def _load_scrapers(self):
        """
        动态发现并加载 'scrapers' 目录下的所有搜索源类。
        """
        scrapers_dir = Path(__file__).parent / "scrapers"
        for file in scrapers_dir.glob("*.py"):
            if file.name.startswith("_") or file.name == "base.py":
                continue

            module_name = f".scrapers.{file.stem}"
            try:
                module = importlib.import_module(module_name, package="src")
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, BaseScraper) and obj is not BaseScraper:
                        scraper_instance = obj()
                        if scraper_instance.provider_name in self.scrapers:
                            print(f"警告: 发现重复的搜索源 '{scraper_instance.provider_name}'。将被覆盖。")
                        self.scrapers[scraper_instance.provider_name] = scraper_instance
                        print(f"搜索源 '{scraper_instance.provider_name}' 已加载。")
            except Exception as e:
                print(f"从 {file.name} 加载搜索源失败: {e}")
    
    async def load_and_sync_scrapers(self):
        """
        动态发现、同步到数据库并根据数据库设置加载搜索源。
        此方法可以被再次调用以重新加载搜索源。
        """
        # 清理现有爬虫以确保全新加载
        await self.close_all()
        self.scrapers.clear()
        self._scraper_classes.clear()
        self.scraper_settings.clear()

        scrapers_dir = Path(__file__).parent / "scrapers"
        discovered_providers = []
        scraper_classes = {}

        for file in scrapers_dir.glob("*.py"):
            if file.name.startswith("_") or file.name == "base.py": continue
            module_name = f".scrapers.{file.stem}"
            try:
                module = importlib.import_module(module_name, package="src")
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, BaseScraper) and obj is not BaseScraper:
                        provider_name = obj.provider_name # 直接访问类属性，避免实例化
                        discovered_providers.append(provider_name)
                        self._scraper_classes[provider_name] = obj
            except TypeError as e:
                if "Couldn't parse file content!" in str(e):
                    # 这是一个针对 protobuf 版本不兼容的特殊情况。
                    error_msg = (
                        f"加载搜索源模块 {module_name} 失败，疑似 protobuf 版本不兼容。 "
                        f"请确保已将 'protobuf' 版本固定为 '3.20.3' (在 requirements.txt 中), "
                        f"并且已经通过 'docker-compose build' 命令重新构建了您的 Docker 镜像。"
                    )
                    logging.getLogger(__name__).error(error_msg, exc_info=True)
                else:
                    # 正常处理其他 TypeError
                    logging.getLogger(__name__).error(f"加载搜索源模块 {module_name} 失败，已跳过。错误: {e}", exc_info=True)
            except Exception as e:
                # 使用标准日志记录器
                logging.getLogger(__name__).error(f"加载搜索源模块 {module_name} 失败，已跳过。错误: {e}", exc_info=True)
        
        # 新增：在同步新搜索源之前，先从数据库中移除不再存在的过时搜索源。
        await crud.remove_stale_scrapers(self.pool, discovered_providers)
        
        await crud.sync_scrapers_to_db(self.pool, discovered_providers)
        settings_list = await crud.get_all_scraper_settings(self.pool)
        self.scraper_settings = {s['provider_name']: s for s in settings_list}

        # Instantiate all discovered scrapers
        for provider_name, scraper_class in self._scraper_classes.items():
            self.scrapers[provider_name] = scraper_class(self.pool, self.config_manager)
            setting = self.scraper_settings.get(provider_name)
            if setting:
                status = "已启用" if setting.get('is_enabled') else "已禁用"
                order = setting.get('display_order', 'N/A')
                logging.getLogger(__name__).info(f"已加载搜索源 '{provider_name}' (状态: {status}, 顺序: {order})。")
            else:
                logging.getLogger(__name__).warning(f"已加载搜索源 '{provider_name}'，但在数据库中未找到其设置。")

    @property
    def has_enabled_scrapers(self) -> bool:
        """检查是否有任何已启用的爬虫。"""
        return any(s.get('is_enabled') for s in self.scraper_settings.values())

    async def search_all(self, keywords: List[str], episode_info: Optional[Dict[str, Any]] = None) -> List[ProviderSearchInfo]:
        """
        在所有已启用的搜索源上并发搜索关键词列表。
        """
        enabled_scrapers = [
            scraper for name, scraper in self.scrapers.items()
            if self.scraper_settings.get(name, {}).get('is_enabled')
        ]

        if not enabled_scrapers:
            return []

        tasks = []
        for keyword in keywords:
            for scraper in enabled_scrapers:
                tasks.append(scraper.search(keyword, episode_info=episode_info))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_search_results = []
        seen_results = set() # 用于去重

        for result in results:
            if isinstance(result, Exception):
                logging.getLogger(__name__).error(f"搜索任务中出现错误: {result}")
            elif result:
                for item in result:
                    # 使用 (provider, mediaId) 作为唯一标识符
                    unique_id = (item.provider, item.mediaId)
                    if unique_id not in seen_results:
                        all_search_results.append(item)
                        seen_results.add(unique_id)

        return all_search_results

    async def search_sequentially(self, keyword: str, episode_info: Optional[Dict[str, Any]] = None) -> Optional[tuple[str, List[ProviderSearchInfo]]]:
        """
        按用户定义的顺序，在已启用的搜索源上顺序搜索。
        一旦找到任何结果，立即停止并返回提供方名称和结果列表。
        """
        if not self.scrapers:
            return None, None

        # 使用缓存的设置来获取有序且已启用的搜索源列表
        ordered_providers = sorted(
            [p for p, s in self.scraper_settings.items() if s.get('is_enabled')],
            key=lambda p: self.scraper_settings[p].get('display_order', 99)
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