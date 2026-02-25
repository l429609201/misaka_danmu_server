"""
弹弹Play 兼容 API 的番剧详情功能

包含番剧详情获取等功能。
"""

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, Path

from src.db import crud, orm_models, get_db_session, ConfigManager
from src.services import ScraperManager
from src.utils import parse_search_keyword

# 从 orm_models 导入需要的模型
Anime = orm_models.Anime

# 同包内相对导入
from .models import (
    BangumiEpisode,
    BangumiDetails,
    BangumiDetailsResponse,
)
from .constants import (
    DANDAN_TYPE_MAPPING,
    DANDAN_TYPE_DESC_MAPPING,
    FALLBACK_SEARCH_BANGUMI_ID,
    FALLBACK_SEARCH_CACHE_PREFIX,
    FALLBACK_SEARCH_CACHE_TTL,
    USER_LAST_BANGUMI_CHOICE_PREFIX,
    USER_LAST_BANGUMI_CHOICE_TTL,
)
from .helpers import (
    get_db_cache, set_db_cache, get_cache_keys,
    store_episode_mapping, update_episode_mapping,
    find_existing_anime_by_bangumi_id,
    get_next_real_anime_id,
)
from .route_handler import get_token_from_path, DandanApiRoute
from .dependencies import (
    get_config_manager,
    get_scraper_manager,
)

logger = logging.getLogger(__name__)

# 创建番剧路由器
bangumi_router = APIRouter(route_class=DandanApiRoute)


def generate_episode_id(anime_id: int, source_order: int, episode_number: int) -> int:
    """
    生成episode ID，格式：25 + animeid（6位）+ 源顺序（2位）+ 集编号（4位）
    按照弹幕库标准，animeId补0到6位
    例如：animeId=136 → episodeId=25000136010001
    """
    if anime_id is None or source_order is None or episode_number is None:
        raise ValueError(f"生成episodeId时参数不能为None: anime_id={anime_id}, source_order={source_order}, episode_number={episode_number}")
    episode_id = int(f"25{anime_id:06d}{source_order:02d}{episode_number:04d}")
    return episode_id


