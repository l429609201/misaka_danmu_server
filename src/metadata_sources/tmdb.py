import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Set, cast

import httpx
from pydantic import BaseModel, Field, ValidationError

from .. import crud, models, utils
from .base import BaseMetadataSource

from fastapi import HTTPException, status
logger = logging.getLogger(__name__)

def _clean_movie_title(title: Optional[str]) -> Optional[str]:
    if not title: return None
    phrases_to_remove = ["劇場版", "the movie"]
    cleaned_title = title
    for phrase in phrases_to_remove:
        cleaned_title = re.sub(r'\s*' + re.escape(phrase) + r'\s*:?', '', cleaned_title, flags=re.IGNORECASE)
    cleaned_title = re.sub(r'\s{2,}', ' ', cleaned_title).strip().strip(':- ')
    return cleaned_title

class TmdbMetadataSource(BaseMetadataSource):
    provider_name = "tmdb"

    async def _get_robust_image_base_url(self) -> str:
        """
        获取TMDB图片基础URL，并对其进行健壮性处理。
        如果用户只配置了域名，则自动附加默认的尺寸路径。
        """
        image_base_url_config = await self.config_manager.get("tmdbImageBaseUrl", "https://image.tmdb.org/t/p/w500")
        
        # 如果配置中不包含 /t/p/ 路径，说明用户可能只填写了域名
        if '/t/p/' not in image_base_url_config:
            # 我们附加一个默认的尺寸路径，使其成为一个有效的图片基础URL
            return f"{image_base_url_config.rstrip('/')}/t/p/w500"
        
        return image_base_url_config.rstrip('/')

    async def _create_client(self) -> httpx.AsyncClient:
        api_key = await self.config_manager.get("tmdbApiKey")
        if not api_key:
            raise ValueError("TMDB API Key not configured.")
        
        # 修正：确保基础URL总是以 /3 结尾，以兼容用户可能输入的各种域名格式
        base_url_from_config = await self.config_manager.get("tmdbApiBaseUrl", "https://api.themoviedb.org/3")
        cleaned_domain = base_url_from_config.rstrip('/')
        base_url = cleaned_domain if cleaned_domain.endswith('/3') else f"{cleaned_domain}/3"
        
        params = {"api_key": api_key, "language": "zh-CN"}
        return httpx.AsyncClient(base_url=base_url, params=params, timeout=20.0, follow_redirects=True)

    async def search(self, keyword: str, user: models.User, mediaType: Optional[str] = None) -> List[models.MetadataDetailsResponse]:
        if not mediaType:
            raise ValueError("TMDB search requires a mediaType ('tv' or 'movie').")
        
        try:
            async with await self._create_client() as client:
                response = await client.get(f"/search/{mediaType}", params={"query": keyword})
                response.raise_for_status()
                data = response.json().get("results", [])
                
                image_base_url = await self._get_robust_image_base_url()
                
                results = []
                for item in data:
                    title = item.get('name') if mediaType == 'tv' else item.get('title')
                    release_date = item.get('first_air_date') if mediaType == 'tv' else item.get('release_date')
                    details_str = f"{release_date or '未知年份'} / {item.get('original_language', 'N/A')}"
                    
                    results.append(models.MetadataDetailsResponse(
                        id=str(item['id']),
                        tmdbId=str(item['id']),
                        title=title,
                        imageUrl=f"{image_base_url}{item.get('poster_path')}" if item.get('poster_path') else None,
                        details=details_str
                    ))
                return results
        except ValueError as e:
            # 捕获 _create_client 中的 API Key 未配置错误
            raise HTTPException(status_code=status.HTTP_412_PRECONDITION_FAILED, detail=str(e))

    async def get_details(self, item_id: str, user: models.User, mediaType: Optional[str] = None) -> Optional[models.MetadataDetailsResponse]:
        if not mediaType:
            raise ValueError("TMDB get_details requires a mediaType ('tv' or 'movie').")

        try:
            async with await self._create_client() as client:
                # 1. Get main details in Chinese
                response = await client.get(f"/{mediaType}/{item_id}", params={"append_to_response": "external_ids"})
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                details = response.json()

                # 2. Get all aliases using the new comprehensive method
                aliases = await self._fetch_and_structure_aliases(client, item_id, mediaType)
                
                image_base_url = await self._get_robust_image_base_url()
                
                # 3. Construct the response
                return models.MetadataDetailsResponse(
                    id=str(details['id']),
                    tmdbId=str(details['id']),
                    title=details.get('name') or details.get('title'),
                    nameEn=aliases.get("name_en"),
                    nameJp=aliases.get("name_jp"),
                    nameRomaji=aliases.get("name_romaji"),
                    aliasesCn=aliases.get("aliases_cn", []),
                    imageUrl=f"{image_base_url}{details.get('poster_path')}" if details.get('poster_path') else None,
                    details=details.get('overview'),
                    imdbId=details.get('external_ids', {}).get('imdb_id'),
                    tvdbId=str(details.get('external_ids', {}).get('tvdb_id')) if details.get('external_ids', {}).get('tvdb_id') else None
                )
        except ValueError as e:
            # 捕获 _create_client 中的 API Key 未配置错误
            raise HTTPException(status_code=status.HTTP_412_PRECONDITION_FAILED, detail=str(e))

    async def _fetch_and_structure_aliases(self, client: httpx.AsyncClient, tmdb_id: str, media_type: str) -> Dict[str, Any]:
        """
        一个更全面的别名获取逻辑，结合了特定语言的详情获取和alternative_titles端点。
        """
        api_path = f"/{media_type}/{tmdb_id}"
        name_en, name_jp, name_romaji = None, None, None
        aliases_cn: set[str] = set()

        # 1. 获取特定语言的主标题
        try:
            zh_res = await client.get(api_path, params={"language": "zh-CN"})
            if zh_res.status_code == 200:
                if title := zh_res.json().get('name') or zh_res.json().get('title'): aliases_cn.add(title)
        except Exception as e:
            self.logger.warning(f"获取 TMDB 中文标题失败 (ID: {tmdb_id}): {e}")

        try:
            en_res = await client.get(api_path, params={"language": "en-US"})
            if en_res.status_code == 200:
                name_en = en_res.json().get('name') or en_res.json().get('title')
        except Exception as e:
            self.logger.warning(f"获取 TMDB 英文标题失败 (ID: {tmdb_id}): {e}")

        try:
            ja_res = await client.get(api_path, params={"language": "ja-JP"})
            if ja_res.status_code == 200:
                name_jp = ja_res.json().get('name') or ja_res.json().get('title')
        except Exception as e:
            self.logger.warning(f"获取 TMDB 日文标题失败 (ID: {tmdb_id}): {e}")

        # 2. 获取所有别名
        try:
            alt_res = await client.get(f"{api_path}/alternative_titles")
            if alt_res.status_code == 200:
                alt_titles_data = alt_res.json()
                alt_titles = alt_titles_data.get("results") or alt_titles_data.get("titles", [])
                for alt in alt_titles:
                    iso_code = alt.get('iso_3166_1')
                    title = alt.get('title')
                    if not title: continue

                    if iso_code in ["CN", "HK", "TW", "SG"]:
                        aliases_cn.add(title)
                    elif iso_code == "JP":
                        if alt.get('type') == "Romaji":
                            if not name_romaji: name_romaji = title
                        else:
                            if not name_jp: name_jp = title
                    elif iso_code in ["US", "GB"]:
                        if not name_en: name_en = title
        except Exception as e:
            self.logger.warning(f"获取 TMDB 别名失败 (ID: {tmdb_id}): {e}")
        
        return {
            "name_en": _clean_movie_title(name_en),
            "name_jp": _clean_movie_title(name_jp),
            "name_romaji": _clean_movie_title(name_romaji),
            "aliases_cn": list(dict.fromkeys([_clean_movie_title(a) for a in aliases_cn if a]))
        }

    async def search_aliases(self, keyword: str, user: models.User) -> Set[str]:
        aliases: Set[str] = set()
        try:
            async with await self._create_client() as client:
                search_response = await client.get("/search/multi", params={"query": keyword})
                search_response.raise_for_status()
                results = search_response.json().get("results", [])
                if not results: return set()

                best_match = results[0]
                media_type = cast(str, best_match.get("media_type"))
                media_id = cast(str, best_match.get("id"))
                if not media_type or not media_id or media_type not in ["tv", "movie"]:
                    return set()

                details = await self.get_details(str(media_id), user, media_type)
                if details:
                    aliases.add(details.title)
                    if details.nameEn: aliases.add(details.nameEn)
                    if details.nameJp: aliases.add(details.nameJp)
                    aliases.update(details.aliasesCn)
            
            self.logger.info(f"TMDB辅助搜索成功，找到别名: {[a for a in aliases if a]}")
        except ValueError as e:
            # 捕获 _create_client 中的 API Key 未配置错误
            self.logger.warning(f"TMDB辅助搜索因配置问题跳过: {e}")
        except Exception as e:
            self.logger.warning(f"TMDB辅助搜索失败: {e}")
        return {alias for alias in aliases if alias}

    async def check_connectivity(self) -> str:
        try:
            async with await self._create_client() as client:
                response = await client.get("/configuration")
                return "连接成功" if response.status_code == 200 else f"连接失败 (状态码: {response.status_code})"
        except ValueError as e: # API Key not configured
            return f"未配置: {e}"
        except Exception as e:
            return f"连接失败: {e}"

    async def execute_action(self, action_name: str, payload: Dict[str, Any], user: models.User, request: Any) -> Any:
        try:
            async with await self._create_client() as client:
                if action_name == "get_episode_groups":
                    tmdb_id = payload.get("tmdbId")
                    if not tmdb_id:
                        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少 tmdbId")
                    response = await client.get(f"/tv/{tmdb_id}/episode_groups")
                    response.raise_for_status()
                    raw_results = response.json().get("results", [])
                    # 手动构造驼峰命名的响应，以满足前端要求
                    camel_case_results = []
                    for item in raw_results:
                        camel_case_results.append({
                            "description": item.get("description"),
                            "episodeCount": item.get("episode_count"),
                            "groupCount": item.get("group_count"),
                            "id": item.get("id"),
                            "name": item.get("name"),
                            "network": item.get("network"),
                            "type": item.get("type"),
                        })
                    return camel_case_results
                elif action_name == "get_all_episodes":
                    egid = payload.get("egid")
                    tmdb_id = payload.get("tmdbId")
                    if not egid or not tmdb_id:
                        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少 egid 或 tmdbId")
                    response = await client.get(f"/tv/episode_group/{egid}", params={"language": "zh-CN"})
                    response.raise_for_status()
                    return response.json()
                elif action_name == "update_mappings":
                    tmdb_id = payload.get("tmdbId")
                    group_id = payload.get("groupId")
                    if not tmdb_id or not group_id:
                        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少 tmdbId 或 groupId")
                    await self.update_tmdb_mappings(int(tmdb_id), group_id, user)
                    return {"message": "映射更新成功"}
                
                raise NotImplementedError(f"操作 '{action_name}' 在 {self.provider_name} 中未实现。")
        except ValueError as e:
            # 捕获 _create_client 中的 API Key 未配置错误
            raise HTTPException(status_code=status.HTTP_412_PRECONDITION_FAILED, detail=str(e))

    async def update_tmdb_mappings(self, tmdb_tv_id: int, group_id: str, user: models.User):
        """
        Fetches episode group details from TMDB and saves the mappings to the database.
        This method is specific to the TMDB source and is called by the manager.
        """
        self.logger.info(f"TMDB插件: 正在为 TV ID {tmdb_tv_id} 和 Group ID {group_id} 更新映射...")
        async with await self._create_client() as client:
            # 1. 获取剧集组详情
            response = await client.get(f"/tv/episode_group/{group_id}", params={"language": "zh-CN"})
            response.raise_for_status()
            api_data = response.json()
            camel_case_data = utils.convert_keys_to_camel(api_data)
            group_details = models.TMDBEpisodeGroupDetails.model_validate(camel_case_data)

            # 2. (可选) 丰富分集信息，例如获取日文标题和图片
            # This part can be extended if needed. For now, we focus on mapping.

            # 3. 保存映射到数据库
            async with self._session_factory() as session:
                await crud.save_tmdb_episode_group_mappings(
                    session=session,
                    tmdb_tv_id=tmdb_tv_id,
                    group_id=group_id,
                    group_details=group_details
                )
        self.logger.info(f"TMDB插件: 映射更新完成。")