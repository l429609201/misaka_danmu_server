"""
Danmaku相关的CRUD操作
"""

import logging
from typing import Optional, Dict, Any, List
from pathlib import Path
import xml.etree.ElementTree as ET
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, distinct, case, or_, and_, update, delete
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta

from ..orm_models import Anime, Episode, AnimeMetadata, AnimeSource
from .. import models
from src.core.timezone import get_now
from src.utils.common import handle_danmaku_likes

logger = logging.getLogger(__name__)


def _is_docker_environment():
    """检测是否在Docker容器中运行"""
    import os
    # 方法1: 检查 /.dockerenv 文件（Docker标准做法）
    if Path("/.dockerenv").exists():
        return True
    # 方法2: 检查环境变量
    if os.getenv("DOCKER_CONTAINER") == "true" or os.getenv("IN_DOCKER") == "true":
        return True
    # 方法3: 检查当前工作目录是否为 /app
    if Path.cwd() == Path("/app"):
        return True
    return False


def _get_base_dir():
    """获取基础目录，根据运行环境自动调整"""
    if _is_docker_environment():
        return Path("/app")
    else:
        # 源码运行环境，使用当前工作目录
        return Path(".")


BASE_DIR = _get_base_dir()
DANMAKU_BASE_DIR = BASE_DIR / "config/danmaku"


async def save_danmaku_for_episode(
    session: AsyncSession,
    episode_id: int,
    comments: List[Dict[str, Any]],
    config_manager = None,
    fire_threshold: int = 1000
) -> int:
    """
    将弹幕写入XML文件，并更新数据库记录，返回新增数量。

    刷新逻辑：
    - 如果episode已有danmakuFilePath，使用原有路径（刷新场景）
    - 如果episode没有danmakuFilePath，生成新路径（首次下载场景，支持自定义路径）
    - 比较新旧弹幕数量，只有新的更多才替换
    """
    if not comments:
        return 0

    episode_stmt = select(Episode).where(Episode.id == episode_id).options(
        selectinload(Episode.source).selectinload(AnimeSource.anime)
    )
    episode_result = await session.execute(episode_stmt)
    episode = episode_result.scalar_one_or_none()
    if not episode:
        raise ValueError(f"找不到ID为 {episode_id} 的分集")

    comments = handle_danmaku_likes(list(comments), fire_threshold)

    new_comment_count = len(comments)
    old_comment_count = episode.commentCount or 0

    # 刷新场景：如果新弹幕数量不比原有的多，跳过写入
    if episode.danmakuFilePath and new_comment_count <= old_comment_count:
        logger.info(f"分集 {episode_id} 弹幕数量未增加 (新:{new_comment_count} <= 旧:{old_comment_count})，跳过刷新")
        return 0

    # 获取原始弹幕服务器信息
    provider_name = episode.source.providerName
    chat_server_map = {
        "bilibili": "comment.bilibili.com"
    }
    xml_content = _generate_xml_from_comments(comments, episode_id, provider_name, chat_server_map.get(provider_name, "danmaku.misaka.org"))

    # 判断路径：刷新使用原有路径，首次下载生成新路径
    if episode.danmakuFilePath:
        # 刷新场景：使用原有路径
        web_path = episode.danmakuFilePath
        absolute_path = _get_fs_path_from_web_path(web_path)
        if absolute_path is None:
            logger.error(f"无法解析原有弹幕路径: {web_path}，尝试生成新路径")
            web_path, absolute_path = await _generate_danmaku_path(session, episode, config_manager)
        else:
            logger.info(f"刷新弹幕：使用原有路径 {absolute_path} (新:{new_comment_count} > 旧:{old_comment_count})")
    else:
        # 首次下载场景：生成新路径（支持自定义路径）
        web_path, absolute_path = await _generate_danmaku_path(session, episode, config_manager)
        logger.info(f"首次下载：生成新路径 {absolute_path}")

    try:
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_text(xml_content, encoding='utf-8')
        logger.info(f"弹幕已成功写入文件: {absolute_path} (共 {new_comment_count} 条)")
    except OSError as e:
        logger.error(f"写入弹幕文件失败: {absolute_path}。错误: {e}")
        raise

    # 更新Episode的弹幕信息
    from .episode import update_episode_danmaku_info
    await update_episode_danmaku_info(session, episode_id, web_path, new_comment_count)
    return new_comment_count



