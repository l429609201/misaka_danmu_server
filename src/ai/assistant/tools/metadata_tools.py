"""
御坂助手 · 元数据源与密钥管理工具（A组 + D组）
------------------------------------------------------------
A组（只读）：列出元数据源状态、查看某源配置（密钥脱敏）、元数据搜索、取详情。
D组（密钥）：写入密钥（WRITE）、验证密钥有效性（只读，走 check_connectivity）、
            查看密钥配置状态（只读，仅返回掩码，永不返回明文）。

安全约束（密钥绝不出库门）：
- 读类工具一律不返回明文密钥，只返回「是否已配置 + 掩码」。
- base.py 的 sanitize_output 作为最后防线再兜一层。
- 验证密钥通过源自身的 check_connectivity() 实发请求，只回布尔与说明文字。

context 依赖（由 app.state / 渠道端注入）：
- context["metadata_manager"]: MetadataSourceManager
"""

import logging
from typing import Any, Dict, List

from src.db import models
from ..security_gateway import ToolPermission
from .base import Tool, registry

logger = logging.getLogger(__name__)

# 单次返回给模型的最大条数（控制 token）
_MAX_ITEMS = 15

# 御坂助手代理执行时使用的内部用户身份（元数据源接口需要 user 参数）
_ASSISTANT_USER = models.User(id=0, username="misaka_assistant")


def _mask_secret(value: Any) -> str:
    """把密钥掩码成 前4***后4(len=N) 形式，便于用户核对而不泄露明文。"""
    if not value or not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    if len(text) <= 8:
        return f"***(len={len(text)})"
    return f"{text[:4]}***{text[-4:]}(len={len(text)})"


def _is_secret_config_key(key: str) -> bool:
    """判断某配置项是否属于密钥类（用于决定是否掩码）。"""
    lower = (key or "").lower()
    return any(kw in lower for kw in ("key", "token", "secret", "cookie", "password", "auth"))


