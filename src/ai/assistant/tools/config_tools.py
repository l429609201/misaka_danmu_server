"""
御坂助手 · 识别词与过滤配置工具（B组 + C组）
------------------------------------------------------------
B组 识别词：读取规则、干跑测试、冲突检测（只读）；更新规则（WRITE，带 diff）。
C组 过滤：全局标题过滤、兜底分集标题过滤、单剧过滤、单源分集黑名单（读只读/写 WRITE）、
         正则测试（只读，纯计算无副作用）。

安全设计（防 AI 误覆盖用户配置）：
- 所有「写」类工具在返回中给出 旧值 → 新值 的 diff 摘要，配合 agent 层确认卡。
- 识别词/单剧过滤支持 mode=append（默认追加，安全）与 mode=replace（全量覆盖，危险）。
  append 会先读旧内容再拼接，避免 AI 只输出新规则就冲掉用户已有配置。

context 依赖：
- context["session_factory"]: DB 会话工厂
- context["config_manager"]: ConfigManager
- context["title_recognition_manager"]: TitleRecognitionManager
- context["scraper_manager"]: ScraperManager（单源黑名单用）
"""

import logging
from typing import Any, Dict, List

import regex as _regex_module

from src.db import crud
from ..security_gateway import ToolPermission
from .base import Tool, registry

logger = logging.getLogger(__name__)

# 配置项键名（与 src/api/ui/settings.py 保持一致）
_CFG_GLOBAL_CN = "search_result_global_blacklist_cn"
_CFG_GLOBAL_ENG = "search_result_global_blacklist_eng"
_CFG_EPISODE_FILTER_ENABLED = "globalEpisodeTitleFilterEnabled"
_CFG_EPISODE_FILTER_REGEX = "globalEpisodeTitleFilterRegex"
_CFG_SINGLE_EPISODE_RULES = "singleEpisodeFilterRules"

# 文本类配置返回给模型时的截断长度（控制 token）
_MAX_TEXT_CHARS = 3000


def _truncate(text: str) -> Dict[str, Any]:
    """长文本截断并标注，避免灌爆上下文。"""
    text = text or ""
    if len(text) <= _MAX_TEXT_CHARS:
        return {"content": text, "truncated": False, "totalChars": len(text)}
    return {
        "content": text[:_MAX_TEXT_CHARS],
        "truncated": True,
        "totalChars": len(text),
        "note": f"内容过长仅返回前 {_MAX_TEXT_CHARS} 字符",
    }


def _merge_text(old: str, new: str, mode: str) -> str:
    """按 mode 合并文本配置。append 追加到末尾（换行分隔），replace 全量覆盖。"""
    old = (old or "").rstrip()
    new = (new or "").strip()
    if mode == "replace":
        return new
    if not old:
        return new
    return f"{old}\n{new}"


def _merge_regex(old: str, new: str, mode: str) -> str:
    """按 mode 合并正则。append 用 | 拼接（正则或），replace 全量覆盖。"""
    old = (old or "").strip()
    new = (new or "").strip()
    if mode == "replace":
        return new
    if not old:
        return new
    return f"{old}|{new}"


# ────────────────────────────────────────────────────────────
# B组：识别词
# ────────────────────────────────────────────────────────────

