"""
弹幕存储管理相关的CRUD操作
包括批量迁移、重命名、模板转换
"""

import logging
import shutil
import re
import os
from pathlib import Path
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from ..orm_models import Anime, AnimeSource, Episode
from src.utils.path_template import normalize_title

logger = logging.getLogger(__name__)


def _is_docker_environment():
    """检测是否在Docker容器中运行"""
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
CONFIG_DIR = BASE_DIR / "config"
DANMAKU_BASE_DIR = CONFIG_DIR / "danmaku"


def get_fs_path_from_web_path(web_path: str) -> Optional[Path]:
    """将web路径转换为文件系统路径

    支持两种路径格式:
    1. Docker 标准路径: /app/config/danmaku/xxx/yyy.xml → {CONFIG_DIR}/danmaku/xxx/yyy.xml
    2. 自定义绝对路径: /opt/xxx/yyy.xml → /opt/xxx/yyy.xml (直接使用)
    """
    if not web_path:
        return None
    # Docker 标准路径格式
    if web_path.startswith("/app/config/"):
        relative_path = web_path[len("/app/config/"):]
        return CONFIG_DIR / relative_path
    # 自定义绝对路径（Linux 或 Windows）
    if web_path.startswith("/") or (len(web_path) >= 2 and web_path[1] == ':'):
        return Path(web_path)
    return None


async def get_episodes_for_anime(session: AsyncSession, anime_id: int) -> List[Episode]:
    """获取anime的所有episodes"""
    stmt = (
        select(Episode)
        .join(AnimeSource, Episode.sourceId == AnimeSource.id)
        .where(AnimeSource.animeId == anime_id)
        .options(selectinload(Episode.source).selectinload(AnimeSource.anime))
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def preview_migrate_danmaku(
    session: AsyncSession,
    anime_ids: List[int],
    target_path: str,
    keep_structure: bool = True
) -> Dict[str, Any]:
    """预览批量迁移结果（不实际执行）"""
    results = {
        "totalCount": 0,
        "previewItems": []
    }

    target_base = Path(target_path.replace("/app/config/", str(CONFIG_DIR) + "/"))

    for anime_id in anime_ids:
        episodes = await get_episodes_for_anime(session, anime_id)
        if not episodes:
            continue

        anime = episodes[0].source.anime

        for episode in episodes:
            if not episode.danmakuFilePath:
                continue

            old_path = get_fs_path_from_web_path(episode.danmakuFilePath)
            if not old_path:
                continue

            # 计算新路径
            if keep_structure:
                try:
                    relative = old_path.relative_to(DANMAKU_BASE_DIR)
                except ValueError:
                    relative = Path(old_path.name)
                new_path = target_base / relative
            else:
                new_path = target_base / old_path.name

            # 计算新的 web 路径 - 支持自定义路径（不在 CONFIG_DIR 下）
            try:
                new_web_path = "/app/config/" + str(new_path.relative_to(CONFIG_DIR))
            except ValueError:
                # 自定义路径不在 CONFIG_DIR 下，直接使用绝对路径
                new_web_path = str(new_path)

            results["totalCount"] += 1
            results["previewItems"].append({
                "animeId": anime_id,
                "animeTitle": anime.title,
                "episodeId": episode.id,
                "episodeIndex": episode.episodeIndex,
                "oldPath": episode.danmakuFilePath,
                "newPath": new_web_path,
                "exists": old_path.exists() if old_path else False
            })

    return results


async def batch_migrate_danmaku(
    session: AsyncSession,
    anime_ids: List[int],
    target_path: str,
    keep_structure: bool = True,
    conflict_action: str = "skip"
) -> Dict[str, Any]:
    """批量迁移弹幕文件到新目录"""
    results = {
        "success": True, 
        "totalCount": 0, 
        "successCount": 0, 
        "failedCount": 0, 
        "skippedCount": 0, 
        "details": []
    }
    
    target_base = Path(target_path.replace("/app/config/", str(CONFIG_DIR) + "/"))
    target_base.mkdir(parents=True, exist_ok=True)
    
    for anime_id in anime_ids:
        episodes = await get_episodes_for_anime(session, anime_id)
        
        for episode in episodes:
            results["totalCount"] += 1
            
            if not episode.danmakuFilePath:
                results["skippedCount"] += 1
                results["details"].append({
                    "episodeId": episode.id, 
                    "status": "skipped", 
                    "reason": "无弹幕文件"
                })
                continue
            
            old_path = get_fs_path_from_web_path(episode.danmakuFilePath)
            if not old_path or not old_path.exists():
                results["skippedCount"] += 1
                results["details"].append({
                    "episodeId": episode.id, 
                    "status": "skipped", 
                    "reason": "文件不存在"
                })
                continue
            
            # 计算新路径
            if keep_structure:
                try:
                    relative = old_path.relative_to(DANMAKU_BASE_DIR)
                except ValueError:
                    relative = old_path.name
                new_path = target_base / relative
            else:
                new_path = target_base / old_path.name
            
            # 处理冲突
            if new_path.exists():
                if conflict_action == "skip":
                    results["skippedCount"] += 1
                    results["details"].append({
                        "episodeId": episode.id, 
                        "status": "skipped", 
                        "reason": "目标文件已存在"
                    })
                    continue
                elif conflict_action == "rename":
                    stem, suffix = new_path.stem, new_path.suffix
                    counter = 1
                    while new_path.exists():
                        new_path = new_path.parent / f"{stem}_{counter}{suffix}"
                        counter += 1
            
            try:
                new_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_path), str(new_path))

                # 更新数据库路径 - 支持自定义路径（不在 CONFIG_DIR 下）
                try:
                    new_web_path = "/app/config/" + str(new_path.relative_to(CONFIG_DIR))
                except ValueError:
                    # 自定义路径不在 CONFIG_DIR 下，直接使用绝对路径
                    new_web_path = str(new_path)

                await session.execute(
                    update(Episode).where(Episode.id == episode.id).values(danmakuFilePath=new_web_path)
                )

                results["successCount"] += 1
                results["details"].append({
                    "episodeId": episode.id,
                    "status": "success",
                    "newPath": new_web_path
                })
            except Exception as e:
                logger.error(f"迁移文件失败: {e}")
                results["failedCount"] += 1
                results["details"].append({
                    "episodeId": episode.id,
                    "status": "failed",
                    "reason": str(e)
                })
    
    await session.commit()
    results["success"] = results["failedCount"] == 0
    return results


