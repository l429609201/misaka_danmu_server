"""
AI 响应缓存模块

AI 调用结果走统一缓存服务（CacheService，region="ai_response"），
跟随全局缓存后端（内存 / Redis / 数据库 / 混合），重启和多实例都能命中。
统一缓存尚未初始化时（如启动早期、独立脚本）回退到进程内字典。
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from src.services.cache_service import get_cache_service

logger = logging.getLogger(__name__)

AI_CACHE_REGION = "ai_response"


def _get_unified_cache():
    """获取统一缓存服务；尚未初始化时返回 None（走进程内兜底）。"""
    return get_cache_service()


class AIResponseCache:
    """AI 响应缓存（统一缓存优先，进程内字典兜底）"""

    def __init__(self, ttl_seconds: int = 3600, max_size: int = 1000):
        """
        Args:
            ttl_seconds: 缓存过期时间(秒)，默认 1 小时
            max_size: 兜底内存缓存的最大条目数（统一缓存由其后端自行管理容量）
        """
        self._fallback: Dict[str, tuple[Any, datetime]] = {}
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self.logger = logging.getLogger(self.__class__.__name__)
        self._hits = 0
        self._misses = 0

    def _generate_cache_key(self, method: str, **kwargs) -> str:
        """根据方法名和参数生成缓存键(MD5)。"""
        cache_data = {"method": method}
        for key, value in kwargs.items():
            if hasattr(value, 'to_dict'):
                cache_data[key] = value.to_dict()
            elif isinstance(value, (list, tuple)):
                cache_data[key] = [
                    item.to_dict() if hasattr(item, 'to_dict') else str(item)
                    for item in value
                ]
            elif isinstance(value, dict):
                cache_data[key] = value
            else:
                cache_data[key] = str(value)
        cache_str = json.dumps(cache_data, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.md5(cache_str.encode()).hexdigest()

    async def get(self, method: str, **kwargs) -> Optional[Any]:
        """读取缓存，不存在或已过期返回 None。"""
        cache_key = self._generate_cache_key(method, **kwargs)
        result = None
        service = _get_unified_cache()
        if service is not None:
            try:
                result = await service.get(cache_key, region=AI_CACHE_REGION)
            except Exception as e:
                # 缓存故障只影响命中率，不能阻断 AI 调用
                self.logger.warning(f"AI缓存读取失败，按未命中处理: {e}")
                result = None
        else:
            entry = self._fallback.get(cache_key)
            if entry is not None:
                value, timestamp = entry
                if datetime.now() - timestamp < timedelta(seconds=self.ttl_seconds):
                    result = value
                else:
                    del self._fallback[cache_key]

        if result is not None:
            self._hits += 1
            self.logger.debug(f"AI缓存命中: {method} | key={cache_key[:8]}...")
            return result
        self._misses += 1
        return None

    async def set(self, result: Any, method: str, **kwargs) -> None:
        """写入缓存。"""
        cache_key = self._generate_cache_key(method, **kwargs)
        service = _get_unified_cache()
        if service is not None:
            try:
                await service.set(cache_key, result, ttl=self.ttl_seconds, region=AI_CACHE_REGION)
                self.logger.debug(f"AI缓存设置: {method} | key={cache_key[:8]}...")
            except Exception as e:
                self.logger.warning(f"AI缓存写入失败: {e}")
            return

        if cache_key not in self._fallback and len(self._fallback) >= self.max_size:
            oldest_key = min(self._fallback, key=lambda k: self._fallback[k][1])
            del self._fallback[oldest_key]
        self._fallback[cache_key] = (result, datetime.now())
        self.logger.debug(f"AI缓存设置(内存兜底): {method} | key={cache_key[:8]}...")

    async def clear(self) -> int:
        """清空 AI 缓存（只清 ai_response 区域，不影响其他业务缓存）。"""
        count = len(self._fallback)
        self._fallback.clear()
        service = _get_unified_cache()
        if service is not None:
            try:
                count += await service.clear(region=AI_CACHE_REGION)
            except Exception as e:
                self.logger.warning(f"AI缓存清空失败: {e}")
        self._hits = 0
        self._misses = 0
        self.logger.info("AI缓存已清空")
        return count

    async def get_stats(self) -> Dict:
        """缓存统计（命中/未命中为本进程计数）。"""
        service = _get_unified_cache()
        size = len(self._fallback)
        backend = "memory-fallback"
        if service is not None:
            backend = "unified"
            try:
                size = len(await service.keys("*", region=AI_CACHE_REGION))
            except Exception as e:
                self.logger.debug(f"AI缓存条目统计失败: {e}")
        total = self._hits + self._misses
        return {
            "size": size,
            "max_size": self.max_size,
            "ttl_seconds": self.ttl_seconds,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / total if total > 0 else 0.0,
            "backend": backend,
        }
