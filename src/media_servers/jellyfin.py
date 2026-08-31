"""
Jellyfin媒体服务器实现
"""

from typing import List, Dict, Any, Optional
from .base import BaseMediaServer, MediaLibrary, MediaItem


class JellyfinMediaServer(BaseMediaServer):
    """Jellyfin媒体服务器"""

    async def test_connection(self) -> Dict[str, Any]:
        """测试连接"""
        try:
            data = await self._request('GET', '/System/Info')
            if data and 'ServerName' in data:
                return {
                    "ServerName": data.get('ServerName'),
                    "Version": data.get('Version'),
                    "Id": data.get('Id')
                }
            raise Exception("无法获取服务器信息")
        except Exception as e:
            self.logger.error(f"Jellyfin连接测试失败: {e}")
            raise

    async def get_libraries(self) -> List[MediaLibrary]:
        """获取所有媒体库"""
        try:
            data = await self._request('GET', '/Library/VirtualFolders')
            libraries = []

            for item in data or []:
                lib_type = item.get('CollectionType', 'mixed')
                # 转换Jellyfin的类型到统一类型
                if lib_type == 'movies':
                    unified_type = 'movies'
                elif lib_type == 'tvshows':
                    unified_type = 'tvshows'
                else:
                    unified_type = 'mixed'

                libraries.append(MediaLibrary(
                    id=item.get('ItemId'),
                    name=item.get('Name'),
                    type=unified_type
                ))

            return libraries
        except Exception as e:
            self.logger.error(f"获取Jellyfin媒体库失败: {e}")
            return []

    async def get_library_items(
        self,
        library_id: str,
        media_type: Optional[str] = None
    ) -> List[MediaItem]:
        """获取媒体库中的所有项(包括季度和集数)"""
        try:
            # 对于电影库,需要使用Recursive=true来获取所有电影
            # 对于电视剧库,使用Recursive=true获取所有剧集,然后手动获取季度和集数
            params = {
                'ParentId': library_id,
                'Recursive': 'true',  # 使用递归获取所有项
                'Fields': 'ProviderIds,ProductionYear,Overview',
            }

            # 根据类型过滤
            if media_type == 'movie':
                params['IncludeItemTypes'] = 'Movie'
            elif media_type == 'tv_series':
                params['IncludeItemTypes'] = 'Series'

            data = await self._request('GET', '/Items', params=params)
            items = []

            for item in data.get('Items', []):
                item_type = item.get('Type')

                if item_type == 'Movie':
                    items.append(self._parse_movie(item, library_id))
                elif item_type == 'Series':
                    # 只获取剧集的所有集,不添加剧集本身和季度
                    # 因为前端会自动根据集来构建树形结构
                    series_id = item.get('Id')
                    series_name = item.get('Name')
                    series_year = item.get('ProductionYear')
                    series_tmdb_id = item.get('ProviderIds', {}).get('Tmdb')
                    series_tvdb_id = item.get('ProviderIds', {}).get('Tvdb')
                    series_imdb_id = item.get('ProviderIds', {}).get('Imdb')
                    series_poster = self._get_image_url(series_id, 'Primary')

                    # 获取所有季度
                    seasons = await self.get_tv_seasons(series_id)
                    for season in seasons:
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

            return items
        except Exception as e:
            self.logger.error(f"获取Jellyfin媒体库项失败: {e}")
            return []

    async def search_items(
        self,
        keyword: str,
        media_type: Optional[str] = None,
        limit: int = 50
    ) -> List[MediaItem]:
        """按关键词搜索顶层条目，使用 Jellyfin 原生 searchTerm 参数（单次请求，不展开分集）。"""
        try:
            # 默认只搜剧集与电影两类顶层条目
            include_types = 'Series,Movie'
            if media_type == 'movie':
                include_types = 'Movie'
            elif media_type == 'tv_series':
                include_types = 'Series'

            params = {
                'searchTerm': keyword,
                'includeItemTypes': include_types,
                'recursive': 'true',
                'fields': 'ProviderIds,ProductionYear',
                'limit': limit,
            }
            data = await self._request('GET', '/Items', params=params)

            items = []
            for item in (data or {}).get('Items', []):
                item_type = item.get('Type')
                if item_type == 'Movie':
                    items.append(self._parse_movie(item))
                elif item_type == 'Series':
                    items.append(self._parse_series(item))
            return items
        except Exception as e:
            self.logger.error(f"Jellyfin 原生搜索失败: {e}")
            return []

    async def get_item_details(self, item_id: str) -> Optional[MediaItem]:
        """获取单个媒体项详情"""
        try:
            data = await self._request('GET', f'/Users/{{UserId}}/Items/{item_id}')
            if not data:
                return None

            item_type = data.get('Type')
            if item_type == 'Movie':
                return self._parse_movie(data)
            elif item_type == 'Series':
                return self._parse_series(data)
            elif item_type == 'Episode':
                return self._parse_episode(data)

            return None
        except Exception as e:
            self.logger.error(f"获取Jellyfin媒体项详情失败: {e}")
            return None

    async def get_tv_seasons(self, series_id: str) -> List[Dict[str, Any]]:
        """获取电视节目的所有季度"""
        try:
            params = {
                'ParentId': series_id,
                'Fields': 'ChildCount',
            }
            data = await self._request('GET', '/Items', params=params)

            seasons = []
            for item in data.get('Items', []):
                if item.get('Type') == 'Season':
                    seasons.append({
                        'season_id': item.get('Id'),
                        'season_number': item.get('IndexNumber', 0),
                        'episode_count': item.get('ChildCount', 0),
                    })

            return sorted(seasons, key=lambda x: x['season_number'])
        except Exception as e:
            self.logger.error(f"获取Jellyfin季度信息失败: {e}")
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

            params = {
                'ParentId': season_id,
                'Fields': 'ProviderIds,ProductionYear',
            }
            data = await self._request('GET', '/Items', params=params)

            episodes = []
            for item in data.get('Items', []):
                if item.get('Type') == 'Episode':
                    episodes.append(self._parse_episode(item, library_id=library_id, season_id=season_id))

            return sorted(episodes, key=lambda x: x.episode or 0)
        except Exception as e:
            self.logger.error(f"获取Jellyfin集数信息失败: {e}")
            return []

    def _parse_movie(self, data: Dict[str, Any], library_id: Optional[str] = None) -> MediaItem:
        """解析电影数据"""
        provider_ids = data.get('ProviderIds', {})

        return MediaItem(
            media_id=data.get('Id'),
            title=data.get('Name'),
            media_type='movie',
            year=data.get('ProductionYear'),
            tmdb_id=provider_ids.get('Tmdb'),
            tvdb_id=provider_ids.get('Tvdb'),
            imdb_id=provider_ids.get('Imdb'),
            poster_url=self._get_image_url(data.get('Id'), 'Primary'),
            library_id=library_id,
        )

    def _parse_series(self, data: Dict[str, Any], library_id: Optional[str] = None) -> MediaItem:
        """解析剧集数据"""
        provider_ids = data.get('ProviderIds', {})

        return MediaItem(
            media_id=data.get('Id'),
            title=data.get('Name'),
            media_type='tv_series',
            year=data.get('ProductionYear'),
            tmdb_id=provider_ids.get('Tmdb'),
            tvdb_id=provider_ids.get('Tvdb'),
            imdb_id=provider_ids.get('Imdb'),
            poster_url=self._get_image_url(data.get('Id'), 'Primary'),
            library_id=library_id,
        )

    def _parse_episode(self, data: Dict[str, Any], library_id: Optional[str] = None, season_id: Optional[str] = None) -> MediaItem:
        """解析集数据"""
        provider_ids = data.get('ProviderIds', {})

        return MediaItem(
            media_id=data.get('Id'),
            title=data.get('SeriesName', data.get('Name')),
            media_type='tv_series',
            year=data.get('ProductionYear'),
            season=data.get('ParentIndexNumber'),
            episode=data.get('IndexNumber'),
            tmdb_id=provider_ids.get('Tmdb'),
            tvdb_id=provider_ids.get('Tvdb'),
            imdb_id=provider_ids.get('Imdb'),
            poster_url=self._get_image_url(data.get('SeriesId'), 'Primary'),
            library_id=library_id,
            series_id=str(data.get('SeriesId')) if data.get('SeriesId') is not None else None,
            season_id=str(season_id or data.get('SeasonId') or data.get('ParentId')) if (season_id or data.get('SeasonId') or data.get('ParentId')) is not None else None,
            episode_id=str(data.get('Id')) if data.get('Id') is not None else None,
        )

    def _get_image_url(self, item_id: str, image_type: str = 'Primary') -> Optional[str]:
        """获取图片URL"""
        if not item_id:
            return None
        return f"{self.url}/Items/{item_id}/Images/{image_type}"

    def _get_headers(self) -> Dict[str, str]:
        """获取请求头"""
        return {
            'X-Emby-Token': self.api_token,  # Jellyfin兼容Emby的Token头
            'Accept': 'application/json',
        }

    async def _request(self, method: str, endpoint: str, params: Optional[Dict[str, Any]] = None, data: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """发送HTTP请求"""
        url = f"{self.url}{endpoint}"
        headers = self._get_headers()

        try:
            if method == 'GET':
                response = await self.client.get(url, headers=headers, params=params)
            elif method == 'POST':
                response = await self.client.post(url, headers=headers, json=data)
            else:
                raise ValueError(f"不支持的HTTP方法: {method}")

            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(f"Jellyfin API请求失败 [{method} {endpoint}]: {e}")
            raise

