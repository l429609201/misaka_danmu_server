#!/usr/bin/env python3
"""
E站弹幕网 (ezdmw.site) 弹幕搜索源
"""

__version__ = "1.0.0"

import re
import time
import logging
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List, Optional, Union
from urllib.parse import urlencode, urlparse, parse_qs, unquote
from html.parser import HTMLParser

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db import models, ConfigManager
from .base import BaseScraper, get_season_from_title, track_performance
from src.utils import parse_search_keyword

logger = logging.getLogger(__name__)

# ───────────────────────── 常量 ─────────────────────────
BASE_URL = "https://www.ezdmw.site"
PLAYER_URL = "https://player.ezdmw.com"


# ───────────────────────── HTML 解析器 ─────────────────────────

class SearchResultParser(HTMLParser):
    """解析搜索结果页面，提取番剧链接和标题"""

    def __init__(self):
        super().__init__()
        self.results: List[dict] = []
        self._current_href = ""
        self._capture_text = False
        self._current_text = ""
        self._in_a = False
        self._in_p_inside_a = False  # <a> 内部的 <p> 标签

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "a":
            href = attrs_dict.get("href", "")
            if "/Index/bangumi/" in href and href.endswith(".html"):
                self._current_href = href
                self._in_a = True
                self._current_text = ""
        # 只捕获 <a> 内部 <p> 标签的文本（标题区域）
        if tag == "p" and self._in_a:
            self._in_p_inside_a = True
            self._capture_text = True

    def handle_data(self, data):
        if self._capture_text and self._in_p_inside_a:
            self._current_text += data.strip()

    def handle_endtag(self, tag):
        if tag == "p" and self._in_p_inside_a:
            self._in_p_inside_a = False
            self._capture_text = False
        if tag == "a" and self._in_a and self._current_href:
            self._in_a = False
            title = self._current_text.strip()
            if title:
                # 清理标题：移除 "更至XX话"、年份季度信息等
                title = re.sub(r'\s*更至\d+话.*$', '', title).strip()
                title = re.sub(r'\s*\d{4}年\d+月.*$', '', title).strip()
                # 提取番剧 ID
                match = re.search(r"/Index/bangumi/(\d+)\.html", self._current_href)
                if match and title:
                    bangumi_id = match.group(1)
                    if not any(r["id"] == bangumi_id for r in self.results):
                        self.results.append({
                            "id": bangumi_id,
                            "title": title,
                            "url": self._current_href,
                        })
            self._current_href = ""
            self._current_text = ""


class EpisodeListParser(HTMLParser):
    """解析番剧详情页，提取分集列表"""

    def __init__(self):
        super().__init__()
        self.episodes: List[dict] = []
        self._in_anthology = False
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        css_class = attrs_dict.get("class", "")
        # 进入分集列表区域 (circuit_switch1 是主线)
        if "circuit_switch1" in css_class:
            self._in_anthology = True
            self._depth = 0
        if self._in_anthology:
            self._depth += 1
        # 分集链接
        if tag == "a" and self._in_anthology:
            href = attrs_dict.get("href", "")
            if "/Index/video/" in href or "/Index/bangumi/" in href:
                match = re.search(r"/Index/(?:video|bangumi)/(\d+)\.html", href)
                if match:
                    video_id = match.group(1)
                    css = attrs_dict.get("class", "")
                    self.episodes.append({
                        "video_id": video_id,
                        "css_class": css,
                        "href": href,
                    })

    def handle_endtag(self, tag):
        if self._in_anthology:
            self._depth -= 1
            if self._depth <= 0:
                self._in_anthology = False


# ───────────────────────── Scraper 主类 ─────────────────────────

