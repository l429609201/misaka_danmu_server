"""
DanmakuService - 弹幕业务服务层

职责：
1. 协调 Repository 和 Processor
2. 管理事务边界
3. 处理缓存失效
4. 统一业务接口

流程：
1. Repository 读取数据
2. Processor 处理数据
3. Repository 写入数据
4. 返回结果
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repositories.episode import EpisodeRepository
from src.db.repositories.source import SourceRepository
from src.processors.danmaku import DanmakuProcessor
from src.services.cache_service import get_cache_service
from src.services.file_storage_service import get_file_storage_service
from src.utils.parsing.danmaku_parser import parse_dandan_xml_to_comments

logger = logging.getLogger(__name__)


class DanmakuService:
    """弹幕服务"""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.episode_repo = EpisodeRepository(session)
        self.source_repo = SourceRepository(session)
        self.processor = DanmakuProcessor()
    
    async def save_danmaku(
        self,
        episode_id: int,
        comments: List[Dict[str, Any]],
        fire_threshold: int = 10,
        config_service=None,
        force: bool = False
    ) -> Dict[str, Any]:
        """
        保存弹幕到分集
        
        Args:
            episode_id: 分集 ID
            comments: 弹幕列表
            fire_threshold: 热度阈值
            config_service: 配置服务（用于获取配置）
            force: 是否强制刷新
            
        Returns:
            保存结果
        """
        # 步骤 1：Repository 读取数据
        episode = await self.episode_repo.get_by_id(episode_id)
        if not episode:
            raise ValueError(f"找不到ID为 {episode_id} 的分集")
        
        source = episode.source
        anime = source.anime
        
        # 步骤 2：Processor 处理弹幕数据
        # 2.1 获取配置
        likes_fetch_enabled = True
        if config_service is not None:
            try:
                likes_fetch_enabled = (
                    await config_service.get('danmakuLikesFetchEnabled', 'true')
                ).lower() == 'true'
            except Exception:
                pass
        
        # 2.2 处理弹幕（点赞数）
        processed_comments = self.processor.process_comments(
            comments,
            fire_threshold,
            likes_fetch_enabled
        )
        
        new_comment_count = len(processed_comments)
        old_comment_count = episode.commentCount or 0
        
        # 2.3 判断是否需要刷新
        should_refresh, reason = self.processor.should_refresh_danmaku(
            old_comment_count,
            new_comment_count,
            force
        )
        
        if not should_refresh:
            logger.info(f"分集 {episode_id} {reason}，跳过刷新")
            return {
                "status": "skipped",
                "reason": reason,
                "episode_id": episode_id,
                "old_count": old_comment_count,
                "new_count": new_comment_count
            }
        
        # 2.4 生成 Web 路径
        web_path = self.processor.generate_danmaku_path(
            anime.id,
            source.id,
            episode.episodeIndex,
            source.providerName
        )
        
        # 2.5 生成 XML
        xml_content = self.processor.generate_xml_from_comments(
            processed_comments,
            episode_id,
            source.providerName
        )
        
        # 2.6 写入磁盘
        success = await self.processor.write_danmaku_to_disk(xml_content, web_path)
        if not success:
            raise RuntimeError(f"写入弹幕文件失败: {web_path}")
        
        # 步骤 3：Repository 更新数据库
        # 显式绑定路径与计数，避免历史重复签名导致两者传反。
        await self.episode_repo.update_danmaku_info(
            episode_id=episode_id,
            file_path=web_path,
            count=new_comment_count,
        )
        
        await self.session.flush()
        
        # 步骤 4：失效缓存
        await self._invalidate_danmaku_cache(episode_id)
        
        # 步骤 5：返回结果
        logger.info(f"成功保存 {new_comment_count} 条弹幕到分集 {episode_id}")
        return {
            "status": "success",
            "episode_id": episode_id,
            "comment_count": new_comment_count,
            "file_path": web_path,
            "reason": reason
        }
    
    async def _invalidate_danmaku_cache(self, episode_id: int) -> None:
        """失效弹幕相关缓存。"""
        try:
            cache = get_cache_service()
            await cache.delete(key=f"fetch_comments_{episode_id}", region="default")
            keys = await cache.keys(pattern=f"sampled_{episode_id}_*", region="default")
            for key in keys:
                await cache.delete(key=key, region="default")
        except Exception as exc:
            logger.warning(f"失效弹幕缓存失败: {exc}")

    async def fetch_comments(self, episode_id: int) -> List[Dict[str, Any]]:
        """读取分集弹幕，文件读取统一委托文件服务，解析在线程池执行。"""
        cache_key = f"fetch_comments_{episode_id}"
        cache = None
        try:
            cache = get_cache_service()
            cached = await cache.get(key=cache_key, region="default")
            if cached is not None:
                return cached
        except Exception:
            pass

        episode = await self.episode_repo.get_by_id(episode_id)
        if not episode or not episode.danmakuFilePath:
            return []
        try:
            fs = get_file_storage_service()
            absolute_path = fs.resolve_fs_path(episode.danmakuFilePath)
            if not absolute_path:
                return []
            # 文件服务统一处理路径存在性、编码与读取错误，避免重复实现 I/O。
            xml_content = await fs.read_text(absolute_path)
            if xml_content is None:
                return []
            result = await asyncio.to_thread(parse_dandan_xml_to_comments, xml_content)
            if cache is not None:
                try:
                    await cache.set(key=cache_key, value=result, ttl=300, region="default")
                except Exception:
                    pass
            return result
        except Exception as exc:
            logger.error(f"读取或解析弹幕文件失败: {episode.danmakuFilePath}。错误: {exc}", exc_info=True)
            return []

    async def clear_episode_comments(self, episode_id: int) -> None:
        """清空分集弹幕；当前仍由调用方提交事务，完整变更边界待调用链整改。"""
        episode = await self.episode_repo.get_by_id(episode_id)
        if not episode:
            return
        if episode.danmakuFilePath:
            await get_file_storage_service().delete_by_web_path(episode.danmakuFilePath)
        episode.danmakuFilePath = None
        episode.commentCount = 0
        await self.session.flush()
        await self._invalidate_danmaku_cache(episode_id)
