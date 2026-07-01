# 订阅分页与订阅助手实施方案

## 1. 目标

1. 将当前「批量管理」中的订阅/日历相关能力拆成独立的「订阅」分页，降低批量管理页面复杂度。
2. 做一个通用「订阅助手」：按运行时可用能力动态展示 Bangumi、Trakt、Bilibili 等订阅源，而不是硬编码固定入口。
3. 复用现有订阅意向模型：外部条目先进入 `pending/importing/imported/failed` 状态，再由定时任务自动建库并开启追更。
4. Bilibili 作为「弹幕源能力」特殊处理：只有已加载且启用 Bilibili 弹幕源时，订阅助手才显示 Bilibili 相关入口。
5. Bilibili 订阅支持三类目标：UP 主、UP 主某系列视频、Bilibili 番剧；第一阶段优先保证单 P 视频/单集番剧弹幕导入链路稳定。

## 2. 现状依据

- 前端现有「批量管理」页同时承载本地追更列表和日历订阅，`viewMode` 为 `list/calendar`：`web/src/pages/library/batch-manage.jsx:39`。
- 同一页面直接调用日历订阅 API：`subscribeCalendarItem` / `batchSubscribeCalendarItems` / `unsubscribeCalendarItem`：`web/src/pages/library/batch-manage.jsx:21-26`。
- 页面内已存在订阅确认状态和订阅/取消订阅逻辑：`web/src/pages/library/batch-manage.jsx:905-1218`。
- 现有后端日历订阅请求模型为 `SubscribeRequest` / `BatchSubscribeRequest` / `UnsubscribeRequest`：`src/api/ui/calendar.py:52-80`。
- 现有订阅 API 已支持「订阅意向 + 可选立即导入」：`src/api/ui/calendar.py:656-738`，批量订阅在 `src/api/ui/calendar.py:741-814`。
- 订阅数据目前落在 `external_calendar_item`，字段包含 `isSubscribed`、`subscriptionStatus`、失败次数：`src/db/orm_models.py:530-613`。
- 定时任务 `IncrementalRefreshJob` 已包含「阶段0：处理 pending 订阅」和「阶段1：本地追更」：`src/jobs/incremental_refresh.py:16-23`、`src/jobs/incremental_refresh.py:190-207`。
- 弹幕源列表可通过 `GET /api/ui/scrapers` 获取，且每个源带 `isEnabled`、`configurableFields`、`actions`、`version`：`src/api/ui/scraper.py:21-83`。
- Bilibili 入口必须基于弹幕源运行时状态判断，因为弹幕源是动态加载的，`ScraperManager` 维护 `scrapers` 和 `scraper_settings`：`src/services/scraper_manager.py:38-53`。
- 元数据源列表可通过 `GET /api/ui/metadata-sources` 获取，并带连接状态与启用状态：`src/api/ui/metadata_source.py:19-25`、`src/services/metadata_manager.py:452-510`。
- 参考项目 `bilibili-danmaku-matcher` 的核心模式：`up_subscription` 记录 UP 主，后台扫描 `recent_videos(uid)`，拉 `video_detail(bvid)`，跳过多 P，标题解析后匹配 Bangumi/弹弹Play，并提供管理后台。

## 3. 目标信息架构

### 3.1 前端导航

推荐将「订阅」作为媒体库的同级分页，而不是藏在批量管理内：

- `/library`：媒体库本体。
- `/library/batch-manage`：只保留本地批量操作：追更开关、精确标记、完结、删除。
- `/library/subscriptions`：新增订阅分页，承载通用订阅助手。

### 3.2 订阅页 Tab

1. `订阅助手`：动态展示当前可用订阅源，按源类型给出订阅入口。
2. `日历订阅`：迁移现有 Bangumi/Trakt/TMDB 日历视图和订阅/取消订阅 Modal。
3. `Bilibili`：仅在 Bilibili 弹幕源已加载且启用时出现，包含 UP 主、UP 主系列、B站番剧三种订阅。
4. `订阅记录`：统一展示所有订阅意向和导入状态，便于重试/取消/查看失败原因。

### 3.3 订阅源可用性规则

- Bilibili：必须满足 `scraperManager.scrapers` 中存在 `bilibili`，且 `/api/ui/scrapers` 返回该源 `isEnabled=true`；否则订阅助手不显示 Bilibili。
- Bangumi：必须满足元数据源已启用、连通性正常；如果该功能需要用户 token/OAuth，则必须 token 存在且认证有效，否则不显示订阅入口，只显示「去认证」提示。
- Trakt：必须满足元数据源已启用、OAuth 认证有效；未认证时不作为可订阅源。
- TMDB/其他公共源：如果不需要用户认证，可作为发现/日历源；如果需要 API Key，则必须配置完整。
- 可用性判断由后端统一返回，前端只按返回的 `availableSources` 渲染，避免前端重复猜测。

