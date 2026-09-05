"""
御坂助手 · API 网关路由反射校验
------------------------------------------------------------
对齐 MoviePilot v3 「schema 由业务 OpenAPI 生成 + 漂移测试锁定」的思路。
本项目所有 UI 路由都是 include_in_schema=False（OpenAPI 里查不到），
因此改为直接反射 FastAPI 的 app.routes 来核对。

职责：
1. 反射真实路由表，校验 policy.py 里登记的每条白名单是否真实存在。
   —— 后端改了路径而白名单没跟着改时，启动阶段就能发现，而不是等 AI 调用时 404。
2. 提供「已有路由 vs 已加白操作」的缺口清单，便于后续按需扩充白名单。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from .policy import (
    FORBIDDEN_PATH_PREFIXES,
    ApiOperation,
    list_exposed_operations,
)

logger = logging.getLogger(__name__)

# 反射时忽略的非业务路由
_IGNORED_PATH_PREFIXES = (
    "/docs", "/redoc", "/openapi.json", "/static", "/assets",
)
_IGNORED_METHODS = frozenset({"HEAD", "OPTIONS"})


@dataclass(frozen=True)
class RouteFact:
    """从 FastAPI 反射出的单条真实路由事实。"""

    method: str
    path: str
    summary: str

    @property
    def key(self) -> Tuple[str, str]:
        return self.method, self.path


def reflect_routes(app: Any) -> Tuple[RouteFact, ...]:
    """
    反射 FastAPI 应用的真实路由表。

    :param app: FastAPI 应用实例
    :return: 路由事实元组（已过滤静态资源与 HEAD/OPTIONS）
    """
    facts: List[RouteFact] = []
    for route in getattr(app, "routes", []):
        path = getattr(route, "path", "") or ""
        methods: Set[str] = getattr(route, "methods", None) or set()
        if not path or not methods:
            continue
        if any(path.startswith(p) for p in _IGNORED_PATH_PREFIXES):
            continue
        summary = getattr(route, "summary", "") or getattr(route, "name", "") or ""
        for method in methods:
            if method in _IGNORED_METHODS:
                continue
            facts.append(RouteFact(method=method, path=path, summary=summary))
    return tuple(facts)


def validate_whitelist(app: Any) -> Dict[str, Any]:
    """
    校验白名单里每条操作是否真实存在于路由表（防路径漂移）。

    :param app: FastAPI 应用实例
    :return: {"ok": bool, "missing": [...], "checked": int}
    """
    facts = reflect_routes(app)
    real_keys = {f.key for f in facts}

    missing: List[Dict[str, str]] = []
    operations = list_exposed_operations()
    for op in operations:
        if (op.method, op.full_path) not in real_keys:
            missing.append({
                "operationId": op.operation_id,
                "method": op.method,
                "path": op.full_path,
            })

    if missing:
        # 白名单与真实路由脱节属于配置错误，必须显式告警而不是静默降级
        for item in missing:
            logger.error(
                "御坂助手 API 网关白名单失效：%s %s（operation=%s）路由不存在，"
                "请检查后端路径是否变更",
                item["method"], item["path"], item["operationId"],
            )

    return {
        "ok": not missing,
        "missing": missing,
        "checked": len(operations),
    }


def list_coverage_gap(app: Any) -> Tuple[Dict[str, str], ...]:
    """
    列出「后端已有路由，但尚未加白给助手」的缺口清单。

    用于人工评估还需要给助手开放哪些能力；不参与运行时决策。
    已命中禁止前缀的路由不出现在缺口里（它们本就不该开放）。

    :param app: FastAPI 应用实例
    :return: 缺口条目元组
    """
    exposed_keys = {(op.method, op.full_path) for op in list_exposed_operations()}
    gap: List[Dict[str, str]] = []
    for fact in reflect_routes(app):
        if fact.key in exposed_keys:
            continue
        # 去掉 /api 前缀再比对禁止清单（禁止清单按 api_router 内路径书写）
        inner_path = fact.path[4:] if fact.path.startswith("/api") else fact.path
        if any(inner_path.startswith(p) for p in FORBIDDEN_PATH_PREFIXES):
            continue
        gap.append({
            "method": fact.method,
            "path": fact.path,
            "summary": fact.summary,
        })
    return tuple(sorted(gap, key=lambda x: (x["path"], x["method"])))


__all__ = [
    "RouteFact",
    "reflect_routes",
    "validate_whitelist",
    "list_coverage_gap",
]
