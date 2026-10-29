import logging
import re
import asyncio
import json as _json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

import httpx
from pydantic import BaseModel, ValidationError, model_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.schemas import MetadataDetailsResponse, ProviderSearchInfo, User
from src.core import get_app_timezone
from src.utils import parse_search_keyword
from src.utils import clean_movie_title as _clean_movie_title
from src.services.config_service import ConfigService
from src.services.cache_service import get_cache_service
from src.services.oauth_service import get_oauth_service
from src.services.service_container import get_database_service, get_metadata_service
from .base import BaseMetadataSource

logger = logging.getLogger(__name__)
metadata_logger = logging.getLogger("scraper_responses")


class InfoboxItem(BaseModel):
    key: str
    value: Any

class BangumiSearchSubject(BaseModel):
    id: int
    name: str
    name_cn: str
    images: Optional[Dict[str, str]] = None
    date: Optional[str] = None
    infobox: Optional[List[InfoboxItem]] = None

    @model_validator(mode='after')
    def clean_titles(self) -> 'BangumiSearchSubject':
        self.name = _clean_movie_title(self.name)
        self.name_cn = _clean_movie_title(self.name_cn)
        return self

    @property
    def display_name(self) -> str:
        return self.name_cn or self.name

    @property
    def image_url(self) -> Optional[str]:
        if self.images:
            for size in ["large", "common", "medium", "small", "grid"]:
                if url := self.images.get(size):
                    return url
        return None

    @property
    def aliases(self) -> Dict[str, Any]:
        data = {"name_en": None, "name_romaji": None, "aliases_cn": []}
        if not self.infobox: return data

        def extract_value(value: Any) -> List[str]:
            if isinstance(value, str): return [v.strip() for v in value.split('/') if v.strip()]
            elif isinstance(value, list): return [v.get("v", "").strip() for v in value if isinstance(v, dict) and v.get("v")]
            return []

        all_raw_aliases = []
        for item in self.infobox:
            key, value = item.key.strip(), item.value
            if key == "英文名" and isinstance(value, str): data["name_en"] = _clean_movie_title(value.strip())
            elif key == "罗马字" and isinstance(value, str): data["name_romaji"] = _clean_movie_title(value.strip())
            elif key == "别名": all_raw_aliases.extend(extract_value(value))

        chinese_char_pattern = re.compile(r'[\u4e00-\u9fa5]')
        cleaned_aliases = [_clean_movie_title(alias) for alias in all_raw_aliases]
        data["aliases_cn"] = [alias for alias in cleaned_aliases if alias and chinese_char_pattern.search(alias)]
        data["aliases_cn"] = list(dict.fromkeys(data["aliases_cn"]))
        return data

    @property
    def details_string(self) -> str:
        parts = []
        if self.date:
            try: parts.append(date.fromisoformat(self.date).strftime('%Y年%m月%d日'))
            except (ValueError, TypeError): parts.append(self.date)

        if self.infobox:
            staff_keys = ["导演", "原作", "脚本", "人物设定", "系列构成", "总作画监督"]
            staff_found = {}
            for item in self.infobox:
                if item.key in staff_keys:
                    value_str = ""
                    if isinstance(item.value, str): value_str = item.value.strip()
                    elif isinstance(item.value, list): value_str = "、".join([v.get("v", "").strip() for v in item.value if isinstance(v, dict) and v.get("v")])
                    if value_str: staff_found[item.key] = value_str
            for key in staff_keys:
                if key in staff_found and len(parts) < 5: parts.append(staff_found[key])
        return " / ".join(parts)

class BangumiSearchResponse(BaseModel):
    data: Optional[List[BangumiSearchSubject]] = None