class EzdmwScraper(BaseScraper):
    """E站弹幕网弹幕搜索源"""

    provider_name = "ezdmw"

    handled_domains = ["www.ezdmw.site", "ezdmw.site", "player.ezdmw.com"]

    configurable_fields = {
        "ezdmw_search_timeout": ("搜索超时(秒)", "string", "搜索请求超时时间，默认15秒"),
    }

    def __init__(self, session_factory, config_manager, transport_manager):
        super().__init__(session_factory, config_manager, transport_manager)
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            timeout_str = await self.config_manager.get("ezdmw_search_timeout", "15")
            try:
                timeout_val = float(timeout_str)
            except (ValueError, TypeError):
                timeout_val = 15.0
            proxy = await self._get_proxy_url()
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(timeout_val),
                follow_redirects=True,
                proxy=proxy,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
            )
        return self._client

    # ─────── 内部工具方法 ───────

    async def _fetch_html(self, url: str) -> str:
        """获取 HTML 页面内容"""
        client = await self._get_client()
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text

    async def _extract_sign_and_nk(self, video_id: str) -> Optional[dict]:
        """从播放页面提取 sign 和 nk 参数"""
        cache_key = f"ezdmw_sign_{video_id}"
        cached = await self._get_from_cache(cache_key)
        if cached:
            self.logger.debug(f"ezdmw: sign 缓存命中 video_id={video_id}")
            return cached

        url = f"{BASE_URL}/Index/video/{video_id}.html"
        try:
            html = await self._fetch_html(url)
        except Exception as e:
            self.logger.warning(f"ezdmw: 获取播放页面失败 {url}: {e}")
            return None

        # 从 JS 变量 danmuPlayer1 提取 nk 和 sign
        m = re.search(r'var\s+danmuPlayer1\s*=\s*"([^"]+)"', html)
        if not m:
            self.logger.warning(f"ezdmw: 未找到 danmuPlayer1 变量 video_id={video_id}")
            return None

        player_url = m.group(1).replace("&amp;", "&")
        parsed = urlparse(player_url)
        params = parse_qs(parsed.query)

        nk = params.get("nk", [None])[0]
        sign = params.get("sign", [None])[0]

        if not nk or not sign:
            self.logger.warning(f"ezdmw: 解析 nk/sign 失败 video_id={video_id}")
            return None

        result = {"nk": nk, "sign": sign, "video_id": video_id}
        # sign 长期有效，缓存 24 小时
        await self._set_to_cache(cache_key, result, "ezdmw_sign_ttl", 86400)
        return result

    # ─────── BaseScraper 接口实现 ───────

    @track_performance
    async def search(self, keyword: str, episode_info: Optional[Dict[str, Any]] = None) -> List[models.ProviderSearchInfo]:
        """搜索番剧"""
        parsed = parse_search_keyword(keyword)
        search_title = parsed["title"]
        search_season = parsed.get("season")

        cache_key = f"search_base_{self.provider_name}_{search_title}"
        cached_results = await self._get_from_cache(cache_key)

        if cached_results:
            self.logger.info(f"ezdmw: 搜索缓存命中 '{search_title}'")
            all_results = [models.ProviderSearchInfo.model_validate(r) for r in cached_results]
            for item in all_results:
                item.currentEpisodeIndex = episode_info.get("episode") if episode_info else None
        else:
            self.logger.info(f"ezdmw: 搜索 '{search_title}'...")
            all_results = await self._perform_search(search_title, episode_info)
            if all_results:
                await self._set_to_cache(cache_key, [r.model_dump() for r in all_results], "search_ttl_seconds", 3600)

        if search_season is None:
            return all_results
        final = [r for r in all_results if r.season == search_season]
        self.logger.info(f"ezdmw: S{search_season} 过滤后 {len(final)} 个结果")
        return final

    async def _perform_search(self, keyword: str, episode_info: Optional[Dict[str, Any]] = None) -> List[models.ProviderSearchInfo]:
        """执行实际搜索"""
        url = f"{BASE_URL}/Index/search_some.html?searchText={keyword}&page=0"
        try:
            html = await self._fetch_html(url)
        except Exception as e:
            self.logger.error(f"ezdmw: 搜索请求失败: {e}")
            return []

        parser = SearchResultParser()
        parser.feed(html)

        results = []
        for item in parser.results:
            info = models.ProviderSearchInfo(
                provider=self.provider_name,
                mediaId=item["id"],
                title=item["title"],
                type="tv_series",
                season=get_season_from_title(item["title"]),
                year=None,
                imageUrl="",
                episodeCount=None,
                currentEpisodeIndex=episode_info.get("episode") if episode_info else None,
            )
            results.append(info)

        self.logger.info(f"ezdmw: 搜索 '{keyword}' 完成，找到 {len(results)} 个结果")
        return results

    async def get_info_from_url(self, url: str) -> Optional[models.ProviderSearchInfo]:
        """从URL获取番剧信息"""
        media_id = await self.get_id_from_url(url)
        if not media_id:
            return None
        return models.ProviderSearchInfo(
            provider=self.provider_name,
            mediaId=str(media_id),
            title=f"E站弹幕网 {media_id}",
            type="tv_series",
            season=1,
            year=None,
            imageUrl="",
            episodeCount=None,
            currentEpisodeIndex=None,
        )

    async def get_id_from_url(self, url: str) -> Optional[str]:
        """从URL解析番剧ID"""
        try:
            parsed = urlparse(url)
            if "ezdmw" not in parsed.netloc:
                return None
            m = re.search(r"/Index/(?:bangumi|video)/(\d+)\.html", parsed.path)
            if m:
                return m.group(1)
            return None
        except Exception:
            return None

    async def get_episodes(self, media_id: str, target_episode_index: Optional[int] = None,
                           db_media_type: Optional[str] = None) -> List[models.ProviderEpisodeInfo]:
        """获取分集列表"""
        cache_key = f"episodes_raw_{self.provider_name}_{media_id}"
        raw_episodes = []

        if target_episode_index is None:
            cached = await self._get_from_cache(cache_key)
            if cached:
                self.logger.info(f"ezdmw: 分集缓存命中 media_id={media_id}")
                raw_episodes = [models.ProviderEpisodeInfo.model_validate(e) for e in cached]

        if not raw_episodes:
            self.logger.info(f"ezdmw: 获取分集列表 media_id={media_id}...")
            try:
                html = await self._fetch_html(f"{BASE_URL}/Index/bangumi/{media_id}.html")
            except Exception as e:
                self.logger.error(f"ezdmw: 获取番剧页面失败: {e}")
                return []

            # 解析分集链接 - 从 HTML 中提取 video ID 列表
            # 查找 circuit_switch1 区域中的链接 (主线线路)
            episodes = []
            # 使用正则提取主线分集链接
            # 匹配 class="circuit_switch1" 区域内的链接
            switch1_match = re.search(
                r'class="circuit_switch1"[^>]*>(.*?)</div>',
                html, re.DOTALL
            )
            if switch1_match:
                ep_links = re.findall(
                    r'<a[^>]*href="[^"]*?/Index/video/(\d+)\.html"[^>]*class="(\d+)"[^>]*>',
                    switch1_match.group(1)
                )
                if not ep_links:
                    # 备选: 只提取 video ID
                    ep_links_alt = re.findall(
                        r'href="[^"]*?/Index/video/(\d+)\.html"',
                        switch1_match.group(1)
                    )
                    for i, vid in enumerate(ep_links_alt):
                        ep_index = i + 1
                        ep = models.ProviderEpisodeInfo(
                            provider=self.provider_name,
                            episodeId=vid,
                            title=f"第{ep_index}集",
                            episodeIndex=ep_index,
                            url=f"{BASE_URL}/Index/video/{vid}.html",
                        )
                        episodes.append(ep)
                        if target_episode_index and ep_index >= target_episode_index:
                            break
                else:
                    for vid, css_class in ep_links:
                        try:
                            ep_index = int(css_class)
                        except ValueError:
                            ep_index = len(episodes) + 1
                        ep = models.ProviderEpisodeInfo(
                            provider=self.provider_name,
                            episodeId=vid,
                            title=f"第{ep_index}集",
                            episodeIndex=ep_index,
                            url=f"{BASE_URL}/Index/video/{vid}.html",
                        )
                        episodes.append(ep)
                        if target_episode_index and ep_index >= target_episode_index:
                            break

            if not episodes:
                # 最后的后备方案：当前页面本身就是一集
                self.logger.info(f"ezdmw: 未找到分集链接，使用当前 media_id 作为单集")
                episodes.append(models.ProviderEpisodeInfo(
                    provider=self.provider_name,
                    episodeId=media_id,
                    title="第1集",
                    episodeIndex=1,
                    url=f"{BASE_URL}/Index/video/{media_id}.html",
                ))

            # 按 episodeIndex 排序
            episodes.sort(key=lambda x: x.episodeIndex)
            raw_episodes = episodes

            if raw_episodes and target_episode_index is None:
                await self._set_to_cache(cache_key, [e.model_dump() for e in raw_episodes], "episodes_ttl_seconds", 1800)

        # 应用黑名单过滤
        return await self.filter_episodes(raw_episodes)

    async def get_comments(self, episode_id: str, progress_callback: Optional[Callable] = None) -> List[dict]:
        """获取弹幕数据

        episode_id: 播放页面的 video ID (如 "90971")
        """
        if progress_callback:
            await progress_callback(10, "正在获取播放页面签名...")

        # 1. 获取 sign 和 nk
        sign_info = await self._extract_sign_and_nk(episode_id)
        if not sign_info:
            self.logger.warning(f"ezdmw: 无法获取 sign episode_id={episode_id}")
            return []

        nk = sign_info["nk"]
        sign = sign_info["sign"]

        if progress_callback:
            await progress_callback(30, "正在下载弹幕数据...")

        # 2. 请求弹幕 XML
        danmaku_url = (
            f"{PLAYER_URL}/index/getData.html?"
            f"video_id={nk}&json=xml&danmu={nk}"
            f"&sign={sign}&timeAxis=false&getUser=游客"
        )

        try:
            client = await self._get_client()
            resp = await client.get(danmaku_url)
            xml_text = resp.text
        except Exception as e:
            self.logger.error(f"ezdmw: 弹幕请求失败: {e}")
            return []

        if not xml_text or not xml_text.strip():
            # sign 可能过期，清除缓存后重试
            self.logger.warning(f"ezdmw: 弹幕为空，尝试刷新 sign...")
            cache_key = f"ezdmw_sign_{episode_id}"
            # 手动清除缓存（通过设置一个空值或短TTL）
            await self._set_to_cache(cache_key, None, "ezdmw_sign_ttl", 1)
            sign_info = await self._extract_sign_and_nk(episode_id)
            if sign_info:
                nk = sign_info["nk"]
                sign = sign_info["sign"]
                danmaku_url = (
                    f"{PLAYER_URL}/index/getData.html?"
                    f"video_id={nk}&json=xml&danmu={nk}"
                    f"&sign={sign}&timeAxis=false&getUser=游客"
                )
                try:
                    resp = await client.get(danmaku_url)
                    xml_text = resp.text
                except Exception:
                    return []
            if not xml_text or not xml_text.strip():
                return []

        if progress_callback:
            await progress_callback(60, "正在解析弹幕...")

        # 3. 解析 XML 弹幕
        comments = self._parse_danmaku_xml(xml_text)

        if progress_callback:
            await progress_callback(100, f"完成，共 {len(comments)} 条弹幕")

        self.logger.info(f"ezdmw: 获取弹幕 episode_id={episode_id} 共 {len(comments)} 条")
        return comments

    def _parse_danmaku_xml(self, xml_text: str) -> List[dict]:
        """解析 ezdmw XML 弹幕格式

        XML 格式:
        <i>
          <d p="时间(秒),模式,字号,颜色(十进制),时间戳,0,用户id,时间戳">弹幕文本</d>
        </i>

        模式: 1=滚动, 4=底部, 5=顶部
        """
        comments = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            self.logger.warning("ezdmw: XML 解析失败")
            return []

        for d_elem in root.findall(".//d"):
            p_attr = d_elem.get("p", "")
            text = d_elem.text or ""
            text = text.strip()

            if not p_attr or not text:
                continue
            # 跳过系统弹幕
            if "admins" in p_attr:
                continue

            parts = p_attr.split(",")
            if len(parts) < 4:
                continue

            try:
                time_sec = float(parts[0])
                mode = int(parts[1])
                color = int(parts[3])
            except (ValueError, IndexError):
                continue

            # 转换模式: ezdmw(1=滚动,4=底部,5=顶部) -> dandanplay 兼容格式
            # dandanplay 模式: 1=滚动, 4=底部, 5=顶部 (一样的)
            mode_str = "scroll"
            if mode == 4:
                mode_str = "bottom"
            elif mode == 5:
                mode_str = "top"

            # 颜色转换: 十进制 -> 十六进制(6位)
            color_hex = f"#{color:06x}" if color != 16777215 else "#ffffff"

            comments.append({
                "time": round(time_sec, 2),
                "mode": mode_str,
                "color": color_hex,
                "text": text,
            })

        return comments

    async def close(self):
        """关闭 HTTP 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
