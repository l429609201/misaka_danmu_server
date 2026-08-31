"""
御坂助手 · 通用配置工具
------------------------------------------------------------
提供读写任意配置项的能力（基于白名单+类型校验）。

设计原则：
1. 读取（get_config）：任何白名单内配置均可读，密钥类字段自动脱敏
2. 写入（set_config）：白名单 + 类型校验 + 枚举校验 + 范围校验，确保安全
3. 与 HTTP API 白名单（settings_routes.ALLOWED_CONFIG_KEYS）职责分离：
   - HTTP API 白名单面向外部 Webhook/集成，覆盖控制面配置
   - 本工具白名单面向 AI 助手，覆盖用户常调参数（弹幕输出/AI/文件路径等）

context 依赖：
- context["session_factory"]: DB 会话工厂
- context["config_manager"]: ConfigManager
"""

import logging
from typing import Any, Dict

from ..security_gateway import ToolPermission
from .base import Tool, registry

logger = logging.getLogger(__name__)

# AI 助手可读写的配置白名单（键名均已与 src/core/default_configs.py 核对）
# 只收录"用户会让助手帮忙调"的参数；密钥、域名、容器名等敏感/危险项不放进来
ASSISTANT_CONFIG_WHITELIST = {
    # ---------- 弹幕输出与转换 ----------
    "danmakuOutputLimitPerSource": {
        "type": "integer",
        "description": "单源弹幕输出上限，-1 为无限制；超出时按时间段均匀采样",
        "min": -1,
        "max": 100000,
    },
    "danmakuMergeOutputEnabled": {
        "type": "boolean",
        "description": "是否合并所有源的弹幕后再统一采样输出",
    },
    "danmakuChConvert": {
        "type": "enum",
        "description": "简繁转换：0 不转换 / 1 转为简体 / 2 转为繁体",
        "values": ["0", "1", "2"],
    },
    "danmakuChConvertPriority": {
        "type": "enum",
        "description": "简繁转换优先级：player 播放器优先 / server 服务端优先",
        "values": ["player", "server"],
    },
    "danmakuTopConvertTo": {
        "type": "enum",
        "description": "顶部弹幕(mode=5)转换目标：none 不转 / bottom 转底部 / scroll 转滚动",
        "values": ["none", "bottom", "scroll"],
    },
    "danmakuBottomConvertTo": {
        "type": "enum",
        "description": "底部弹幕(mode=4)转换目标：none 不转 / top 转顶部 / scroll 转滚动",
        "values": ["none", "top", "scroll"],
    },
    # ---------- 点赞显示 ----------
    "danmakuLikesFetchEnabled": {
        "type": "boolean",
        "description": "下载弹幕时是否获取并存储点赞数据（关闭后新下载的弹幕永久无点赞信息）",
    },
    "danmakuLikesOutputEnabled": {
        "type": "boolean",
        "description": "输出时是否显示点赞状态（数据仍保留，仅控制显示）",
    },
    "danmakuLikesStyle": {
        "type": "enum",
        "description": "点赞显示样式，danmakuLikesOutputEnabled=false 时无效",
        "values": ["heart_white", "heart_red", "heart_outline", "like_bracket", "text", "num_only"],
    },
    # ---------- 随机染色 ----------
    "danmakuRandomColorMode": {
        "type": "enum",
        "description": "弹幕颜色模式：off 不用 / white_to_random 只染白色 / all_random 全部随机 / all_white 全部变白",
        "values": ["off", "white_to_random", "all_random", "all_white"],
    },
    "danmakuRandomColorPalette": {
        "type": "text",
        "description": "随机颜色色板，逗号分隔的十进制颜色值（#FFFFFF = 16777215）",
    },
    # ---------- 弹幕内容黑名单 ----------
    "danmakuBlacklistEnabled": {
        "type": "boolean",
        "description": "是否启用弹幕内容黑名单过滤",
    },
    "danmakuBlacklistPatterns": {
        "type": "text",
        "description": "弹幕黑名单正则，| 分隔。系统内置数百条规则，追加时务必先读取原值再拼接",
    },
    # ---------- 自动刷新 ----------
    "danmakuAutoRefreshDays": {
        "type": "integer",
        "description": "弹幕超过多少天自动重抓，0 为禁用",
        "min": 0,
        "max": 3650,
    },
    "danmakuRefreshThreshold": {
        "type": "integer",
        "description": "自动刷新的条数阈值，仅当该集弹幕低于此值才重抓，0 为不限条数",
        "min": 0,
        "max": 1000000,
    },
    # ---------- 弹幕文件路径与命名 ----------
    "customDanmakuPathEnabled": {
        "type": "boolean",
        "description": "是否启用自定义弹幕文件路径（关闭时下面四项均无效）",
    },
    "movieDanmakuDirectoryPath": {
        "type": "string",
        "description": "电影/剧场版弹幕文件根目录",
    },
    "movieDanmakuFilenameTemplate": {
        "type": "string",
        "description": "电影命名模板，可用变量 ${title} ${titleBase} ${season} ${episode} ${year} ${provider} ${animeId} ${episodeId} ${sourceId} ${tmdbId}",
    },
    "tvDanmakuDirectoryPath": {
        "type": "string",
        "description": "电视节目弹幕文件根目录",
    },
    "tvDanmakuFilenameTemplate": {
        "type": "string",
        "description": "电视节目命名模板，变量同电影模板；必须含唯一标识避免文件互相覆盖",
    },
    # ---------- AI 功能开关 ----------
    "aiMatchEnabled": {
        "type": "boolean",
        "description": "AI 智能匹配开关（自动匹配场景中用 AI 选最佳搜索结果）",
    },
    "aiFallbackEnabled": {
        "type": "boolean",
        "description": "AI 匹配失败时是否降级到传统算法（建议保持开启）",
    },
    "aiRecognitionEnabled": {
        "type": "boolean",
        "description": "AI 辅助识别标题与季度开关",
    },
    "aiAliasCorrectionEnabled": {
        "type": "boolean",
        "description": "AI 别名验证与修正开关",
    },
    "aiAliasExpansionEnabled": {
        "type": "boolean",
        "description": "AI 别名扩展开关（非中文标题时生成可能的中文别名）",
    },
    "aiNameConversionEnabled": {
        "type": "boolean",
        "description": "AI 名称转换兜底开关（元数据源查询失败时启用）",
    },
    "aiEpisodeGroupEnabled": {
        "type": "boolean",
        "description": "AI 剧集组自动选择开关",
    },
    "aiThinkingEnabled": {
        "type": "boolean",
        "description": "DeepSeek 思考模式，提升准确性但显著增加耗时与 token 消耗，仅对 DeepSeek 生效",
    },
    # ---------- AI 性能与成本 ----------
    "aiCacheEnabled": {
        "type": "boolean",
        "description": "AI 响应缓存开关，开启可显著降低 API 成本（建议保持开启）",
    },
    "aiCacheTtl": {
        "type": "integer",
        "description": "AI 缓存过期时间（秒）",
        "min": 60,
        "max": 604800,
    },
    "aiCallTimeout": {
        "type": "integer",
        "description": "AI API 单次请求超时（秒），o3/o4 等慢速推理模型建议 120-300",
        "min": 10,
        "max": 600,
    },
    "aiLogRawResponse": {
        "type": "boolean",
        "description": "是否记录 AI 原始交互到 ai_responses.log（排查异常时开启，查完建议关掉）",
    },
    # ---------- 御坂助手 LLM 参数 ----------
    "assistantTemperature": {
        "type": "float",
        "description": "LLM 温度，0 精确 / 0.7 平衡 / 2 创意。管理类操作建议 0.3-0.7",
        "min": 0.0,
        "max": 2.0,
    },
    "assistantMaxTokens": {
        "type": "integer",
        "description": "单次回答最大输出 token 数",
        "min": 100,
        "max": 8000,
    },
    "assistantTopP": {
        "type": "float",
        "description": "Top-p 采样，控制词汇多样性。一般与 temperature 二选一调整",
        "min": 0.0,
        "max": 1.0,
    },
    "assistantPresencePenalty": {
        "type": "float",
        "description": "存在惩罚，抑制重复话题",
        "min": -2.0,
        "max": 2.0,
    },
    "assistantFrequencyPenalty": {
        "type": "float",
        "description": "频率惩罚，抑制重复用词",
        "min": -2.0,
        "max": 2.0,
    },
    "assistantTimeout": {
        "type": "integer",
        "description": "助手 API 请求超时（秒），慢速模型建议 180-300",
        "min": 10,
        "max": 300,
    },
    "assistantProxyEnabled": {
        "type": "boolean",
        "description": "是否为助手启用代理（复用全局 proxyUrl）",
    },
}

