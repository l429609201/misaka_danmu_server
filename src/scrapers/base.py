"""搜索源基类与公共依赖门面；平台协议及纯工具不在此实现。"""

import asyncio
import base64
import hashlib
import hmac
import html
import ipaddress
import json
import logging
import math
import os
import random
import re
import secrets
import socket
import string
import struct
import time
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zlib
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from typing import Any, Callable, ClassVar, Dict, List, Mapping, Optional, Tuple, Union
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlparse, urlsplit

import aiohttp
import brotli
import chardet
import httpx
import wasmtime
from bs4 import BeautifulSoup
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from gmssl import func, sm3
from google.protobuf.descriptor_pb2 import FileDescriptorProto
from lxml import etree
from opencc import OpenCC
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from thefuzz import fuzz

from src.rate_limiter import RateLimitExceededError
from src.schemas.auth import User
from src.schemas.common import SCRAPER_API_VERSION
from src.schemas.ui.search import ProviderEpisodeInfo, ProviderSearchInfo
from src.security_core import require_download_permission
from src.services.service_container import get_database_service, get_rate_limiter
from src.services.cache_service import get_cache_service
from src.services.config_service import ConfigService
from src.utils.danmaku.p_fields import normalize_p_attr
from src.utils.diagnostics.performance_tracker import track_performance
from src.utils.parsing.danmaku_parser import parse_dandan_xml_to_comments
from src.utils.parsing.episode_filter import COMMON_EPISODE_BLACKLIST_REGEX
from src.utils.parsing.filename_parser import (
    get_season_from_title, is_movie_by_title, normalize_title, parse_search_keyword,
)
from src.utils.parsing.protobuf import build_protobuf_message_classes
from src.utils.runtime.server_instance_id import generate_server_instance_id
from src.utils.runtime.transport_manager import TransportManager

# 库和工具均由本模块公开原对象，来源仅从.base选择所需名字。
__all__ = [
    "BaseScraper", "ProviderEpisodeInfo", "ProviderSearchInfo", "User",
    "ConfigService", "TransportManager", "RateLimitExceededError",
    "require_download_permission", "get_season_from_title", "track_performance",
    "COMMON_EPISODE_BLACKLIST_REGEX", "parse_search_keyword", "normalize_title",
    "is_movie_by_title", "normalize_p_attr", "parse_dandan_xml_to_comments",
    "build_protobuf_message_classes", "generate_server_instance_id",
    "asyncio", "base64", "hashlib", "hmac", "html", "ipaddress", "json",
    "logging", "math", "os", "random", "re", "secrets", "socket", "string",
    "struct", "time", "urllib", "uuid", "ET", "defaultdict", "dataclass",
    "datetime", "timezone", "unescape", "HTMLParser", "Any", "Callable",
    "ClassVar", "Dict", "List", "Mapping", "Optional", "Tuple", "Union",
    "parse_qs", "quote", "unquote", "urlencode", "urljoin", "urlparse", "urlsplit",
    "aiohttp", "brotli", "chardet", "httpx", "wasmtime", "BeautifulSoup",
    "AES", "pad", "unpad", "func", "sm3", "FileDescriptorProto", "etree",
    "OpenCC", "BaseModel", "ConfigDict", "Field", "ValidationError",
    "field_validator", "model_validator", "fuzz", "zlib",
]


