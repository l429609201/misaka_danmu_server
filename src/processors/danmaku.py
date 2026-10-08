"""
DanmakuProcessor - 弹幕数据处理器

职责：
1. 弹幕数据清洗和过滤
2. XML 格式转换
3. 文件路径生成
4. 文件系统操作
5. 业务规则判断（如是否需要刷新）

不依赖数据库，纯数据处理逻辑。
"""

import asyncio
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from src.core.env import is_docker_environment
from src.services.file_storage_service import get_file_storage_service
from src.utils.misc.common import handle_danmaku_likes
from src.utils.danmaku.xml_generator import generate_xml_from_comments, normalize_p_attr

logger = logging.getLogger(__name__)


class DanmakuProcessor:
    """弹幕处理器"""
    
    def __init__(self):
        pass
    
    def should_refresh_danmaku(
        self,
        current_count: int,
        new_count: int,
        force: bool = False
    ) -> Tuple[bool, str]:
        """
        判断是否需要刷新弹幕
        
        Args:
            current_count: 当前弹幕数量
            new_count: 新弹幕数量
            force: 是否强制刷新
            
        Returns:
            (是否刷新, 原因说明)
        """
        if force:
            return True, "强制刷新"
        
        if new_count <= current_count:
            return False, f"弹幕数未增加 (新:{new_count} <= 旧:{current_count})"
        
        return True, f"弹幕数增加 (新:{new_count} > 旧:{current_count})"
    
    def process_comments(
        self,
        comments: List[Dict[str, Any]],
        fire_threshold: int = 10,
        likes_fetch_enabled: bool = True
    ) -> List[Dict[str, Any]]:
        """
        处理弹幕数据（点赞数处理）
        
        Args:
            comments: 原始弹幕列表
            fire_threshold: 热度阈值
            likes_fetch_enabled: 是否启用点赞数获取
            
        Returns:
            处理后的弹幕列表
        """
        # 使用通用工具处理点赞数
        return handle_danmaku_likes(
            list(comments),
            fire_threshold,
            enabled=likes_fetch_enabled
        )
    
    def normalize_p_attr(
        self,
        p_attr: str,
        provider_name: Optional[str] = None
    ) -> str:
        """委托共享编码器生成九段参数，避免处理器与读取端格式漂移。"""
        return normalize_p_attr(p_attr, provider_name)

    def generate_xml_from_comments(
        self,
        comments: List[Dict[str, Any]],
        episode_id: int,
        provider_name: str = '',
        chat_server: str = 'chat.bilibili.com',
        add_source_tag: bool = True
    ) -> str:
        """委托共享生成器输出九段 XML，保留处理器原有公共参数。"""
        return generate_xml_from_comments(
            comments, episode_id, provider_name, chat_server,
            source_tag='' if add_source_tag else None,
        )

    def generate_danmaku_path(
        self,
        anime_id: int,
        source_id: int,
        episode_index: int,
        provider_name: str
    ) -> str:
        """
        生成弹幕文件的 Web 路径

        Args:
            anime_id: 动漫 ID
            source_id: 数据源 ID
            episode_index: 集数
            provider_name: 数据源名称

        Returns:
            Web 路径（相对路径）
        """
        return f"/danmaku/{anime_id}/{provider_name}_{source_id}_{episode_index}.xml"

    def convert_web_path_to_fs_path(self, web_path: str) -> Optional[Path]:
        """
        将 Web 路径转换为文件系统路径

        Args:
            web_path: Web 路径（如 /danmaku/123/bilibili_456_1.xml）

        Returns:
            文件系统绝对路径
        """
        if not web_path:
            return None

        # 移除前导斜杠
        if web_path.startswith('/'):
            web_path = web_path[1:]

        # 根据运行环境确定基础目录
        if is_docker_environment():
            base_dir = Path("/app/config")
        else:
            base_dir = Path(__file__).resolve().parent.parent.parent / "config"

        try:
            result_path = (base_dir / web_path).resolve()
            return result_path
        except (OSError, RuntimeError):
            logger.warning(f"无效路径: {web_path}")
            return None

    async def write_danmaku_to_disk(
        self,
        xml_content: str,
        web_path: str
    ) -> bool:
        """
        将弹幕 XML 写入磁盘

        Args:
            xml_content: XML 内容
            web_path: Web 路径

        Returns:
            是否成功
        """
        absolute_path = self.convert_web_path_to_fs_path(web_path)
        if not absolute_path:
            logger.error(f"无法转换路径: {web_path}")
            return False

        # 文件服务负责原子写入及取消收尾，处理器不再直接操作磁盘。
        return await get_file_storage_service().write_text(absolute_path, xml_content)

    def calculate_danmaku_statistics(
        self,
        comments: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        计算弹幕统计信息

        Args:
            comments: 弹幕列表

        Returns:
            统计信息字典
        """
        if not comments:
            return {
                "total": 0,
                "time_range": {"start": 0, "end": 0},
                "avg_density": 0
            }

        times = [comment.get('t', 0) for comment in comments if comment.get('t', 0) > 0]

        if not times:
            return {
                "total": len(comments),
                "time_range": {"start": 0, "end": 0},
                "avg_density": 0
            }

        time_start = min(times)
        time_end = max(times)
        duration = (time_end - time_start) / 60  # 转换为分钟

        return {
            "total": len(comments),
            "time_range": {"start": time_start, "end": time_end},
            "avg_density": len(comments) / duration if duration > 0 else 0
        }