async def preview_rename_danmaku(
    session: AsyncSession,
    anime_ids: List[int],
    mode: str,
    prefix: str = "",
    suffix: str = "",
    regex_pattern: str = "",
    regex_replace: str = ""
) -> Dict[str, Any]:
    """预览批量重命名结果（不实际执行）"""
    results = {
        "totalCount": 0,
        "previewItems": []
    }

    for anime_id in anime_ids:
        episodes = await get_episodes_for_anime(session, anime_id)
        if not episodes:
            continue

        anime = episodes[0].source.anime

        for episode in episodes:
            if not episode.danmakuFilePath:
                continue

            old_path = episode.danmakuFilePath
            old_fs_path = get_fs_path_from_web_path(old_path)
            exists = old_fs_path and old_fs_path.exists()

            # 提取文件名
            old_name = old_path.rsplit('/', 1)[-1] if '/' in old_path else old_path
            old_stem = old_name.rsplit('.', 1)[0] if '.' in old_name else old_name
            extension = '.' + old_name.rsplit('.', 1)[1] if '.' in old_name else ''

            # 计算新文件名
            try:
                if mode == "prefix":
                    new_name = f"{prefix}{old_stem}{suffix}{extension}"
                elif mode == "regex":
                    new_name = re.sub(regex_pattern, regex_replace, old_stem) + extension
                else:
                    new_name = old_name

                # 构建新路径 - 使用 Path 确保分隔符一致
                new_path = str(Path(old_path).parent / new_name)

                results["totalCount"] += 1
                results["previewItems"].append({
                    "animeId": anime.id,
                    "animeTitle": anime.title,
                    "episodeId": episode.id,
                    "episodeIndex": episode.episodeIndex,
                    "oldName": old_name,
                    "newName": new_name,
                    "oldPath": old_path,
                    "newPath": new_path,
                    "exists": exists
                })
            except re.error as e:
                results["totalCount"] += 1
                results["previewItems"].append({
                    "animeId": anime.id,
                    "animeTitle": anime.title,
                    "episodeId": episode.id,
                    "episodeIndex": episode.episodeIndex,
                    "oldName": old_name,
                    "newName": f"[正则错误: {e}]",
                    "oldPath": old_path,
                    "newPath": "",
                    "exists": exists,
                    "error": True
                })

    return results