class BaseScraper(ABC):
    """
    所有搜索源的抽象基类。
    定义了搜索媒体、获取分集和获取弹幕的通用接口。

    注意：分集过滤规则现在完全从 config 表读取，不再使用硬编码的默认值。
    - 特定源分集黑名单：{provider_name}_episode_blacklist_regex
    如果 config 表中键不存在，启动时会通过 register_defaults 创建并填充默认值。
    如果键存在但值为空，则不进行过滤。
    """

    scraper_api_version = SCRAPER_API_VERSION

    def __init__(self, config_service: ConfigService, transport_manager: TransportManager):
        self.config_service = config_service
        self.transport_manager = transport_manager
        # 管理器组装时注入既有离线服务，来源不导入业务服务包。
        self._bangumi_data: Optional[Any] = None
        self.logger = logging.getLogger(self.__class__.__name__)
        self._search_timeout: float = 30.0
        self._current_proxy_config: Optional[str] = None
        self._scraper_manager_ref: Optional[Any] = None
        self._api_lock = asyncio.Lock()
        self._last_request_time: float = 0.0
        self._min_interval: float = 0.0
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_proxy_for_provider(self) -> Optional[str]:
        """
        获取当前 provider 的代理配置。
        优先使用预加载的缓存,避免重复数据库查询。

        支持三种代理模式：
        - none: 不使用代理
        - http_socks: HTTP/SOCKS 代理
        - accelerate: 加速代理（URL 重写模式，不返回代理 URL）
        """
        # 获取代理模式
        proxy_mode = await self.config_service.get("proxyMode", "none")

        # 兼容旧配置：如果 proxyMode 为 none 但 proxyEnabled 为 true，则使用 http_socks 模式
        if proxy_mode == "none":
            proxy_enabled_globally = (await self.config_service.get("proxyEnabled", "false")).lower() == 'true'
            if proxy_enabled_globally:
                proxy_mode = "http_socks"

        # 如果代理模式为 none 或 accelerate，则不返回 HTTP 代理 URL
        # accelerate 模式通过 URL 重写实现，不需要设置 httpx 的 proxy 参数
        if proxy_mode != "http_socks":
            return None

        proxy_url = await self.config_service.get("proxyUrl", "")
        if not proxy_url:
            return None

        # 获取当前 provider 的代理设置
        provider_setting = None
        if self._scraper_manager_ref and hasattr(self._scraper_manager_ref, '_cached_scraper_settings'):
            # 使用预加载的缓存（快速路径）
            provider_setting = self._scraper_manager_ref._cached_scraper_settings.get(self.provider_name)
        else:
            db = get_database_service()
            async with db.transaction():
                scraper_settings = await db.scraper.get_all_scraper_settings()
            provider_setting = next((s for s in scraper_settings if s['providerName'] == self.provider_name), None)

        use_proxy_for_this_provider = provider_setting.get('useProxy', False) if provider_setting else False

        return proxy_url if use_proxy_for_this_provider else None

    async def _should_use_accelerate_proxy(self) -> bool:
        """检查是否应该使用加速代理模式"""
        proxy_mode = await self.config_service.get("proxyMode", "none")
        return proxy_mode == "accelerate"

    async def _get_accelerate_proxy_url(self) -> str:
        """获取加速代理地址"""
        return await self.config_service.get("accelerateProxyUrl", "")

    def _transform_url_for_accelerate(self, original_url: str, proxy_base: str) -> str:
        """
        转换 URL 为加速代理格式

        原始: https://api.example.com/path
        转换: https://proxy.vercel.app/https/api.example.com/path
        """
        if not proxy_base:
            return original_url

        proxy_base = proxy_base.rstrip('/')
        protocol = "https" if original_url.startswith("https://") else "http"
        target = original_url.replace(f"{protocol}://", "")

        return f"{proxy_base}/{protocol}/{target}"

    async def _transform_url_if_needed(self, url: str) -> str:
        """
        根据代理模式转换 URL

        - none/http_socks: 返回原始 URL
        - accelerate: 返回加速代理格式的 URL（如果当前 provider 启用了代理）
        """
        if not await self._should_use_accelerate_proxy():
            return url

        # 检查当前 provider 是否启用了代理
        provider_setting = None
        if self._scraper_manager_ref and hasattr(self._scraper_manager_ref, '_cached_scraper_settings'):
            provider_setting = self._scraper_manager_ref._cached_scraper_settings.get(self.provider_name)
        else:
            db = get_database_service()
            async with db.transaction():
                scraper_settings = await db.scraper.get_all_scraper_settings()
            provider_setting = next((s for s in scraper_settings if s['providerName'] == self.provider_name), None)

        use_proxy_for_this_provider = provider_setting.get('useProxy', False) if provider_setting else False

        if not use_proxy_for_this_provider:
            return url

        proxy_base = await self._get_accelerate_proxy_url()
        if proxy_base:
            return self._transform_url_for_accelerate(url, proxy_base)

        return url

    def _format_search_result_log(self, result: Any) -> str:
        """统一搜索结果日志字段，省略海报链接以减少噪音，缺失值显示为 null。"""
        def display(value: Any) -> Any:
            return value if value is not None and value != "" else "null"

        return (
            f"  - {display(getattr(result, 'title', None))} "
            f"(ID: {display(getattr(result, 'mediaId', None))}, "
            f"类型: {display(getattr(result, 'type', None))}, "
            f"季: {display(getattr(result, 'season', None))}, "
            f"年份: {display(getattr(result, 'year', None))}, "
            f"集数: {display(getattr(result, 'episodeCount', None))})"
        )


    async def _log_proxy_usage(self, proxy_url: Optional[str]):
        if proxy_url:
            self.logger.debug(f"通过代理 '{proxy_url}' 发起请求...")

    async def _create_client(self, **kwargs) -> httpx.AsyncClient: # type: ignore
        """
        创建 httpx.AsyncClient，并根据配置应用代理。
        超时统一由 _search_timeout 控制（由 scraper_manager 从 config 注入），
        忽略子类传入的 timeout 参数。
        """
        proxy_to_use = await self._get_proxy_for_provider()
        await self._log_proxy_usage(proxy_to_use)
        self._current_proxy_config = proxy_to_use

        # 忽略子类传的 timeout，统一用配置的 _search_timeout
        kwargs.pop("timeout", None)

        client_kwargs = {"proxy": proxy_to_use, "timeout": self._search_timeout, "follow_redirects": True, **kwargs}
        return httpx.AsyncClient(**client_kwargs)

    def _client_defaults(self) -> Dict[str, Any]:
        """
        提供本源发起请求时的默认 client 参数（headers / cookies 等）。

        子类覆盖此方法即可让统一出口带上自己的请求头，
        无需各自重写 _request_with_rate_limit。
        """
        return {}

    async def _acquire_client(self, **kwargs) -> httpx.AsyncClient:
        """
        基于 TransportManager 的共享连接池创建轻量 client。

        与 _create_client 的区别：
        - _create_client 走 proxy= 参数，httpx 会为每个 client 自建 transport（连接池不复用）
        - 本方法走 transport= 参数，连接池由 TransportManager 全局持有并复用

        超时同样统一由 _search_timeout 控制，忽略调用方传入的 timeout。
        """
        proxy_to_use = await self._get_proxy_for_provider()
        await self._log_proxy_usage(proxy_to_use)
        self._current_proxy_config = proxy_to_use

        transport = (
            await self.transport_manager.get_proxy_transport(proxy_to_use)
            if proxy_to_use
            else await self.transport_manager.get_shared_transport()
        )

        kwargs.pop("timeout", None)
        return httpx.AsyncClient(
            transport=transport,
            timeout=self._search_timeout,
            follow_redirects=True,
            **kwargs,
        )

    async def _ensure_client(self) -> httpx.AsyncClient:
        """
        获取本源的长驻 client，不存在时创建。

        子类如需在建立会话时做额外初始化（加载 Cookie、换取鉴权票据等），
        覆盖本方法即可，无需重写 _request_with_rate_limit。
        """
        if self._client is None:
            self._client = await self._acquire_client(**self._client_defaults())
        return self._client

    async def _request_with_rate_limit(
        self, method: str, url: str, **kwargs
    ) -> httpx.Response:
        """
        所有搜索源发起 HTTP 请求的统一出口。
        """
        client = await self._ensure_client()

        async with self._api_lock:
            if self._min_interval > 0:
                elapsed = time.time() - self._last_request_time
                if elapsed < self._min_interval:
                    await asyncio.sleep(self._min_interval - elapsed)

            response = await client.request(method, url, **kwargs)
            self._last_request_time = time.time()

        await self._log_raw_response(response, f"{method} Request", url=url)
        return response

    async def close(self):
        """
        关闭本源持有的长驻 client。

        仅关闭 client 外壳，底层 transport（连接池）由 TransportManager 统一管理，
        在应用关闭时通过 close_all() 释放。
        """
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as e:
                self.logger.warning(f"关闭 {self.provider_name} 的 client 时出错: {e}")
            self._client = None

    async def _get_from_cache(self, key: str) -> Optional[Any]:
        """
        从缓存中获取数据。
        优先使用预取的缓存（批量查询优化），否则单独查询数据库。
        """
        # 【优化】优先使用预取的缓存
        if hasattr(self, '_prefetched_cache'):
            if key in self._prefetched_cache:
                cached_value = self._prefetched_cache[key]
                if cached_value is not None:
                    self.logger.debug(f"{self.provider_name}: 使用预取缓存 (命中) - {key}")
                    return cached_value
                else:
                    # 批量查询已执行，但缓存不存在
                    self.logger.debug(f"{self.provider_name}: 使用预取缓存 (未命中) - {key}")
                    return None

        self.logger.debug(f"{self.provider_name}: 缓存未预取，进行单独查询 - {key}")
        try:
            return await get_cache_service().get(key=key, region="default")
        except Exception:
            self.logger.debug(f"{self.provider_name}: 缓存读取失败 - {key}", exc_info=True)
            return None

    async def _set_to_cache(self, key: str, value: Any, config_key: str, default_ttl: int):
        """将数据存入缓存，TTL从配置中读取。"""
        ttl_str = await self.config_service.get(config_key, str(default_ttl))
        ttl = int(ttl_str)
        if ttl > 0:
            try:
                await get_cache_service().set(key=key, value=value, ttl=ttl, region="default")
            except Exception:
                self.logger.debug(f"{self.provider_name}: 缓存写入失败 - {key}", exc_info=True)

    # 每个子类都必须覆盖这个类属性
    provider_name: str

    # (可选) 子类可覆盖此属性，设置在UI上显示的友好名称。
    # 如果未设置，前端将 fallback 到 provider_name。
    display_name: Optional[str] = None

    # (可选) 子类可以覆盖此字典来声明其可配置的字段。
    #
    # 支持两种格式:
    # 1. 简单格式（元组）: { "config_key": ("标签", "类型", "提示") }
    # 2. 扩展格式（字典）: { "config_key": { "label": "标签", "type": "类型", ... } }
    #
    # 支持的字段类型:
    #   - "string": 文本输入框
    #   - "password": 密码输入框
    #   - "boolean": 开关
    #   - "textarea": 多行文本框
    #   - "radio_group": 单选组（需提供 options）
    #   - "qrcode_login": 二维码登录组件（bilibili 专用）
    #   - "conditional": 条件显示字段（根据其他字段值动态显示）
    #
    # 扩展格式支持的属性:
    #   - label (str): UI 显示的标签
    #   - type (str): 字段类型（见上方列表）
    #   - tooltip (str): 提示文本
    #   - placeholder (str): 占位符文本
    #   - required (bool): 是否必填
    #   - default (any): 默认值
    #   - options (List[Dict]): radio_group 类型的选项列表 [{"label": "显示名", "value": "值"}, ...]
    #   - conditional (Dict): 条件显示配置 {"field": "关联字段key", "value": "触发值"}
    #   - rows (int): textarea 类型的行数
    #   - prefix_icon (str): 输入框前缀图标名（如 "KeyOutlined"）
    #   - link (str): 字段标签旁的帮助链接
    configurable_fields: Dict[str, Union[Tuple[str, str, str], Dict[str, Any]]] = {}

    # (可选) 子类可覆盖此列表来自定义 UI 字段的渲染顺序
    # 通用字段标识符（所有源都有）:
    #   - "proxyLogRow": 第一行（启用代理 + 记录原始响应，同行显示）
    #   - "searchTimeout": 第二行（搜索超时滑块）
    #   - "episodeBlacklist": 第三行（分集黑名单正则文本框）
    #   - "enrichEnabled": 信息增强开关+字段输入框（同一行）
    # 特殊标记:
    #   - "@custom": 插入源特有字段（来自 configurable_fields）的位置
    # 默认顺序（不设置时使用）:
    ui_field_order: Optional[List[str]] = None  # None 表示使用默认顺序
    _default_ui_field_order = [
        "proxyLogRow",       # 第一行：代理开关 + 日志开关（同行）
        "searchTimeout",     # 第二行：搜索超时
        "episodeBlacklist",  # 第三行：分集黑名单
        "@custom",           # 第四行起：源特有字段插入在这里
        "enrichEnabled",     # 信息增强（在源特有字段之后）
    ]

    # (新增) 子类应覆盖此列表，声明它们可以处理的域名
    handled_domains: List[str] = []

    # (新增) 信息增强补全字段（源在代码里硬编码，如 ["year", "episodeCount"]）
    # 空列表表示该源不支持信息增强，搜索时不会触发补全逻辑
    enrich_fields: List[str] = []

    # (新增) 子类可以覆盖此属性，以提供一个默认的 Referer
    referer: Optional[str] = None

    # (新增) 子类可以覆盖此属性，以表明其是否支持日志记录
    is_loggable: bool = True

    rate_limit_quota: Optional[int] = None # 新增：特定源的配额

    # 点赞火焰阈值：l >= 此值显示 ??，否则显示 ??（各源可在内部覆盖）
    likes_fire_threshold: int = 1000

    def build_media_url(self, media_id: str) -> Optional[str]:
        """
        构造平台播放页面URL。
        子类可以覆盖此方法以提供特定平台的URL构造逻辑。

        Args:
            media_id: 媒体ID

        Returns:
            平台播放页面URL，如果无法构造则返回None
        """
        return None

    async def _should_log_responses(self) -> bool:
        """动态检查是否应记录原始响应，确保配置实时生效。"""
        if not self.is_loggable:
            return False

        # 修正：使用特定于提供商的配置键，例如 'scraper_tencent_log_responses'
        config_key = f"scraper_{self.provider_name}_log_responses"
        is_enabled_str = await self.config_service.get(config_key, "false")
        # 健壮性检查：同时处理布尔值和字符串 "true"，以防配置值类型不确定。
        if isinstance(is_enabled_str, bool):
            return is_enabled_str
        return str(is_enabled_str).lower() == 'true'

    async def _log_raw_response(self, response_or_text, operation: str, **context):
        """
        统一的原始响应日志记录方法

        Args:
            response_or_text: httpx.Response 对象或原始响应文本
            operation: 操作描述，如 "Search"、"Episodes"、"Danmaku"
            **context: 额外的上下文信息，如 keyword、media_id、episode_id 等
        """
        if not await self._should_log_responses():
            return

        # 提取响应文本
        if isinstance(response_or_text, str):
            response_text = response_or_text
        elif isinstance(response_or_text, bytes):
            response_text = response_or_text.decode('utf-8', errors='ignore')
        else:
            # httpx.Response 对象
            response_text = response_or_text.text

        # 构建上下文信息字符串
        context_parts = [f"{k}={v}" for k, v in context.items() if v is not None]
        context_str = ", ".join(context_parts) if context_parts else ""

        # 记录完整日志，不截断
        logger = logging.getLogger("scraper_responses")
        logger.debug(
            f"{self.provider_name.upper()} {operation} Response "
            f"({context_str}): {response_text}"
        )

    async def get_episode_blacklist_pattern(self) -> Optional[re.Pattern]:
        """
        获取用于过滤分集标题的正则表达式对象。
        只使用特定于提供商的黑名单，不再有全局黑名单。

        注意：此方法不使用硬编码的默认值作为兜底。
        - 如果 config 表中键不存在，启动时会通过 register_defaults 创建并填充默认值
        - 如果键存在但值为空，则不进行过滤
        """
        # 获取特定于提供商的黑名单
        provider_key = f"{self.provider_name}_episode_blacklist_regex"
        # 不提供默认值，如果数据库中没有则返回空字符串
        provider_pattern_str = await self.config_service.get(provider_key, "")

        # 打印实际读取到的过滤规则，便于排查
        self.logger.info(f"读取到分集黑名单（正则）：{provider_pattern_str if provider_pattern_str else '(空)'}")

        if not provider_pattern_str or not provider_pattern_str.strip():
            return None

        try:
            return re.compile(provider_pattern_str, re.IGNORECASE)
        except re.error as e:
            self.logger.error(f"编译分集黑名单正则表达式失败: '{provider_pattern_str}'. 错误: {e}")
        return None

    async def execute_action(self, action_name: str, payload: Dict[str, Any]) -> Any:
        """
        执行一个指定的操作。
        子类应重写此方法来处理其声明的操作。
        :param action_name: 要执行的操作的名称。
        :param payload: 包含操作所需参数的字典。
        """
        raise NotImplementedError(f"操作 '{action_name}' 在 {self.provider_name} 中未实现。")

    @abstractmethod
    async def search(self, keyword: str, episode_info: Optional[Dict[str, Any]] = None) -> List[ProviderSearchInfo]:
        """
        根据关键词搜索媒体。
        episode_info: 可选字典，包含 'season' 和 'episode'。
        """
        raise NotImplementedError

    def select_search_keywords(self, keywords: List[str]) -> List[str]:
        """从候选关键词列表中挑选本源实际要搜索的关键词。

        keywords 约定 keywords[0] 为主搜索词，其后为别名增强追加的多语言译名。
        默认策略：只用主搜索词（避免用全量别名对每个源逐个网络搜索，既慢又易 0 结果）。
        需要按语言挑别名的源（如 gamer 繁中站）覆写本方法叠加自身偏好。
        """
        return [keywords[0]] if keywords else []

    @abstractmethod
    async def get_info_from_url(self, url: str) -> Optional[ProviderSearchInfo]:
        """
        (新增) 从一个作品的URL中提取信息，并返回一个 ProviderSearchInfo 对象。
        这用于支持从URL直接导入整个作品。
        """
        raise NotImplementedError

    @abstractmethod
    async def get_id_from_url(self, url: str) -> Optional[Union[str, Dict[str, str]]]:
        """
        (新增) 统一的从URL解析ID的接口。
        子类应重写此方法以支持从URL直接导入。
        """
        raise NotImplementedError

    @abstractmethod
    async def get_episodes(self, media_id: str, target_episode_index: Optional[int] = None, db_media_type: Optional[str] = None) -> List[ProviderEpisodeInfo]:
        """
        获取给定媒体ID的所有分集。
        如果提供了 target_episode_index，则可以优化为只获取到该分集为止。
        db_media_type: 从数据库中读取的媒体类型 ('movie', 'tv_series')，可用于指导刮削策略。
        """
        raise NotImplementedError

    async def enrich_result(
        self,
        result: ProviderSearchInfo,
        fields: List[str]
    ) -> ProviderSearchInfo:
        """
        补全单条搜索结果的缺失字段（通用信息增强接口）。

        :param result: 搜索结果对象
        :param fields: 需要补全的字段列表（如 ["year", "episodeCount"]）
        :return: 补全后的搜索结果（原地修改）

        子类可覆写此方法以提供更高效的批量补全逻辑。
        默认实现：逐字段调用对应方法。
        """
        for field in fields:
            try:
                if field == "episodeCount" and result.episodeCount is None:
                    # 调用现有 get_episodes 补全集数
                    episodes = await self.get_episodes(result.mediaId)
                    if episodes:
                        result.episodeCount = len(episodes)
                        self.logger.debug(f"[{self.provider_name}] 补全 episodeCount: {result.title} -> {result.episodeCount}")

                elif field == "year" and not result.year:
                    # 调用新方法 get_year 补全年份
                    year = await self.get_year(result.mediaId)
                    if year:
                        result.year = year
                        self.logger.debug(f"[{self.provider_name}] 补全 year: {result.title} -> {result.year}")

                # 未来扩展：season, type, aliases 等

            except Exception as e:
                self.logger.warning(f"[{self.provider_name}] 补全字段 {field} 失败: {e}")
                continue

        return result

    async def get_year(self, media_id: str) -> Optional[int]:
        """
        获取媒体的年份。

        默认实现：返回 None（子类按需覆写）。
        部分源可从详情页提取，部分源可从搜索结果的 URL 或 ID 推断。

        :param media_id: 媒体ID
        :return: 年份（如 2024），无法获取时返回 None
        """
        return None

    async def get_comments(
        self, episode_id: str, progress_callback: Optional[Callable] = None,
        *, pool: str = "global",
    ) -> Optional[List[dict]]:
        """统一业务下载入口，委托流控核心占额后执行源内下载。"""
        # 基类只负责转发，避免与流控核心重复占额或递归调用。
        return await get_rate_limiter().download_comments(
            self.provider_name, episode_id, progress_callback=progress_callback,
            pool=pool,
        )

    @abstractmethod
    async def fetch_comments(self, episode_id: str, progress_callback: Optional[Callable] = None) -> Optional[List[dict]]:
        """源内下载实现；须先校验下载许可，业务统一调用 get_comments。"""
        raise NotImplementedError

    def format_episode_id_for_comments(self, provider_episode_id: Any) -> str:
        """
        (新增) 将 get_comments 所需的 episode_id 格式化为字符串。
        大多数源直接返回字符串，但Bilibili和MGTV需要特殊处理。
        """
        return str(provider_episode_id)

    async def _filter_junk_episodes(
        self,
        episodes: List["ProviderEpisodeInfo"],
        return_filtered: bool = False,
    ):
        """
        过滤掉垃圾分集（预告、花絮等）

        注意：此方法现在从 config 表读取过滤规则，不再使用硬编码的正则表达式。
        如果 config 表中没有配置过滤规则，则不进行过滤。

        Args:
            episodes: 待过滤的分集列表
            return_filtered: 是否同时返回被过滤的分集信息
                - False（默认）: 返回 List[ProviderEpisodeInfo]（向后兼容）
                - True: 返回 (保留的分集列表, 被过滤的分集列表[(episode, 匹配规则)])
        """
        if not episodes:
            return (episodes, []) if return_filtered else episodes

        # 从 config 表获取过滤规则，不使用硬编码兜底
        blacklist_pattern = await self.get_episode_blacklist_pattern()

        # 如果没有配置过滤规则，直接返回所有分集
        if not blacklist_pattern:
            return (episodes, []) if return_filtered else episodes

        filtered_episodes = []
        filtered_out_episodes = []

        for episode in episodes:
            # 使用从 config 表获取的正则表达式进行过滤
            match = blacklist_pattern.search(episode.title)
            if match:
                # 优先取第2个捕获组（通常是关键词如"幕后""预告"），否则取整个匹配
                junk_type = match.group(2) if match.lastindex and match.lastindex >= 2 else match.group(0)
                filtered_out_episodes.append((episode, junk_type))
            else:
                filtered_episodes.append(episode)

        if return_filtered:
            return filtered_episodes, filtered_out_episodes
        return filtered_episodes

    def _log_episodes_result(
        self,
        kept_episodes: List["ProviderEpisodeInfo"],
        filtered_out: List[Tuple["ProviderEpisodeInfo", str]],
        elapsed_ms: int,
        target_episode_index: Optional[int] = None,
    ) -> None:
        """
        统一的分集获取结果日志，同时显示保留和被过滤的分集。

        Args:
            kept_episodes: 保留的分集列表
            filtered_out: 被过滤的分集列表 [(episode, 匹配规则)]
            elapsed_ms: 耗时（毫秒）
            target_episode_index: 如果指定了目标集数，只显示该集
        """
        episodes_to_log = (
            [ep for ep in kept_episodes if ep.episodeIndex == target_episode_index]
            if target_episode_index is not None
            else kept_episodes
        )

        log_lines = ["-", f"┌─── {self.provider_name} ({len(episodes_to_log)}个结果, {elapsed_ms}ms) ───"]
        for ep in episodes_to_log:
            log_lines.append(f"  - {ep.title}")

        if filtered_out:
            log_lines.append(f"  已过滤 {len(filtered_out)} 集:")
            for ep, rule in filtered_out:
                log_lines.append(f"    ? {ep.title} （黑名单正则匹配：{rule}）")

        # 保留本次过滤明细，供编辑导入接口展示“不导入”列表；普通导入仍只使用 kept_episodes。
        self._last_logged_filtered_out = list(filtered_out)

        log_lines.append(f"└─── {self.provider_name} ───")
        self.logger.info("\n".join(log_lines))

    # ============ 可选订阅能力（订阅助手） ============
    # 说明：以下属性/方法为「订阅助手」功能的可选扩展能力。
    # 默认 supports_subscription=False，现有所有弹幕源无需改动即可保持原行为；
    # 只有声明支持订阅的源（如 Bilibili）才覆盖这些属性/方法。
    # 设计依据：docs/subscription_page_implementation_plan.md 第 5.4/5.5 节。
    #
    # 设计原则（与 search/get_episodes/get_comments 同款）：基类只定义统一的抽象方法签名，
    # 源内部按 subscription_type 自行区分（如 Bilibili 的 UP主/系列/番剧）。

    # 是否支持订阅助手；默认 False，避免影响现有源。
    supports_subscription: bool = False

    # 声明该源支持的订阅类型，由 /available-sources 读取。
    # 每项形如：{"type": "bilibili_up", "label": "UP 主", "description": "...", "payloadSchema": {...}}
    subscription_types: List[Dict[str, Any]] = []

    async def check_subscription_capability(self, user=None) -> Dict[str, Any]:
        """返回该源的订阅能力状态。

        子类应覆盖此方法，返回是否可用、是否需要认证、认证状态、原因及支持的订阅类型。
        默认实现表示「未实现订阅能力」，订阅助手不会展示该源。

        :param user: 可选用户对象（OAuth 类源用它判断授权状态）。
        """
        return {
            "available": False,
            "authRequired": False,
            "authStatus": "none",
            "reason": "该源未实现订阅能力",
            "subscriptionTypes": [],
        }

    async def discover_subscription_targets(self, query: str, subscription_type: str = "", user=None) -> List[Dict[str, Any]]:
        """根据 query（关键词或 URL）发现可订阅目标候选，供前端列表挑选。

        子类按 subscription_type 或 query 形态（关键词/URL）区分搜索逻辑。
        返回统一结构列表，每项形如：
        {type, title, cover, description, payload}
        其中 payload 选中后直接喂给 validate_subscription_payload 创建订阅。

        :param user: 可选用户对象（OAuth 类源用它取 token/api-key）。
        """
        raise NotImplementedError(f"{self.provider_name} 未实现 discover_subscription_targets")

    async def fetch_subscription_calendar(self, category: str = "") -> List[Dict[str, Any]]:
        """拉取该订阅源的「探索榜单」数据（如 Bilibili PGC 番剧/国创热门列表）。

        与 discover 区别：discover 是「按用户输入搜」，本方法是「拉平台榜单」。
        返回可写入 external_calendar_item 的标准条目列表（airWeekday 可为空 = 无播出日，
        进探索发现海报网格而非日历）。子类在支持探索时覆盖。

        :param category: 可选分类（如 'bangumi'/'guochuang'），空表示默认全部支持的分类。
        """
        raise NotImplementedError(f"{self.provider_name} 未实现 fetch_subscription_calendar")

    async def validate_subscription_payload(self, subscription_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """校验并标准化订阅参数。

        返回可写入 external_calendar_item 的标准结构：
        {provider, externalId, title, animeType, subscriptionType, extraData}。
        子类在支持订阅时覆盖此方法，并按 subscription_type 区分不同类型。
        """
        raise NotImplementedError(f"{self.provider_name} 未实现 validate_subscription_payload")

    async def scan_subscription_target(self, target: Dict[str, Any]) -> List[Dict[str, Any]]:
        """扫描一个订阅目标，返回待写入 external_calendar_item 的候选项列表。

        只负责发现候选项，不直接写库；写库由任务层统一调用 CRUD。
        子类在支持订阅时覆盖此方法，并按 subscriptionType 区分不同类型。
        """
        raise NotImplementedError(f"{self.provider_name} 未实现 scan_subscription_target")

    async def fetch_subscription_item_comments(self, item: Dict[str, Any]) -> List[dict]:
        """对某个订阅候选项获取弹幕；默认委托给 get_comments。

        item 至少包含定位弹幕所需的 episodeId/cid 等字段（存于 extraData）。
        """
        raise NotImplementedError(f"{self.provider_name} 未实现 fetch_subscription_item_comments")

    async def resolve_url_structured(self, url: str, user: Optional[Any] = None) -> Optional[Dict[str, Any]]:
        """结构化解析一个 URL，返回「当前视频/所属合集/合集内全部视频」三段数据。

        与 discover 区别：discover 是按关键词搜索榜单，本方法是针对具体 URL 拆出
        可订阅的层级结构，供前端独立的 URL 解析弹框展示与多选批量订阅。

        返回结构（子类覆盖时遵循）：
        {
            "currentVideo": {...},          # 当前 URL 指向的视频（含展示参数）
            "collection": {...} | None,     # 所属合集/系列（如有）
            "collectionVideos": [{...}],    # 合集内全部视频，供多选
        }
        默认返回 None 表示该源不支持结构化解析，调用方应降级到 discover。

        :param url: 待解析的完整 URL。
        :param user: 可选用户对象（OAuth 类源用它取 token/api-key）。
        """
        return None


    @abstractmethod
    async def close(self):
        """关闭所有打开的资源，例如HTTP客户端。"""
        pass
