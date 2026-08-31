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
        series_id: Optional[str] = None,
        season_id: Optional[str] = None,
        episode_id: Optional[str] = None,
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
        self.series_id = series_id
        self.season_id = season_id
        self.episode_id = episode_id

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
            'seriesId': self.series_id,
            'seasonId': self.season_id,
            'episodeId': self.episode_id,
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
    async def test_connection(self) -> Dict[str, Any]:
        """
        测试连接是否正常

        Returns:
            服务器信息字典,例如: {"ServerName": "My Server", "Version": "4.7.0.0"}
        """
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

    async def search_items(
        self,
        keyword: str,
        media_type: Optional[str] = None,
        limit: int = 50
    ) -> List[MediaItem]:
        """
        按关键词搜索顶层媒体条目（Series / Movie），走服务端原生搜索。

        与 get_library_items 的区别：
        - 只返回顶层条目，不展开季度与分集，请求数恒定
        - 由媒体服务器完成匹配，无需拉全库到内存过滤

        子类应重写此方法以使用各自的原生搜索接口。默认实现返回空列表。

        Args:
            keyword: 搜索关键词
            media_type: 限定类型 (movie / tv_series)，None 表示两者都要
            limit: 最多返回条数

        Returns:
            匹配的顶层媒体条目列表
        """
        self.logger.warning(f"{self.__class__.__name__} 未实现原生搜索，返回空结果")
        return []
    
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
        season_number: int,
        library_id: Optional[str] = None,
        series_name: Optional[str] = None,
        series_year: Optional[int] = None,
        series_tmdb_id: Optional[str] = None,
        series_tvdb_id: Optional[str] = None,
        series_imdb_id: Optional[str] = None,
        series_poster: Optional[str] = None
    ) -> List[MediaItem]:
        """
        获取某一季的所有集

        Args:
            series_id: 剧集ID
            season_number: 季度号
            library_id: 媒体库ID (可选)
            series_name: 剧集名称 (可选)
            series_year: 剧集年份 (可选)
            series_tmdb_id: TMDB ID (可选)
            series_tvdb_id: TVDB ID (可选)
            series_imdb_id: IMDB ID (可选)
            series_poster: 海报URL (可选)
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

