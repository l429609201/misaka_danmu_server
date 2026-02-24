"""
本地弹幕文件扫描器
"""
import os
import re
import logging
import shutil
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.crud import local_danmaku as local_danmaku_crud


logger = logging.getLogger(__name__)


class LocalDanmakuScanner:
    """本地弹幕文件扫描器"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory
        self.logger = logging.getLogger(self.__class__.__name__)

    async def scan_directory(self, scan_path: str) -> Dict[str, Any]:
        """
        扫描指定目录下的所有.xml弹幕文件
        
        Args:
            scan_path: 扫描根目录
            
        Returns:
            扫描结果统计
        """
        if not os.path.exists(scan_path):
            raise ValueError(f"扫描路径不存在: {scan_path}")

        if not os.path.isdir(scan_path):
            raise ValueError(f"扫描路径不是目录: {scan_path}")

        self.logger.info(f"开始扫描目录: {scan_path}")

        # 清空之前的扫描结果
        async with self.session_factory() as session:
            await local_danmaku_crud.clear_all_local_items(session)

        # 递归扫描.xml文件
        xml_files = []
        for root, dirs, files in os.walk(scan_path):
            for file in files:
                if file.lower().endswith('.xml'):
                    xml_files.append(os.path.join(root, file))

        self.logger.info(f"找到 {len(xml_files)} 个.xml文件")

        # 解析每个文件
        success_count = 0
        error_count = 0

        for xml_file in xml_files:
            try:
                await self._process_xml_file(xml_file, scan_path)
                success_count += 1
            except Exception as e:
                self.logger.error(f"处理文件失败 {xml_file}: {e}")
                error_count += 1

        self.logger.info(f"扫描完成: 成功 {success_count}, 失败 {error_count}")

        return {
            "total": len(xml_files),
            "success": success_count,
            "error": error_count
        }

    async def _process_xml_file(self, xml_file: str, scan_root: str):
        """处理单个.xml文件"""
        file_path = Path(xml_file)
        parent_dir = file_path.parent
        file_name = file_path.stem  # 不含扩展名的文件名

        # 尝试查找nfo文件
        nfo_path, nfo_data = self._find_and_parse_nfo(file_path)

        # 从文件名或nfo提取信息
        title, media_type, season, episode = self._extract_metadata(
            file_name, parent_dir, nfo_data
        )

        # 如果是电视剧分集,尝试从父剧集的tvshow.nfo继承类型
        if media_type == "tv_series" and season is not None and episode is not None:
            parent_nfo_data = self._find_parent_tvshow_nfo(file_path)
            if parent_nfo_data and 'type' in parent_nfo_data:
                # 将nfo中的type映射到数据库支持的类型
                nfo_type = parent_nfo_data['type'].lower()
                media_type = self._normalize_media_type(nfo_type)
                self.logger.debug(f"分集继承父剧集类型: {nfo_type} -> {media_type}")

        # 从nfo提取其他元数据
        year_str = nfo_data.get('year') if nfo_data else None
        year = int(year_str) if year_str and str(year_str).isdigit() else None
        tmdb_id = nfo_data.get('tmdbid') if nfo_data else None
        tvdb_id = nfo_data.get('tvdbid') if nfo_data else None
        imdb_id = nfo_data.get('imdbid') if nfo_data else None

        # 查找海报文件(从nfo所在目录)
        poster_url = self._find_poster(file_path, nfo_path, media_type, season, scan_root)

        # 存入数据库
        async with self.session_factory() as session:
            await local_danmaku_crud.create_local_item(
                session,
                file_path=str(xml_file),
                title=title,
                media_type=media_type,
                season=season,
                episode=episode,
                year=year,
                tmdb_id=tmdb_id,
                tvdb_id=tvdb_id,
                imdb_id=imdb_id,
                poster_url=poster_url,
                nfo_path=nfo_path
            )

        self.logger.debug(f"已添加: {title} (S{season}E{episode})" if season and episode else f"已添加: {title}")

    def _find_poster(
        self,
        xml_file: Path,
        nfo_path: Optional[str],
        media_type: str,
        season: Optional[int],
        scan_root: str
    ) -> Optional[str]:
        """
        查找海报文件

        Args:
            xml_file: xml文件路径
            nfo_path: nfo文件路径
            media_type: 媒体类型(movie/tv_series)
            season: 季度(仅电视剧)
            scan_root: 扫描根目录

        Returns:
            海报相对路径(相对于scan_root)
        """
        if not nfo_path:
            return None

        nfo_dir = Path(nfo_path).parent

        if media_type == "movie":
            # 电影: 查找nfo同目录下的poster.jpg
            poster_file = nfo_dir / 'poster.jpg'
            if poster_file.exists():
                return os.path.relpath(str(poster_file), scan_root)
        else:
            # 电视剧: 查找季度海报或剧集海报
            if season is not None:
                # 优先查找季度海报: season01-poster.jpg
                season_poster = nfo_dir / f'season{season:02d}-poster.jpg'
                if season_poster.exists():
                    return os.path.relpath(str(season_poster), scan_root)

            # 查找剧集海报: poster.jpg
            poster_file = nfo_dir / 'poster.jpg'
            if poster_file.exists():
                return os.path.relpath(str(poster_file), scan_root)

        return None

    def _normalize_media_type(self, nfo_type: str) -> str:
        """
        将nfo文件中的type字段映射到数据库支持的类型

        Args:
            nfo_type: nfo文件中的type值(如tvshow, season, episode等)

        Returns:
            数据库支持的类型(movie或tv_series)
        """
        # Kodi nfo type映射
        type_mapping = {
            'movie': 'movie',
            'tvshow': 'tv_series',
            'season': 'tv_series',
            'episode': 'tv_series',
        }

        return type_mapping.get(nfo_type.lower(), 'tv_series')

    def _find_parent_tvshow_nfo(self, xml_file: Path) -> Optional[Dict[str, Any]]:
        """
        查找并解析父剧集的tvshow.nfo文件
        用于分集继承父剧集的类型信息

        Returns:
            nfo数据字典,如果找不到则返回None
        """
        # 策略1: 查找上级目录的tvshow.nfo(季度文件夹内的分集)
        # 文件结构: 越狱/Season 1/S01E01.xml -> 越狱/tvshow.nfo
        parent_tvshow_nfo = xml_file.parent.parent / 'tvshow.nfo'
        if parent_tvshow_nfo.exists():
            return self._parse_nfo(parent_tvshow_nfo)

        return None

    def _find_and_parse_nfo(self, xml_file: Path) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """
        查找并解析nfo文件

        Returns:
            (nfo文件路径, nfo数据字典)
        """
        # 策略1: 查找父目录下的tvshow.nfo(电视剧)
        tvshow_nfo = xml_file.parent / 'tvshow.nfo'
        if tvshow_nfo.exists():
            return str(tvshow_nfo), self._parse_nfo(tvshow_nfo)

        # 策略2: 查找上级目录的tvshow.nfo(季度文件夹)
        parent_tvshow_nfo = xml_file.parent.parent / 'tvshow.nfo'
        if parent_tvshow_nfo.exists():
            return str(parent_tvshow_nfo), self._parse_nfo(parent_tvshow_nfo)

        # 策略3: 查找父目录下唯一的nfo文件(电影)
        # 电影文件夹通常只有一个nfo文件,可能是任意名称
        parent_nfo_files = list(xml_file.parent.glob('*.nfo'))
        if len(parent_nfo_files) == 1:
            return str(parent_nfo_files[0]), self._parse_nfo(parent_nfo_files[0])

        return None, None

    def _parse_nfo(self, nfo_file: Path) -> Dict[str, Any]:
        """解析nfo文件"""
        try:
            tree = ET.parse(nfo_file)
            root = tree.getroot()

            data = {}

            # 提取常见字段(不包括thumb,海报从文件系统查找)
            for tag in ['title', 'year', 'tmdbid', 'tvdbid', 'imdbid', 'type']:
                elem = root.find(tag)
                if elem is not None and elem.text:
                    data[tag] = elem.text.strip()

            # 处理uniqueid标签(Kodi格式)
            for uniqueid in root.findall('uniqueid'):
                id_type = uniqueid.get('type', '').lower()
                id_value = uniqueid.text.strip() if uniqueid.text else None
                if id_value:
                    if id_type == 'tmdb':
                        data['tmdbid'] = id_value
                    elif id_type == 'tvdb':
                        data['tvdbid'] = id_value
                    elif id_type == 'imdb':
                        data['imdbid'] = id_value

            return data
        except Exception as e:
            self.logger.warning(f"解析nfo文件失败 {nfo_file}: {e}")
            return {}

    def _extract_metadata(
        self,
        file_name: str,
        parent_dir: Path,
        nfo_data: Optional[Dict[str, Any]]
    ) -> Tuple[str, str, Optional[int], Optional[int]]:
        """
        从文件名和目录结构提取元数据
        
        Returns:
            (title, media_type, season, episode)
        """
        # 尝试从文件名提取季集信息
        season, episode = self._extract_season_episode(file_name)

        # 确定媒体类型
        if season is not None and episode is not None:
            media_type = "tv_series"
        else:
            media_type = "movie"

        # 确定标题
        if nfo_data and 'title' in nfo_data:
            # 优先从nfo读取标题
            title = nfo_data['title']
        else:
            # 从目录结构推断标题
            if media_type == "tv_series":
                # 电视剧: 使用剧集根目录名称
                # 文件结构: 越狱/Season 1/S01E01.xml
                if parent_dir.name.lower().startswith('season'):
                    # 在季度文件夹内,使用上级目录名(剧集根目录)
                    title = parent_dir.parent.name
                else:
                    # 不在季度文件夹内,使用父目录名
                    title = parent_dir.name
            else:
                # 电影: 使用文件夹名称(不是文件名)
                # 文件结构: 阿凡达 (2009)/阿凡达.bilibili.xml
                title = parent_dir.name

        # 清理标题
        title = self._clean_title(title)

        return title, media_type, season, episode

    def _extract_season_episode(self, file_name: str) -> Tuple[Optional[int], Optional[int]]:
        """从文件名提取季集信息 — 委托给统一模块"""
        from src.utils.filename_parser import extract_season_episode
        return extract_season_episode(file_name)

    def _clean_title(self, title: str) -> str:
        """清理标题 — 委托给统一模块"""
        from src.utils.filename_parser import clean_title
        return clean_title(title)


def copy_local_poster(poster_path: str) -> Optional[str]:
    """
    复制本地海报文件到海报目录

    Args:
        poster_path: 本地海报文件路径(可以是相对路径或绝对路径)

    Returns:
        复制后的Web可访问路径,例如 /data/images/xxxx.jpg
        失败返回None
    """
    try:
        from .image_utils import IMAGE_DIR

        poster_file = Path(poster_path)

        # 如果是相对路径,尝试解析为绝对路径
        if not poster_file.is_absolute():
            # 尝试相对于当前工作目录
            poster_file = Path.cwd() / poster_file

        if not poster_file.exists():
            logger.warning(f"海报文件不存在: {poster_file}")
            return None

        if not poster_file.is_file():
            logger.warning(f"海报路径不是文件: {poster_file}")
            return None

        # 确保图片目录存在
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)

        # 生成新文件名
        extension = poster_file.suffix or ".jpg"
        new_filename = f"{uuid.uuid4()}{extension}"
        dest_path = IMAGE_DIR / new_filename

        # 复制文件
        shutil.copy2(poster_file, dest_path)
        logger.info(f"海报已复制: {poster_file} -> {dest_path}")

        return f"/data/images/{new_filename}"

    except Exception as e:
        logger.error(f"复制海报文件失败 {poster_path}: {e}", exc_info=True)
        return None