async def _generate_danmaku_path(session: AsyncSession, episode, config_manager=None) -> tuple[str, Path]:
    """
    生成弹幕文件的Web路径和文件系统路径

    [已重构] 此函数现在调用 path_template.generate_danmaku_path
    保留此函数以保持向后兼容性

    Returns:
        tuple: (web_path, absolute_path)
    """
    # 延迟导入避免循环依赖
    from src.utils.path_template import generate_danmaku_path
    return await generate_danmaku_path(episode, config_manager)
# --- Anime & Library ---


def _normalize_p_attr(p_attr: str, provider_name: Optional[str] = None) -> str:
    """
    规范化 p 属性，确保格式为标准的 4 位核心参数：时间,模式,字体大小,颜色,[来源]

    弹弹play API 返回的格式是 3 位：时间,模式,颜色,[来源]
    标准 XML 格式需要 4 位：时间,模式,字体大小,颜色,[来源]
    """
    if not p_attr:
        default_source = f'[{provider_name}]' if provider_name else ''
        return f'0,1,25,16777215{default_source}'

    p_parts = p_attr.split(',')

    # 查找可选的用户标签（如[bilibili]），以确定核心参数的数量
    core_parts_end_index = len(p_parts)
    has_source_tag = False
    for i, part in enumerate(p_parts):
        if '[' in part and ']' in part:
            core_parts_end_index = i
            has_source_tag = True
            break

    core_parts = p_parts[:core_parts_end_index]
    optional_parts = p_parts[core_parts_end_index:]

    # 场景1: 只有 3 个核心参数 (时间,模式,颜色) - 弹弹play API 格式
    # 需要在 index 2 插入默认字体大小 25
    if len(core_parts) == 3:
        core_parts.insert(2, '25')
    # 场景2: 4 个核心参数，需要区分两种格式：
    #   - 标准/Bilibili格式: 时间,模式,字号,颜色 (第3个是字号，通常 < 100)
    #   - Dandanplay API格式: 时间,模式,颜色,用户ID (第3个是颜色，通常 > 1000；第4个是用户ID哈希)
    elif len(core_parts) == 4:
        fontsize_candidate = core_parts[2].strip()
        userid_candidate = core_parts[3].strip()
        is_dandanplay_format = False

        if fontsize_candidate.isdigit() and int(fontsize_candidate) > 1000:
            # 第3个参数 > 1000，不可能是字号，实际是颜色值 → dandanplay格式
            is_dandanplay_format = True
        elif not userid_candidate.isdigit():
            # 第4个参数包含非数字字符（如十六进制哈希 0d3ed9dd）→ 不是颜色值 → dandanplay格式
            is_dandanplay_format = True
        elif userid_candidate.isdigit() and int(userid_candidate) > 16777215:
            # 第4个参数超过RGB最大值(16777215)，不可能是颜色 → dandanplay格式
            is_dandanplay_format = True

        if is_dandanplay_format:
            # Dandanplay格式: 时间,模式,颜色,用户ID → 丢弃用户ID，插入默认字号
            core_parts = [core_parts[0], core_parts[1], '25', core_parts[2]]
        elif not fontsize_candidate or not fontsize_candidate.isdigit():
            # 字体大小为空或无效，使用默认值
            core_parts[2] = '25'
    # 场景3: 超过4个核心参数 (如Bilibili 8参数格式)，只保留前4个
    elif len(core_parts) > 4:
        core_parts = core_parts[:4]
        fontsize_val = core_parts[2].strip()
        if not fontsize_val or not fontsize_val.isdigit():
            core_parts[2] = '25'
    # 场景4: 参数不足 3 个，补全默认值
    elif len(core_parts) < 3:
        while len(core_parts) < 4:
            if len(core_parts) == 0:
                core_parts.append('0')      # 时间
            elif len(core_parts) == 1:
                core_parts.append('1')      # 模式
            elif len(core_parts) == 2:
                core_parts.append('25')     # 字体大小
            elif len(core_parts) == 3:
                core_parts.append('16777215')  # 颜色（白色）

    # 如果没有来源标签且提供了 provider_name，添加来源标签
    if not has_source_tag and provider_name:
        optional_parts.append(f'[{provider_name}]')

    return ','.join(core_parts + optional_parts)


