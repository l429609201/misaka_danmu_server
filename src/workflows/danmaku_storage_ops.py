"""弹幕存储批量管理编排：迁移、重命名、按模板重新组织（#462）。

v2.9.0 删除 src/db/crud/danmaku_storage.py 后，API 仍调用 ``crud.*`` 导致 NameError。
本模块按新架构重建这六个操作：
- 数据库经 DatabaseService → DanmakuStorageRepository（仓储只 flush）；
- 文件操作经 FileStorageService；
- 执行类操作由本模块自持事务：先移动文件，再在同一事务里更新路径；
  事务失败时把已移动的文件移回原处，避免数据库与磁盘不一致。
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.services.config_service import ConfigService
from src.services.database_service import DatabaseService
from src.services.file_storage_service import FileStorageService, get_file_storage_service
from src.utils.storage.path_template import DanmakuPathTemplate, create_danmaku_context

logger = logging.getLogger(__name__)

DEFAULT_DANMAKU_DIR = "/app/config/danmaku"

# 预设模板：与前端 DanmakuStorage.jsx 的 presetTemplates 对应
_PRESET_TEMPLATES: Dict[str, Tuple[str, str]] = {
    # 模板类型: (根目录配置键, 命名模板)
    "tv": ("tvDanmakuDirectoryPath", "${title}/Season ${season:02d}/${title} - S${season:02d}E${episode:02d}"),
    "movie": ("movieDanmakuDirectoryPath", "${title}/${title}"),
    "plex": ("tvDanmakuDirectoryPath", "${title}/${title} - S${season:02d}E${episode:02d}"),
    "emby": ("tvDanmakuDirectoryPath", "${title}/${title} S${season:02d}/${title} S${season:02d}E${episode:02d}"),
    "titleBase": ("tvDanmakuDirectoryPath", "${titleBase}/Season ${season:02d}/${titleBase} - S${season:02d}E${episode:02d}"),
}


def _empty_result() -> Dict[str, Any]:
    """构造批量操作结果的初始结构。"""
    return {"success": True, "totalCount": 0, "successCount": 0, "failedCount": 0, "skippedCount": 0, "details": []}


def _skip(results: Dict[str, Any], episode_id: int, reason: str) -> None:
    """记录一条跳过明细。"""
    results["skippedCount"] += 1
    results["details"].append({"episodeId": episode_id, "status": "skipped", "reason": reason})


def _fail(results: Dict[str, Any], episode_id: int, reason: str) -> None:
    """记录一条失败明细。"""
    results["failedCount"] += 1
    results["details"].append({"episodeId": episode_id, "status": "failed", "reason": reason})


def _to_fs_base(fs: FileStorageService, web_dir: str) -> Path:
    """把目标目录（/app/config/... 或自定义绝对路径）转换为文件系统路径。"""
    resolved = fs.resolve_fs_path(web_dir) if web_dir.startswith("/app/config/") else None
    return resolved if resolved is not None else Path(web_dir)


async def _resolve_template(
    template_type: str, custom_template: Optional[str], config_service: Optional[ConfigService]
) -> Tuple[str, str]:
    """根据模板类型返回 (根目录 web 路径, 命名模板)。"""

    async def cfg(key: str, default: str) -> str:
        if not config_service:
            return default
        value = await config_service.get(key, default)
        return (value or "").strip() or default

    if template_type in _PRESET_TEMPLATES:
        dir_key, template = _PRESET_TEMPLATES[template_type]
        return await cfg(dir_key, DEFAULT_DANMAKU_DIR), template
    if template_type == "custom_movie":
        return (await cfg("movieDanmakuDirectoryPath", f"{DEFAULT_DANMAKU_DIR}/movies"),
                await cfg("movieDanmakuFilenameTemplate", "${animeId}/${episodeId}"))
    if template_type == "custom_tv":
        return (await cfg("tvDanmakuDirectoryPath", f"{DEFAULT_DANMAKU_DIR}/tv"),
                await cfg("tvDanmakuFilenameTemplate", "${animeId}/${episodeId}"))
    if template_type == "custom" and custom_template:
        return await cfg("tvDanmakuDirectoryPath", DEFAULT_DANMAKU_DIR), custom_template
    return DEFAULT_DANMAKU_DIR, "${animeId}/${episodeId}"


def _render_relative_path(template: str, episode: Any) -> str:
    """用分集上下文渲染模板，返回不带 .xml 的相对路径。

    复用下载时的 DanmakuPathTemplate，使 ${category}/${type} 等变量与下载路径一致；
    变量值中的 / 等非法字符会被替换，避免标题拆出意外子目录。
    """
    anime = episode.source.anime
    tmdb_id = getattr(anime.metadataRecord, "tmdbId", None) if anime.metadataRecord else None
    context = create_danmaku_context(
        anime_title=anime.title or "Unknown",
        # season 可能为 0（特别篇），不能用 or 1
        season=anime.season if anime.season is not None else 1,
        episode_index=episode.episodeIndex if episode.episodeIndex is not None else 1,
        year=anime.year,
        provider=episode.source.providerName,
        anime_id=anime.id,
        episode_id=episode.id,
        source_id=episode.source.id,
        tmdb_id=tmdb_id,
        anime_type=anime.type,
    )
    # 自定义模板可能含 DanmakuPathTemplate 拒绝的字符，先把模板里的非法字符替换掉
    # 注意不能替换 ':'，否则 ${season:02d} 这类格式化变量会失效
    safe_template = re.sub(r'[<>"|?*\\]', "_", template).replace("..", "_")
    rendered = str(DanmakuPathTemplate(safe_template).generate_path(context))
    return rendered[:-4].lstrip("/") if rendered.endswith(".xml") else rendered.lstrip("/")


async def _commit_moves(
    db: DatabaseService, fs: FileStorageService, moves: List[Tuple[int, Path, Path]]
) -> bool:
    """在一个事务内写入新路径；失败时把文件移回原处。返回是否提交成功。"""
    if not moves:
        return True
    try:
        async with db.transaction():
            for episode_id, _old, new in moves:
                await db.danmaku_storage.update_danmaku_path(episode_id, fs.to_web_path(new))
    except Exception:
        logger.exception("弹幕路径写入数据库失败，回滚已移动的文件")
        for _episode_id, old, new in reversed(moves):
            if await fs.move_file(new, old, on_conflict="error") is None:
                logger.error("回滚移动失败，请手动检查: %s → %s", new, old)
        return False
    # 提交成功后清理因迁移留下的空目录（issue #462 诉求：迁移后不残留空文件夹）
    for _episode_id, old, _new in moves:
        try:
            fs.cleanup_empty_parent_directories(old, stop_at=fs.determine_cleanup_stop_dir(old))
        except Exception:
            logger.warning("清理空目录失败: %s", old.parent, exc_info=True)
    return True


async def _execute_moves(
    db: DatabaseService,
    anime_ids: List[int],
    plan_target: Any,
    conflict_action: str = "rename",
) -> Dict[str, Any]:
    """通用执行流程：对每个分集计算目标路径 → 移动文件 → 统一写库。

    Args:
        plan_target: ``(episode, old_path) -> Path | str``，返回 str 表示失败原因。
        conflict_action: 目标已存在时 skip / overwrite / rename。
    """
    fs = get_file_storage_service()
    results = _empty_result()
    moves: List[Tuple[int, Path, Path]] = []
    async with fs.danmaku_mutation():
        async with db.transaction():
            episodes = [ep for aid in anime_ids for ep in await db.danmaku_storage.get_episodes_for_anime(aid)]
        for episode in episodes:
            results["totalCount"] += 1
            if not episode.danmakuFilePath:
                _skip(results, episode.id, "无弹幕文件")
                continue
            old_path = fs.resolve_fs_path(episode.danmakuFilePath)
            if not old_path or not fs.exists(old_path):
                _skip(results, episode.id, "文件不存在")
                continue
            target = plan_target(episode, old_path)
            if isinstance(target, str):
                _fail(results, episode.id, target)
                continue
            if fs.same_file_path(target, old_path):
                _skip(results, episode.id, "路径未变化")
                continue
            if target.exists() and conflict_action == "skip":
                _skip(results, episode.id, "目标文件已存在")
                continue
            moved = await fs.move_file(old_path, target, on_conflict=conflict_action if conflict_action != "skip" else "error")
            if moved is None:
                _fail(results, episode.id, "移动文件失败")
                continue
            moves.append((episode.id, old_path, moved))
            results["details"].append({
                "episodeId": episode.id, "status": "success",
                "oldPath": episode.danmakuFilePath, "newPath": fs.to_web_path(moved),
                "oldName": old_path.name, "newName": moved.name,
            })
        if not await _commit_moves(db, fs, moves):
            # 已全部回滚：成功明细改记为失败
            for item in results["details"]:
                if item["status"] == "success":
                    item.update(status="failed", reason="数据库写入失败，文件已回滚")
                    results["failedCount"] += 1
            moves = []
    results["successCount"] = len(moves)
    results["success"] = results["failedCount"] == 0
    return results


async def _collect_preview(db: DatabaseService, anime_ids: List[int]) -> List[Any]:
    """读取需要预览的分集（只读事务）。"""
    async with db.transaction():
        return [ep for aid in anime_ids for ep in await db.danmaku_storage.get_episodes_for_anime(aid)]


def _preview_item(episode: Any, fs: FileStorageService, new_path: str, **extra: Any) -> Dict[str, Any]:
    """构造一条预览明细。"""
    old_fs = fs.resolve_fs_path(episode.danmakuFilePath)
    item = {
        "animeId": episode.source.anime.id,
        "animeTitle": episode.source.anime.title,
        "episodeId": episode.id,
        "episodeIndex": episode.episodeIndex,
        "oldPath": episode.danmakuFilePath,
        "newPath": new_path,
        "exists": bool(old_fs and fs.exists(old_fs)),
    }
    item.update(extra)
    return item


# ==================== 迁移 ====================

def _migrate_target(fs: FileStorageService, target_base: Path, old_path: Path, keep_structure: bool) -> Path:
    """计算迁移目标：保持结构时沿用相对弹幕根目录的子路径。"""
    if keep_structure:
        try:
            return target_base / old_path.relative_to(fs._base_dir)
        except ValueError:
            pass
    return target_base / old_path.name


async def preview_migrate_danmaku(
    db: DatabaseService, anime_ids: List[int], target_path: str, keep_structure: bool = True
) -> Dict[str, Any]:
    """预览批量迁移结果（不实际执行）。"""
    fs = get_file_storage_service()
    target_base = _to_fs_base(fs, target_path)
    items = []
    for episode in await _collect_preview(db, anime_ids):
        old_path = fs.resolve_fs_path(episode.danmakuFilePath) if episode.danmakuFilePath else None
        if not old_path:
            continue
        new_path = _migrate_target(fs, target_base, old_path, keep_structure)
        items.append(_preview_item(episode, fs, fs.to_web_path(new_path)))
    return {"totalCount": len(items), "previewItems": items}


async def batch_migrate_danmaku(
    db: DatabaseService, anime_ids: List[int], target_path: str,
    keep_structure: bool = True, conflict_action: str = "skip",
) -> Dict[str, Any]:
    """批量迁移弹幕文件到新目录，并同步数据库路径。"""
    fs = get_file_storage_service()
    target_base = _to_fs_base(fs, target_path)
    return await _execute_moves(
        db, anime_ids,
        lambda _ep, old: _migrate_target(fs, target_base, old, keep_structure),
        conflict_action,
    )


# ==================== 重命名 ====================

def _rename(old_name: str, mode: str, prefix: str, suffix: str, pattern: str, replace: str) -> str:
    """按模式计算新文件名（保留扩展名）；正则错误时抛 re.error。"""
    stem, dot, ext = old_name.rpartition(".")
    if not dot:
        stem, ext = old_name, ""
    extension = f".{ext}" if ext else ""
    if mode == "prefix":
        return f"{prefix}{stem}{suffix}{extension}"
    if mode == "regex":
        return re.sub(pattern, replace, stem) + extension
    return old_name


async def preview_rename_danmaku(
    db: DatabaseService, anime_ids: List[int], mode: str,
    prefix: str = "", suffix: str = "", regex_pattern: str = "", regex_replace: str = "",
) -> Dict[str, Any]:
    """预览批量重命名结果（不实际执行）。"""
    fs = get_file_storage_service()
    items = []
    for episode in await _collect_preview(db, anime_ids):
        if not episode.danmakuFilePath:
            continue
        old_name = Path(episode.danmakuFilePath).name
        try:
            new_name = _rename(old_name, mode, prefix, suffix, regex_pattern, regex_replace)
            new_path = str(Path(episode.danmakuFilePath).parent / new_name)
            items.append(_preview_item(episode, fs, new_path, oldName=old_name, newName=new_name))
        except re.error as e:
            items.append(_preview_item(episode, fs, "", oldName=old_name, newName=f"[正则错误: {e}]", error=True))
    return {"totalCount": len(items), "previewItems": items}


async def batch_rename_danmaku(
    db: DatabaseService, anime_ids: List[int], mode: str,
    prefix: str = "", suffix: str = "", regex_pattern: str = "", regex_replace: str = "",
    direct_renames: Optional[Dict[int, str]] = None,
) -> Dict[str, Any]:
    """批量重命名弹幕文件（prefix / regex / direct）。"""

    def plan(episode: Any, old_path: Path) -> Any:
        if mode == "direct" and direct_renames and episode.id in direct_renames:
            new_name = Path(direct_renames[episode.id]).name  # 只取文件名，防止借重命名跨目录
        else:
            try:
                new_name = _rename(old_path.name, mode, prefix, suffix, regex_pattern, regex_replace)
            except re.error as e:
                return f"正则表达式错误: {e}"
        if not new_name or "/" in new_name or new_name in (".", ".."):
            return "新文件名无效"
        return old_path.parent / new_name

    return await _execute_moves(db, anime_ids, plan, "rename")


# ==================== 应用模板 ====================

async def preview_apply_template(
    db: DatabaseService, anime_ids: List[int], template_type: str,
    custom_template: Optional[str] = None, config_service: Optional[ConfigService] = None,
) -> Dict[str, Any]:
    """预览按模板重新组织后的路径（不实际执行）。"""
    fs = get_file_storage_service()
    base_dir, template = await _resolve_template(template_type, custom_template, config_service)
    items = []
    for episode in await _collect_preview(db, anime_ids):
        if not episode.danmakuFilePath:
            continue
        try:
            relative = _render_relative_path(template, episode)
            items.append(_preview_item(episode, fs, f"{base_dir.rstrip('/')}/{relative}.xml"))
        except Exception:
            logger.exception("预览模板失败: episode=%s", episode.id)
    return {"totalCount": len(items), "previewItems": items}


async def apply_danmaku_template(
    db: DatabaseService, anime_ids: List[int], template_type: str,
    custom_template: Optional[str] = None, config_service: Optional[ConfigService] = None,
) -> Dict[str, Any]:
    """按模板重新组织弹幕文件并同步数据库路径。"""
    fs = get_file_storage_service()
    base_dir, template = await _resolve_template(template_type, custom_template, config_service)
    base_path = _to_fs_base(fs, base_dir)

    def plan(episode: Any, _old: Path) -> Any:
        try:
            return base_path / f"{_render_relative_path(template, episode)}.xml"
        except Exception as e:
            return f"生成路径失败: {e}"

    return await _execute_moves(db, anime_ids, plan, "rename")
