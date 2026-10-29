"""后备 ID 编解码（纯算术，零 IO，可零依赖单元测试）

14 位 episodeId 格式：25{animeId:06d}{sourceOrder:02d}{episode:04d}
示例：25000166010001 → animeId=166, sourceOrder=1, episode=1

本模块仅保留零 IO 的纯函数，避免 ID 算术依赖业务服务。
涉及缓存或数据库的映射记录由上层编排通过 CacheService / DatabaseService
处理，不在本模块直接访问缓存或数据库。

本模块的 encode_episode_id 等价于原 src/api/dandan/bangumi.py 的 generate_episode_id，
迁移后应统一使用本函数，删除 bangumi.py 中的重复定义。
"""

# 14 位 episodeId 的基数：前缀 "25" + 12 位数字
#   25{animeId:06d}{sourceOrder:02d}{episode:04d}
# 拆解：animeId 占第 3-8 位（×1000000），sourceOrder 占第 9-10 位（×10000），episode 占末 4 位
EPISODE_ID_BASE = 25_000_000_000_000

# 后备搜索展示用的虚拟 animeId 区间（真实 animeId 不会落在此区间）
VIRTUAL_ANIME_ID_MIN = 900_000
VIRTUAL_ANIME_ID_MAX = 1_000_000  # 开区间上界（不含）


def encode_episode_id(anime_id: int, source_order: int, episode: int) -> int:
    """将 (animeId, sourceOrder, episode) 编码为 14 位 episodeId。

    等价于原 bangumi.generate_episode_id：int(f"25{anime_id:06d}{source_order:02d}{episode:04d}")。
    这里用算术加法实现，结果与字符串拼接完全一致。

    Args:
        anime_id: 真实 animeId（作品主键），取值应满足 0 <= anime_id <= 999999
        source_order: 源顺序号（AnimeSource.sourceOrder），0 <= source_order <= 99
        episode: 分集号，0 <= episode <= 9999

    Returns:
        14 位整数 episodeId

    Raises:
        ValueError: 任一参数为 None（对齐原 generate_episode_id 的防御式校验）
    """
    if anime_id is None or source_order is None or episode is None:
        raise ValueError(
            f"生成 episodeId 时参数不能为 None: "
            f"anime_id={anime_id}, source_order={source_order}, episode={episode}"
        )
    return EPISODE_ID_BASE + anime_id * 1_000_000 + source_order * 10_000 + episode


def decode_episode_id(episode_id: int) -> tuple[int, int, int]:
    """将 14 位 episodeId 解码为 (animeId, sourceOrder, episode)。

    与 comments.py 中的解析逻辑一致：
        temp = episodeId - 25000000000000
        anime_id      = temp // 1000000
        source_order  = (temp % 1000000) // 10000
        episode       = temp % 10000

    Args:
        episode_id: 14 位 episodeId

    Returns:
        (anime_id, source_order, episode) 三元组
    """
    rest = episode_id - EPISODE_ID_BASE
    anime_id = rest // 1_000_000
    source_order = (rest % 1_000_000) // 10_000
    episode = rest % 10_000
    return anime_id, source_order, episode


def encode_series_base_id(anime_id: int, source_order: int) -> int:
    """整季基准 ID（末 4 位分集号补 0）。

    用于"整季基准缓存"键 fallback_episode_{base_id}：
    连续播放时，同一作品同一源的任意分集都映射到同一个基准 ID，
    使下一集请求能命中该缓存并触发后备下载。
    """
    return encode_episode_id(anime_id, source_order, 0)


def is_virtual_anime_id(anime_id: int) -> bool:
    """判断是否为后备搜索的虚拟展示 animeId（900000 <= x < 1000000）。

    虚拟 animeId 仅用于后备搜索结果的临时展示与排序占位，
    与真实 animeId（作品主键）之间无算术关系，靠 bangumi_mapping 缓存记录对应。
    """
    return VIRTUAL_ANIME_ID_MIN <= anime_id < VIRTUAL_ANIME_ID_MAX


def is_fallback_episode_id(episode_id: int) -> bool:
    """判断是否为后备编码的 episodeId（x >= 25000000000000）。

    comments.py 用此判据识别"虚拟 episodeId"分支，进而解码并触发后备下载。
    """
    return episode_id >= EPISODE_ID_BASE
