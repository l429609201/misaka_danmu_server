"""
Plex媒体服务器实现
"""

from typing import List, Dict, Any, Optional
import xml.etree.ElementTree as ET
from .base import BaseMediaServer, MediaLibrary, MediaItem


class PlexMediaServer(BaseMediaServer):
    """Plex媒体服务器"""
    
    def _get_headers(self) -> Dict[str, str]:
        """Plex使用X-Plex-Token"""
        return {
            'X-Plex-Token': self.api_token,
            'Accept': 'application/json',
        }
    
    async def test_connection(self) -> Dict[str, Any]:
        """测试连接"""
        try:
            data = await self._request('GET', '/')
            if data and 'MediaContainer' in data:
                container = data['MediaContainer']
                return {
                    "ServerName": container.get('friendlyName', 'Plex Server'),
                    "Version": container.get('version'),
                    "Id": container.get('machineIdentifier')
                }
            raise Exception("无法获取服务器信息")
        except Exception as e:
            self.logger.error(f"Plex连接测试失败: {e}")
            raise
    
    async def get_libraries(self) -> List[MediaLibrary]:
        """获取所有媒体库"""
        try:
            data = await self._request('GET', '/library/sections')
            libraries = []
            
            for item in data.get('MediaContainer', {}).get('Directory', []):
                lib_type = item.get('type')
                # 转换Plex的类型到统一类型
                if lib_type == 'movie':
                    unified_type = 'movies'
                elif lib_type == 'show':
                    unified_type = 'tvshows'
                else:
                    unified_type = 'mixed'
                
                libraries.append(MediaLibrary(
                    id=item.get('key'),
                    name=item.get('title'),
                    type=unified_type
                ))
            
            return libraries
        except Exception as e:
            self.logger.error(f"获取Plex媒体库失败: {e}")
            return []
    
    async def get_library_items(
        self,
        library_id: str,
        media_type: Optional[str] = None
    ) -> List[MediaItem]:
        """获取媒体库中的所有项(包括季度和集数)"""
        try:
            data = await self._request('GET', f'/library/sections/{library_id}/all')
            items = []

            for item in data.get('MediaContainer', {}).get('Metadata', []):
                item_type = item.get('type')

                if item_type == 'movie' and (not media_type or media_type == 'movie'):
                    items.append(self._parse_movie(item, library_id))
                elif item_type == 'show' and (not media_type or media_type == 'tv_series'):
                    # 只获取剧集的所有集,不添加剧集本身和季度
                    # 因为前端会自动根据集来构建树形结构
                    series_id = item.get('ratingKey')
                    series_name = item.get('title')
                    series_year = item.get('year')
                    series_tmdb_id = None
                    series_tvdb_id = None
                    series_imdb_id = None

                    # 解析Plex的GUID来获取外部ID
                    for guid in item.get('Guid', []):
                        guid_id = guid.get('id', '')
                        if 'tmdb://' in guid_id:
                            series_tmdb_id = guid_id.replace('tmdb://', '')
                        elif 'tvdb://' in guid_id:
                            series_tvdb_id = guid_id.replace('tvdb://', '')
                        elif 'imdb://' in guid_id:
                            series_imdb_id = guid_id.replace('imdb://', '')

                    series_poster = self._get_image_url(item.get('thumb'))

                    # 获取所有季度
                    seasons = await self.get_tv_seasons(series_id)
                    for season in seasons:
                        try:
                            # 获取该季度的所有集
                            episodes = await self.get_season_episodes(
                                series_id,
                                season['season_number'],
                                library_id,
                                series_name,
                                series_year,
                                series_tmdb_id,
                                series_tvdb_id,
                                series_imdb_id,
                                series_poster
                            )
                            items.extend(episodes)
                        except Exception as e:
                            self.logger.warning(f"获取剧集 {series_name} 第 {season['season_number']} 季失败: {e}, 跳过该季度")

            return items
        except Exception as e:
            self.logger.error(f"获取Plex媒体库项失败: {e}")
            return []
    
    async def search_items(
        self,
        keyword: str,
        media_type: Optional[str] = None,
        limit: int = 50
    ) -> List[MediaItem]:
        """按关键词搜索顶层条目，使用 Plex 原生 /search 端点（单次请求，不展开分集）。"""
        try:
            params = {'query': keyword, 'limit': limit}
            data = await self._request('GET', '/search', params=params)

            items = []
            for item in (data or {}).get('MediaContainer', {}).get('Metadata', []):
                item_type = item.get('type')
                # Plex 的 show 对应剧集，movie 对应电影；其他类型（season/episode/artist 等）忽略
                if item_type == 'movie' and (not media_type or media_type == 'movie'):
                    items.append(self._parse_movie(item))
                elif item_type == 'show' and (not media_type or media_type == 'tv_series'):
                    items.append(self._parse_series(item))
            return items[:limit]
        except Exception as e:
            self.logger.error(f"Plex 原生搜索失败: {e}")
            return []

    async def get_item_details(self, item_id: str) -> Optional[MediaItem]:
        """获取单个媒体项详情"""
        try:
            data = await self._request('GET', f'/library/metadata/{item_id}')
            metadata = data.get('MediaContainer', {}).get('Metadata', [])
            
            if not metadata:
                return None
            
            item = metadata[0]
            item_type = item.get('type')
            
            if item_type == 'movie':
                return self._parse_movie(item)
            elif item_type == 'show':
                return self._parse_series(item)
            elif item_type == 'episode':
                return self._parse_episode(item)
            
            return None
        except Exception as e:
            self.logger.error(f"获取Plex媒体项详情失败: {e}")
            return None
    
    async def get_tv_seasons(self, series_id: str) -> List[Dict[str, Any]]:
        """获取电视节目的所有季度"""
        try:
            data = await self._request('GET', f'/library/metadata/{series_id}/children')

            seasons = []
            for item in data.get('MediaContainer', {}).get('Metadata', []):
                if item.get('type') == 'season':
                    season_id = item.get('ratingKey')
                    season_number = item.get('index', 0)

                    # 获取该季度的实际集数(不使用leafCount,因为可能不准确)
                    episode_count = 0
                    try:
                        episodes_data = await self._request('GET', f'/library/metadata/{season_id}/children')
                        episodes = episodes_data.get('MediaContainer', {}).get('Metadata', [])
                        # 只统计type为episode的项目
                        episode_count = sum(1 for ep in episodes if ep.get('type') == 'episode')
                    except Exception as e:
                        self.logger.warning(f"获取季度 {season_number} 的集数失败: {e}, 使用leafCount")
                        episode_count = item.get('leafCount', 0)

                    seasons.append({
                        'season_id': season_id,
                        'season_number': season_number,
                        'episode_count': episode_count,
                    })

            return sorted(seasons, key=lambda x: x['season_number'])
        except Exception as e:
            self.logger.error(f"获取Plex季度信息失败: {e}")
            return []
    
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
        """获取某一季的所有集"""
        try:
            # 先获取季度ID
            seasons = await self.get_tv_seasons(series_id)
            season_id = None
            for season in seasons:
                if season['season_number'] == season_number:
                    season_id = season['season_id']
                    break

            if not season_id:
                return []

            data = await self._request('GET', f'/library/metadata/{season_id}/children')

            episodes = []
            for item in data.get('MediaContainer', {}).get('Metadata', []):
                if item.get('type') == 'episode':
                    # 使用传入的剧集信息来补充集数据
                    episode = self._parse_episode(
                        item,
                        library_id=library_id,
                        series_name=series_name,
                        series_year=series_year,
                        series_tmdb_id=series_tmdb_id,
                        series_tvdb_id=series_tvdb_id,
                        series_imdb_id=series_imdb_id,
                        series_poster=series_poster
                    )
                    episodes.append(episode)

            return sorted(episodes, key=lambda x: x.episode or 0)
        except Exception as e:
            self.logger.error(f"获取Plex集数信息失败: {e}")
            return []
    
    def _parse_movie(self, data: Dict[str, Any], library_id: Optional[str] = None) -> MediaItem:
        """解析电影数据"""
        # Plex的GUID格式: plex://movie/5d776825880197001ec967c8
        # 或者: tmdb://12345, imdb://tt1234567
        guids = data.get('Guid', [])
        tmdb_id = None
        tvdb_id = None
        imdb_id = None
        
        for guid in guids:
            guid_id = guid.get('id', '')
            if guid_id.startswith('tmdb://'):
                tmdb_id = guid_id.replace('tmdb://', '')
            elif guid_id.startswith('tvdb://'):
                tvdb_id = guid_id.replace('tvdb://', '')
            elif guid_id.startswith('imdb://'):
                imdb_id = guid_id.replace('imdb://', '')
        
        return MediaItem(
            media_id=data.get('ratingKey'),
            title=data.get('title'),
            media_type='movie',
            year=data.get('year'),
            tmdb_id=tmdb_id,
            tvdb_id=tvdb_id,
            imdb_id=imdb_id,
            poster_url=self._get_image_url(data.get('thumb')),
            library_id=library_id,
        )
    
    def _parse_series(self, data: Dict[str, Any], library_id: Optional[str] = None) -> MediaItem:
        """解析剧集数据"""
        guids = data.get('Guid', [])
        tmdb_id = None
        tvdb_id = None
        imdb_id = None
        
        for guid in guids:
            guid_id = guid.get('id', '')
            if guid_id.startswith('tmdb://'):
                tmdb_id = guid_id.replace('tmdb://', '')
            elif guid_id.startswith('tvdb://'):
                tvdb_id = guid_id.replace('tvdb://', '')
            elif guid_id.startswith('imdb://'):
                imdb_id = guid_id.replace('imdb://', '')
        
        return MediaItem(
            media_id=data.get('ratingKey'),
            title=data.get('title'),
            media_type='tv_series',
            year=data.get('year'),
            tmdb_id=tmdb_id,
            tvdb_id=tvdb_id,
            imdb_id=imdb_id,
            poster_url=self._get_image_url(data.get('thumb')),
            library_id=library_id,
        )
    
    def _parse_episode(
        self,
        data: Dict[str, Any],
        library_id: Optional[str] = None,
        series_name: Optional[str] = None,
        series_year: Optional[int] = None,
        series_tmdb_id: Optional[str] = None,
        series_tvdb_id: Optional[str] = None,
        series_imdb_id: Optional[str] = None,
        series_poster: Optional[str] = None
    ) -> MediaItem:
        """解析集数据"""
        # 优先使用传入的剧集信息,如果没有则从集数据中获取
        guids = data.get('Guid', [])
        tmdb_id = series_tmdb_id
        tvdb_id = series_tvdb_id
        imdb_id = series_imdb_id

        # 如果没有传入剧集ID,尝试从集的GUID中获取
        if not tmdb_id and not tvdb_id and not imdb_id:
            for guid in guids:
                guid_id = guid.get('id', '')
                if guid_id.startswith('tmdb://'):
                    tmdb_id = guid_id.replace('tmdb://', '')
                elif guid_id.startswith('tvdb://'):
                    tvdb_id = guid_id.replace('tvdb://', '')
                elif guid_id.startswith('imdb://'):
                    imdb_id = guid_id.replace('imdb://', '')

        # 优先使用传入的剧集名称和年份
        title = series_name or data.get('grandparentTitle', data.get('title'))
        year = series_year or data.get('year')
        poster_url = series_poster or self._get_image_url(data.get('grandparentThumb'))

        return MediaItem(
            media_id=data.get('ratingKey'),
            title=title,
            media_type='tv_series',
            year=year,
            season=data.get('parentIndex'),
            episode=data.get('index'),
            tmdb_id=tmdb_id,
            tvdb_id=tvdb_id,
            imdb_id=imdb_id,
            poster_url=poster_url,
            library_id=library_id,
            series_id=str(data.get('grandparentRatingKey')) if data.get('grandparentRatingKey') is not None else None,
            season_id=str(data.get('parentRatingKey')) if data.get('parentRatingKey') is not None else None,
            episode_id=str(data.get('ratingKey')) if data.get('ratingKey') is not None else None,
        )
    
    def _get_image_url(self, thumb_path: Optional[str]) -> Optional[str]:
        """获取图片URL"""
        if not thumb_path:
            return None
        return f"{self.url}{thumb_path}?X-Plex-Token={self.api_token}"

