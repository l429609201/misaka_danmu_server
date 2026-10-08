"""
番剧详情获取业务流程

包含：
- 番剧详情查询的完整业务逻辑
- 后备搜索结果的处理
- 多源番剧的支持
"""

from typing import Optional

from src.schemas.dandan import BangumiDetailsResponse, BangumiDetails, BangumiEpisode
from src.services.service_container import get_database_service
from src.utils.dandan.constants import (
    DANDAN_TYPE_MAPPING,
    DANDAN_TYPE_DESC_MAPPING,
    FALLBACK_SEARCH_BANGUMI_ID,
)
from src.workflows.bangumi.fallback_details import get_fallback_bangumi_details


async def get_bangumi_details_flow(bangumiId: str, token: str) -> BangumiDetailsResponse:
    """获取番剧详情，后备虚拟编号从搜索缓存恢复，普通编号查询库内详情。"""
    db = get_database_service()

    async with db.transaction() as session:
        # 检查是否是搜索中的固定bangumiId
        if bangumiId == str(FALLBACK_SEARCH_BANGUMI_ID):
            return BangumiDetailsResponse(
                success=False,
                bangumi=None,
                errorMessage="搜索正在进行中，请等待..."
            )

        anime_id_int: Optional[int] = None

        # 格式1: 'A' 开头（我们的备用格式）或后备搜索的新剧集/源切换ID (900001-999999)
        if bangumiId.startswith("A"):
            try:
                anime_id_int = int(bangumiId[1:])
            except ValueError:
                return BangumiDetailsResponse(
                    success=True,
                    bangumi=None,
                    errorMessage=f"无效的番剧标识符格式: {bangumiId}"
                )

            # 搜索虚拟 ID 与库内 ID 分流；详情必须从搜索发布的映射恢复源信息。
            if anime_id_int >= 900000000 or 900000 < anime_id_int < 1000000:
                return await get_fallback_bangumi_details(bangumiId)

        elif bangumiId.isdigit():
            # 格式2: 纯数字的 Bangumi ID
            anime_id_int = await session.anime.get_anime_id_by_bangumi_id(bangumiId)

        if anime_id_int is None:
            return BangumiDetailsResponse(
                success=True,
                bangumi=None,
                errorMessage=f"找不到与标识符 '{bangumiId}' 关联的作品。"
            )

        details = await session.anime.get_anime_details_for_dandan(anime_id_int)
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
