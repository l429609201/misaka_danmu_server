"""
番剧相关辅助函数

包含：
- generate_episode_id: 生成符合弹幕库标准的 episode ID
- get_or_predict_source_order: 查询或预测源的 sourceOrder
"""

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import orm_models

AnimeSource = orm_models.AnimeSource


def generate_episode_id(anime_id: int, source_order: int, episode_number: int) -> int:
    """
    生成episode ID，格式：25 + animeid（6位）+ 源顺序（2位）+ 集编号（4位）
    按照弹幕库标准，animeId补0到6位
    例如：animeId=136 → episodeId=25000136010001
    """
    if anime_id is None or source_order is None or episode_number is None:
        raise ValueError(
            f"生成episodeId时参数不能为None: "
            f"anime_id={anime_id}, source_order={source_order}, episode_number={episode_number}"
        )
    episode_id = int(f"25{anime_id:06d}{source_order:02d}{episode_number:04d}")
    return episode_id


async def get_or_predict_source_order(
    session: AsyncSession, anime_id: int, provider: str, media_id: str
) -> int:
    """
    查询已有源的 sourceOrder；若源尚未入库，预测并返回下一个可用序号。
    
    why：后备搜索复用已存在番剧时，episodeId 里的 source_order 段必须与
         实际入库的 AnimeSource.sourceOrder 完全一致。若硬编码为 1，多源番剧
         的第 2+ 个源会拼出错误的 ID（如 01 而非 02），导致客户端拿到的 ID
         与数据库存储的 ID 不匹配，弹幕查不到。
    """
    # 先查已有源的 sourceOrder
    existing_stmt = select(AnimeSource.sourceOrder).where(
        AnimeSource.animeId == anime_id,
        AnimeSource.providerName == provider,
        AnimeSource.mediaId == media_id,
    )
    existing_order = (await session.execute(existing_stmt)).scalar_one_or_none()
    if existing_order is not None:
        return existing_order

    # 源尚未入库（入库在后续导入任务中完成），预测下一个可用序号
    max_stmt = select(func.max(AnimeSource.sourceOrder)).where(
        AnimeSource.animeId == anime_id
    )
    current_max = (await session.execute(max_stmt)).scalar_one_or_none() or 0
    return current_max + 1
