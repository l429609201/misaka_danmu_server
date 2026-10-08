"""UI 搜索持久化编排：以短事务访问数据库，避免搜索请求长期占用连接。"""
from typing import Any, Dict, List, Optional

from src.services.service_container import get_database_service


async def search_local_anime(keyword: str) -> List[Dict[str, Any]]:
    """按关键词读取本地作品，保持原搜索结果字段。"""
    db = get_database_service()
    async with db.transaction():
        return await db.anime.search_anime(keyword)


async def read_search_cache(key: str) -> Optional[Any]:
    """读取历史数据库搜索缓存，保留 JSON 解码及过期语义。"""
    db = get_database_service()
    async with db.transaction():
        return await db.cache.get_json(key)


async def write_search_cache(key: str, value: Any, ttl_seconds: int) -> None:
    """将缓存回退结果持久化，提交由数据库服务统一管理。"""
    db = get_database_service()
    async with db.transaction():
        await db.cache.set_json(key, value, ttl_seconds)


async def get_search_source_settings() -> List[Dict[str, Any]]:
    """读取搜索源排序配置，事务结束后再执行结果排序。"""
    db = get_database_service()
    async with db.transaction():
        return await db.scraper.get_all_scraper_settings()