# 密钥类配置键：即使有人误加进白名单，读取时也一律脱敏，绝不回灌明文给模型。
# 这些键当前都不在 ASSISTANT_CONFIG_WHITELIST 里（读不到），此处是纵深防御——
# 万一将来有人往白名单加了敏感项，脱敏这层仍能兜住。
# jwtSecretKey 由 main.py 运行时动态生成，不在 default_configs 中，一并列入。
SENSITIVE_KEYS = {
    "aiApiKey",
    "aiBaseUrl",
    "tmdbApiKey",
    "tvdbApiKey",
    "bangumiClientId",
    "bangumiClientSecret",
    "doubanCookie",
    "bilibiliCookie",
    "gamerCookie",
    "webhookApiKey",
    "externalApiKey",
    "jwtSecretKey",
}


def _validate_and_cast(key: str, value_str: str, meta: Dict[str, Any]) -> Any:
    """根据白名单元数据校验并转换配置值。

    Args:
        key: 配置键名
        value_str: 待校验的值（字符串）
        meta: 白名单中的元数据（type/min/max/values）

    Returns:
        转换后的值（保持字符串类型，供 ConfigManager.setValue 使用）

    Raises:
        ValueError: 校验失败
    """
    cfg_type = meta["type"]

    if cfg_type == "boolean":
        if value_str.lower() not in ("true", "false"):
            raise ValueError(f"{key} 必须为 true 或 false")
        return value_str.lower()

    if cfg_type == "integer":
        try:
            val = int(value_str)
        except ValueError as e:
            raise ValueError(f"{key} 必须为整数") from e
        if "min" in meta and val < meta["min"]:
            raise ValueError(f"{key} 不能小于 {meta['min']}")
        if "max" in meta and val > meta["max"]:
            raise ValueError(f"{key} 不能大于 {meta['max']}")
        return str(val)

    if cfg_type == "float":
        try:
            val = float(value_str)
        except ValueError as e:
            raise ValueError(f"{key} 必须为数字") from e
        if "min" in meta and val < meta["min"]:
            raise ValueError(f"{key} 不能小于 {meta['min']}")
        if "max" in meta and val > meta["max"]:
            raise ValueError(f"{key} 不能大于 {meta['max']}")
        return str(val)

    if cfg_type == "enum":
        allowed = meta.get("values", [])
        if value_str not in allowed:
            raise ValueError(f"{key} 必须为以下之一: {allowed}")
        return value_str

    # string / text 类型：直接返回
    return value_str