async def batch_rename_danmaku(
    session: AsyncSession,
    anime_ids: List[int],
    mode: str,
    prefix: str = "",
    suffix: str = "",
    regex_pattern: str = "",
    regex_replace: str = "",
    direct_renames: Dict[int, str] = None
) -> Dict[str, Any]:
    """批量重命名弹幕文件

    Args:
        mode: 重命名模式 - 'prefix' (前后缀), 'regex' (正则替换), 'direct' (直接指定新名称)
        direct_renames: 直接指定新名称的字典 {episodeId: newName}，仅当 mode='direct' 时使用
    """
    results = {
        "success": True,
        "totalCount": 0,
        "successCount": 0,
        "failedCount": 0,
        "skippedCount": 0,
        "details": []
    }

    for anime_id in anime_ids:
        episodes = await get_episodes_for_anime(session, anime_id)

        for episode in episodes:
            results["totalCount"] += 1

            if not episode.danmakuFilePath:
                results["skippedCount"] += 1
                results["details"].append({
                    "episodeId": episode.id,
                    "status": "skipped",
                    "reason": "无弹幕文件"
                })
                continue

            old_path = get_fs_path_from_web_path(episode.danmakuFilePath)
            if not old_path or not old_path.exists():
                results["skippedCount"] += 1
                results["details"].append({
                    "episodeId": episode.id,
                    "status": "skipped",
                    "reason": "文件不存在"
                })
                continue

            old_name = old_path.stem
            extension = old_path.suffix

            # 计算新文件名
            if mode == "direct" and direct_renames and episode.id in direct_renames:
                # 直接使用前端计算好的新名称
                new_name = direct_renames[episode.id]
            elif mode == "prefix":
                new_name = f"{prefix}{old_name}{suffix}{extension}"
            elif mode == "regex":
                try:
                    new_name = re.sub(regex_pattern, regex_replace, old_name) + extension
                except re.error as e:
                    results["failedCount"] += 1
                    results["details"].append({
                        "episodeId": episode.id,
                        "status": "failed",
                        "reason": f"正则表达式错误: {e}"
                    })
                    continue
            else:
                new_name = old_name + extension

            new_path = old_path.parent / new_name

            # 如果新旧路径相同，跳过
            if new_path == old_path:
                results["skippedCount"] += 1
                results["details"].append({
                    "episodeId": episode.id,
                    "status": "skipped",
                    "reason": "文件名未变化"
                })
                continue

            # 处理冲突
            if new_path.exists():
                stem, suffix_ext = new_path.stem, new_path.suffix
                counter = 1
                while new_path.exists():
                    new_path = new_path.parent / f"{stem}_{counter}{suffix_ext}"
                    counter += 1

            try:
                old_path.rename(new_path)

                # 更新数据库路径 - 支持自定义路径（不在 CONFIG_DIR 下）
                try:
                    new_web_path = "/app/config/" + str(new_path.relative_to(CONFIG_DIR))
                except ValueError:
                    # 自定义路径不在 CONFIG_DIR 下，直接使用绝对路径
                    new_web_path = str(new_path)

                await session.execute(
                    update(Episode).where(Episode.id == episode.id).values(danmakuFilePath=new_web_path)
                )

                results["successCount"] += 1
                results["details"].append({
                    "episodeId": episode.id,
                    "status": "success",
                    "oldName": old_path.name,
                    "newName": new_path.name
                })
            except Exception as e:
                logger.error(f"重命名文件失败: {e}")
                results["failedCount"] += 1
                results["details"].append({
                    "episodeId": episode.id,
                    "status": "failed",
                    "reason": str(e)
                })

    await session.commit()
    results["success"] = results["failedCount"] == 0
    return results


