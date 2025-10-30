"""
媒体服务器基类
定义统一的接口规范
"""

import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
import httpx

logger = logging.getLogger(__name__)


class MediaLibrary:
    """媒体库信息"""
    def __init__(self, id: str, name: str, type: str):
        self.id = id
        self.name = name
        self.type = type  # movies, tvshows, mixed


class MediaItem:
    """媒体项信息"""
    def __init__(
        self,
        media_id: str,
        title: str,
        media_type: str,  # movie, tv_series
        year: Optional[int] = None,
        season: Optional[int] = None,
        episode: Optional[int] = None,
        tmdb_id: Optional[str] = None,
        tvdb_id: Optional[str] = None,
        imdb_id: Optional[str] = None,
        poster_url: Optional[str] = None,
        library_id: Optional[str] = None,
    ):
        self.media_id = media_id
        self.title = title
        self.media_type = media_type
        self.year = year
        self.season = season
        self.episode = episode
        self.tmdb_id = tmdb_id
        self.tvdb_id = tvdb_id
        self.imdb_id = imdb_id
        self.poster_url = poster_url
        self.library_id = library_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            'mediaId': self.media_id,
            'title': self.title,
            'mediaType': self.media_type,
            'year': self.year,
            'season': self.season,
            'episode': self.episode,
            'tmdbId': self.tmdb_id,
            'tvdbId': self.tvdb_id,
            'imdbId': self.imdb_id,
            'posterUrl': self.poster_url,
            'libraryId': self.library_id,
        }


class BaseMediaServer(ABC):
    """媒体服务器基类"""
    
    def __init__(self, url: str, api_token: str):
        self.url = url.rstrip('/')
        self.api_token = api_token
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self.client = httpx.AsyncClient(timeout=30.0)
    
    async def close(self):
        """关闭HTTP客户端"""
        await self.client.aclose()
    
    @abstractmethod
    async def test_connection(self) -> bool:
        """测试连接是否正常"""
        pass
    
    @abstractmethod
    async def get_libraries(self) -> List[MediaLibrary]:
        """获取所有媒体库"""
        pass
    
    @abstractmethod
    async def get_library_items(
        self,
        library_id: str,
        media_type: Optional[str] = None
    ) -> List[MediaItem]:
        """
        获取媒体库中的所有项
        
        Args:
            library_id: 媒体库ID
            media_type: 过滤类型 (movie, tv_series)
        """
        pass
    
    @abstractmethod
    async def get_item_details(self, item_id: str) -> Optional[MediaItem]:
        """获取单个媒体项的详细信息"""
        pass
    
    @abstractmethod
    async def get_tv_seasons(self, series_id: str) -> List[Dict[str, Any]]:
        """
        获取电视节目的所有季度
        
        Returns:
            List of dicts with keys: season_id, season_number, episode_count
        """
        pass
    
    @abstractmethod
    async def get_season_episodes(
        self,
        series_id: str,
        season_number: int
    ) -> List[MediaItem]:
        """
        获取某一季的所有集
        
        Args:
            series_id: 剧集ID
            season_number: 季度号
        """
        pass
    
    def _get_headers(self) -> Dict[str, str]:
        """获取请求头 (子类可覆盖)"""
        return {
            'X-Emby-Token': self.api_token,
            'Accept': 'application/json',
        }
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs
    ) -> Optional[Dict[str, Any]]:
        """
        发送HTTP请求
        
        Args:
            method: HTTP方法
            endpoint: API端点
            **kwargs: 其他请求参数
        """
        url = f"{self.url}{endpoint}"
        headers = self._get_headers()
        
        try:
            response = await self.client.request(
                method,
                url,
                headers=headers,
                **kwargs
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            self.logger.error(f"HTTP错误: {e.response.status_code} - {e.response.text}")
            raise
        except httpx.RequestError as e:
            self.logger.error(f"请求错误: {e}")
            raise
        except Exception as e:
            self.logger.error(f"未知错误: {e}", exc_info=True)
            raise