async def _get_config(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """读取一个或多个配置项（READ，白名单内均可读）。

    Args:
        keys: 配置键名列表（字符串数组）。若不传或为空，返回全部白名单配置。

    Returns:
        {"configs": [{"key": "xxx", "value": "yyy", "type": "zzz", "description": "..."}, ...]}
        密钥类字段value自动脱敏为 "***<last4>"
    """
    keys = arguments.get("keys") or []
    if not keys:
        keys = list(ASSISTANT_CONFIG_WHITELIST.keys())
    if not isinstance(keys, list):
        return {"error": "keys 参数必须为字符串数组"}

    unknown = [k for k in keys if k not in ASSISTANT_CONFIG_WHITELIST]
    if unknown:
        return {
            "error": f"以下配置键不在白名单中: {unknown}",
            "available": list(ASSISTANT_CONFIG_WHITELIST.keys()),
        }

    config_manager = context.get("config_manager")
    if not config_manager:
        return {"error": "ConfigManager 未初始化"}

    results = []
    for key in keys:
        raw_value = await config_manager.get(key)
        # 脱敏处理
        if key in SENSITIVE_KEYS and raw_value:
            if len(raw_value) > 4:
                display_value = f"***{raw_value[-4:]}"
            else:
                display_value = "***"
        else:
            display_value = raw_value if raw_value is not None else ""

        meta = ASSISTANT_CONFIG_WHITELIST[key]
        results.append({
            "key": key,
            "value": display_value,
            "type": meta["type"],
            "description": meta.get("description", ""),
        })

    return {"configs": results}


async def _set_config(arguments: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """写入单个配置项（WRITE，需白名单+类型校验）。

    Args:
        key: 配置键名
        value: 新值（字符串）

    Returns:
        {"ok": True, "key": "xxx", "oldValue": "aaa", "newValue": "bbb"}
    """
    key = (arguments.get("key") or "").strip()
    value_str = arguments.get("value")
    if not key:
        return {"error": "缺少 key 参数"}
    if value_str is None:
        return {"error": "缺少 value 参数"}

    if key not in ASSISTANT_CONFIG_WHITELIST:
        return {
            "error": f"配置键 {key} 不在白名单中",
            "available": list(ASSISTANT_CONFIG_WHITELIST.keys()),
        }

    meta = ASSISTANT_CONFIG_WHITELIST[key]
    try:
        validated_value = _validate_and_cast(key, str(value_str), meta)
    except ValueError as e:
        return {"error": str(e)}

    config_manager = context.get("config_manager")
    if not config_manager:
        return {"error": "ConfigManager 未初始化"}

    old_value = await config_manager.get(key)
    await config_manager.setValue(key, validated_value)
    logger.info(f"AI助手修改配置: {key} = {validated_value!r}（旧值 {old_value!r}）")

    return {
        "ok": True,
        "key": key,
        "oldValue": old_value if old_value is not None else "",
        "newValue": validated_value,
    }


def register_general_config_tools() -> None:
    """注册通用配置读写工具（由 tools/__init__.py 在导入时调用）。"""
    registry.register(
        Tool(
            name="get_config",
            description=(
                "读取系统配置项的当前值。可一次读多个键，不传 keys 则返回全部可读配置。"
                "密钥类字段自动脱敏。适用于查询弹幕输出、AI 参数、文件路径模板等设置。"
                "改配置前应先用本工具确认现值。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "配置键名列表。不传或空数组则返回全部可读配置。"
                            "示例: ['danmakuOutputLimitPerSource', 'assistantTemperature']"
                        ),
                    },
                },
            },
            executor=_get_config,
            permission=ToolPermission.READ_ONLY,
            running_label="正在读取系统配置…",
        )
    )

    registry.register(
        Tool(
            name="set_config",
            description=(
                "修改单个配置项（需用户确认）。写入前做白名单、类型、范围与枚举校验，"
                "返回 旧值 → 新值 的对比。适用于调整弹幕输出、AI 参数、文件命名模板等。"
                "注意：黑名单这类长文本配置要追加内容时，必须先 get_config 读原值再拼接，"
                "否则会覆盖掉系统内置规则。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "配置键名，如 danmakuOutputLimitPerSource",
                    },
                    "value": {
                        "type": "string",
                        "description": "新值（统一传字符串，工具会按声明类型校验转换）",
                    },
                },
                "required": ["key", "value"],
            },
            executor=_set_config,
            permission=ToolPermission.WRITE,
            running_label="正在修改系统配置…",
        )
    )