## 4. 前端实施

1. 新建 `web/src/pages/subscription/index.jsx`，只负责页面壳和 Tab。
2. 从 `batch-manage.jsx` 提取日历相关组件：
   - `SubscriptionCalendar.jsx`：`fetchCalendar`、日历筛选、同步、清缓存、卡片订阅按钮。
   - `CalendarAnimeCard.jsx`：现有日历卡片移动过去。
   - `SubscribeConfirmModal.jsx`：单条/批量订阅确认框。
3. `batch-manage.jsx` 删除 `viewMode === 'calendar'` 分支，只保留本地源批量管理。
4. 新增路由常量：`RoutePaths.SUBSCRIPTIONS = '/library/subscriptions'`。
5. `LibraryTabsPage` 的 tabs 增加「订阅」项；`key` 从 pathname 判断 `library/batch/subscriptions` 三态。
6. 新增 API 封装：
   - 保留现有 `/api/ui/calendar/*` 方法，但迁移调用位置。
   - 新增 `/api/ui/subscriptions/*` 方法，用于统一订阅记录和 Bilibili UP 主订阅。

## 5. 后端 API 设计

### 5.1 订阅源能力探测 API

新增 `src/api/ui/subscriptions.py`，prefix `/subscriptions`：

- `GET /api/ui/subscriptions/available-sources`：返回当前可用订阅源。
  - `calendarSources`：Bangumi/Trakt/TMDB 等元数据源，必须包含 `available`、`reason`、`authRequired`、`authStatus`。
  - `danmakuSources`：Bilibili 等弹幕源能力，必须基于已加载弹幕源和启用状态。
  - `features`：如 `bilibili.up`、`bilibili.upSeries`、`bilibili.bangumi`、`bangumi.calendar`、`trakt.calendar`。
- 前端订阅助手只渲染 `available=true` 的入口；`available=false` 只在「配置提示」里展示原因。

### 5.2 统一订阅记录 API

- `GET /api/ui/subscriptions`：分页查询订阅记录。
  - 参数：`provider`、`subscriptionType`、`status`、`keyword`、`page`、`pageSize`。
  - 来源以 `external_calendar_item` 为主，Bilibili 的订阅目标、视频候选、番剧分集候选也统一落在该表，通过 `provider + externalId + extraData.subscriptionType` 区分。
- `POST /api/ui/subscriptions/{id}/retry`：失败订阅重新置为 `pending`。
- `DELETE /api/ui/subscriptions/{id}`：统一取消订阅。

### 5.3 通用订阅目标 API

不要设计 `/subscriptions/bilibili/*` 这类专用接口。统一使用 provider/type 参数，由后端路由到对应订阅能力实现：

- `GET /api/ui/subscriptions/targets`
  - 参数：`provider`、`type`、`status`、`keyword`、`page`、`pageSize`。
  - 返回所有订阅目标，如 Bangumi 日历订阅、Trakt 日历订阅、Bilibili UP 主/UP 系列/B站番剧。
- `POST /api/ui/subscriptions/targets`
  - body：`provider`、`type`、`payload`、`runNow`。
  - 例：`provider=bilibili,type=up,payload={uid}`。
- `PATCH /api/ui/subscriptions/targets/{id}`
  - 修改启用状态、备注、过滤条件、匹配规则等通用字段。
- `DELETE /api/ui/subscriptions/targets/{id}`
  - 取消订阅。
- `POST /api/ui/subscriptions/targets/{id}/scan`
  - 立即扫描该订阅目标。
- `GET /api/ui/subscriptions/items`
  - 查询订阅目标产生的子项：Bilibili 视频候选、B站番剧分集候选、未来其他源的候选项。
- `POST /api/ui/subscriptions/items/{id}/retry`
  - 重新处理候选项。
- `POST /api/ui/subscriptions/items/{id}/ignore`
  - 忽略候选项。

路由层只做通用参数校验、鉴权和数据库读写；provider 特有逻辑由弹幕源或元数据源提供的通用订阅方法处理。

### 5.4 弹幕源基类订阅能力设计

在 `BaseScraper` 增加可选订阅能力，而不是在 API 层硬编码 Bilibili。现有基类已经统一了 `search`、`get_episodes`、`get_comments` 等能力，订阅也按同样模式扩展：**基类只定义统一的抽象方法签名，源内部按 `subscription_type` 自行区分**（如 Bilibili 的 UP主/系列/番剧）。