def _generate_xml_from_comments(
    comments: List[Dict[str, Any]],
    episode_id: int,
    provider_name: Optional[str] = "misaka",
    chat_server: Optional[str] = "danmaku.misaka.org"
) -> str:
    """根据弹幕字典列表生成符合dandanplay标准的XML字符串。"""
    root = ET.Element('i')
    ET.SubElement(root, 'chatserver').text = chat_server
    ET.SubElement(root, 'chatid').text = str(episode_id)
    ET.SubElement(root, 'mission').text = '0'
    ET.SubElement(root, 'maxlimit').text = '2000'
    ET.SubElement(root, 'source').text = 'k-v' # 保持与官方格式一致
    # 新增字段
    ET.SubElement(root, 'sourceprovider').text = provider_name
    ET.SubElement(root, 'datasize').text = str(len(comments))

    for comment in comments:
        # 规范化 p 属性，确保是标准的 4 位格式，并补全来源标签
        p_attr = _normalize_p_attr(str(comment.get('p', '')), provider_name)
        d = ET.SubElement(root, 'd', p=p_attr)
        d.text = comment.get('m', '')
    return ET.tostring(root, encoding='unicode', xml_declaration=True)


def _get_fs_path_from_web_path(web_path: Optional[str]) -> Optional[Path]:
    """
    将Web路径转换为文件系统路径。
    支持: Docker路径(/app/...)、Linux绝对路径(/)、Windows绝对路径(D:\...)、旧格式相对路径。
    """
    if not web_path:
        return None

    # Docker 标准路径: /app/config/... → 移除 /app/ 前缀
    if web_path.startswith('/app/'):
        return Path(web_path[5:])  # 移除 "/app/" 前缀

    # Windows 绝对路径 (如 D:\..., C:\...)
    if len(web_path) >= 2 and web_path[1] == ':':
        return Path(web_path)

    # Linux/Mac 绝对路径
    if web_path.startswith('/'):
        return Path(web_path)

    # 兼容旧的相对路径格式
    if '/danmaku/' in web_path or '\\danmaku\\' in web_path:
        sep = '/danmaku/' if '/danmaku/' in web_path else '\\danmaku\\'
        relative_part = web_path.split(sep, 1)[1]
        return DANMAKU_BASE_DIR / relative_part
    elif '/custom_danmaku/' in web_path:
        relative_part = web_path.split('/custom_danmaku/', 1)[1]
        return Path(relative_part)

    logger.warning(f"无法从Web路径 '{web_path}' 解析文件系统路径: {web_path}")
    return None


async def update_metadata_if_empty(
    session: AsyncSession,
    anime_id: int,
    *,
    tmdb_id: Optional[str] = None,
    imdb_id: Optional[str] = None,
    tvdb_id: Optional[str] = None,
    douban_id: Optional[str] = None,
    bangumi_id: Optional[str] = None,
    tmdb_episode_group_id: Optional[str] = None
):
    """
    如果 anime_metadata 记录中的字段为空，则使用提供的值进行更新。
    如果记录不存在，则创建一个新记录。
    使用关键字参数以提高可读性和安全性。
    """
    stmt = select(AnimeMetadata).where(AnimeMetadata.animeId == anime_id)
    result = await session.execute(stmt)
    metadata_record = result.scalar_one_or_none()

    if not metadata_record:
        metadata_record = AnimeMetadata(animeId=anime_id)
        session.add(metadata_record)
        await session.flush()

    if tmdb_id and not metadata_record.tmdbId: metadata_record.tmdbId = tmdb_id
    if imdb_id and not metadata_record.imdbId: metadata_record.imdbId = imdb_id
    if tvdb_id and not metadata_record.tvdbId: metadata_record.tvdbId = tvdb_id
    if douban_id and not metadata_record.doubanId: metadata_record.doubanId = douban_id
    if bangumi_id and not metadata_record.bangumiId: metadata_record.bangumiId = bangumi_id
    if tmdb_episode_group_id and not metadata_record.tmdbEpisodeGroupId: metadata_record.tmdbEpisodeGroupId = tmdb_episode_group_id

    await session.flush()

# --- User & Auth ---
# 已迁移到 crud/user.py:
# - get_user_by_id
# - get_user_by_username
# - create_user
# - update_user_password
# - update_user_login_info

# --- Episode & Comment ---

