import asyncio
import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

import aiomysql
import httpx
from opencc import OpenCC

from .. import models
from ..config_manager import ConfigManager
from .base import BaseScraper

logger = logging.getLogger(__name__)

class AcfunScraper(BaseScraper):
    """
    用于从 AcFun 获取外部弹幕的刮削器。
    这个刮削器主要为 /ext 接口服务，因此 search 和 get_episodes 方法为空。
    """
    provider_name = "acfun"

    def __init__(self, pool: aiomysql.Pool, config_manager: ConfigManager):
        super().__init__(pool, config_manager)
        self.client = httpx.AsyncClient(
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.acfun.cn/",
            },
            timeout=20.0,
            follow_redirects=True,
        )
        self.cc_t2s = OpenCC('t2s')

    async def close(self):
        await self.client.aclose()

    async def search(self, keyword: str, episode_info: Optional[Dict[str, Any]] = None) -> List[models.ProviderSearchInfo]:
        # 此方法不用于外部弹幕获取
        return []

    async def get_episodes(self, media_id: str, target_episode_index: Optional[int] = None, db_media_type: Optional[str] = None) -> List[models.ProviderEpisodeInfo]:
        # 此方法不用于外部弹幕获取
        return []

    async def get_comments(self, episode_id: str, progress_callback: Optional[Callable] = None) -> List[dict]:
        """
        对于 AcFun, episode_id 是 danmakuId (也称为 contentId).
        """
        if progress_callback: await progress_callback(10, "正在请求弹幕数据...")
        
        url = f"https://www.acfun.cn/comment_list_json.aspx?contentId={episode_id}"
        try:
            response = await self.client.get(url)
            response.raise_for_status()
            data = response.json()
            
            if progress_callback: await progress_callback(50, f"收到 {len(data.get('commentList', []))} 条原始弹幕，正在处理...")
            
            comments = self._format_comments(data.get("commentList", []))
            
            if progress_callback: await progress_callback(100, "弹幕处理完成")
            return comments
        except Exception as e:
            self.logger.error(f"AcFun: 获取弹幕失败 (danmakuId={episode_id}): {e}", exc_info=True)
            return []

    def _format_comments(self, comments: List[Dict[str, Any]]) -> List[dict]:
        formatted = []
        for comment in comments:
            p_str = comment.get("c", "")
            p_parts = p_str.split(',')
            if len(p_parts) < 4:
                continue
            
            time_sec = float(p_parts[0])
            color = int(p_parts[1])
            mode_acfun = int(p_parts[2])
            
            # AcFun 模式到 dandanplay 模式的映射
            # AcFun: 1-滚动, 2-底部, 3-顶部
            # dandan: 1-滚动, 4-底部, 5-顶部
            mode_dandan = 1
            if mode_acfun == 2: mode_dandan = 4
            elif mode_acfun == 3: mode_dandan = 5
            
            p_string = f"{time_sec:.2f},{mode_dandan},{color},[acfun]"
            
            # 使用评论ID作为弹幕的唯一标识符
            cid = str(comment.get("cid", "0"))
            
            formatted.append({
                "cid": cid,
                "p": p_string,
                "m": self.cc_t2s.convert(comment.get("m", "")),
                "t": round(time_sec, 2)
            })
        return formatted

    async def get_danmaku_id_from_url(self, url: str) -> Optional[str]:
        """
        抓取 AcFun 视频页面并从中提取 danmakuId。
        """
        try:
            response = await self.client.get(url)
            response.raise_for_status()
            html = response.text
            
            # 从HTML内容中查找 danmakuId
            match = re.search(r'danmakuId["\']\s*:\s*["\'](\d+)["\']', html)
            if match:
                danmaku_id = match.group(1)
                self.logger.info(f"AcFun: 从URL {url} 中解析到 danmakuId: {danmaku_id}")
                return danmaku_id
            
            self.logger.warning(f"AcFun: 未能从URL {url} 的HTML中找到 danmakuId。")
            return None
        except Exception as e:
            self.logger.error(f"AcFun: 获取或解析页面失败 (URL={url}): {e}", exc_info=True)
            return None