```python
class BaseScraper(ABC):
    # 硬编码：是否支持订阅助手；默认 False，避免影响现有源。
    supports_subscription: bool = False

    # 硬编码：声明该源支持的订阅类型，由 /available-sources 读取。
    subscription_types: list[dict] = []

    async def check_subscription_capability(self) -> dict:
        """返回该弹幕源订阅能力状态，如 available/authStatus/reason。"""
        return {"available": False, "reason": "该弹幕源未实现订阅能力"}

    async def validate_subscription_payload(self, subscription_type, payload) -> dict:
        """校验并标准化订阅参数；子类按 subscription_type 区分不同类型。"""
        raise NotImplementedError

    async def scan_subscription_target(self, target) -> list[dict]:
        """扫描订阅目标返回候选项；子类按 subscriptionType 区分不同类型。"""
        raise NotImplementedError

    async def fetch_subscription_item_comments(self, item) -> list[dict]:
        """对某个候选项获取弹幕；默认可委托 get_comments。"""
        raise NotImplementedError
```

Bilibili 弹幕源只需要在自身实现：

- 硬编码 `supports_subscription = True` 和 `subscription_types = [up, up_series, bangumi]`（类属性）。
- `check_subscription_capability()`：检查源是否启用、Cookie/WBI 是否可用；可用时 `subscriptionTypes` 直接复用 `self.subscription_types`。
- `validate_subscription_payload()`：内部用 if/elif 按 `subscription_type` 区分，把 UID、系列关键词、seasonId/mediaId 标准化为 `externalId` 与 `extraData`。
- `scan_subscription_target()`：内部用 if/elif 按 `subscriptionType` 分别扫描 UP 投稿、UP 系列过滤、B站番剧分集。

外部 API 调用流程：

1. `/available-sources` 遍历 `scraper_manager.scrapers`（及元数据源）。
2. 只读取硬编码声明 `supports_subscription=True` 的源。
3. 调用源实例的 `check_subscription_capability()` 得到可用性，`subscription_types` 读取源声明的类属性。
4. 创建订阅时调用 `validate_subscription_payload()`（源内按 type 区分），再写入 `external_calendar_item`。
5. 定时扫描时按 `provider` 取 `scraper_manager.get_scraper(provider)`，再调用 `scan_subscription_target()`（源内按 type 区分）。

这样以后其他弹幕源/元数据源也支持订阅时，只需在该源内硬编码声明能力 + 实现这组通用方法，API 与基类都不需要改动。

### 5.5 BaseScraper 订阅方法返回结构

`check_subscription_capability()` 返回：

```json
{
  "available": true,
  "authRequired": true,
  "authStatus": "valid",
  "reason": null,
  "subscriptionTypes": [
    {
      "type": "up",
      "label": "UP 主",
      "description": "订阅某个 UP 主的最新投稿",
      "payloadSchema": {
        "uid": { "type": "string", "required": true, "label": "UP 主 UID" },
        "remark": { "type": "string", "required": false, "label": "备注" }
      }
    }
  ]
}
```

`validate_subscription_payload()` 返回：

```json
{
  "provider": "bilibili",
  "externalId": "up:123456",
  "title": "某UP主",
  "animeType": "subscription",
  "subscriptionType": "bilibili_up",
  "extraData": {
    "uid": 123456,
    "nickname": "某UP主",
    "scanMode": "recent_videos",
    "enabled": true
  }
}
```

`scan_subscription_target()` 返回候选项列表：

```json
[
  {
    "provider": "bilibili",
    "externalId": "video:BVxxxx",
    "title": "视频标题",
    "animeType": "episode_candidate",
    "subscriptionType": "bilibili_video_candidate",
    "status": "waiting",
    "extraData": {
      "parentExternalId": "up:123456",
      "bvid": "BVxxxx",
      "cid": "456",
      "pageCount": 1
    }
  }
]
```

约定：
- 返回结构全部使用通用字段，不能返回 API 层无法理解的私有对象。
- `extraData` 可以保留 provider 私有字段，但必须可 JSON 序列化。
- 扫描方法只负责发现候选项，不直接写库；写库统一由任务层调用 `external_calendar` CRUD。

## 6. 数据模型设计

### 6.1 以 `external_calendar_item` 作为通用外部订阅表

MVP 不新增 Bilibili 专用表。现有 `external_calendar_item` 已具备通用化基础：

