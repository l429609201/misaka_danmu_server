"""
御坂助手 · API 网关策略表（默认拒绝，显式加白）
------------------------------------------------------------
对齐 MoviePilot v3 `app/agent/policy/api.py` 的思路：
模型只能提交 operation_id，由本模块解析为固定的 HTTP 方法与路径，
模型永远不能提供 URL、认证头、Token 或任意 HTTP 方法。

设计原则：
1. **默认拒绝**：未在 EXPOSED_OPERATIONS 显式登记的路由，一律不暴露给 AI。
   这样后端新增接口不会被自动放出去，必须经人工审核加白。
2. **风险分级**：READ_ONLY 直接执行；WRITE 由 agent 先说明再执行；
   DANGEROUS 永不暴露（写在这里只为留档说明为何禁止）。
3. **路径以 api_router 内的原始路径登记**（不含 /api 前缀），
   由 registry 反射校验其真实存在，防止手写笔误静默失效。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from ..security_gateway import ToolPermission
from .contracts import (
    ActionEffect,
    ConfirmationMode,
    ResultSensitivity,
    default_confirmation,
    effect_to_permission,
)


@dataclass(frozen=True)
class ApiOperation:
    """单个白名单 API 操作的固定契约。"""

    operation_id: str          # 稳定标识，AI 只能提交这个
    method: str                # 固定 HTTP 方法，AI 不可指定
    path: str                  # api_router 内路径（不含 /api 前缀）
    summary: str               # 中文用途说明，进入工具描述供 AI 选择
    # ── 三维风险声明（对齐 MoviePilot 的正交风险模型） ──
    effect: ActionEffect       # 副作用类别：决定确认强度与权限档位
    result_sensitivity: ResultSensitivity = ResultSensitivity.NORMAL
    # 确认强度：留空则按 effect 推导（写操作一律 REQUIRED）
    confirmation: Optional[ConfirmationMode] = None
    # 路径参数名 → 中文说明
    path_params: Dict[str, str] = field(default_factory=dict)
    # 查询参数名 → 中文说明
    query_params: Dict[str, str] = field(default_factory=dict)
    # 请求体字段名 → 中文说明（仅用于给 AI 提示，真实校验由路由层 Pydantic 完成）
    body_fields: Dict[str, str] = field(default_factory=dict)
    # 执行成功后给用户的中文提示模板
    success_hint: str = ""
    # ── 明文回传豁免（默认关闭，逐操作显式声明） ──
    # 出口脱敏 sanitize_output 会把 token/secret 类字段一律打码，这是既有安全防线。
    # 少数操作（如新建 Token）必须把明文交付给用户才有意义，才在此显式登记豁免字段。
    # ⚠️ 豁免意味着该字段明文会进入对话历史，并随后续每轮请求发送给大模型提供商，
    #    因此只允许用于「用户主动索取、且本轮刚生成」的凭据，绝不可用于读取既有密钥。
    plaintext_exempt_fields: Tuple[str, ...] = ()

    @property
    def full_path(self) -> str:
        """补上 /api 前缀，得到内部 ASGI 调用的真实路径。"""
        return f"/api{self.path}"

    @property
    def permission(self) -> ToolPermission:
        """由副作用类别推导既有三档权限，供 ToolRegistry 权限校验复用。"""
        return effect_to_permission(self.effect)

    @property
    def required_confirmation(self) -> ConfirmationMode:
        """取实际生效的确认强度（显式声明优先，否则按 effect 推导）。"""
        return self.confirmation or default_confirmation(self.effect)


# ── 白名单：Token 管理组 ──────────────────────────────────
# 后端实现见 src/api/ui/token.py，路由已挂在 prefix="/ui"
#
# 注意：列表查询不在此登记 —— 已有只读工具 list_tokens 承担该职责，
# 两处都做会让 AI 面对两个等价入口而摇摆。此处只登记 list_tokens 无法完成的写操作，
# 以及需要额外路径参数的日志查询。
_TOKEN_OPERATIONS: Tuple[ApiOperation, ...] = (
    ApiOperation(
        operation_id="token.create",
        method="POST",
        path="/ui/tokens",
        summary="创建一个新的弹幕 API Token（供第三方播放器使用），并返回可直接填入播放器的完整地址",
        # 新增记录，删掉即可还原，属可逆写
        effect=ActionEffect.REVERSIBLE_WRITE,
        result_sensitivity=ResultSensitivity.SECRET,
        body_fields={
            "name": "Token 名称，必填，如「我的播放器」",
            "validityPeriod": "有效期，可选值 permanent / 1d / 7d / 30d / 180d / 365d，默认 permanent",
            "dailyCallLimit": "每日调用上限，整数；-1 表示不限制，默认 500",
            "customToken": "自定义 Token 字符串（可选）；不填则自动生成 20 位随机串。"
                           "仅允许字母数字下划线短横线，长度 5~100",
        },
        # 新建的 Token 明文必须交付给用户才有使用价值，故对 token 字段开豁免。
        # 注意：token.list / token.access_logs 等读取类操作不开豁免，既有密钥仍全程打码。
        plaintext_exempt_fields=("token",),
        success_hint="Token 已创建，请把下面的地址填入播放器。此明文仅本次展示，请及时保存。",
    ),
    ApiOperation(
        operation_id="token.toggle",
        method="PUT",
        path="/ui/tokens/{token_id}/toggle",
        summary="切换指定 Token 的启用/禁用状态",
        # 再切一次即可还原
        effect=ActionEffect.REVERSIBLE_WRITE,
        path_params={"token_id": "Token 的数字 ID，可先用 token.list 获取"},
        success_hint="Token 启用状态已切换。",
    ),
    ApiOperation(
        operation_id="token.update",
        method="PUT",
        path="/ui/tokens/{token_id}",
        summary="更新 Token 的名称、每日调用上限等信息",
        # 旧值会被覆盖，但可再次改回
        effect=ActionEffect.REVERSIBLE_WRITE,
        path_params={"token_id": "Token 的数字 ID"},
        body_fields={
            "name": "新的 Token 名称",
            "dailyCallLimit": "新的每日调用上限；-1 表示不限制",
        },
        success_hint="Token 信息已更新。",
    ),
    ApiOperation(
        operation_id="token.reset_counter",
        method="POST",
        path="/ui/tokens/{token_id}/reset",
        summary="重置指定 Token 的今日调用次数计数",
        # 计数一旦清零无法恢复原值，但影响面仅限统计，不涉及数据丢失
        effect=ActionEffect.REVERSIBLE_WRITE,
        path_params={"token_id": "Token 的数字 ID"},
        success_hint="Token 调用次数已重置。",
    ),
    ApiOperation(
        operation_id="token.delete",
        method="DELETE",
        path="/ui/tokens/{token_id}",
        summary="删除指定 Token（删除后使用该 Token 的播放器将立即失效）",
        # 不可逆：删除后原 Token 字符串无法找回，依赖它的播放器会立刻断连
        effect=ActionEffect.DESTRUCTIVE_WRITE,
        path_params={"token_id": "Token 的数字 ID"},
        success_hint="Token 已删除。",
    ),
    ApiOperation(
        operation_id="token.access_logs",
        method="GET",
        path="/ui/tokens/{tokenId}/logs",
        summary="查看指定 Token 的访问日志（请求方法、路径、状态码、来源 IP）",
        # 日志含来源 IP，属个人可识别信息
        effect=ActionEffect.SAFE_READ,
        result_sensitivity=ResultSensitivity.PRIVATE,
        path_params={"tokenId": "Token 的数字 ID"},
    ),
)


# ── 白名单：通知渠道组 ────────────────────────────────────
# 后端实现见 src/api/ui/notification_routes.py（prefix="/ui"）
_NOTIFICATION_OPERATIONS: Tuple[ApiOperation, ...] = (
    ApiOperation(
        operation_id="notification.channel_types",
        method="GET",
        path="/ui/notification/channel-types",
        summary="获取所有可用通知渠道类型及其配置字段 Schema（新增渠道前先查此项确认要填哪些字段）",
        effect=ActionEffect.SAFE_READ,
        confirmation=ConfirmationMode.NONE,
    ),
    ApiOperation(
        operation_id="notification.channel_schema",
        method="GET",
        path="/ui/notification/schema/{channel_type}",
        summary="获取指定渠道类型的配置字段 Schema（如 telegram 需要哪些字段）",
        effect=ActionEffect.SAFE_READ,
        confirmation=ConfirmationMode.NONE,
        path_params={"channel_type": "渠道类型标识，取自 notification.channel_types"},
    ),
    ApiOperation(
        operation_id="notification.list",
        method="GET",
        path="/ui/notification/channels",
        summary="列出所有已配置的通知渠道（含 id、类型、启用状态）",
        # 渠道 config 里含 bot token / webhook 密钥，属凭据面读取
        effect=ActionEffect.SENSITIVE_READ,
        result_sensitivity=ResultSensitivity.SECRET,
        confirmation=ConfirmationMode.NONE,
    ),
    ApiOperation(
        operation_id="notification.create",
        method="POST",
        path="/ui/notification/channels",
        summary="新增一个通知渠道（如 Telegram、企业微信）",
        effect=ActionEffect.REVERSIBLE_WRITE,
        result_sensitivity=ResultSensitivity.SECRET,
        body_fields={
            "name": "渠道显示名称，必填",
            "channelType": "渠道类型标识，必填，取自 notification.channel_types",
            "isEnabled": "是否启用，布尔，默认 true",
            "useProxy": "是否走代理，布尔，默认 false",
            "config": "渠道专属配置对象，字段以 notification.channel_schema 返回的 Schema 为准",
            "eventsConfig": "事件订阅配置对象，可留空",
        },
        success_hint="通知渠道已新增，建议接着用 notification.test 验证连通性。",
    ),
    ApiOperation(
        operation_id="notification.update",
        method="PUT",
        path="/ui/notification/channels/{channel_id}",
        summary="更新通知渠道配置（只传要改的字段）",
        effect=ActionEffect.REVERSIBLE_WRITE,
        result_sensitivity=ResultSensitivity.SECRET,
        path_params={"channel_id": "渠道数字 ID，取自 notification.list"},
        body_fields={
            "name": "新名称（可选）",
            "channelType": "新渠道类型（可选）",
            "isEnabled": "启用状态（可选）",
            "useProxy": "是否走代理（可选）",
            "config": "渠道专属配置对象（可选）",
            "eventsConfig": "事件订阅配置（可选）",
        },
        success_hint="通知渠道配置已更新。",
    ),
    ApiOperation(
        operation_id="notification.test",
        method="POST",
        path="/ui/notification/channels/{channel_id}/test",
        summary="测试通知渠道连通性（会真实发送一条测试消息）",
        # 会向外部平台真实推送消息
        effect=ActionEffect.EXTERNAL_SIDE_EFFECT,
        path_params={"channel_id": "渠道数字 ID"},
        success_hint="测试消息已发送，请到对应平台查看是否收到。",
    ),
    ApiOperation(
        operation_id="notification.delete",
        method="DELETE",
        path="/ui/notification/channels/{channel_id}",
        summary="删除通知渠道（删除后该渠道的配置与密钥一并丢失）",
        effect=ActionEffect.DESTRUCTIVE_WRITE,
        path_params={"channel_id": "渠道数字 ID"},
        success_hint="通知渠道已删除。",
    ),
)


# ── 白名单：媒体服务器组 ──────────────────────────────────
# 后端实现见 src/api/ui/media_server.py（prefix="/ui"）
_MEDIA_SERVER_OPERATIONS: Tuple[ApiOperation, ...] = (
    ApiOperation(
        operation_id="mediaserver.list",
        method="GET",
        path="/ui/media-servers",
        summary="列出所有已配置的媒体服务器（Emby/Jellyfin/Plex 等，含 id 与启用状态）",
        # 返回体含 apiToken，属凭据面读取
        effect=ActionEffect.SENSITIVE_READ,
        result_sensitivity=ResultSensitivity.SECRET,
        confirmation=ConfirmationMode.NONE,
    ),
    ApiOperation(
        operation_id="mediaserver.create",
        method="POST",
        path="/ui/media-servers",
        summary="添加一个媒体服务器（Emby/Jellyfin/Plex）",
        effect=ActionEffect.REVERSIBLE_WRITE,
        result_sensitivity=ResultSensitivity.SECRET,
        body_fields={
            "name": "显示名称，必填",
            "providerName": "服务器类型，必填，如 emby / jellyfin / plex",
            "url": "服务器地址，必填，如 http://192.168.1.10:8096",
            "apiToken": "服务器 API 密钥，必填（需用户自行提供，不要编造）",
            "isEnabled": "是否启用，布尔，默认 true",
            "selectedLibraries": "要纳入的媒体库 ID 字符串数组，可留空表示全部",
            "filterRules": "过滤规则对象，可留空",
        },
        success_hint="媒体服务器已添加，建议接着用 mediaserver.test 验证连接。",
    ),
    ApiOperation(
        operation_id="mediaserver.update",
        method="PUT",
        path="/ui/media-servers/{server_id}",
        summary="更新媒体服务器配置（只传要改的字段）",
        effect=ActionEffect.REVERSIBLE_WRITE,
        result_sensitivity=ResultSensitivity.SECRET,
        path_params={"server_id": "服务器数字 ID，取自 mediaserver.list"},
        body_fields={
            "name": "新名称（可选）",
            "providerName": "新类型（可选）",
            "url": "新地址（可选）",
            "apiToken": "新 API 密钥（可选）",
            "isEnabled": "启用状态（可选）",
            "selectedLibraries": "媒体库 ID 数组（可选）",
            "filterRules": "过滤规则（可选）",
        },
        success_hint="媒体服务器配置已更新。",
    ),
    ApiOperation(
        operation_id="mediaserver.test",
        method="POST",
        path="/ui/media-servers/{server_id}/test",
        summary="测试媒体服务器连接是否正常（实发请求探测）",
        # 仅探测不改数据，允许随时自检
        effect=ActionEffect.SAFE_READ,
        confirmation=ConfirmationMode.NONE,
        path_params={"server_id": "服务器数字 ID"},
    ),
    ApiOperation(
        operation_id="mediaserver.libraries",
        method="GET",
        path="/ui/media-servers/{server_id}/libraries",
        summary="获取该媒体服务器上的媒体库列表（用于挑选 selectedLibraries）",
        effect=ActionEffect.SAFE_READ,
        confirmation=ConfirmationMode.NONE,
        path_params={"server_id": "服务器数字 ID"},
    ),
    ApiOperation(
        operation_id="mediaserver.scan",
        method="POST",
        path="/ui/media-servers/{server_id}/scan",
        summary="触发扫描该媒体服务器的媒体库（后台任务）",
        # 会向媒体服务器发起扫描并拉取条目
        effect=ActionEffect.EXTERNAL_SIDE_EFFECT,
        path_params={"server_id": "服务器数字 ID"},
        body_fields={
            "library_ids": "要扫描的媒体库 ID 字符串数组；留空表示扫描全部已选媒体库",
        },
        success_hint="扫描任务已提交，可用 list_tasks 查看进度。",
    ),
    ApiOperation(
        operation_id="mediaserver.delete",
        method="DELETE",
        path="/ui/media-servers/{server_id}",
        summary="删除媒体服务器配置（其密钥与已选媒体库设置一并丢失）",
        effect=ActionEffect.DESTRUCTIVE_WRITE,
        path_params={"server_id": "服务器数字 ID"},
        success_hint="媒体服务器已删除。",
    ),
)


# ── 白名单：订阅管理组 ────────────────────────────────────
# 后端实现见 src/api/ui/subscriptions.py（router 自带 prefix="/subscriptions"，挂在 "/ui" 下）
_SUBSCRIPTION_OPERATIONS: Tuple[ApiOperation, ...] = (
    ApiOperation(
        operation_id="subscription.available_sources",
        method="GET",
        path="/ui/subscriptions/available-sources",
        summary="探测当前可用的订阅源（创建订阅前先查有哪些 provider 可用）",
        effect=ActionEffect.SAFE_READ,
        confirmation=ConfirmationMode.NONE,
    ),
    ApiOperation(
        operation_id="subscription.discover",
        method="GET",
        path="/ui/subscriptions/discover",
        summary="在指定订阅源里按关键词或 URL 发现可订阅目标",
        effect=ActionEffect.SAFE_READ,
        confirmation=ConfirmationMode.NONE,
        query_params={
            "provider": "订阅源标识，必填，如 bilibili，取自 subscription.available_sources",
            "query": "关键词或视频/合集 URL，必填",
        },
    ),
    ApiOperation(
        operation_id="subscription.resolve_url",
        method="POST",
        path="/ui/subscriptions/resolve-url",
        summary="直接给一条链接，自动判断属于哪个订阅源并列出候选（用户丢链接时用这个）",
        effect=ActionEffect.SAFE_READ,
        confirmation=ConfirmationMode.NONE,
        body_fields={"url": "要解析的链接，必填"},
    ),
    ApiOperation(
        operation_id="subscription.list_targets",
        method="GET",
        path="/ui/subscriptions/targets",
        summary="列出已创建的订阅目标（含 id、启用状态）",
        effect=ActionEffect.SAFE_READ,
        confirmation=ConfirmationMode.NONE,
    ),
    ApiOperation(
        operation_id="subscription.create_target",
        method="POST",
        path="/ui/subscriptions/targets",
        summary="创建一个订阅目标（持续追更某个 UP 主／合集／番剧）",
        effect=ActionEffect.REVERSIBLE_WRITE,
        body_fields={
            "provider": "订阅源标识，必填",
            "type": "订阅类型，必填（取值参考 subscription.discover 返回的候选）",
            "payload": "订阅目标描述对象，必填，内容取自 discover 返回的候选项",
            "runNow": "是否立即执行一次扫描，布尔，默认 false",
        },
        success_hint="订阅已创建。若未立即扫描，可用 subscription.scan_target 手动触发一次。",
    ),
    ApiOperation(
        operation_id="subscription.update_target",
        method="PATCH",
        path="/ui/subscriptions/targets/{target_id}",
        summary="修改订阅目标（启停、改状态）",
        effect=ActionEffect.REVERSIBLE_WRITE,
        path_params={"target_id": "订阅目标数字 ID，取自 subscription.list_targets"},
        body_fields={
            "enabled": "是否启用（可选）",
            "status": "订阅状态（可选）",
            "extraPatch": "附加配置补丁对象（可选）",
        },
        success_hint="订阅目标已更新。",
    ),
    ApiOperation(
        operation_id="subscription.scan_target",
        method="POST",
        path="/ui/subscriptions/targets/{target_id}/scan",
        summary="立即扫描一个订阅目标，抓取新增内容",
        # 会向订阅源实发请求并可能触发导入
        effect=ActionEffect.EXTERNAL_SIDE_EFFECT,
        path_params={"target_id": "订阅目标数字 ID"},
        success_hint="扫描已触发，可用 list_tasks 查看进度。",
    ),
    ApiOperation(
        operation_id="subscription.delete_target",
        method="DELETE",
        path="/ui/subscriptions/targets/{target_id}",
        summary="取消订阅目标（不再自动追更该目标）",
        effect=ActionEffect.DESTRUCTIVE_WRITE,
        path_params={"target_id": "订阅目标数字 ID"},
        success_hint="订阅已取消。",
    ),
    ApiOperation(
        operation_id="subscription.list_items",
        method="GET",
        path="/ui/subscriptions/items",
        summary="查询订阅产生的候选项（含失败待重试的条目）",
        effect=ActionEffect.SAFE_READ,
        confirmation=ConfirmationMode.NONE,
    ),
    ApiOperation(
        operation_id="subscription.retry_item",
        method="POST",
        path="/ui/subscriptions/items/{item_id}/retry",
        summary="重试一个失败的订阅候选项",
        effect=ActionEffect.EXTERNAL_SIDE_EFFECT,
        path_params={"item_id": "候选项数字 ID，取自 subscription.list_items"},
        success_hint="已重新提交该候选项。",
    ),
    ApiOperation(
        operation_id="subscription.ignore_item",
        method="POST",
        path="/ui/subscriptions/items/{item_id}/ignore",
        summary="忽略一个订阅候选项（不再重试该条）",
        effect=ActionEffect.REVERSIBLE_WRITE,
        path_params={"item_id": "候选项数字 ID"},
        success_hint="该候选项已忽略。",
    ),
)


# ── 白名单：日历追更组 ────────────────────────────────────
# 后端实现见 src/api/ui/calendar.py（router 自带 prefix="/calendar"）
# 与 src/api/ui/calendar_extra.py（路径直写 /calendar/...）
_CALENDAR_OPERATIONS: Tuple[ApiOperation, ...] = (
    ApiOperation(
        operation_id="calendar.weekly",
        method="GET",
        path="/ui/calendar/weekly",
        summary="获取本周番剧放送表（用户问「这周有什么新番」时用）",
        effect=ActionEffect.SAFE_READ,
        confirmation=ConfirmationMode.NONE,
    ),
    ApiOperation(
        operation_id="calendar.upcoming",
        method="GET",
        path="/ui/calendar/upcoming",
        summary="获取即将播出的条目",
        effect=ActionEffect.SAFE_READ,
        confirmation=ConfirmationMode.NONE,
        query_params={"days": "未来天数，整数 1~30，默认 7"},
    ),
    ApiOperation(
        operation_id="calendar.stale_episodes",
        method="GET",
        path="/ui/calendar/stale-episodes",
        summary="列出已播出但尚未刷新弹幕的分集（用户问「哪些该更新了」时用）",
        effect=ActionEffect.SAFE_READ,
        confirmation=ConfirmationMode.NONE,
    ),
    ApiOperation(
        operation_id="calendar.discover",
        method="GET",
        path="/ui/calendar/discover",
        summary="发现当前季度的新番",
        effect=ActionEffect.SAFE_READ,
        confirmation=ConfirmationMode.NONE,
    ),
    ApiOperation(
        operation_id="calendar.subscribe",
        method="POST",
        path="/ui/calendar/subscribe",
        summary="订阅一部外部番剧（标记追更意向，可选立即执行一次轮询）",
        effect=ActionEffect.EXTERNAL_SIDE_EFFECT,
        body_fields={
            "animeTitle": "作品标题，必填",
            "mediaType": "媒体类型，tv_series 或 movie，默认 tv_series",
            "season": "季度号，整数，可选",
            "traktTmdbId": "TMDB ID（可选）",
            "bangumiId": "Bangumi ID（可选）",
            "provider": "来源标识（可选）",
            "externalId": "外部条目 ID（可选）",
            "runNow": "是否立即执行一次轮询，布尔，默认 true",
        },
        success_hint="已加入追更。若已立即轮询，可用 list_tasks 查看抓取进度。",
    ),
    ApiOperation(
        operation_id="calendar.subscribe_batch",
        method="POST",
        path="/ui/calendar/subscribe/batch",
        summary="批量订阅多部外部番剧",
        effect=ActionEffect.EXTERNAL_SIDE_EFFECT,
        body_fields={
            "items": "订阅条目数组，每项字段同 calendar.subscribe 的请求体",
            "runNow": "是否立即执行轮询，布尔，默认 true",
        },
        success_hint="批量追更已提交。数量较多时请提醒用户抓取需要时间。",
    ),
    ApiOperation(
        operation_id="calendar.unsubscribe",
        method="POST",
        path="/ui/calendar/unsubscribe",
        summary="取消订阅（同时处理本地取消追更与外部取消订阅）",
        # 仅取消追更标记，不删除已入库弹幕，可再次订阅恢复
        effect=ActionEffect.REVERSIBLE_WRITE,
        body_fields={
            "provider": "来源标识（可选）",
            "externalId": "外部条目 ID（可选）",
            "sourceId": "数据源 ID，整数（可选）",
            "bangumiId": "Bangumi ID（可选）",
            "traktId": "Trakt ID（可选）",
            "traktTmdbId": "TMDB ID（可选）",
        },
        success_hint="已取消追更（已入库的弹幕不受影响）。",
    ),
)


# ── 白名单：弹幕存储整理组 ────────────────────────────────
# 后端实现见 src/api/ui/danmaku_storage.py（prefix="/ui/danmaku-storage"）
#
# ⚠️ 这组会批量移动/重命名磁盘上的弹幕文件，是当前风险最高的一组。
# 每个执行类操作都有配套的 preview_* 只读接口，务必先预览再执行。
_DANMAKU_STORAGE_OPERATIONS: Tuple[ApiOperation, ...] = (
    ApiOperation(
        operation_id="storage.template_variables",
        method="GET",
        path="/ui/danmaku-storage/template-variables",
        summary="获取命名模板可用变量列表（写自定义模板前先查）",
        effect=ActionEffect.SAFE_READ,
        confirmation=ConfirmationMode.NONE,
    ),
    ApiOperation(
        operation_id="storage.preview_rename",
        method="POST",
        path="/ui/danmaku-storage/preview-rename",
        summary="预览批量重命名结果（只算不改，执行 storage.batch_rename 前必须先跑这个）",
        effect=ActionEffect.SAFE_READ,
        confirmation=ConfirmationMode.NONE,
        body_fields={
            "animeIds": "作品 ID 整数数组，必填",
            "mode": "模式，必填：prefix（加前后缀）或 regex（正则替换）",
            "prefix": "前缀（mode=prefix 时用）",
            "suffix": "后缀（mode=prefix 时用）",
            "regexPattern": "匹配正则（mode=regex 时用）",
            "regexReplace": "替换内容（mode=regex 时用）",
        },
    ),
    ApiOperation(
        operation_id="storage.batch_rename",
        method="POST",
        path="/ui/danmaku-storage/batch-rename",
        summary="执行批量重命名弹幕文件（真实改磁盘文件名，务必先用 storage.preview_rename 确认）",
        # 文件名改动后无法自动回退，需人工逐个改回
        effect=ActionEffect.DESTRUCTIVE_WRITE,
        body_fields={
            "animeIds": "作品 ID 整数数组，必填",
            "mode": "模式，必填：prefix / regex / direct",
            "prefix": "前缀（可选）",
            "suffix": "后缀（可选）",
            "regexPattern": "匹配正则（可选）",
            "regexReplace": "替换内容（可选）",
            "directRenames": "直接指定新名称的条目数组（mode=direct 时用）",
        },
        success_hint="批量重命名已执行。文件名改动无法自动撤销，请核对结果。",
    ),
    ApiOperation(
        operation_id="storage.preview_migrate",
        method="POST",
        path="/ui/danmaku-storage/preview-migrate",
        summary="预览批量迁移结果（只算不动文件，执行 storage.batch_migrate 前必须先跑）",
        effect=ActionEffect.SAFE_READ,
        confirmation=ConfirmationMode.NONE,
        body_fields={
            "animeIds": "作品 ID 整数数组，必填",
            "targetPath": "目标根目录，必填",
            "keepStructure": "是否保留原目录结构，布尔，默认 true",
        },
    ),
    ApiOperation(
        operation_id="storage.batch_migrate",
        method="POST",
        path="/ui/danmaku-storage/batch-migrate",
        summary="执行批量迁移弹幕文件到新目录（真实移动磁盘文件，务必先预览）",
        effect=ActionEffect.DESTRUCTIVE_WRITE,
        body_fields={
            "animeIds": "作品 ID 整数数组，必填",
            "targetPath": "目标根目录，必填",
            "keepStructure": "是否保留原目录结构，布尔，默认 true",
            "conflictAction": "冲突处理，skip / overwrite / rename，默认 skip。"
                              "overwrite 会覆盖同名文件，选用前必须向用户明示",
        },
        success_hint="批量迁移已执行。文件已移动到新位置，请核对结果。",
    ),
    ApiOperation(
        operation_id="storage.preview_template",
        method="POST",
        path="/ui/danmaku-storage/preview-template",
        summary="预览应用命名模板的结果（只算不改）",
        effect=ActionEffect.SAFE_READ,
        confirmation=ConfirmationMode.NONE,
        body_fields={
            "animeIds": "作品 ID 整数数组，必填",
            "templateType": "模板类型，必填：tv / movie / id / plex / emby / custom",
            "customTemplate": "自定义模板字符串（templateType=custom 时必填）",
        },
    ),
    ApiOperation(
        operation_id="storage.apply_template",
        method="POST",
        path="/ui/danmaku-storage/apply-template",
        summary="按命名模板重排弹幕文件（真实改文件名与路径，务必先预览）",
        effect=ActionEffect.DESTRUCTIVE_WRITE,
        body_fields={
            "animeIds": "作品 ID 整数数组，必填",
            "templateType": "模板类型，必填：tv / movie / id / plex / emby / custom",
            "customTemplate": "自定义模板字符串（templateType=custom 时必填）",
        },
        success_hint="模板已应用。文件名与路径改动无法自动撤销，请核对结果。",
    ),
)


# ── 白名单：媒体项导入组 ──────────────────────────────────
# 后端实现见 src/api/ui/media_server.py（prefix="/ui"）
_MEDIA_ITEM_OPERATIONS: Tuple[ApiOperation, ...] = (
    ApiOperation(
        operation_id="mediaitem.list",
        method="GET",
        path="/ui/media-items",
        summary="获取媒体服务器已扫描到的媒体项列表（含是否已导入弹幕库）",
        effect=ActionEffect.SAFE_READ,
        confirmation=ConfirmationMode.NONE,
    ),
    ApiOperation(
        operation_id="mediaitem.unimported_count",
        method="GET",
        path="/ui/media-items/unimported-count",
        summary="统计还有多少媒体项尚未导入弹幕（用户问「还有多少没导」时用）",
        effect=ActionEffect.SAFE_READ,
        confirmation=ConfirmationMode.NONE,
        query_params={"server_id": "媒体服务器 ID，整数，必填"},
    ),
    ApiOperation(
        operation_id="mediaitem.import",
        method="POST",
        path="/ui/media-items/import",
        summary="导入选中的媒体项（为它们抓取弹幕）",
        effect=ActionEffect.EXTERNAL_SIDE_EFFECT,
        body_fields={
            "itemIds": "要导入的媒体项 ID 整数数组",
            "shows": "按剧集维度导入的描述数组（可选）",
            "seasons": "按季度维度导入的描述数组（可选）",
        },
        success_hint="导入任务已提交，可用 list_tasks 查看进度。",
    ),
    ApiOperation(
        operation_id="mediaitem.import_all_unimported",
        method="POST",
        path="/ui/media-items/import-all-unimported",
        summary="一键导入全部未导入的媒体项（数量可能很大，会产生大量抓取请求）",
        # 一次触发全量抓取，对外部源压力大且难以中途撤回
        effect=ActionEffect.EXTERNAL_SIDE_EFFECT,
        body_fields={
            "server_id": "媒体服务器 ID，整数",
            "library_ids": "限定的媒体库 ID 数组（可选）",
        },
        success_hint="全量导入已提交。数量较大时请提醒用户这会持续较久并对源站产生较多请求。",
    ),
    ApiOperation(
        operation_id="mediaitem.update",
        method="PUT",
        path="/ui/media-items/{item_id}",
        summary="更新媒体项的识别信息（标题、季集号、外部 ID 等）",
        effect=ActionEffect.REVERSIBLE_WRITE,
        path_params={"item_id": "媒体项数字 ID，取自 mediaitem.list"},
        body_fields={
            "title": "标题（可选）",
            "mediaType": "媒体类型（可选）",
            "season": "季度号，整数（可选）",
            "episode": "集号，整数（可选）",
            "year": "年份，整数（可选）",
            "tmdbId": "TMDB ID（可选）",
            "tvdbId": "TVDB ID（可选）",
            "imdbId": "IMDB ID（可选）",
        },
        success_hint="媒体项信息已更新。",
    ),
    ApiOperation(
        operation_id="mediaitem.delete",
        method="DELETE",
        path="/ui/media-items/{item_id}",
        summary="删除单个媒体项记录",
        effect=ActionEffect.DESTRUCTIVE_WRITE,
        path_params={"item_id": "媒体项数字 ID"},
        success_hint="媒体项已删除。",
    ),
    ApiOperation(
        operation_id="mediaitem.batch_delete",
        method="POST",
        path="/ui/media-items/batch-delete",
        summary="批量删除媒体项记录（不可逆，执行前必须把要删的数量与范围说清楚）",
        effect=ActionEffect.DESTRUCTIVE_WRITE,
        body_fields={
            "itemIds": "要删除的媒体项 ID 整数数组，必填",
        },
        success_hint="批量删除已执行，记录无法恢复。",
    ),
)


# 全部已加白操作（后续按需追加其他业务组）
_ALL_OPERATIONS: Tuple[ApiOperation, ...] = (
    _TOKEN_OPERATIONS
    + _NOTIFICATION_OPERATIONS
    + _MEDIA_SERVER_OPERATIONS
    + _SUBSCRIPTION_OPERATIONS
    + _CALENDAR_OPERATIONS
    + _DANMAKU_STORAGE_OPERATIONS
    + _MEDIA_ITEM_OPERATIONS
)

EXPOSED_OPERATIONS: Dict[str, ApiOperation] = {
    op.operation_id: op for op in _ALL_OPERATIONS
}


# ── 明确禁止清单（留档说明，不参与解析） ──────────────────
# 这些路由即便未来有人误加白，也应在 code review 阶段被拦下。
FORBIDDEN_PATH_PREFIXES: Tuple[str, ...] = (
    "/ui/auth",          # 登录、改密、MFA —— 认证面禁止代理
    "/ui/backup",        # 备份恢复 —— 可覆盖全库
    "/ui/debug",         # 调试端点 —— 可能暴露内部状态
    "/webhook",          # 外部回调入口 —— 不应由 AI 主动触发
)


def resolve_api_operation(operation_id: str) -> Optional[ApiOperation]:
    """
    将 AI 提交的 operation_id 解析为固定 API 契约。

    :param operation_id: AI 提交的操作标识
    :return: 命中的操作契约；未加白则返回 None（调用方须按拒绝处理）
    """
    if not operation_id or not isinstance(operation_id, str):
        return None
    op = EXPOSED_OPERATIONS.get(operation_id.strip())
    if op is None:
        return None
    # 双保险：即便被误加白，命中禁止前缀也一律拒绝
    if any(op.path.startswith(prefix) for prefix in FORBIDDEN_PATH_PREFIXES):
        return None
    return op


def list_exposed_operations(
    include_write: bool = True,
) -> Tuple[ApiOperation, ...]:
    """
    列出可暴露给 AI 的操作。

    :param include_write: 为 False 时只返回只读操作
    :return: 操作契约元组
    """
    result = []
    for op in _ALL_OPERATIONS:
        if op.permission == ToolPermission.DANGEROUS:
            continue
        if not include_write and op.permission == ToolPermission.WRITE:
            continue
        if any(op.path.startswith(p) for p in FORBIDDEN_PATH_PREFIXES):
            continue
        result.append(op)
    return tuple(result)


__all__ = [
    "ApiOperation",
    "EXPOSED_OPERATIONS",
    "FORBIDDEN_PATH_PREFIXES",
    "resolve_api_operation",
    "list_exposed_operations",
    # 风险契约枚举透出，供工具层构造提示与判断确认强度
    "ActionEffect",
    "ConfirmationMode",
    "ResultSensitivity",
]