class BangumiMetadataSource(BaseMetadataSource):
    provider_name = "bangumi"
    config_keys = ["bangumiClientId", "bangumiClientSecret", "bangumiToken", "authMode", "bangumiApiBaseUrl", "bangumiImageBaseUrl", "bangumiDataSyncEnabled", "bangumiDataOfflineEnabled", "bangumiDataSyncCron", "bangumiDataUrl"]
    # 需要做布尔转换的配置 key（getProviderConfig 时返回 true/false 布尔值）
    bool_config_keys = ["bangumiDataSyncEnabled", "bangumiDataOfflineEnabled"]

    # ============ 订阅助手能力 ============
    # 注意：这里的「订阅」是【弹幕库内订阅】(自动建库追更)，不是 Bangumi 站内的「在看」。
    # 流程：discover 搜番 → 用户选 → 写 external_calendar_item(bangumiId)
    #       → IncrementalRefreshJob 自动调 auto_search_and_import_task 在弹幕库内整剧建库 + 持续追更。
    supports_subscription = True
    handled_domains = ["bgm.tv", "bangumi.tv"]
    subscription_types = [
        {
            "type": "bangumi_subject",
            "label": "番剧（弹幕库追更）",
            "description": "通过 Bangumi 搜番，订阅后会自动在本地弹幕库建库 + 追更（不会同步到 Bangumi 账号）",
            "payloadSchema": {"fields": [
                {"name": "bangumiId", "type": "string", "required": True, "label": "Bangumi 条目 ID", "placeholder": "例如 12345"},
            ]},
        },
    ]

    DEFAULT_API_BASE_URL = "https://api.bgm.tv"
    DEFAULT_IMAGE_BASE_URL = "https://lain.bgm.tv"

    # ============ 搜索补充源能力（bangumi-data 离线 id 直链）============
    # 复用 360 同款补充源模板：当某弹幕源搜索无结果时，用 bangumi-data 离线库里
    # 人工维护的精确平台 id 直链解析出 (provider, mediaId) 作为兜底，精准命中不靠相似度猜。
    # 与 360 的差异：离线管理器 resolve_sources_by_title 内部已用 SITE_TO_PROVIDER 转好 provider，
    # 这里的 PLATFORM_TO_PROVIDER 仅供基类 supplement_search 过滤 empty_providers 判断可补充范围。
    is_search_supplement_source = True
    PLATFORM_TO_PROVIDER: Dict[str, str] = {
        "qq": "tencent",
        "youku": "youku",
        "mgtv": "mgtv",
        "bilibili": "bilibili",
        "iqiyi": "iqiyi",
    }
    configurable_fields = {
        "searchSupplementEnabled": ("启用搜索补充", "boolean", "启用后，当弹幕源搜索无结果时，将通过 bangumi-data 离线库的平台直链为其补充搜索结果"),
    }

    # C4：CacheManager 已删除，构造函数移除 cache_manager 参数
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], config_service: ConfigService, scraper_manager: Any):
        # 注意：父类 BaseMetadataSource 的 __init__ 可能也需要相应调整
        super().__init__(session_factory, config_service, scraper_manager)
        self._token: Optional[str] = None
        self._config_loaded = False

    @property
    async def test_url(self) -> str:
        """动态地从配置中获取测试URL。"""
        api_base = await self.config_service.get("bangumiApiBaseUrl", self.DEFAULT_API_BASE_URL)
        return api_base.rstrip('/')

    async def _get_api_base_url(self) -> str:
        """获取 Bangumi API 基础 URL。"""
        url = await self.config_service.get("bangumiApiBaseUrl", self.DEFAULT_API_BASE_URL)
        return url.rstrip('/')

    async def _rewrite_image_url(self, url: Optional[str]) -> Optional[str]:
        """将图片 URL 中的默认域名替换为用户配置的域名。"""
        if not url:
            return None
        custom_base = await self.config_service.get("bangumiImageBaseUrl", "")
        if not custom_base or custom_base.rstrip('/') == self.DEFAULT_IMAGE_BASE_URL:
            return url
        # 替换域名部分：https://lain.bgm.tv/... → https://custom.domain/...
        return url.replace(self.DEFAULT_IMAGE_BASE_URL, custom_base.rstrip('/'), 1)

    async def _match_supplement_items(
        self,
        keyword: str,
        providers_to_supplement: Set[str],
        provider_platforms_map: Dict[str, List[str]],
        user: User,
    ) -> List[ProviderSearchInfo]:
        """搜索补充源实现：用 bangumi-data 离线库的平台直链为空结果弹幕源兜底。

        与 360 的差异：离线管理器 resolve_sources_by_title 返回的 mediaId 已是弹幕源
        可直接使用的真实 id（如 tencent cid、bilibili ss_id），无需二次 URL 解析。
        因此这里直接用真实 (provider, mediaId) 构建结果，不套 sup_ 前缀——导入时会走
        正常弹幕源路径（get_episodes_routed 对非 sup_ 的 mediaId 直接调 scraper.get_episodes）。

        基类 supplement_search 已完成 empty_providers 过滤和去重，这里只需：
        1. 调离线直链解析
        2. 仅保留 providers_to_supplement 里需要补充的 provider
        """
        metadata_service = get_metadata_service()
        if not await metadata_service.is_offline_bangumi_enabled():
            return []

        try:
            core_title = parse_search_keyword(keyword)["title"]
            resolver = getattr(self, "resolve_offline_sources", None)
            if resolver is None:
                return []
            direct_sources = await resolver(core_title)
        except Exception as e:
            self.logger.warning(f"bangumi-data 直链补充解析失败: {type(e).__name__}: {e}")
            return []

        if not direct_sources:
            return []

        items: List[ProviderSearchInfo] = []
        for ds in direct_sources:
            provider = ds.get("provider")
            media_id = ds.get("mediaId")
            # 仅补充「确实没搜到结果」且本源支持的 provider
            if not provider or not media_id or provider not in providers_to_supplement:
                continue
            items.append(ProviderSearchInfo(
                provider=provider,
                mediaId=str(media_id),
                title=ds.get("title") or core_title,
                type=ds.get("type") or "tv_series",
                season=1,
                year=ds.get("year"),
            ))
        return items

    async def _get_from_cache(self, key: str) -> Optional[Any]:
        """从缓存中获取数据。"""
        _backend = get_cache_service()
        if _backend is not None:
            try:
                result = await _backend.get(key, region="metadata")
                if result is not None:
                    return result
            except Exception:
                pass
        db = get_database_service()
        async with self._session_factory() as session:
            async with db.transaction(session):
                return await db.cache.get_json(key)

    async def _set_to_cache(self, key: str, value: Any, ttl_key: str, default_ttl: int):
        """将数据设置到缓存中，并从配置中读取TTL。"""
        ttl_seconds = default_ttl
        try:
            ttl_from_config = await self.config_service.get(ttl_key)
            if ttl_from_config:
                ttl_seconds = int(ttl_from_config)
        except (ValueError, TypeError):
            self.logger.warning(f"无法从配置 '{ttl_key}' 中解析TTL，将使用默认值 {default_ttl} 秒。")

        _backend = get_cache_service()
        if _backend is not None:
            try:
                await _backend.set(key, value, ttl=ttl_seconds, region="metadata")
            except Exception:
                db = get_database_service()
                async with self._session_factory() as session:
                    async with db.transaction(session):
                        await db.cache.set_json(key, value, ttl_seconds)
        else:
            db = get_database_service()
            async with self._session_factory() as session:
                async with db.transaction(session):
                    await db.cache.set_json(key, value, ttl_seconds)

    async def _ensure_config(self):
        """从数据库配置中加载个人访问令牌。"""
        if self._config_loaded:
            return
        self._token = await self.config_service.get("bangumiToken")
        self._config_loaded = True

    async def _get_proxy(self) -> Optional[str]:
        """获取 Bangumi 代理配置。当全局代理启用且 bgm 源设置了 useProxy 时返回代理 URL。"""
        proxy_mode = await self.config_service.get("proxyMode", "none")
        if proxy_mode == "none":
            if (await self.config_service.get("proxyEnabled", "false")).lower() == "true":
                proxy_mode = "http_socks"
        if proxy_mode != "http_socks":
            return None
        proxy_url = await self.config_service.get("proxyUrl", "")
        if not proxy_url:
            return None
        db = get_database_service()
        async with self._session_factory() as session:
            async with db.transaction(session):
                all_settings = await db.metadata_source.get_all_metadata_source_settings()
            bgm_setting = next((s for s in all_settings if s.get('providerName') == 'bangumi'), None)
            if bgm_setting and bgm_setting.get('useProxy', False):
                return proxy_url
        return None

    async def _create_client(self, user: User) -> httpx.AsyncClient:
        await self._ensure_config()
        # why: User-Agent 会经过目标站与代理日志，不能携带任何密钥片段。
        headers = {"User-Agent": "DanmuApiServer/1.0"}
        if self._token:
            self.logger.debug("Bangumi: 正在使用 Access Token 进行认证。")
            headers["Authorization"] = f"Bearer {self._token}"
        else:
            auth_info = await get_oauth_service().credentials("bangumi", user.id, auto_refresh=True)

            if auth_info and auth_info.get("accessToken"):
                self.logger.debug("Bangumi: 正在使用 OAuth Access Token 进行认证。")
                headers["Authorization"] = f"Bearer {auth_info['accessToken']}"
        api_base_url = await self._get_api_base_url()
        proxy = await self._get_proxy()
        return httpx.AsyncClient(base_url=api_base_url, headers=headers, timeout=20.0, proxy=proxy)

    async def search(self, keyword: str, user: User, mediaType: Optional[str] = None) -> List[MetadataDetailsResponse]:
        """
        Performs a cached search for Bangumi content.
        It caches the base results for a title.
        """
        parsed = parse_search_keyword(keyword)
        search_title = parsed['title']

        cache_key = f"search_base_{self.provider_name}_{search_title}_{user.id}"
        cached_results = await self._get_from_cache(cache_key)
        if cached_results:
            self.logger.info(f"Bangumi: 从缓存中命中基础搜索结果 (title='{search_title}')")
            return [MetadataDetailsResponse.model_validate(r) for r in cached_results]

        self.logger.info(f"Bangumi: 缓存未命中，正在为标题 '{search_title}' 执行网络搜索...")
        all_results = await self._perform_network_search(search_title, user, mediaType)

        if all_results:
            await self._set_to_cache(cache_key, [r.model_dump() for r in all_results], 'metadata_search_ttl_seconds', 3600)

        return all_results

    async def _perform_network_search(self, keyword: str, user: User, mediaType: Optional[str] = None) -> List[MetadataDetailsResponse]:
        """Performs the actual network search for Bangumi.

        智能过滤逻辑：
        1. 获取第一个结果的 name 和 name_cn 作为基准
        2. 后续结果只有当其 name 包含基准 name，或 name_cn 包含基准 name_cn 时才认为是相关系列
        3. 直接从搜索结果中提取 name 和 name_cn 作为别名，不需要调用 get_details
        """
        async with await self._create_client(user) as client:
            # 只搜索动画类型 (type=2)
            search_payload = {"keyword": keyword, "filter": {"type": [2]}}
            search_response = await client.post("/v0/search/subjects", json=search_payload)
            if search_response.status_code == 404: return []
            search_response.raise_for_status()

            search_result = BangumiSearchResponse.model_validate(search_response.json())
            if not search_result.data: return []

            # 获取第一个结果作为基准
            first_result = search_result.data[0]
            base_name = first_result.name or ""
            base_name_cn = first_result.name_cn or ""

            # 过滤出相关的系列作品
            related_subjects = [first_result]  # 第一个结果一定是相关的

            for subject in search_result.data[1:]:
                subject_name = subject.name or ""
                subject_name_cn = subject.name_cn or ""

                # 检查是否是相关系列：name 包含基准 name，或 name_cn 包含基准 name_cn
                is_related = False
                if base_name and subject_name and base_name in subject_name:
                    is_related = True
                if base_name_cn and subject_name_cn and base_name_cn in subject_name_cn:
                    is_related = True

                if is_related:
                    related_subjects.append(subject)

            self.logger.info(f"Bangumi: 搜索返回 {len(search_result.data)} 个结果，过滤后保留 {len(related_subjects)} 个相关系列")

            # 直接从搜索结果中提取别名，不需要调用 get_details
            # 每个结果有 name（日文名）和 name_cn（中文名），直接构建返回结果
            results = []
            for subject in related_subjects:
                # 收集别名：name_cn 作为 aliasesCn
                aliases_cn = [subject.name_cn] if subject.name_cn else []

                # 从 subject.date（如 "2022-01-08"）提取年份，供下游（如 bgmtv 兜底多季消歧/日志）使用
                sub_year = None
                if subject.date and len(subject.date) >= 4 and subject.date[:4].isdigit():
                    sub_year = int(subject.date[:4])

                results.append(MetadataDetailsResponse(
                    id=str(subject.id),
                    bangumiId=str(subject.id),
                    title=subject.name_cn or subject.name,
                    type="tv_series",
                    nameJp=subject.name,
                    imageUrl=await self._rewrite_image_url(subject.image_url),
                    aliasesCn=aliases_cn,
                    year=sub_year,
                ))

            return results

    async def get_details(self, item_id: str, user: User, mediaType: Optional[str] = None) -> Optional[MetadataDetailsResponse]:
        async with await self._create_client(user) as client:
            details_url = f"/v0/subjects/{item_id}"
            details_response = await client.get(details_url)
            if details_response.status_code == 404: return None
            details_response.raise_for_status()

            subject_data = details_response.json()
            subject = BangumiSearchSubject.model_validate(subject_data)
            aliases = subject.aliases

            # 推断媒体类型
            media_type = "tv_series" # 默认为 tv_series
            if subject_data.get("type") == 2: # Anime
                # 如果总集数为1，则认为是电影
                if subject_data.get("eps") == 1:
                    media_type = "movie"
                # 检查标题中是否包含电影关键词
                elif _clean_movie_title(subject.display_name) != subject.display_name:
                    media_type = "movie"

            # 提取年份信息
            year = None
            if subject_data.get("date"):
                try:
                    year = int(subject_data["date"][:4])
                except (ValueError, TypeError):
                    pass

            # 确保 name_cn 也被加入到 aliasesCn 中
            aliases_cn = aliases.get("aliases_cn", [])
            if subject.name_cn and subject.name_cn not in aliases_cn:
                aliases_cn = [subject.name_cn] + aliases_cn

            return MetadataDetailsResponse(
                id=str(subject.id), bangumiId=str(subject.id), title=subject.display_name,
                type=media_type, nameJp=subject.name, imageUrl=await self._rewrite_image_url(subject.image_url), details=subject.details_string,
                nameEn=aliases.get("name_en"), nameRomaji=aliases.get("name_romaji"),
                aliasesCn=aliases_cn, year=year
            )

    async def search_aliases(self, keyword: str, user: User) -> Set[str]:
        local_aliases: Set[str] = set()
        try:
            async with await self._create_client(user) as client:
                search_payload = {"keyword": keyword, "filter": {"type": [2]}}
                search_response = await client.post("/v0/search/subjects", json=search_payload)
                if search_response.status_code != 200: return set()

                search_result = BangumiSearchResponse.model_validate(search_response.json())
                if not search_result.data: return set()

                best_match = search_result.data[0]
                details_response = await client.get(f"/v0/subjects/{best_match.id}")
                if details_response.status_code != 200: return set()

                details = details_response.json()
                local_aliases.add(details.get('name'))
                local_aliases.add(details.get('name_cn'))
                for item in details.get('infobox', []):
                    if item.get('key') == '别名':
                        if isinstance(item['value'], str): local_aliases.add(item['value'])
                        elif isinstance(item['value'], list):
                            for v_item in item['value']:
                                if isinstance(v_item, dict) and v_item.get('v'): local_aliases.add(v_item['v'])
                self.logger.info(f"Bangumi辅助搜索成功，找到别名: {[a for a in local_aliases if a]}")
        except Exception as e:
            self.logger.warning(f"Bangumi辅助搜索失败: {e}")
        return {alias for alias in local_aliases if alias}

    async def get_calendar(self, user: User) -> List[Dict[str, Any]]:
        """从 Bangumi /calendar 获取当季全量番剧日历

        集数策略（与 Trakt 一致的"两步走"）：
        - BGM /calendar 接口不返回任何集数字段，因此首次刷新时所有番显示 ?/∞
        - 返回前 fire-and-forget 触发两个后台任务并发拉数据：
          1) /v0/subjects/{id} 取 total_episodes/eps（总集数，TTL 6h）
          2) /v0/episodes?subject_id=X 数 airdate≤today 的条目（已播集数，TTL 12h）
        - 下次刷新即可命中缓存显示真实「已播/总」进度

        加载性能：整体日历结果缓存 1 小时，避免每次刷新都重打 BGM /calendar
        （这是页面卡 3+ 秒的根因——BGM 国内访问偏慢，单次 1.5-3s）
        """
        # 局部缓存常量（避免污染模块顶层）
        _CACHE_REGION = "metadata"
        _BGM_EPS_KEY = "bgm_subject_eps_{bgm_id}"
        _BGM_EPS_TTL = 6 * 3600
        _BGM_EPS_CONCURRENCY = 5
        # 已播集数缓存（airdate ≤ today 的集数）
        _BGM_AIRED_KEY = "bgm_subject_aired_{bgm_id}"
        _BGM_AIRED_TTL = 12 * 3600  # 12h，平衡新鲜度与请求量
        _BGM_AIRED_CONCURRENCY = 5

        # 整体日历结果缓存（仿 Trakt 同款机制）—— 关键性能优化
        cache_backend = get_cache_service()
        today = datetime.now().strftime("%Y-%m-%d")
        _CALENDAR_CACHE_KEY = f"bangumi_calendar_{today}"
        _CALENDAR_CACHE_TTL = 3600  # 1 小时
        try:
            cached = await cache_backend.get(_CALENDAR_CACHE_KEY, region=_CACHE_REGION)
            if cached is not None:
                self.logger.debug(f"Bangumi 日历缓存命中（{len(cached)} 条），跳过 API 请求")
                return cached
        except Exception:
            pass

        metadata_logger = logging.getLogger('metadata_responses')
        provider_setting = await self._get_provider_setting()
        log_raw = provider_setting.get('logRawResponses', False)

        await self._ensure_config()
        api_base = await self._get_api_base_url()

        items = []
        try:
            proxy = await self._get_proxy()
            async with httpx.AsyncClient(timeout=15, proxy=proxy) as client:
                resp = await client.get(f"{api_base}/calendar")
                resp.raise_for_status()
                bgm_calendar = resp.json()

                if log_raw:
                    # why：resp.text 里中文/日文原本就是 \uXXXX 转义；先反序列化再用
                    # ensure_ascii=False 重新序列化，日志中显示可读的中文。
                    metadata_logger.info(
                        f"Bangumi Calendar Response: URL={resp.url} | Status={resp.status_code} | Body={_json.dumps(bgm_calendar, ensure_ascii=False)}"
                    )

            for day_group in bgm_calendar:
                weekday = day_group.get("weekday", {}).get("id")
                if not weekday:
                    continue
                for bgm in day_group.get("items", []):
                    images = bgm.get("images") or {}
                    air_date = bgm.get("air_date") or ""
                    year = None
                    if air_date and len(air_date) >= 4 and air_date[:4].isdigit():
                        year = int(air_date[:4])
                    bgm_id = str(bgm.get("id", ""))
                    # 总集数：尝试读个例缓存（由后台任务补全），未命中则 None → 前端 ∞
                    eps_total = None
                    aired_now = None
                    if bgm_id:
                        try:
                            eps_total = await cache_backend.get(
                                _BGM_EPS_KEY.format(bgm_id=bgm_id), region=_CACHE_REGION
                            )
                        except Exception:
                            eps_total = None
                        # 已播集数：尝试读个例缓存（由后台任务补全），未命中则 None → 前端 ?
                        try:
                            aired_now = await cache_backend.get(
                                _BGM_AIRED_KEY.format(bgm_id=bgm_id), region=_CACHE_REGION
                            )
                        except Exception:
                            aired_now = None
                    items.append({
                        "animeTitle": bgm.get("name_cn") or bgm.get("name", ""),
                        "titleJp": bgm.get("name", ""),
                        "airWeekday": weekday,
                        "origin": "bangumi",
                        "isLocal": False,
                        "bangumiId": bgm_id,
                        "traktId": None,
                        "imageUrl": images.get("common") or images.get("medium"),
                        "rating": bgm.get("rating", {}).get("score"),
                        "rank": bgm.get("rank"),
                        "year": year,
                        "airDate": air_date or None,
                        "latestEpisodeIndex": aired_now,  # 已播集数（缓存命中才有）
                        "episodeCount": eps_total,         # 总集数（缓存命中才有）
                    })
        except Exception as e:
            self.logger.warning(f"Bangumi 日历获取失败: {e}")
            return items

        # 后台慢拉总集数：仅对未命中缓存的 bgm_id 触发
        missing_ids = [it.get("bangumiId") for it in items
                       if it.get("bangumiId") and it.get("episodeCount") is None]
        # 后台慢拉已播集数：仅对未命中 aired 缓存的 bgm_id 触发
        missing_aired_ids = [it.get("bangumiId") for it in items
                             if it.get("bangumiId") and it.get("latestEpisodeIndex") is None]
        if missing_ids or missing_aired_ids:
            if missing_ids:
                asyncio.create_task(self._fetch_eps_background(
                    missing_ids, api_base,
                    concurrency=_BGM_EPS_CONCURRENCY,
                    cache_key_tpl=_BGM_EPS_KEY,
                    cache_region=_CACHE_REGION,
                    cache_ttl=_BGM_EPS_TTL,
                ))
            if missing_aired_ids:
                asyncio.create_task(self._fetch_aired_episodes_background(
                    missing_aired_ids, api_base,
                    concurrency=_BGM_AIRED_CONCURRENCY,
                    cache_key_tpl=_BGM_AIRED_KEY,
                    cache_region=_CACHE_REGION,
                    cache_ttl=_BGM_AIRED_TTL,
                ))

        # 写入整体日历缓存（1小时），下次刷新瞬间命中，不再走 BGM API
        try:
            await cache_backend.set(
                _CALENDAR_CACHE_KEY, items,
                ttl=_CALENDAR_CACHE_TTL, region=_CACHE_REGION,
            )
        except Exception:
            pass

        return items

    async def get_user_watching_collection(self, user: User) -> Dict[str, Dict[str, Any]]:
        """拉取「平台账号下我的在追」列表 — 用于补充 external_calendar_item.platformWatchStatus。

        Bangumi collection type 取值（参考官方文档）：
            1 = 想看 (wish)
            2 = 看过 (collect/done)
            3 = 在看 (do/watching)
            4 = 搁置 (on_hold)
            5 = 抛弃 (dropped)

        本方法默认拉取**全部状态**的动画收藏，让上层决定如何映射展示。

        :return: { bangumi_id: {'status': 'watching'|'wish'|..., 'watchedEps': int|None, 'rating': float|None} }
                  未授权时返回 {}
        """
        # 状态码 → 标准化字符串
        STATUS_MAP = {
            1: "wish",
            2: "done",
            3: "watching",
            4: "on_hold",
            5: "dropped",
        }

        # 检查 OAuth 授权（必须有 access_token 才能调 /v0/users/-/collections）
        auth_info = await get_oauth_service().credentials("bangumi", user.id, auto_refresh=False)
        if not (auth_info and auth_info.get("accessToken")):
            self.logger.debug("Bangumi: 未授权，跳过 get_user_watching_collection")
            return {}

        result: Dict[str, Dict[str, Any]] = {}
        try:
            async with await self._create_client(user) as client:
                # subject_type=2 表示动画；不传 type 则返回全部状态
                # /v0/users/-/collections 是私有接口，必须带 Authorization
                offset = 0
                limit = 50  # API 默认 30，最大 50
                page_count = 0
                MAX_PAGES = 20  # 安全上限：最多 1000 条收藏，避免循环失控
                while page_count < MAX_PAGES:
                    resp = await client.get(
                        "/v0/users/-/collections",
                        params={"subject_type": 2, "limit": limit, "offset": offset},
                    )
                    if resp.status_code != 200:
                        self.logger.warning(
                            f"Bangumi 拉取在追列表失败 status={resp.status_code} body={resp.text[:200]}"
                        )
                        break
                    data = resp.json() or {}
                    items = data.get("data") or []
                    if not items:
                        break
                    for it in items:
                        subject = it.get("subject") or {}
                        bgm_id = subject.get("id") or it.get("subject_id")
                        if not bgm_id:
                            continue
                        status_code = it.get("type")
                        status_str = STATUS_MAP.get(status_code)
                        if not status_str:
                            continue
                        result[str(bgm_id)] = {
                            "status": status_str,
                            "watchedEps": it.get("ep_status"),  # 看到第几集
                            "rating": it.get("rate") or None,    # 1-10 的整数评分
                        }
                    total = data.get("total") or 0
                    offset += len(items)
                    if offset >= total or len(items) < limit:
                        break
                    page_count += 1
        except Exception as e:
            self.logger.warning(f"Bangumi get_user_watching_collection 异常: {e}")
            return {}

        self.logger.info(f"Bangumi 在追列表: 共 {len(result)} 条 (status 分布略)")
        return result



    async def _fetch_eps_background(
        self, bgm_ids: List[str], api_base: str,
        concurrency: int, cache_key_tpl: str, cache_region: str, cache_ttl: int,
    ) -> None:
        """后台并发拉取每个 subject 的总集数，写入缓存。
        - 安静失败（后台任务，不抛异常）
        - 优先 total_episodes，回退 eps
        """
        cache_backend = get_cache_service()
        sem = asyncio.Semaphore(concurrency)

        async def _one(client: httpx.AsyncClient, bgm_id: str) -> None:
            async with sem:
                try:
                    resp = await client.get(f"{api_base}/v0/subjects/{bgm_id}")
                    if resp.status_code != 200:
                        return
                    data = resp.json() or {}
                    # 优先 total_episodes（"完整最终集数"），回退到 eps
                    total = data.get("total_episodes") or data.get("eps")
                    if isinstance(total, int) and total > 0:
                        try:
                            await cache_backend.set(
                                cache_key_tpl.format(bgm_id=bgm_id),
                                total, ttl=cache_ttl, region=cache_region,
                            )
                        except Exception:
                            pass
                except Exception:
                    pass

        try:
            proxy = await self._get_proxy()
            async with httpx.AsyncClient(timeout=15, proxy=proxy) as client:
                await asyncio.gather(*(_one(client, bid) for bid in bgm_ids), return_exceptions=True)
            self.logger.debug(f"Bangumi 后台拉取 total_episodes 完成: {len(bgm_ids)} 个 subject")
        except Exception as e:
            self.logger.debug(f"Bangumi 后台拉取 total_episodes 失败: {e}")

    async def _fetch_aired_episodes_background(
        self, bgm_ids: List[str], api_base: str,
        concurrency: int, cache_key_tpl: str, cache_region: str, cache_ttl: int,
    ) -> None:
        """后台并发拉取每个 subject 的"已播集数"，写入缓存。

        BGM 没有原生"当前集"字段，唯一办法是调 /v0/episodes 拿全部集，
        然后数 airdate ≤ today 的条目数 = 已播集数（仿 ani-rss BgmUtil.getEpisodes）。

        - 安静失败（后台任务，不抛异常）
        - 并发受 concurrency 限制，避免触发 BGM 限流
        - type=0 仅取正篇（排除 OVA/SP/番外）
        - limit=200 足以覆盖绝大多数番（最长番剧《海螺小姐》也就 2700+ 集，分页另说）
        """
        cache_backend = get_cache_service()
        sem = asyncio.Semaphore(concurrency)
        # 用 UTC+8（BGM 数据基本是日本/中国时区），避免时差导致今日已播被误算
        today = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")

        async def _one(client: httpx.AsyncClient, bgm_id: str) -> None:
            async with sem:
                try:
                    resp = await client.get(
                        f"{api_base}/v0/episodes",
                        params={"subject_id": bgm_id, "type": 0, "limit": 200, "offset": 0},
                    )
                    if resp.status_code != 200:
                        return
                    data = resp.json() or {}
                    eps_list = data.get("data") or []
                    if not isinstance(eps_list, list):
                        return
                    aired = sum(
                        1 for e in eps_list
                        if isinstance(e, dict) and e.get("airdate") and e.get("airdate") <= today
                    )
                    if aired > 0:
                        try:
                            await cache_backend.set(
                                cache_key_tpl.format(bgm_id=bgm_id),
                                aired, ttl=cache_ttl, region=cache_region,
                            )
                        except Exception:
                            pass
                except Exception:
                    pass

        try:
            proxy = await self._get_proxy()
            async with httpx.AsyncClient(timeout=15, proxy=proxy) as client:
                await asyncio.gather(*(_one(client, bid) for bid in bgm_ids), return_exceptions=True)
            self.logger.debug(f"Bangumi 后台拉取 aired_episodes 完成: {len(bgm_ids)} 个 subject")
        except Exception as e:
            self.logger.debug(f"Bangumi 后台拉取 aired_episodes 失败: {e}")


    async def _get_provider_setting(self) -> Dict[str, Any]:
        """获取当前源的设置"""
        db = get_database_service()
        # 使用 DatabaseService 的内部 session 管理，避免并发冲突
        async with db.transaction():
            settings = await db.metadata_source.get_all_metadata_source_settings()
        return next((s for s in settings if s['providerName'] == self.provider_name), {})

    async def check_connectivity(self) -> Dict[str, str]:
        """检查Bangumi源配置状态"""
        try:
            await self._ensure_config()

            # 1. 优先检查 Access Token 模式
            if self._token:
                return {"code": "ok", "message": "Access Token 模式 (已配置)"}

            # 2. 检查 OAuth 模式
            client_id = await self.config_service.get("bangumiClientId", "")
            client_secret = await self.config_service.get("bangumiClientSecret", "")

            if client_id and client_secret:
                # 检查是否有用户已授权
                try:
                    valid_token_count = await get_oauth_service().count_valid("bangumi")

                    if valid_token_count > 0:
                        return {"code": "ok", "message": f"OAuth 模式 ({valid_token_count}个用户已授权)"}
                    else:
                        return {"code": "warning", "message": "OAuth 模式 (App已配置，等待用户授权)"}
                except Exception:
                    return {"code": "ok", "message": "OAuth 模式 (App已配置)"}
            elif client_id:
                return {"code": "warning", "message": "OAuth 模式 (App ID已填，App Secret 未填)"}
            else:
                return {"code": "unconfigured", "message": "未配置 (请填写Access Token 或 OAuth App信息)"}

        except Exception as e:
            return {"code": "error", "message": f"配置检查失败: {e}"}

    # ============ 订阅助手实现 ============

    async def check_subscription_capability(self, user=None) -> Dict[str, Any]:
        """Bangumi 订阅可用性：公共搜索 API 即可（无 OAuth 也能搜）。"""
        return {
            "available": True,
            "authRequired": False,
            "authStatus": "valid",
            "reason": None,
            "subscriptionTypes": self.subscription_types,
        }

    async def discover_subscription_targets(self, query: str, subscription_type: str = "", user=None) -> List[Dict[str, Any]]:
        """搜 Bangumi 番剧条目候选；复用 search()。无登录用户时构造一个匿名占位。"""
        query = (query or "").strip()
        if not query:
            return []
        # search() 需要 user.id 做缓存键；订阅探测阶段允许 user 缺省，构造匿名兜底
        u = user or User(id=0, username="__sub_discover__")
        try:
            results = await self.search(query, user=u)
        except Exception as e:
            self.logger.warning(f"Bangumi: 订阅 discover 搜索失败: {type(e).__name__}: {e}")
            return []
        out = []
        for r in results[:20]:
            if not r.id:
                continue
            year = f" · {r.year}" if r.year else ""
            desc = (r.details or "")[:80]
            out.append({
                "type": "bangumi_subject",
                "title": r.title or f"Bangumi {r.id}",
                "cover": r.imageUrl,
                "description": f"Bangumi ID {r.id}{year}" + (f" · {desc}" if desc else ""),
                "payload": {
                    "bangumiId": str(r.id),
                    "title": r.title or "",
                    "year": r.year,
                    "imageUrl": r.imageUrl,
                },
            })
        return out

    async def validate_subscription_payload(self, subscription_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """校验 Bangumi 订阅 payload；统一返回写入 external_calendar_item 的标准结构。"""
        if subscription_type != "bangumi_subject":
            raise ValueError(f"Bangumi 暂不支持订阅类型: {subscription_type}")
        bgm_id = (payload or {}).get("bangumiId") or ""
        if not str(bgm_id).strip():
            raise ValueError("缺少 bangumiId")
        title = (payload or {}).get("title") or ""
        return {
            "provider": self.provider_name,
            "externalId": f"bgm-{bgm_id}",
            "title": title,
            "animeType": "tv_series",
            "subscriptionType": "bangumi_subject",
            "extraData": {
                "bangumiId": str(bgm_id),
                "year": (payload or {}).get("year"),
                "imageUrl": (payload or {}).get("imageUrl"),
            },
        }