- `provider + externalId` 联合唯一，可表达任意外部订阅目标。
- `isSubscribed` / `subscriptionStatus` / `subscriptionFailureCount` 已能承载订阅意向和处理状态。
- `localAnimeId` / `localSourceId` 已能回写本地建库关联。
- `extraData` 已用于保存平台特有字段，适合存 Bilibili 的 UID、BVID、CID、seasonId 等非通用字段。

因此本方案将它从「外部日历条目表」扩展为「外部数据与订阅条目表」。表名暂不改，避免大迁移；只在代码与文档中明确新语义。

### 6.2 通用字段约定

- `provider`：来源，如 `bangumi`、`trakt`、`tmdb`、`bilibili`。
- `externalId`：该来源下唯一 ID，按类型加前缀，避免冲突。
- `animeTitle`：用于展示的标题；UP 主订阅可填昵称或备注。
- `animeType`：继续兼容现有枚举；订阅目标可用 `subscription`，视频候选可用 `episode_candidate`。
- `isSubscribed`：是否为用户订阅目标；视频候选一般为 `false`，由父订阅目标关联。
- `subscriptionStatus`：扩展为 `pending/importing/imported/failed/review/ignored/skipped_multi_page`。
- `localAnimeId` / `localSourceId`：导入成功后回写。
- `extraData`：保存各平台特有字段，并必须包含 `subscriptionType`。

### 6.3 `externalId` 规范

标识规则（用户明确）：UP主=UID、合集=season_id、番剧=番剧ID、单视频候选=BV/AV。

Bilibili：

- UP 主订阅：`up:{uid}`
- 视频合集订阅：`collection:{season_id}`（合集稳定唯一标识，BV 仅作发现入口）
- B站番剧订阅：`bangumi:ss{season_id}` 或 `bangumi:md{media_id}`
- UP 视频候选 / 合集视频候选：`video:{bvid}`
- B站番剧分集候选：`episode:{season_id}:{episode_id_or_cid}`

Bangumi / Trakt：继续沿用现有外部 ID；如果后续引入更多订阅类型，也统一通过 `extraData.subscriptionType` 区分。

### 6.4 `extraData` 约定

UP 主订阅：

```json
{
  "subscriptionType": "bilibili_up",
  "uid": 123456,
  "nickname": "某UP主",
  "scanMode": "recent_videos",
  "lastScanAt": null,
  "nextScanAt": null,
  "lastError": null
}
```

视频合集订阅：

```json
{
  "subscriptionType": "bilibili_collection",
  "seasonId": 3541247,
  "mid": 645769214,
  "scanMode": "ugc_season_archives"
}
```

B站番剧订阅：

```json
{
  "subscriptionType": "bilibili_bangumi",
  "seasonId": "ss12345",
  "mediaId": "md12345",
  "scanMode": "bangumi_episodes",
  "latestKnownEpisode": 5
}
```

视频/分集候选：

```json
{
  "subscriptionType": "bilibili_video_candidate | bilibili_collection_candidate | bilibili_bangumi_candidate",
  "parentExternalId": "up:123456 | collection:3541247 | bangumi:ss12345",
  "bvid": "BVxxxx",
  "aid": "123",
  "cid": "456",
  "seasonId": 3541247,
  "mid": 645769214,
  "episodeIndex": 1,
  "parentTitle": "合集名/番剧名",
  "mediaType": "tv_series",
  "season": 1
}
```

### 6.5 是否需要改表结构

MVP 尽量不改表结构。只建议新增少量通用索引或配置：

- 保留现有 `idx_external_provider_external_unique`。
- 如后续发现订阅扫描慢，再增加 `(provider, is_subscribed, subscription_status)` 或 `(provider, updated_at)` 索引。
- 不新增 Bilibili 专表；只有当 `extraData` 查询明显成为瓶颈，再考虑拆表。

## 7. Bilibili 订阅流程

### 7.1 前置条件

1. `/api/ui/subscriptions/available-sources` 检测到 Bilibili 弹幕源已加载且启用。
2. Bilibili 源配置中 Cookie/登录态可用；如果源本身支持匿名但订阅接口需要 WBI，则必须提示用户配置 Cookie。
3. 不满足条件时：订阅助手不出现 Bilibili 卡片，直接避免用户点进去后失败。

### 7.2 UP 主订阅