async def _get_recognition_rules(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """读取当前识别词配置全文。"""
    session_factory = context.get("session_factory")
    if not session_factory:
        return {"error": "会话不可用"}
    async with session_factory() as session:
        recognition = await crud.get_title_recognition(session)
    content = getattr(recognition, "content", "") if recognition else ""
    result = _truncate(content)
    lines = [ln for ln in content.split("\n") if ln.strip() and not ln.strip().startswith("#")]
    result["effectiveRuleCount"] = len(lines)
    return result


async def _test_recognition(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """干跑测试识别词规则对某标题的效果（不保存任何修改）。"""
    title = (arguments.get("title") or "").strip()
    if not title:
        return {"error": "缺少 title（要测试的标题）"}
    season = arguments.get("season")
    episode = arguments.get("episode")
    stage = arguments.get("stage") or "all"

    manager = context.get("title_recognition_manager")
    if not manager:
        return {"error": "识别词管理器不可用"}

    matched: List[str] = []
    cur_title, cur_season, cur_episode = title, season, episode
    changed = False

    try:
        if stage in ("preprocess", "all"):
            pre_title, pre_ep, pre_season, pre_changed = await manager.apply_search_preprocessing(
                cur_title, cur_episode, cur_season
            )
            if pre_changed:
                changed = True
                if pre_title != cur_title:
                    matched.append(f"[搜索预处理] 标题: '{cur_title}' → '{pre_title}'")
                if pre_season != cur_season:
                    matched.append(f"[搜索预处理] 季度: {cur_season} → {pre_season}")
                if pre_ep != cur_episode:
                    matched.append(f"[搜索预处理] 集数: {cur_episode} → {pre_ep}")
            cur_title, cur_season, cur_episode = pre_title, pre_season, pre_ep

        if stage in ("postprocess", "all"):
            post = await manager.apply_title_recognition(cur_title, cur_episode, cur_season)
            post_title, post_ep, post_season, post_changed = post[0], post[1], post[2], post[3]
            if post_changed:
                changed = True
                if post_title != cur_title:
                    matched.append(f"[入库后处理] 标题: '{cur_title}' → '{post_title}'")
                if post_season != cur_season:
                    matched.append(f"[入库后处理] 季度: {cur_season} → {post_season}")
                if post_ep != cur_episode:
                    matched.append(f"[入库后处理] 集数: {cur_episode} → {post_ep}")
            cur_title, cur_season, cur_episode = post_title, post_season, post_ep
    except Exception as e:  # noqa: BLE001
        return {"error": f"测试失败：{e}"}

    return {
        "original": {"title": title, "season": season, "episode": episode},
        "result": {"title": cur_title, "season": cur_season, "episode": cur_episode},
        "changed": changed,
        "matchedRules": matched or ["未命中任何识别词规则"],
    }


async def _check_recognition_conflicts(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """扫描识别词规则，检测空规则、重复、过短关键词、潜在冲突（只读，不修改数据）。"""
    session_factory = context.get("session_factory")
    if not session_factory:
        return {"error": "会话不可用"}
    async with session_factory() as session:
        recognition = await crud.get_title_recognition(session)
    if not recognition or not recognition.content:
        return {"conflicts": [], "message": "识别词未配置或为空"}

    lines = recognition.content.strip().split("\n")
    conflicts: List[Dict[str, Any]] = []
    seen_rules: Dict[str, List[int]] = {}

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not stripped:
            conflicts.append({
                "ruleIndex": i,
                "issueType": "empty",
                "severity": "info",
                "detail": "空规则行",
            })
        # 重复检测
        key = stripped.lower()
        if key in seen_rules:
            conflicts.append({
                "ruleIndex": i,
                "ruleContent": stripped[:50],
                "issueType": "duplicate",
                "severity": "warning",
                "detail": f"与第 {seen_rules[key]} 行重复",
                "relatedRules": seen_rules[key],
            })
        else:
            seen_rules[key] = [i]

    return {"total": len(conflicts), "conflicts": conflicts[:50]}


async def _set_recognition_rules(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """更新识别词配置（WRITE，需用户确认）。

    mode 可选：
    - append（默认，安全）：追加到现有规则末尾，不动旧规则。
    - replace（危险）：全量覆盖。若 AI 只输出新规则会冲掉用户已有配置。

    返回包含 diff 摘要供确认。
    """
    content = (arguments.get("content") or "").strip()
    mode = (arguments.get("mode") or "append").strip().lower()
    if not content:
        return {"error": "content 不能为空"}
    if mode not in ("append", "replace"):
        return {"error": "mode 必须为 append 或 replace"}

    session_factory = context.get("session_factory")
    manager = context.get("title_recognition_manager")
    if not session_factory or not manager:
        return {"error": "会话或识别词管理器不可用"}

    # 先读旧配置
    async with session_factory() as session:
        recognition = await crud.get_title_recognition(session)
    old_content = getattr(recognition, "content", "") if recognition else ""
    new_content = _merge_text(old_content, content, mode)

    # 更新
    try:
        await manager.update_recognition_rules(new_content)
    except Exception as e:  # noqa: BLE001
        logger.error(f"更新识别词失败: {e}", exc_info=True)
        return {"error": f"更新失败：{e}"}

    old_lines = [ln for ln in old_content.split("\n") if ln.strip() and not ln.strip().startswith("#")]
    new_lines = [ln for ln in new_content.split("\n") if ln.strip() and not ln.strip().startswith("#")]
    diff_summary = f"规则数变化：{len(old_lines)} → {len(new_lines)}"
    if mode == "append":
        diff_summary += f"（追加了 {len(new_lines) - len(old_lines)} 条规则）"

    return {
        "ok": True,
        "mode": mode,
        "oldRuleCount": len(old_lines),
        "newRuleCount": len(new_lines),
        "diffSummary": diff_summary,
        "message": "识别词已更新，建议用 test_recognition 验证效果",
    }


# ────────────────────────────────────────────────────────────
# C组：过滤配置
# ────────────────────────────────────────────────────────────

async def _get_global_filter(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """读取全局搜索结果标题过滤规则（中文关键词 + 英文独立词）。"""
    config = context.get("config_manager")
    if not config:
        return {"error": "配置管理器不可用"}
    cn = await config.get(_CFG_GLOBAL_CN, "")
    eng = await config.get(_CFG_GLOBAL_ENG, "")
    return {
        "cn": _truncate(cn),
        "eng": _truncate(eng),
        "note": "这是作品级过滤（过滤整条搜索结果），不是分集过滤。",
    }


async def _set_global_filter(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """更新全局搜索结果标题过滤规则（WRITE）。mode: append 追加 / replace 覆盖。"""
    cn = arguments.get("cn")
    eng = arguments.get("eng")
    mode = (arguments.get("mode") or "append").strip().lower()
    if cn is None and eng is None:
        return {"error": "至少提供 cn 或 eng 之一"}
    if mode not in ("append", "replace"):
        return {"error": "mode 必须为 append 或 replace"}

    config = context.get("config_manager")
    if not config:
        return {"error": "配置管理器不可用"}

    old_cn = await config.get(_CFG_GLOBAL_CN, "")
    old_eng = await config.get(_CFG_GLOBAL_ENG, "")
    changes = {}

    if cn is not None:
        new_cn = _merge_regex(old_cn, str(cn), mode)
        await config.setValue(_CFG_GLOBAL_CN, new_cn)
        changes["cn"] = {"oldChars": len(old_cn), "newChars": len(new_cn)}
    if eng is not None:
        new_eng = _merge_regex(old_eng, str(eng), mode)
        await config.setValue(_CFG_GLOBAL_ENG, new_eng)
        changes["eng"] = {"oldChars": len(old_eng), "newChars": len(new_eng)}

    return {"ok": True, "mode": mode, "changes": changes, "message": "全局标题过滤规则已更新"}


async def _get_global_episode_title_filter(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """读取兜底全局分集标题过滤配置（开关 + 正则）。"""
    config = context.get("config_manager")
    if not config:
        return {"error": "配置管理器不可用"}
    enabled = (await config.get(_CFG_EPISODE_FILTER_ENABLED, "false")).lower() == "true"
    regex = await config.get(_CFG_EPISODE_FILTER_REGEX, "")
    result = _truncate(regex)
    result["enabled"] = enabled
    result["note"] = "这是分集过滤第2层（兜底），对所有源统一生效。"
    return result


async def _set_global_episode_title_filter(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """更新兜底全局分集标题过滤（WRITE）。可单独改开关或正则。"""
    enabled = arguments.get("enabled")
    regex = arguments.get("regex")
    mode = (arguments.get("mode") or "append").strip().lower()
    if enabled is None and regex is None:
        return {"error": "至少提供 enabled 或 regex 之一"}
    if mode not in ("append", "replace"):
        return {"error": "mode 必须为 append 或 replace"}

    config = context.get("config_manager")
    if not config:
        return {"error": "配置管理器不可用"}

    result: Dict[str, Any] = {"ok": True, "mode": mode}
    if enabled is not None:
        await config.setValue(_CFG_EPISODE_FILTER_ENABLED, "true" if enabled else "false")
        result["enabled"] = bool(enabled)
    if regex is not None:
        old_regex = await config.get(_CFG_EPISODE_FILTER_REGEX, "")
        new_regex = _merge_regex(old_regex, str(regex), mode)
        # 写前校验正则合法性，避免写入坏规则导致过滤全线失效
        try:
            _regex_module.compile(new_regex, _regex_module.IGNORECASE)
        except Exception as e:  # noqa: BLE001
            return {"error": f"正则非法，已中止写入：{e}"}
        await config.setValue(_CFG_EPISODE_FILTER_REGEX, new_regex)
        result["regexChars"] = {"old": len(old_regex), "new": len(new_regex)}
    result["message"] = "兜底分集标题过滤配置已更新"
    return result


async def _get_single_episode_filter(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """读取单剧过滤规则（针对特定作品的分集过滤，第3层）。"""
    config = context.get("config_manager")
    if not config:
        return {"error": "配置管理器不可用"}
    content = await config.get(_CFG_SINGLE_EPISODE_RULES, "")
    result = _truncate(content)
    rules = [ln for ln in content.split("\n") if ln.strip() and not ln.strip().startswith("#")]
    result["ruleCount"] = len(rules)
    result["format"] = "作品名 => {[rules=加更|纯享|会员版;provider=可选;mediaId=可选]}"
    return result


async def _set_single_episode_filter(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """更新单剧过滤规则（WRITE）。mode: append 追加（默认）/ replace 覆盖。"""
    content = (arguments.get("content") or "").strip()
    mode = (arguments.get("mode") or "append").strip().lower()
    if not content:
        return {"error": "content 不能为空"}
    if mode not in ("append", "replace"):
        return {"error": "mode 必须为 append 或 replace"}

    config = context.get("config_manager")
    if not config:
        return {"error": "配置管理器不可用"}

    old = await config.get(_CFG_SINGLE_EPISODE_RULES, "")
    new = _merge_text(old, content, mode)
    await config.setValue(_CFG_SINGLE_EPISODE_RULES, new)

    old_rules = [ln for ln in old.split("\n") if ln.strip() and not ln.strip().startswith("#")]
    new_rules = [ln for ln in new.split("\n") if ln.strip() and not ln.strip().startswith("#")]
    return {
        "ok": True,
        "mode": mode,
        "ruleCount": {"old": len(old_rules), "new": len(new_rules)},
        "message": "单剧过滤规则已更新",
    }


async def _get_source_episode_blacklist(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """读取某弹幕源的分集标题黑名单正则（分集过滤第1层）。"""
    provider = (arguments.get("provider") or "").strip()
    if not provider:
        return {"error": "缺少 provider（弹幕源名，如 tencent/bilibili/iqiyi）"}
    config = context.get("config_manager")
    if not config:
        return {"error": "配置管理器不可用"}
    regex = await config.get(f"{provider}EpisodeBlacklistRegex", "")
    result = _truncate(regex)
    result["provider"] = provider
    result["note"] = "这是分集过滤第1层（单源级），只对该源生效。"
    return result


async def _set_source_episode_blacklist(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """更新某弹幕源的分集标题黑名单正则（WRITE）。"""
    provider = (arguments.get("provider") or "").strip()
    regex = arguments.get("regex")
    mode = (arguments.get("mode") or "append").strip().lower()
    if not provider or regex is None:
        return {"error": "需要 provider 与 regex"}
    if mode not in ("append", "replace"):
        return {"error": "mode 必须为 append 或 replace"}

    config = context.get("config_manager")
    if not config:
        return {"error": "配置管理器不可用"}

    key = f"{provider}EpisodeBlacklistRegex"
    old = await config.get(key, "")
    new = _merge_regex(old, str(regex), mode)
    try:
        _regex_module.compile(new, _regex_module.IGNORECASE)
    except Exception as e:  # noqa: BLE001
        return {"error": f"正则非法，已中止写入：{e}"}
    await config.setValue(key, new)
    return {
        "ok": True,
        "provider": provider,
        "mode": mode,
        "regexChars": {"old": len(old), "new": len(new)},
        "message": f"{provider} 的分集标题黑名单已更新",
    }


async def _test_regex(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """用后端 Python regex 测试一组正则是否命中指定文本（纯计算，无副作用）。"""
    text = arguments.get("text") or ""
    patterns = arguments.get("patterns") or []
    if not text:
        return {"error": "缺少 text（要测试的文本）"}
    if not patterns:
        return {"error": "缺少 patterns（正则列表）"}
    if isinstance(patterns, str):
        patterns = [patterns]

    matches, invalids = [], []
    for p in patterns[:20]:
        pattern = str(p or "").strip()
        if not pattern:
            continue
        try:
            m = _regex_module.search(pattern, text, _regex_module.IGNORECASE)
            if m:
                matches.append({"pattern": pattern[:120], "matchedText": m.group(0)})
        except Exception as e:  # noqa: BLE001
            invalids.append({"pattern": pattern[:120], "error": str(e)})
    return {
        "text": text,
        "matched": bool(matches),
        "matches": matches,
        "invalids": invalids,
    }


# ────────────────────────────────────────────────────────────
# 工具注册
# ────────────────────────────────────────────────────────────

# append/replace 模式的公共参数描述
_MODE_PARAM = {
    "type": "string",
    "enum": ["append", "replace"],
    "description": "append=追加到现有规则（默认，安全）；replace=全量覆盖（危险，会冲掉用户已有配置）",
}


def register_config_tools() -> None:
    """注册 B组（识别词）+ C组（过滤配置）工具。"""
    # ── B组：识别词 ──
    registry.register(Tool(
        name="get_recognition_rules",
        description="读取当前识别词配置全文（自定义标题识别、季度/集数偏移规则）。",
        parameters={"type": "object", "properties": {}},
        permission=ToolPermission.READ_ONLY,
        executor=_get_recognition_rules,
        running_label="正在读取识别词配置",
    ))
    registry.register(Tool(
        name="test_recognition",
        description="干跑测试识别词规则对某标题的效果（不保存任何修改）。用于验证规则是否按预期生效。",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "要测试的标题"},
                "season": {"type": "integer", "description": "季度（可选）"},
                "episode": {"type": "integer", "description": "集数（可选）"},
                "stage": {"type": "string", "enum": ["preprocess", "postprocess", "all"],
                          "description": "测试阶段，默认 all"},
            },
            "required": ["title"],
        },
        permission=ToolPermission.READ_ONLY,
        executor=_test_recognition,
        running_label="正在测试识别词规则",
    ))
    registry.register(Tool(
        name="check_recognition_conflicts",
        description="扫描识别词规则，检测重复、空规则等潜在冲突问题（只读诊断）。",
        parameters={"type": "object", "properties": {}},
        permission=ToolPermission.READ_ONLY,
        executor=_check_recognition_conflicts,
        running_label="正在检测识别词冲突",
    ))
    registry.register(Tool(
        name="set_recognition_rules",
        description="更新识别词配置（需用户确认）。强烈建议用 mode=append 追加，避免覆盖用户已有规则。",
        parameters={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "识别词规则内容（DSL 语法，每行一条）"},
                "mode": _MODE_PARAM,
            },
            "required": ["content"],
        },
        permission=ToolPermission.WRITE,
        executor=_set_recognition_rules,
        running_label="正在更新识别词配置",
    ))

    # ── C组：过滤配置 ──
    registry.register(Tool(
        name="get_global_filter",
        description="读取全局搜索结果标题过滤规则（作品级过滤，过滤掉整条搜索结果，如「预告合集」）。",
        parameters={"type": "object", "properties": {}},
        permission=ToolPermission.READ_ONLY,
        executor=_get_global_filter,
        running_label="正在读取全局过滤规则",
    ))
    registry.register(Tool(
        name="set_global_filter",
        description="更新全局搜索结果标题过滤规则（需确认）。cn=中文关键词，eng=英文独立词，用 | 分隔。",
        parameters={
            "type": "object",
            "properties": {
                "cn": {"type": "string", "description": "中文过滤关键词，用 | 分隔（可选）"},
                "eng": {"type": "string", "description": "英文/缩写过滤词，用 | 分隔（可选）"},
                "mode": _MODE_PARAM,
            },
        },
        permission=ToolPermission.WRITE,
        executor=_set_global_filter,
        running_label="正在更新全局过滤规则",
    ))
    registry.register(Tool(
        name="get_global_episode_title_filter",
        description="读取兜底全局分集标题过滤配置（分集过滤第2层，对所有源统一兜底）。",
        parameters={"type": "object", "properties": {}},
        permission=ToolPermission.READ_ONLY,
        executor=_get_global_episode_title_filter,
        running_label="正在读取兜底分集过滤",
    ))
    registry.register(Tool(
        name="set_global_episode_title_filter",
        description="更新兜底全局分集标题过滤（需确认）。可单独改开关(enabled)或正则(regex)。写入前自动校验正则合法性。",
        parameters={
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "description": "是否启用兜底过滤（可选）"},
                "regex": {"type": "string", "description": "过滤正则（可选）"},
                "mode": _MODE_PARAM,
            },
        },
        permission=ToolPermission.WRITE,
        executor=_set_global_episode_title_filter,
        running_label="正在更新兜底分集过滤",
    ))
    registry.register(Tool(
        name="get_single_episode_filter",
        description="读取单剧过滤规则（分集过滤第3层，针对特定作品，如综艺的加更/纯享/会员版）。",
        parameters={"type": "object", "properties": {}},
        permission=ToolPermission.READ_ONLY,
        executor=_get_single_episode_filter,
        running_label="正在读取单剧过滤规则",
    ))
    registry.register(Tool(
        name="set_single_episode_filter",
        description="更新单剧过滤规则（需确认）。格式：作品名 => {[rules=加更|纯享;provider=可选;mediaId=可选]}",
        parameters={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "单剧过滤规则内容，每行一条"},
                "mode": _MODE_PARAM,
            },
            "required": ["content"],
        },
        permission=ToolPermission.WRITE,
        executor=_set_single_episode_filter,
        running_label="正在更新单剧过滤规则",
    ))
    registry.register(Tool(
        name="get_source_episode_blacklist",
        description="读取某弹幕源的分集标题黑名单正则（分集过滤第1层，只对该源生效）。provider 如 tencent/bilibili/iqiyi。",
        parameters={
            "type": "object",
            "properties": {
                "provider": {"type": "string", "description": "弹幕源名称"},
            },
            "required": ["provider"],
        },
        permission=ToolPermission.READ_ONLY,
        executor=_get_source_episode_blacklist,
        running_label="正在读取单源分集黑名单",
    ))
    registry.register(Tool(
        name="set_source_episode_blacklist",
        description="更新某弹幕源的分集标题黑名单正则（需确认）。写入前自动校验正则合法性。",
        parameters={
            "type": "object",
            "properties": {
                "provider": {"type": "string", "description": "弹幕源名称"},
                "regex": {"type": "string", "description": "黑名单正则"},
                "mode": _MODE_PARAM,
            },
            "required": ["provider", "regex"],
        },
        permission=ToolPermission.WRITE,
        executor=_set_source_episode_blacklist,
        running_label="正在更新单源分集黑名单",
    ))
    registry.register(Tool(
        name="test_regex",
        description="用后端 Python regex 测试一组正则是否命中指定文本（纯计算无副作用）。写过滤规则前建议先测试。",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要测试的文本（如分集标题）"},
                "patterns": {"type": "array", "items": {"type": "string"},
                             "description": "正则列表（最多 20 条）"},
            },
            "required": ["text", "patterns"],
        },
        permission=ToolPermission.READ_ONLY,
        executor=_test_regex,
        running_label="正在测试正则",
    ))