async def preview_apply_template(
    session: AsyncSession,
    anime_ids: List[int],
    template_type: str,
    custom_template: str = None,
    config_manager = None
) -> Dict[str, Any]:
    """预览应用模板结果（不实际执行）"""
    results = {
        "totalCount": 0,
        "previewItems": []
    }

    # 根据模板类型确定目标目录和命名模板
    # 注意：这里使用固定的预设模板，而不是用户配置的模板
    # 因为"应用新模板"功能的目的是让用户选择一个预设模板来重新组织文件
    if template_type == "tv":
        base_dir = "/app/config/danmaku"
        template = "${title}/Season ${season:02d}/${title} - S${season:02d}E${episode:02d}"
        if config_manager:
            base_dir = await config_manager.get('tvDanmakuDirectoryPath', base_dir)
    elif template_type == "movie":
        base_dir = "/app/config/danmaku"
        template = "${title}/${title}"
        if config_manager:
            base_dir = await config_manager.get('movieDanmakuDirectoryPath', base_dir)
    elif template_type == "plex":
        base_dir = "/app/config/danmaku"
        template = "${title}/${title} - S${season:02d}E${episode:02d}"
        if config_manager:
            base_dir = await config_manager.get('tvDanmakuDirectoryPath', base_dir)
    elif template_type == "emby":
        base_dir = "/app/config/danmaku"
        template = "${title}/${title} S${season:02d}/${title} S${season:02d}E${episode:02d}"
        if config_manager:
            base_dir = await config_manager.get('tvDanmakuDirectoryPath', base_dir)
    elif template_type == "titleBase":
        base_dir = "/app/config/danmaku"
        template = "${titleBase}/Season ${season:02d}/${titleBase} - S${season:02d}E${episode:02d}"
        if config_manager:
            base_dir = await config_manager.get('tvDanmakuDirectoryPath', base_dir)
    elif template_type == "custom_movie":
        # 使用用户在存储配置中设置的电影模板
        base_dir = "/app/config/danmaku/movies"
        template = "${animeId}/${episodeId}"  # 默认值
        if config_manager:
            base_dir = await config_manager.get('movieDanmakuDirectoryPath', base_dir)
            template = await config_manager.get('movieDanmakuFilenameTemplate', template)
    elif template_type == "custom_tv":
        # 使用用户在存储配置中设置的电视模板
        base_dir = "/app/config/danmaku/tv"
        template = "${animeId}/${episodeId}"  # 默认值
        if config_manager:
            base_dir = await config_manager.get('tvDanmakuDirectoryPath', base_dir)
            template = await config_manager.get('tvDanmakuFilenameTemplate', template)
    elif template_type == "custom" and custom_template:
        base_dir = "/app/config/danmaku"
        template = custom_template
        if config_manager:
            base_dir = await config_manager.get('tvDanmakuDirectoryPath', base_dir)
    else:  # id
        base_dir = "/app/config/danmaku"
        template = "${animeId}/${episodeId}"

    # 保持 base_dir 原样，不再强制转换为 /app/config/ 格式
    # 自定义路径（如 /opt/xxx）应保持原样

    for anime_id in anime_ids:
        episodes = await get_episodes_for_anime(session, anime_id)
        if not episodes:
            continue

        anime = episodes[0].source.anime

        for episode in episodes:
            if not episode.danmakuFilePath:
                continue

            old_path = episode.danmakuFilePath
            old_fs_path = get_fs_path_from_web_path(old_path)
            exists = old_fs_path and old_fs_path.exists()

            # 构建新路径
            try:
                # 注意：season 可能为 0（特别篇/SP），不能使用 or 1 来设置默认值
                season_val = anime.season if anime.season is not None else 1
                episode_val = episode.episodeIndex if episode.episodeIndex is not None else 1
                year_val = anime.year or "Unknown"
                relative_path = template.replace("${title}", anime.title or "Unknown")
                # 处理 titleBase（标准化标题，去除季度信息）
                title_base = normalize_title(anime.title) if anime.title else "Unknown"
                relative_path = relative_path.replace("${titleBase}", title_base)
                # 先处理带格式的变量（补零）
                relative_path = relative_path.replace("${season:02d}", str(season_val).zfill(2))
                relative_path = relative_path.replace("${episode:02d}", str(episode_val).zfill(2))
                relative_path = relative_path.replace("${episode:03d}", str(episode_val).zfill(3))
                # 再处理不带格式的变量（不补零）
                relative_path = relative_path.replace("${season}", str(season_val))
                relative_path = relative_path.replace("${episode}", str(episode_val))
                relative_path = relative_path.replace("${year}", str(year_val))
                relative_path = relative_path.replace("${animeId}", str(anime.id))
                relative_path = relative_path.replace("${episodeId}", str(episode.id))
                # 处理 provider 变量
                provider_name = episode.source.providerName if episode.source else "unknown"
                relative_path = relative_path.replace("${provider}", provider_name or "unknown")

                # 清理非法字符
                relative_path = re.sub(r'[<>:"|?*]', '_', relative_path)

                # 使用 Path 拼接确保路径分隔符与操作系统一致
                new_path = str(Path(base_dir) / f"{relative_path.lstrip('/')}.xml")

                results["totalCount"] += 1
                results["previewItems"].append({
                    "animeId": anime.id,
                    "animeTitle": anime.title,
                    "episodeId": episode.id,
                    "episodeIndex": episode.episodeIndex,
                    "oldPath": old_path,
                    "newPath": new_path,
                    "exists": exists
                })
            except Exception as e:
                logger.error(f"预览模板失败: {e}")

    return results