1. 用户添加 UID。
2. 后端用 Cookie + WBI 签名请求 `https://api.bilibili.com/x/space/wbi/arc/search` 获取最近投稿。
3. 对每个新 `bvid` 拉 `https://api.bilibili.com/x/web-interface/view` 获取详情与 CID。
4. 第一阶段仅处理 `page_count == 1` 的视频；多 P 标记为 `skipped_multi_page`，避免复杂误导入。
5. 标题解析：复用/扩展现有标题识别能力，得到候选 `animeTitle`、`season`、`episodeIndex`。
6. 匹配策略：
   - 先用现有元数据源搜索 Bangumi/TMDB/弹弹Play 候选。
   - 再用现有 AI/规则匹配能力选择最佳番剧和分集。
   - 置信度高于阈值自动导入；低于阈值进入 `review` 人工确认。
7. 弹幕获取：通过 Bilibili 弹幕源能力按 CID/BVID 获取弹幕，并转换为现有弹幕存储格式。
8. 导入成功后写入 `anime` / `anime_sources` / `episode`，并可自动开启 `incrementalRefreshEnabled`。

### 7.3 UP 主某系列视频订阅

1. 用户选择 UID + 系列关键词/系列名，例如某字幕组固定标题前缀。
2. 扫描 UP 投稿后先用 `series_keyword` 过滤标题，只处理命中的视频。
3. 其余流程与 UP 主订阅相同。
4. 第二阶段可扩展为 B站合集/列表 ID；第一阶段先用关键词过滤，避免引入不稳定 API。

### 7.4 Bilibili 番剧订阅

1. 用户输入/搜索 `seasonId` 或 `mediaId`。
2. 后端通过 Bilibili 番剧详情接口拉取分集列表，写入或更新 `external_calendar_item(provider='bilibili', externalId='bangumi:{seasonId}')`。
3. 定时扫描番剧分集更新，发现新 EP 后按 CID 获取弹幕并导入。
4. 番剧订阅不需要标题猜测优先，优先使用 B站分集序号、标题、CID 作为强标识。

## 8. 定时任务集成

1. 新增 `SubscriptionCapabilityRefreshJob` 或在接口实时计算：刷新订阅源可用性，避免不可用源出现在助手中。
2. 新增通用 `SubscriptionScanJob`，负责扫描到期订阅目标；任务本身不写死 Bilibili，而是按 `provider` 路由到对应弹幕源/元数据源的订阅方法。
3. 配置项：
   - `enabled`
   - `scanMinMinutes` / `scanMaxMinutes`：随机下次扫描，避免固定频率打 API。
   - `recentItemPageSize`：默认 10。
   - `autoImportConfidence`：默认 0.85。
   - `skipMultiPage`：默认 true。
   - `enabledProviders`：默认跟随 `/available-sources`。
4. 与现有 `IncrementalRefreshJob` 解耦：
   - `IncrementalRefreshJob` 继续处理日历订阅和本地追更。
   - `SubscriptionScanJob` 只负责外部订阅目标扫描和候选项处理。

### 8.1 `SubscriptionScanJob` 通用扫描伪代码

实际实现见 `src/jobs/subscription_scan.py`，分两阶段（SchedulerManager 自动发现 jobs 目录注册，无需改注册代码）：

```python
class SubscriptionScanJob(BaseJob):
    job_type = "subscriptionScan"
    job_name = "订阅源扫描与导入"

    async def run(self, session, progress_callback):
        # 阶段1：扫描到期订阅目标 → 写候选项
        targets = await ext_cal_crud.get_due_subscription_targets(session)
        for target in targets:
            scraper = self.scraper_manager.get_scraper(target["provider"])
            if not getattr(scraper, "supports_subscription", False):
                continue
            items = await scraper.scan_subscription_target(target)  # 基类按 type 查表分发
            for item in items:
                await ext_cal_crud.upsert_subscription_item(session, **拆解(item))
            await ext_cal_crud.update_subscription_next_scan(session, ...)

        # 阶段2：处理 waiting 候选项 → 获取弹幕 → 建库
        waiting = (await ext_cal_crud.list_subscription_items(session, status="waiting"))["list"]
        for item in waiting:
            scraper = self.scraper_manager.get_scraper(item["provider"])
            await ext_cal_crud.set_subscription_item_status(session, ..., "importing")
            try:
                comments = await scraper.fetch_subscription_item_comments(item)  # 源用 aid/cid 定位
                anime_id = await crud.get_or_create_anime(session, item["parentTitle"], ...)
                source_id = await crud.link_source_to_anime(session, anime_id, provider, parentExternalId)
                ep_id = await crud.create_episode_if_not_exists(session, anime_id, source_id, episodeIndex, ...)
                await crud.save_danmaku_for_episode(session, ep_id, comments, self.config_manager)
                await ext_cal_crud.set_subscription_item_status(session, ..., "imported")
            except Exception:
                await ext_cal_crud.set_subscription_item_status(session, ..., "failed")
```

