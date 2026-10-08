"""
弹幕编辑工作流 - 编排层

职责：协调 DatabaseService（数据访问）和 FileStorageService（文件IO）完成复杂的业务流程
依赖方向：workflows → services（DatabaseService + FileStorageService）
"""

import logging
from collections import defaultdict
from typing import Optional, Dict, Any, List

from src.services.service_container import get_database_service
from src.services.file_storage_service import get_file_storage_service
from src.utils.parsing.danmaku_parser import parse_dandan_xml_to_comments
from src.utils.danmaku import generate_xml_from_comments
# 编辑业务状态归入现有编排模块，不再保留独立文件管理辅助入口。
from src.workflows.danmaku_edit_operations import (
    DanmakuEditWriteContext, shift_comments,
    split_episode_danmaku, merge_episodes_danmaku,
)

logger = logging.getLogger(__name__)


class DanmakuEditWorkflow:
    """弹幕编辑工作流"""

    def __init__(self) -> None:
        # 读操作复用调用方事务；写操作独立管理提交与文件补偿。
        self.db = get_database_service()
        self.fs = get_file_storage_service()
    
    async def get_danmaku_detail(self, episode_id: int) -> Optional[Dict[str, Any]]:
        """
        获取分集弹幕详情（统计 + 预览）

        流程：查询分集 → 读取文件 → 解析 → 统计分析
        """
        # 步骤1：查询分集（通过 DatabaseService）
        episode = await self.db.episode.get_by_id(episode_id)
        if not episode or not episode.danmakuFilePath:
            return None
        
        # 步骤2：读取弹幕文件（FileStorageService）
        absolute_path = self.fs.resolve_fs_path(episode.danmakuFilePath)
        if not absolute_path or not self.fs.exists(absolute_path):
            return None
        
        try:
            xml_content = await self.fs.read_text(absolute_path)
            if not xml_content:
                return None
            comments = parse_dandan_xml_to_comments(xml_content)
        except Exception as e:
            logger.error(f"读取弹幕文件失败: {e}")
            return None
        
        if not comments:
            return {
                "episodeId": episode_id,
                "totalCount": 0,
                "timeRange": {"start": 0, "end": 0},
                "sources": [],
                "distribution": [],
                "comments": []
            }
        
        # 统计工具统一顶部导入，避免业务方法内延迟导入。

        # 统计来源分布
        source_counts = defaultdict(int)
        for comment in comments:
            p_attr = comment.get('p', '')
            if '[' in p_attr and ']' in p_attr:
                source_tag = p_attr[p_attr.rfind('[') + 1:p_attr.rfind(']')]
                source_counts[source_tag] += 1
            else:
                source_counts['unknown'] += 1
        
        sources = [{"name": name, "count": count} for name, count in source_counts.items()]
        
        # 计算时间范围
        times = [comment.get('t', 0) for comment in comments]
        time_start = min(times) if times else 0
        time_end = max(times) if times else 0
        
        # 计算每分钟弹幕分布
        distribution = defaultdict(int)
        for comment in comments:
            minute = int(comment.get('t', 0) // 60)
            distribution[minute] += 1
        
        max_minute = int(time_end // 60) + 1
        distribution_list = [{"minute": m, "count": distribution.get(m, 0)} for m in range(max_minute)]
        
        # 弹幕预览（前100条，按时间排序）
        sorted_comments = sorted(comments, key=lambda x: x.get('t', 0))
        preview_comments = []
        for comment in sorted_comments[:100]:
            p_attr = comment.get('p', '')
            source = 'unknown'
            if '[' in p_attr and ']' in p_attr:
                source = p_attr[p_attr.rfind('[') + 1:p_attr.rfind(']')]
            preview_comments.append({
                "time": comment.get('t', 0),
                "content": comment.get('m', ''),
                "source": source
            })
        
        return {
            "episodeId": episode_id,
            "totalCount": len(comments),
            "timeRange": {"start": time_start, "end": time_end},
            "sources": sources,
            "distribution": distribution_list,
            "comments": preview_comments
        }
    
    async def get_danmaku_comments_page(
        self,
        episode_id: int,
        page: int = 1,
        page_size: int = 100,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """
        分页获取弹幕列表
        
        流程：查询分集 → 读取文件 → 时间筛选 → 分页
        """
        # 查询分集
        episode = await self.db.episode.get_by_id(episode_id)
        if not episode or not episode.danmakuFilePath:
            return None

        # 读取弹幕文件
        absolute_path = self.fs.resolve_fs_path(episode.danmakuFilePath)
        if not absolute_path or not self.fs.exists(absolute_path):
            return None

        try:
            xml_content = await self.fs.read_text(absolute_path)
            if not xml_content:
                return None
            comments = parse_dandan_xml_to_comments(xml_content)
        except Exception as e:
            logger.error(f"读取弹幕文件失败: {e}")
            return None

        # 时间范围筛选
        if start_time is not None or end_time is not None:
            filtered = []
            for c in comments:
                t = c.get('t', 0)
                if start_time is not None and t < start_time:
                    continue
                if end_time is not None and t > end_time:
                    continue
                filtered.append(c)
            comments = filtered

        # 按时间排序
        comments = sorted(comments, key=lambda x: x.get('t', 0))
        total = len(comments)

        # 分页
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        page_comments = comments[start_idx:end_idx]

        # 格式化输出
        result_comments = []
        for comment in page_comments:
            p_attr = comment.get('p', '')
            source = 'unknown'
            if '[' in p_attr and ']' in p_attr:
                source = p_attr[p_attr.rfind('[') + 1:p_attr.rfind(']')]
            result_comments.append({
                "time": comment.get('t', 0),
                "content": comment.get('m', ''),
                "source": source
            })

        return {"total": total, "comments": result_comments, "page": page, "pageSize": page_size}

    async def apply_time_offset(self, episode_id: int, offset_seconds: float) -> Dict[str, Any]:
        """独立提交单集偏移，文件写入或提交失败时恢复原内容。"""
        ctx = DanmakuEditWriteContext()
        try:
            async with ctx.transaction():
                episode = await ctx.db.episode.get_by_id_with_relations(episode_id)
                if not episode:
                    raise ValueError("分集不存在")
                comments = shift_comments(await ctx.read(episode), offset_seconds, clamp=True)
                path = ctx.fs.resolve_fs_path(episode.danmakuFilePath)
                xml = generate_xml_from_comments(comments, episode_id, episode.source.providerName)
                # 偏移也通过写时分离回写当前分集路径，其他引用不受影响。
                await ctx.write(path, xml, episode_id, len(comments))
            return {"success": True, "modifiedCount": len(comments)}
        except Exception:
            logger.exception("分集 %s 时间偏移失败，已尝试恢复文件", episode_id)
            return {"success": False, "modifiedCount": 0}

    async def split_episode_danmaku(
        self, source_episode_id: int, splits: List[Dict[str, Any]],
        delete_source: bool = True, reset_time: bool = True, config_service: Any = None,
    ) -> Dict[str, Any]:
        """委托拆分编排，统一 API 参数契约。"""
        return await split_episode_danmaku(source_episode_id, splits, delete_source, reset_time, config_service)

    async def merge_episodes_danmaku(
        self, source_episodes: List[Dict[str, Any]], target_episode_index: int,
        target_title: str, delete_sources: bool = True, deduplicate: bool = False,
        config_service: Any = None,
    ) -> Dict[str, Any]:
        """委托合并编排，统一 API 参数契约。"""
        return await merge_episodes_danmaku(
            source_episodes, target_episode_index, target_title,
            delete_sources, deduplicate, config_service,
        )