async def apply_danmaku_template(
    session: AsyncSession,
    anime_ids: List[int],
    template_type: str,
    custom_template: str = None,
    config_manager = None
) -> Dict[str, Any]:
    """按新的存储模板重新组织弹幕文件"""
    results = {
        "success": True,
        "totalCount": 0,
        "successCount": 0,
        "failedCount": 0,
        "skippedCount": 0,
        "details": []
    }

    # 根据模板类型确定目标目录和命名模板
    # 注意：这里使用固定的预设模板，而不是用户配置的模板
    # 因为"应用新模板"功能的目的是让用户选择一个预设模板来重新组织文件
    if template_type == "tv":
        base_dir = "/app/config/danmaku"
        template = "${title}/Season ${season:02d}/${title} - S${season:02d}E${episode:02d}"
        if config_manager:
            base_dir = await config_manager.get('tvDanmakuDirectoryPath', base_dir)
    elif template_type == "movie":
        base_dir = "/app/config/danmaku"
        template = "${title}/${title}"
        if config_manager:
            base_dir = await config_manager.get('movieDanmakuDirectoryPath', base_dir)
    elif template_type == "plex":
        base_dir = "/app/config/danmaku"
        template = "${title}/${title} - S${season:02d}E${episode:02d}"
        if config_manager:
            base_dir = await config_manager.get('tvDanmakuDirectoryPath', base_dir)
    elif template_type == "emby":
        base_dir = "/app/config/danmaku"
        template = "${title}/${title} S${season:02d}/${title} S${season:02d}E${episode:02d}"
        if config_manager:
            base_dir = await config_manager.get('tvDanmakuDirectoryPath', base_dir)
    elif template_type == "titleBase":
        base_dir = "/app/config/danmaku"
        template = "${titleBase}/Season ${season:02d}/${titleBase} - S${season:02d}E${episode:02d}"
        if config_manager:
            base_dir = await config_manager.get('tvDanmakuDirectoryPath', base_dir)
    elif template_type == "custom_movie":
        # 使用用户在存储配置中设置的电影模板
        base_dir = "/app/config/danmaku/movies"
        template = "${animeId}/${episodeId}"  # 默认值
        if config_manager:
            base_dir = await config_manager.get('movieDanmakuDirectoryPath', base_dir)
            template = await config_manager.get('movieDanmakuFilenameTemplate', template)
    elif template_type == "custom_tv":
        # 使用用户在存储配置中设置的电视模板
        base_dir = "/app/config/danmaku/tv"
        template = "${animeId}/${episodeId}"  # 默认值
        if config_manager:
            base_dir = await config_manager.get('tvDanmakuDirectoryPath', base_dir)
            template = await config_manager.get('tvDanmakuFilenameTemplate', template)
    elif template_type == "custom" and custom_template:
        base_dir = "/app/config/danmaku"
        template = custom_template
        if config_manager:
            base_dir = await config_manager.get('tvDanmakuDirectoryPath', base_dir)
    else:  # id
        base_dir = "/app/config/danmaku"
        template = "${animeId}/${episodeId}"

    # 转换base_dir为实际路径
    if base_dir.startswith("/app/config/"):
        base_path = CONFIG_DIR / base_dir[len("/app/config/"):]
    else:
        base_path = Path(base_dir)
    base_path.mkdir(parents=True, exist_ok=True)

    for anime_id in anime_ids:
        episodes = await get_episodes_for_anime(session, anime_id)
        if not episodes:
            continue

        anime = episodes[0].source.anime

        for episode in episodes:
            results["totalCount"] += 1

            if not episode.danmakuFilePath:
                results["skippedCount"] += 1
                results["details"].append({
                    "episodeId": episode.id,
                    "status": "skipped",
                    "reason": "无弹幕文件"
                })
                continue

            old_path = get_fs_path_from_web_path(episode.danmakuFilePath)
            if not old_path or not old_path.exists():
                results["skippedCount"] += 1
                results["details"].append({
                    "episodeId": episode.id,
                    "status": "skipped",
                    "reason": "文件不存在"
                })
                continue

            # 构建新路径
            try:
                # 注意：season 可能为 0（特别篇/SP），不能使用 or 1 来设置默认值
                season_val = anime.season if anime.season is not None else 1
                episode_val = episode.episodeIndex if episode.episodeIndex is not None else 1
                year_val = anime.year or "Unknown"
                relative_path = template.replace("${title}", anime.title or "Unknown")
                # 处理 titleBase（标准化标题，去除季度信息）
                title_base = normalize_title(anime.title) if anime.title else "Unknown"
                relative_path = relative_path.replace("${titleBase}", title_base)
                # 先处理带格式的变量（补零）
                relative_path = relative_path.replace("${season:02d}", str(season_val).zfill(2))
                relative_path = relative_path.replace("${episode:02d}", str(episode_val).zfill(2))
                relative_path = relative_path.replace("${episode:03d}", str(episode_val).zfill(3))
                # 再处理不带格式的变量（不补零）
                relative_path = relative_path.replace("${season}", str(season_val))
                relative_path = relative_path.replace("${episode}", str(episode_val))
                relative_path = relative_path.replace("${year}", str(year_val))
                relative_path = relative_path.replace("${animeId}", str(anime.id))
                relative_path = relative_path.replace("${episodeId}", str(episode.id))
                # 处理 provider 变量
                provider_name = episode.source.providerName if episode.source else "unknown"
                relative_path = relative_path.replace("${provider}", provider_name or "unknown")

                # 清理非法字符
                relative_path = re.sub(r'[<>:"|?*]', '_', relative_path)

                new_path = base_path / f"{relative_path}.xml"

                # 如果新旧路径相同，跳过
                if new_path == old_path:
                    results["skippedCount"] += 1
                    results["details"].append({
                        "episodeId": episode.id,
                        "status": "skipped",
                        "reason": "路径未变化"
                    })
                    continue

                # 处理冲突
                if new_path.exists():
                    stem, suffix = new_path.stem, new_path.suffix
                    counter = 1
                    while new_path.exists():
                        new_path = new_path.parent / f"{stem}_{counter}{suffix}"
                        counter += 1

                new_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_path), str(new_path))

                # 更新数据库路径 - 支持自定义路径（不在 CONFIG_DIR 下）
                try:
                    new_web_path = "/app/config/" + str(new_path.relative_to(CONFIG_DIR))
                except ValueError:
                    # 自定义路径不在 CONFIG_DIR 下，直接使用绝对路径
                    new_web_path = str(new_path)

                await session.execute(
                    update(Episode).where(Episode.id == episode.id).values(danmakuFilePath=new_web_path)
                )

                results["successCount"] += 1
                results["details"].append({
                    "episodeId": episode.id,
                    "status": "success",
                    "oldPath": episode.danmakuFilePath,
                    "newPath": new_web_path
                })
            except Exception as e:
                logger.error(f"应用模板失败: {e}")
                results["failedCount"] += 1
                results["details"].append({
                    "episodeId": episode.id,
                    "status": "failed",
                    "reason": str(e)
                })

    await session.commit()
    results["success"] = results["failedCount"] == 0
    return results