要点：
- Job 不判断 Bilibili 业务细节，只根据 `provider` 获取对应源实例，调用源实现的通用方法 `scan_subscription_target` / `fetch_subscription_item_comments`。
- Bilibili 的 UP 主/UP 系列/番剧差异由源内方法用 if/elif 按 `subscriptionType` 区分处理。
- 写库统一使用 `external_calendar_item` CRUD 与建库 CRUD，源实现不直接操作 ORM。
- 候选项状态存于 `extraData.itemStatus`（waiting/importing/imported/failed/review/ignored），与父目标 `subscriptionStatus` 区分；阶段2 用 `set_subscription_item_status` 推进。
- MVP 策略：番剧候选（强标识 season+epIndex+cid）状态 `waiting` 自动导入；UP 视频候选（需标题匹配）状态 `review`，留待阶段 D 人工/规则匹配，不自动导入。
- 建库聚合：同一订阅父目标的分集用 `parentExternalId` 作为源 `mediaId`，归到同一 anime/source 下。


## 9. 认证与安全

1. Bilibili 认证复用 Bilibili 弹幕源配置，不另造一套账号配置；订阅助手只读取能力状态，不直接保存 Cookie。
2. Bangumi/Trakt 订阅入口必须依赖现有 token/OAuth 状态；未填 token 或认证无效时不作为订阅源。
3. 前端设置页输入敏感字段时默认脱敏显示，只允许覆盖，不明文回显。
4. 日志禁止打印 Cookie、Token、完整请求头。
5. 失败时只提示「登录失效/风控/权限不足」，不要把响应中的敏感内容写日志。

## 10. 接口返回示例与迁移细化

### 10.1 `available-sources` 返回 JSON 示例

```json
{
  "calendarSources": [
    {
      "provider": "bangumi",
      "displayName": "Bangumi",
      "sourceType": "metadata",
      "available": true,
      "authRequired": true,
      "authStatus": "valid",
      "features": ["calendar", "watching", "subscribe"],
      "reason": null
    },
    {
      "provider": "trakt",
      "displayName": "Trakt",
      "sourceType": "metadata",
      "available": false,
      "authRequired": true,
      "authStatus": "missing",
      "features": [],
      "reason": "Trakt OAuth 未认证"
    }
  ],
  "danmakuSources": [
    {
      "provider": "bilibili",
      "displayName": "Bilibili",
      "sourceType": "danmaku",
      "available": true,
      "authRequired": true,
      "authStatus": "valid",
      "features": ["up", "up_series", "bangumi"],
      "reason": null
    }
  ],
  "summary": {
    "availableCount": 2,
    "unavailableCount": 1
  }
}
```

约定：
- `available=false` 的源不进入主操作卡片，只进入「配置提示」。
- `authStatus` 枚举：`none`、`missing`、`invalid`、`valid`、`unknown`。
- Bilibili 的 `available` 必须同时满足「源已加载」「源已启用」「订阅所需凭据可用」。

### 10.2 通用订阅 API request/response schema

创建订阅目标：

```json
POST /api/ui/subscriptions/targets
{
  "provider": "bilibili",
  "type": "up",
  "payload": {
    "uid": "123456",
    "remark": "字幕组A"
  },
  "runNow": true
}
```

响应：

```json
{
  "id": 1024,
  "provider": "bilibili",
  "externalId": "up:123456",
  "type": "bilibili_up",
  "title": "字幕组A",
  "status": "pending",
  "message": "订阅目标已创建"
}
```

订阅目标列表：

```json
GET /api/ui/subscriptions/targets?provider=bilibili&type=bilibili_up&status=pending&page=1&pageSize=20
{
  "total": 1,
  "list": [
    {
      "id": 1024,
      "provider": "bilibili",
      "externalId": "up:123456",
      "type": "bilibili_up",
      "title": "字幕组A",
      "enabled": true,
      "status": "pending",
      "extraData": {
        "uid": 123456,
        "scanMode": "recent_videos"
      }
    }
  ]
}
```

候选项列表：

```json
GET /api/ui/subscriptions/items?provider=bilibili&parentExternalId=up:123456&status=review
{
  "total": 1,
  "list": [
    {
      "id": 2048,
      "provider": "bilibili",
      "externalId": "video:BVxxxx",
      "type": "bilibili_video_candidate",
      "title": "视频标题",
      "status": "review",
      "confidence": 0.72,
      "parentExternalId": "up:123456"
    }
  ]
}
```

统一约定：
- API 入参永远是 `provider/type/payload`，不出现 provider 专用 path。
- API 返回永远是 `provider/externalId/type/title/status/extraData`，前端据此渲染。
- provider 私有差异只存在于 `payload` 和 `extraData`。