async def _list_metadata_sources(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """列出所有元数据源及其启用状态、连接状态。"""
    manager = context.get("metadata_manager")
    if not manager:
        return {"error": "元数据源管理器不可用"}
    sources = await manager.get_sources_with_status()
    simplified = [
        {
            "providerName": s.get("providerName"),
            "isEnabled": s.get("isEnabled"),
            "status": s.get("status"),
            "statusCode": s.get("statusCode"),
            "isAuxSearchEnabled": s.get("isAuxSearchEnabled"),
            "displayOrder": s.get("displayOrder"),
        }
        for s in sources
    ]
    return {"total": len(simplified), "sources": simplified}


async def _get_metadata_source_config(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """查看某元数据源的配置项（密钥类字段一律掩码，绝不返回明文）。"""
    provider = (arguments.get("provider") or "").strip()
    if not provider:
        return {"error": "缺少 provider（元数据源名，如 tmdb/tvdb/bangumi/douban/imdb）"}
    manager = context.get("metadata_manager")
    if not manager:
        return {"error": "元数据源管理器不可用"}
    try:
        raw = await manager.getProviderConfig(provider)
    except ValueError as e:
        return {"error": f"未找到元数据源 {provider}：{e}"}

    masked: Dict[str, Any] = {}
    for k, v in (raw or {}).items():
        if _is_secret_config_key(k):
            masked[k] = _mask_secret(v) or "(未配置)"
        else:
            masked[k] = v
    return {
        "provider": provider,
        "config": masked,
        "note": "密钥类字段已掩码显示，无法读取明文。如需更换请用 set_metadata_source_key。",
    }


async def _search_metadata(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """在指定元数据源搜索作品（TMDB/TVDB/Bangumi/豆瓣/IMDb 等）。"""
    provider = (arguments.get("provider") or "").strip()
    keyword = (arguments.get("keyword") or "").strip()
    if not provider or not keyword:
        return {"error": "需要 provider 与 keyword"}
    media_type = arguments.get("mediaType")
    manager = context.get("metadata_manager")
    if not manager:
        return {"error": "元数据源管理器不可用"}
    try:
        results = await manager.search(provider, keyword, _ASSISTANT_USER, mediaType=media_type)
    except Exception as e:  # noqa: BLE001
        return {"error": f"搜索失败：{e}"}

    simplified = []
    for r in (results or [])[:_MAX_ITEMS]:
        simplified.append({
            "id": getattr(r, "id", None),
            "title": getattr(r, "title", None),
            "type": getattr(r, "type", None),
            "season": getattr(r, "season", None),
            "year": getattr(r, "year", None),
            "details": getattr(r, "details", None),
        })
    return {"provider": provider, "total": len(results or []), "results": simplified}


async def _get_metadata_details(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """获取元数据源中某条目的详情（含别名、集数等）。"""
    provider = (arguments.get("provider") or "").strip()
    item_id = str(arguments.get("itemId") or "").strip()
    if not provider or not item_id:
        return {"error": "需要 provider 与 itemId"}
    media_type = arguments.get("mediaType")
    manager = context.get("metadata_manager")
    if not manager:
        return {"error": "元数据源管理器不可用"}
    try:
        detail = await manager.get_details(provider, item_id, _ASSISTANT_USER, mediaType=media_type)
    except Exception as e:  # noqa: BLE001
        return {"error": f"获取详情失败：{e}"}
    if not detail:
        return {"error": "未获取到详情（可能 ID 不存在或网络异常）"}
    return {"provider": provider, "itemId": item_id, "detail": detail.model_dump()
            if hasattr(detail, "model_dump") else str(detail)}


# ────────────────────────────────────────────────────────────
# D组：密钥管理（写入 / 验证 / 查看状态）
# ────────────────────────────────────────────────────────────

async def _get_key_status(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """查看某元数据源的密钥配置状态：仅返回「是否已配置 + 掩码」，永不返回明文。"""
    provider = (arguments.get("provider") or "").strip()
    if not provider:
        return {"error": "缺少 provider"}
    manager = context.get("metadata_manager")
    if not manager:
        return {"error": "元数据源管理器不可用"}
    try:
        raw = await manager.getProviderConfig(provider)
    except ValueError as e:
        return {"error": f"未找到元数据源 {provider}：{e}"}

    keys_status = []
    for k, v in (raw or {}).items():
        if not _is_secret_config_key(k):
            continue
        configured = bool(v and str(v).strip())
        keys_status.append({
            "configKey": k,
            "configured": configured,
            "masked": _mask_secret(v) if configured else "(未配置)",
        })
    return {
        "provider": provider,
        "keys": keys_status,
        "note": "只能看到掩码，明文不可读取。",
    }


async def _verify_metadata_source_key(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """验证某元数据源当前配置的密钥是否有效（实发请求探测连通性与鉴权）。"""
    provider = (arguments.get("provider") or "").strip()
    if not provider:
        return {"error": "缺少 provider"}
    manager = context.get("metadata_manager")
    if not manager:
        return {"error": "元数据源管理器不可用"}

    source = manager.get_source(provider) if hasattr(manager, "get_source") else None
    if not source:
        return {"error": f"未找到或未加载元数据源：{provider}"}
    if not hasattr(source, "check_connectivity"):
        return {"error": f"元数据源 {provider} 不支持连通性检测"}

    try:
        result = await source.check_connectivity()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"验证 {provider} 密钥失败: {e}")
        return {"provider": provider, "valid": False, "message": f"检测异常：{e}"}

    # check_connectivity 返回 {"message": ..., "code": "success|error|..."}
    if isinstance(result, dict):
        code = result.get("code", "error")
        return {
            "provider": provider,
            "valid": code == "success",
            "statusCode": code,
            "message": result.get("message", ""),
        }
    return {"provider": provider, "valid": False, "message": str(result)}


async def _set_metadata_source_key(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """写入某元数据源的密钥（WRITE，需用户确认）。写入后自动验证有效性。

    只写入指定的单个 configKey，不动其它配置项，避免误覆盖。
    返回中不回显明文，只给掩码与验证结果。
    """
    provider = (arguments.get("provider") or "").strip()
    config_key = (arguments.get("configKey") or "").strip()
    value = arguments.get("value")
    if not provider or not config_key:
        return {"error": "需要 provider 与 configKey"}
    if value is None or not str(value).strip():
        return {"error": "value 不能为空"}
    if not _is_secret_config_key(config_key):
        return {"error": f"{config_key} 不是密钥类配置项，本工具只用于写密钥"}

    manager = context.get("metadata_manager")
    if not manager:
        return {"error": "元数据源管理器不可用"}

    # 先取现有配置，只覆盖目标 key，避免整体覆盖丢失其它设置
    try:
        current = await manager.getProviderConfig(provider) or {}
    except ValueError as e:
        return {"error": f"未找到元数据源 {provider}：{e}"}
    if config_key not in current:
        return {"error": f"{provider} 不存在配置项 {config_key}，可用项：{list(current.keys())}"}

    new_value = str(value).strip()
    try:
        await manager.updateProviderConfig(provider, {**current, config_key: new_value})
    except Exception as e:  # noqa: BLE001
        logger.error(f"写入 {provider}.{config_key} 失败: {e}", exc_info=True)
        return {"error": f"写入失败：{e}"}

    # 写入后立即验证
    verify = await _verify_metadata_source_key({"provider": provider}, context)
    return {
        "ok": True,
        "provider": provider,
        "configKey": config_key,
        "written": _mask_secret(new_value),
        "verify": verify,
        "message": "密钥已写入并完成验证（明文不回显）",
    }


# ────────────────────────────────────────────────────────────
# 工具注册
# ────────────────────────────────────────────────────────────

def register_metadata_tools() -> None:
    """注册 A组（元数据源查询）+ D组（密钥管理）工具。"""
    # A组：元数据源管理（只读）
    registry.register(Tool(
        name="list_metadata_sources",
        description="列出所有元数据源及其启用状态、连接状态（TMDB/TVDB/Bangumi/豆瓣/IMDb 等）。",
        parameters={"type": "object", "properties": {}},
        permission=ToolPermission.READ_ONLY,
        executor=_list_metadata_sources,
        running_label="正在查询元数据源列表",
    ))
    registry.register(Tool(
        name="get_metadata_source_config",
        description="查看某元数据源的配置项（密钥类字段自动掩码，不返回明文）。provider 可选: tmdb / tvdb / bangumi / douban / imdb 等。",
        parameters={
            "type": "object",
            "properties": {
                "provider": {"type": "string", "description": "元数据源名称（如 tmdb / tvdb）"},
            },
            "required": ["provider"],
        },
        permission=ToolPermission.READ_ONLY,
        executor=_get_metadata_source_config,
        running_label="正在查询元数据源配置",
    ))
    registry.register(Tool(
        name="search_metadata",
        description="在指定元数据源搜索作品。provider 可选: tmdb / tvdb / bangumi / douban / imdb 等。mediaType 可选: tv_series / movie。",
        parameters={
            "type": "object",
            "properties": {
                "provider": {"type": "string", "description": "元数据源名称（如 tmdb）"},
                "keyword": {"type": "string", "description": "搜索关键词（作品名）"},
                "mediaType": {"type": "string", "description": "媒体类型（可选）：tv_series 或 movie"},
            },
            "required": ["provider", "keyword"],
        },
        permission=ToolPermission.READ_ONLY,
        executor=_search_metadata,
        running_label="正在搜索元数据",
    ))
    registry.register(Tool(
        name="get_metadata_details",
        description="获取元数据源中某条目的详情（别名、集数、年份等）。itemId 是搜索结果里的 id 字段。",
        parameters={
            "type": "object",
            "properties": {
                "provider": {"type": "string", "description": "元数据源名称"},
                "itemId": {"type": "string", "description": "条目 ID（来自 search_metadata）"},
                "mediaType": {"type": "string", "description": "媒体类型（可选）"},
            },
            "required": ["provider", "itemId"],
        },
        permission=ToolPermission.READ_ONLY,
        executor=_get_metadata_details,
        running_label="正在获取元数据详情",
    ))

    # D组：密钥管理（读 + 写 + 验证）
    registry.register(Tool(
        name="get_key_status",
        description="查看某元数据源的密钥配置状态：仅返回是否已配置 + 掩码（前4***后4），永不返回明文。",
        parameters={
            "type": "object",
            "properties": {
                "provider": {"type": "string", "description": "元数据源名称"},
            },
            "required": ["provider"],
        },
        permission=ToolPermission.READ_ONLY,
        executor=_get_key_status,
        running_label="正在查询密钥状态",
    ))
    registry.register(Tool(
        name="verify_metadata_source_key",
        description="验证某元数据源当前配置的密钥是否有效（实发请求探测连通性与鉴权）。返回布尔结果与状态码。",
        parameters={
            "type": "object",
            "properties": {
                "provider": {"type": "string", "description": "元数据源名称"},
            },
            "required": ["provider"],
        },
        permission=ToolPermission.READ_ONLY,
        executor=_verify_metadata_source_key,
        running_label="正在验证密钥有效性",
    ))
    registry.register(Tool(
        name="set_metadata_source_key",
        description="写入某元数据源的密钥（WRITE 权限，需用户确认）。写入后自动验证有效性。configKey 如 tmdbApiKey / tvdbApiKey 等。",
        parameters={
            "type": "object",
            "properties": {
                "provider": {"type": "string", "description": "元数据源名称"},
                "configKey": {"type": "string", "description": "密钥配置项名（如 tmdbApiKey）"},
                "value": {"type": "string", "description": "密钥明文值"},
            },
            "required": ["provider", "configKey", "value"],
        },
        permission=ToolPermission.WRITE,
        executor=_set_metadata_source_key,
        running_label="正在写入密钥并验证",
    ))
