"""订阅能力统一入口：负责源解析、统一契约调用与扫描结果持久化。"""
from typing import Any, Dict, List, Optional

from src.db.crud import external_calendar as ext_cal_crud


class SubscriptionManager:
    """屏蔽弹幕源/元数据源差异，调用方只面向订阅抽象契约。"""

    def __init__(self, scraper_manager, metadata_manager):
        self.scraper_manager = scraper_manager
        self.metadata_manager = metadata_manager

    def resolve_source(self, provider: str) -> Optional[Any]:
        """统一找源：弹幕源优先，找不到再查元数据源。"""
        source = self.scraper_manager.scrapers.get(provider)
        if source is None:
            source = self.metadata_manager.sources.get(provider)
        if source is None or not getattr(source, "supports_subscription", False):
            return None
        return source

    async def probe_source(self, provider: str, user=None) -> Optional[Dict[str, Any]]:
        source = self.resolve_source(provider)
        if source is None:
            return None
        try:
            capability = await source.check_subscription_capability(user=user)
        except TypeError:
            capability = await source.check_subscription_capability()
        return capability

    async def discover(self, provider: str, query: str, subscription_type: str = "", user=None) -> List[Dict[str, Any]]:
        source = self.resolve_source(provider)
        if source is None:
            raise ValueError(f"订阅源 '{provider}' 未加载或不支持订阅")
        try:
            return await source.discover_subscription_targets(query, subscription_type, user=user)
        except TypeError:
            return await source.discover_subscription_targets(query, subscription_type)

    async def validate(self, provider: str, subscription_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        source = self.resolve_source(provider)
        if source is None:
            raise ValueError(f"订阅源 '{provider}' 未加载或不支持订阅")
        return await source.validate_subscription_payload(subscription_type, payload)

    async def scan_target(self, target: Dict[str, Any], session=None) -> Dict[str, Any]:
        """统一扫描入口；兼容旧源直接返回候选列表。"""
        provider = target.get("provider") or ""
        source = self.resolve_source(provider)
        if source is None:
            raise ValueError(f"订阅源 '{provider}' 未加载或不支持订阅")
        result = await source.scan_subscription_target(target)
        # why：各家特殊增量规则由源自己归一化，统一入口只负责调用抽象钩子。
        normalize = getattr(source, "normalize_subscription_scan_result", None)
        if normalize is not None:
            result = await normalize(session, target, result)
        if isinstance(result, dict) and result.get("mode"):
            return {"source": source, "mode": result["mode"], "items": result.get("items") or []}
        return {"source": source, "mode": "candidates", "items": result or []}

    async def persist_scan_result(self, session, target: Dict[str, Any], result: Dict[str, Any]) -> int:
        """统一持久化扫描出的作品订阅；候选项仍由候选池模块写入。"""
        if result.get("mode") != "subscriptions":
            return 0
        count = 0
        for item in result.get("items") or []:
            await ext_cal_crud.upsert_subscription_target(
                session,
                provider=item["provider"],
                external_id=str(item["externalId"]),
                title=item.get("title") or "",
                subscription_type=item.get("subscriptionType") or "subject",
                extra={**(item.get("extraData") or {}), "animeType": item.get("animeType", "tv_series")},
                status=item.get("status") or "pending",
            )
            count += 1
        return count