### 10.3 现有外部数据表通用化草案

不新增 Bilibili 专表，只扩展 `ExternalCalendarItem` 的使用约定。ORM 字段保持现状，重点是补充 CRUD 与查询包装：

```python
# 伪代码：统一创建/更新外部订阅目标
async def upsert_external_subscription(
    session,
    provider: str,
    external_id: str,
    title: str,
    subscription_type: str,
    extra: dict,
    status: str = "pending",
):
    # 说明：upsert_items 当前会把未知字段收集到 extraData，
    # 因此这里把 subscriptionType 和 Bilibili 特有字段平铺到 payload，避免新增表。
    payload = {
        "provider": provider,
        "externalId": external_id,
        "animeTitle": title,
        "animeType": extra.get("animeType", "subscription"),
        "subscriptionType": subscription_type,
        **extra,
    }
    await external_calendar.upsert_items(session, provider, [payload])
    await external_calendar.mark_subscribed(
        session,
        provider,
        external_id,
        status=status,
        item=payload,
    )
```

Bilibili 三类订阅目标映射：

```python
# UP 主
provider = "bilibili"
external_id = f"up:{uid}"
subscription_type = "bilibili_up"

# UP 主某系列
provider = "bilibili"
external_id = f"up_series:{uid}:{keyword_hash}"
subscription_type = "bilibili_up_series"

# B站番剧
provider = "bilibili"
external_id = f"bangumi:{season_id or media_id}"
subscription_type = "bilibili_bangumi"
```

视频/分集候选也落 `external_calendar_item`，但 `isSubscribed=false`，通过 `extraData.parentExternalId` 关联父订阅：

```python
provider = "bilibili"
external_id = f"video:{bvid}"
anime_type = "episode_candidate"
subscription_type = "bilibili_video_candidate"
extraData = {
    "parentExternalId": "up:123456",
    "bvid": bvid,
    "cid": cid,
    "pageCount": 1,
    "confidence": 0.91,
    "matchedProvider": "dandanplay",
    "matchedMediaId": "xxx",
    "matchedEpisodeIndex": 3,
}
```

索引建议：
- MVP 不新增索引，先复用现有 `idx_external_provider_external_unique` 与 `idx_external_subscription_status`。
- 如果后续 review 列表或 Bilibili 扫描明显变慢，再考虑加 `(provider, updated_at)` 或迁移 `extraData` 为 JSON 字段。


### 10.4 `external_calendar_item` CRUD 查询函数清单

在 `src/db/crud/external_calendar.py` 继续扩展通用函数，不新增 Bilibili 专用 CRUD：

- `list_subscription_targets(session, provider=None, subscription_type=None, status=None, keyword=None, page=1, page_size=20)`
  - 查询 `isSubscribed=true` 的订阅目标。
  - 通过 `provider` 和 `extraData.subscriptionType` 做通用过滤。
- `upsert_subscription_target(session, provider, external_id, title, subscription_type, extra, status='pending')`
  - 通用创建/更新订阅目标。
  - 内部复用 `upsert_items()` 和 `mark_subscribed()`。
- `update_subscription_target(session, provider, external_id, enabled=None, extra_patch=None, status=None)`
  - 修改备注、过滤条件、启用状态、状态。
  - `enabled=false` 可映射为 `isSubscribed=false` 或 `extraData.enabled=false`，MVP 推荐保留 `isSubscribed=true` 且写 `extraData.enabled=false`，避免取消订阅和暂停订阅混淆。
- `list_subscription_items(session, parent_external_id=None, provider=None, subscription_type=None, status=None, keyword=None, page=1, page_size=20)`
  - 查询视频候选/分集候选。
  - 候选项通常 `isSubscribed=false`，依靠 `extraData.parentExternalId` 关联父订阅目标。
- `upsert_subscription_item(session, provider, external_id, title, subscription_type, parent_external_id, extra, status='waiting')`
  - 写入扫描产生的候选项。
- `retry_subscription_item(session, provider, external_id)`
  - 将 `review/failed/ignored` 等状态重置为 `waiting` 或 `pending`。
- `ignore_subscription_item(session, provider, external_id)`
  - 标记候选项为 `ignored`。
- `get_due_subscription_targets(session, provider=None, limit=50)`
  - 读取到期目标；到期时间从 `extraData.nextScanAt` 读取。
  - MVP 可先在 Python 层过滤；如果性能不足，再考虑增加真实列或 JSON 字段索引。