@bangumi_router.get(
    "/bangumi/{bangumiId}",
    response_model=BangumiDetailsResponse,
    summary="[dandanplay兼容] 获取番剧详情"
)
async def get_bangumi_details(
    bangumiId: str = Path(..., description="作品ID, A开头的备用ID, 或真实的Bangumi ID"),
    token: str = Depends(get_token_from_path),
    session: AsyncSession = Depends(get_db_session),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """
    模拟 dandanplay 的 /api/v2/bangumi/{bangumiId} 接口。
    返回数据库中存储的番剧详细信息。
    新增：处理后备搜索的特殊bangumiId。
    """
    # 检查是否是搜索中的固定bangumiId
    if bangumiId == str(FALLBACK_SEARCH_BANGUMI_ID):
        return BangumiDetailsResponse(
            success=False,
            bangumi=None,
            errorMessage="搜索正在进行，请耐心等待"
        )

    anime_id_int: Optional[int] = None
    if bangumiId.startswith('A') and bangumiId[1:].isdigit():
        # 格式1: "A" + animeId, 例如 "A123"
        anime_id_int = int(bangumiId[1:])

        # 检查是否是后备搜索的虚拟animeId范围（900000-999999）
        if 900000 <= anime_id_int < 1000000:
            # 从数据库缓存中查找所有搜索结果
            try:
                all_cache_keys = await get_cache_keys(session, f"{FALLBACK_SEARCH_CACHE_PREFIX}*")
                for cache_key in all_cache_keys:
                    search_key = cache_key.replace(FALLBACK_SEARCH_CACHE_PREFIX, "")
                    search_info = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key)

                    if search_info and search_info.get("status") == "completed" and "bangumi_mapping" in search_info:
                        if bangumiId in search_info["bangumi_mapping"]:
                            mapping_info = search_info["bangumi_mapping"][bangumiId]
                            provider = mapping_info["provider"]
                            media_id = mapping_info["media_id"]
                            original_title = mapping_info["original_title"]
                            anime_id = mapping_info["anime_id"]

                            # 记录用户最后选择的虚拟bangumiId
                            await set_db_cache(session, USER_LAST_BANGUMI_CHOICE_PREFIX, search_key, bangumiId, USER_LAST_BANGUMI_CHOICE_TTL)
                            logger.info(f"记录用户选择: search_key={search_key}, bangumiId={bangumiId}, provider={provider}")

                            # 获取原始搜索的季度和集数信息
                            parsed_info = search_info.get("parsed_info", {})
                            target_season = parsed_info.get("season")
                            target_episode = parsed_info.get("episode")

                            episodes = []
                            try:
                                # 完全按照WebUI的流程：调用scraper获取真实的分集信息
                                scraper = scraper_manager.get_scraper(provider)
                                if scraper:
                                    # 从映射信息中获取media_type
                                    media_type = None
                                    all_cache_keys_inner = await get_cache_keys(session, f"{FALLBACK_SEARCH_CACHE_PREFIX}*")
                                    for cache_key_inner in all_cache_keys_inner:
                                        search_key_inner = cache_key_inner.replace(FALLBACK_SEARCH_CACHE_PREFIX, "")
                                        search_info_inner = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key_inner)

                                        if search_info_inner and search_info_inner.get("status") == "completed" and "bangumi_mapping" in search_info_inner:
                                            for bangumi_id_inner, mapping_info_inner in search_info_inner["bangumi_mapping"].items():
                                                if mapping_info_inner.get("anime_id") == anime_id:
                                                    media_type = mapping_info_inner.get("type")
                                                    break
                                            if media_type:
                                                break

                                    # 使用与WebUI完全相同的调用方式
                                    actual_episodes = await scraper.get_episodes(media_id, db_media_type=media_type)

                                    if actual_episodes:
                                        # 检查是否已经有相同剧集的记录（源切换检测）
                                        existing_anime = await find_existing_anime_by_bangumi_id(session, bangumiId, search_key)

                                        if existing_anime:
                                            # 找到现有剧集，这是源切换行为
                                            real_anime_id = existing_anime['animeId']
                                            logger.info(f"检测到源切换: 剧集'{original_title}' 已存在 (anime_id={real_anime_id})，将更新映射到新源 {provider}")

                                            # 更新现有episodeId的映射关系
                                            for i, episode_data in enumerate(actual_episodes):
                                                episode_id = generate_episode_id(real_anime_id, 1, i + 1)
                                                await update_episode_mapping(
                                                    session, episode_id, provider, media_id,
                                                    i + 1, original_title
                                                )
                                            logger.info(f"源切换: '{original_title}' 更新 {len(actual_episodes)} 个分集映射到 {provider}")
                                        else:
                                            # 新剧集，先检查数据库中是否已有相同标题的条目
                                            # 解析搜索关键词，提取纯标题
                                            parsed_info = parse_search_keyword(original_title)
                                            base_title = parsed_info["title"]

                                            # 直接在数据库中查找相同标题的条目
                                            stmt = select(Anime.id, Anime.title).where(
                                                Anime.title == base_title,
                                                Anime.season == 1
                                            )
                                            result = await session.execute(stmt)
                                            existing_db_anime = result.mappings().first()

                                            if existing_db_anime:
                                                # 如果数据库中已有相同标题的条目，使用已有的anime_id
                                                real_anime_id = existing_db_anime['id']
                                                logger.info(f"复用已存在的番剧: '{base_title}' (ID={real_anime_id}) 共 {len(actual_episodes)} 集")
                                            else:
                                                # 如果数据库中没有，获取新的真实animeId
                                                real_anime_id = await get_next_real_anime_id(session)
                                                logger.info(f"新剧集: '{base_title}' (ID={real_anime_id}) 共 {len(actual_episodes)} 集")

                                        # 清除缓存中所有使用这个real_anime_id的其他映射（避免冲突）
                                        all_cache_keys_conflict = await get_cache_keys(session, f"{FALLBACK_SEARCH_CACHE_PREFIX}*")
                                        for cache_key_conflict in all_cache_keys_conflict:
                                            sk = cache_key_conflict.replace(FALLBACK_SEARCH_CACHE_PREFIX, "")
                                            si = await get_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, sk)
                                            if si and si.get("status") == "completed" and "bangumi_mapping" in si:
                                                for bid, mi in list(si["bangumi_mapping"].items()):
                                                    # 如果是其他映射使用了相同的real_anime_id，清除它
                                                    if mi.get("real_anime_id") == real_anime_id and bid != bangumiId:
                                                        del si["bangumi_mapping"][bid]
                                                        logger.info(f"清除冲突的缓存映射: search_key={sk}, bangumiId={bid}, real_anime_id={real_anime_id}")
                                                # 保存更新后的缓存
                                                await set_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, sk, si, FALLBACK_SEARCH_CACHE_TTL)

                                        # 存储真实animeId到虚拟animeId的映射关系
                                        mapping_info["real_anime_id"] = real_anime_id
                                        # 更新缓存中的映射信息
                                        search_info["bangumi_mapping"][bangumiId] = mapping_info
                                        await set_db_cache(session, FALLBACK_SEARCH_CACHE_PREFIX, search_key, search_info, FALLBACK_SEARCH_CACHE_TTL)

                                        for i, episode_data in enumerate(actual_episodes):
                                            episode_index = i + 1

                                            # 如果指定了特定集数，只返回该集数
                                            if target_episode is not None and episode_index != target_episode:
                                                continue

                                            # 使用真实animeId生成标准的episodeId
                                            episode_id = generate_episode_id(real_anime_id, 1, episode_index)
                                            # 直接使用原始分集标题
                                            episode_title = episode_data.title

                                            # 只有在新剧集时才存储映射关系（源切换时已经在上面更新了）
                                            if not existing_anime:
                                                await store_episode_mapping(
                                                    session, episode_id, provider, media_id,
                                                    episode_index, original_title
                                                )

                                            episodes.append(BangumiEpisode(
                                                episodeId=episode_id,
                                                episodeTitle=episode_title,
                                                episodeNumber=str(episode_data.episodeIndex if episode_data.episodeIndex else episode_index)
                                            ))

                                    else:
                                        logger.warning(f"从 {provider} 获取分集列表为空: media_id={media_id}")
                                else:
                                    logger.error(f"找不到 {provider} 的scraper")

                            except Exception as e:
                                logger.error(f"获取分集列表失败: {e}")
                                episodes = []

                            # 获取自定义域名
                            custom_domain = await config_manager.get("customApiDomain", "")
                            image_url = f"{custom_domain}/static/logo.png" if custom_domain else "/static/logo.png"

                            bangumi_details = BangumiDetails(
                                animeId=anime_id,  # 使用分配的animeId
                                bangumiId=bangumiId,
                                animeTitle=f"{original_title} （来源：{provider}）",
                                imageUrl=image_url,
                                searchKeyword=original_title,
                                type="other",
                                typeDescription="其他",
                                episodes=episodes,
                                year=2025,
                                summary=f"来自后备搜索的结果 (源: {provider})",
                            )

                            return BangumiDetailsResponse(bangumi=bangumi_details)
            except Exception as e:
                logger.error(f"查找后备搜索缓存失败: {e}")
                # 如果查找失败,继续执行后续逻辑
                pass

            # 如果没找到对应的后备搜索ID，但在范围内，返回过期信息
            if 900000 <= anime_id_int < 1000000:
                return BangumiDetailsResponse(
                    success=True,
                    bangumi=None,
                    errorMessage="搜索结果不存在或已过期"
                )

    elif bangumiId.isdigit():
        # 格式2: 纯数字的 Bangumi ID, 例如 "148099"
        # 我们需要通过 bangumi_id 找到我们自己数据库中的 anime_id
        anime_id_int = await crud.get_anime_id_by_bangumi_id(session, bangumiId)


    if anime_id_int is None:
        return BangumiDetailsResponse(
            success=True,
            bangumi=None,
            errorMessage=f"找不到与标识符 '{bangumiId}' 关联的作品。"
        )

    details = await crud.get_anime_details_for_dandan(session, anime_id_int)
    if not details:
        return BangumiDetailsResponse(
            success=True,
            bangumi=None,
            errorMessage=f"在数据库中找不到ID为 {anime_id_int} 的作品详情。"
        )

    anime_data = details['anime']
    episodes_data = details['episodes']

    dandan_type = DANDAN_TYPE_MAPPING.get(anime_data.get('type'), "other")
    dandan_type_desc = DANDAN_TYPE_DESC_MAPPING.get(anime_data.get('type'), "其他")

    formatted_episodes = [
        BangumiEpisode(
            episodeId=ep['episodeId'],
            episodeTitle=ep['episodeTitle'],
            episodeNumber=str(ep['episodeNumber'])
        ) for ep in episodes_data
    ]

    bangumi_id_str = anime_data.get('bangumiId') or f"A{anime_data['animeId']}"

    bangumi_details = BangumiDetails(
        animeId=anime_data['animeId'],
        bangumiId=bangumi_id_str,
        animeTitle=anime_data['animeTitle'],
        imageUrl=anime_data.get('imageUrl'),
        searchKeyword=anime_data['animeTitle'],
        type=dandan_type,
        typeDescription=dandan_type_desc,
        episodes=formatted_episodes,
        year=anime_data.get('year'),
        summary="暂无简介",
    )

    return BangumiDetailsResponse(bangumi=bangumi_details)