注意：当前 `extraData` 是 `TEXT`，跨数据库 JSON 查询不一定一致；MVP 允许在 Python 层反序列化过滤，先保证实现简单可靠。

### 10.5 订阅助手 UI 草图与组件拆分

整体气质：极简仪表盘 + Bento Grid，强调「哪些订阅源可用」「下一步该做什么」。使用 Ant Design 现有组件与 Tailwind 工具类，保持当前项目风格。

组件结构：

```text
SubscriptionPage
├─ SubscriptionOverviewCards        # 可用源数量、待处理、失败、今日扫描
├─ SubscriptionAssistantTab
│  ├─ SourceCapabilityGrid          # 动态源卡片，只显示 available=true 的主操作
│  ├─ UnavailableSourceTips         # 未认证/未加载源的配置提示
│  └─ QuickSubscribeModal           # 根据 provider/type 渲染不同表单
├─ CalendarSubscriptionTab
│  ├─ SubscriptionCalendar          # 从 batch-manage 迁移
│  └─ SubscribeConfirmModal
├─ ProviderSubscriptionTab          # 根据 availableSources 动态挂载源详情，不写死专用路由
│  ├─ ProviderTargetTypeCards       # 当前 provider 支持的订阅类型卡片
│  ├─ ProviderTargetList            # 已订阅目标列表
│  └─ ProviderItemReviewTable       # review/failed/manual match
└─ SubscriptionRecordsTab
   ├─ SubscriptionFilterBar
   └─ SubscriptionRecordTable
```

交互规则：
- 可用源卡片：绿色状态点 +「立即订阅」按钮；hover 轻微上浮。
- 不可用源提示：灰色卡片 + 明确原因 +「去配置」按钮，不提供订阅动作。
- Provider 订阅类型入口来自 `availableSources.features`，例如 Bilibili 会渲染 `UP 主`、`UP 系列`、`B站番剧` 三张卡片；其他源以后也按同一组件渲染。
- 移动端使用纵向卡片流；桌面端使用 2~3 列 Bento 网格。
- 订阅记录表以状态色区分：pending=蓝、importing=紫、imported=绿、failed=红、review=橙。

视觉提示词：干净、模块化、圆角、柔和阴影、高信息密度但不拥挤；避免霓虹、过度玻璃拟态、复杂动画和娱乐化贴纸风。

## 11. 分阶段实施

### 阶段 A：页面拆分

- 新增订阅路由和页面。
- 把日历订阅从 `batch-manage.jsx` 提取到订阅页。
- 批量管理只保留本地源管理。
- 不改后端数据库，风险最低。

### 阶段 B：订阅源能力探测 + 统一订阅记录

- 新增 `/api/ui/subscriptions/available-sources`，统一判断 Bilibili/Bangumi/Trakt/TMDB 是否可作为订阅源。
- 新增 `/api/ui/subscriptions` 查询接口。
- 增加订阅记录 Tab，可查看 pending/importing/imported/failed。
- 增加失败重试与取消。

### 阶段 C：Bilibili 订阅 MVP

- 复用 `external_calendar_item` 保存 Bilibili 订阅目标、视频候选和番剧分集候选，不新增专表。
- 实现 Bilibili 弹幕源可用性校验、WBI 签名、UP 主投稿扫描、UP 系列关键词过滤、B站番剧分集扫描。
- 第一阶段只自动导入高置信度结果；低置信度进入人工 review。

### 阶段 D：规则与人工匹配增强

- 增加标题处理规则：忽略关键词、删除关键词、标题映射、集数偏移。
- 视频列表支持人工指定番剧/分集后重新导入。
- 第二阶段再评估 B站合集/收藏夹订阅，不在 MVP 中强塞。

## 12. 风险与回滚

- 订阅源不可用误展示：后端统一能力探测，前端只渲染 `available=true`。
- Bilibili 弹幕源未加载：所有 Bilibili API 前置校验，避免后台任务空跑。
- Bangumi/Trakt 未认证：不显示订阅入口，只显示配置提示。
- Bilibili API 风控：使用随机扫描间隔、页大小限制、失败退避。
- 标题误匹配：设置置信度阈值，低置信度不自动入库。
- 多 P 视频复杂：第一阶段直接跳过，避免误导入合集。
- 数据混杂：通过 `extraData.subscriptionType`、`provider`、`externalId` 前缀约束语义，避免把 Bilibili 候选数据当普通日历条目误展示。
- 回滚：阶段 A 只改前端路由和组件；后端 MVP 不新增表，只扩展 `external_calendar_item` 使用约定；关闭 Bilibili 扫描任务即可停止新链路。
