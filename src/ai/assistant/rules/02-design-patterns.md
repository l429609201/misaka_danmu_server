# 设计模式

本文档定义 Misaka Danmu Server 的核心设计模式和业务流程。所有 AI Agent 在实现功能时，**必须严格遵循**这些模式，不得自行发明新的实现方式。

---

## 🎯 核心设计模式

### 1. 任务系统模式 (Task System Pattern)

所有后台任务必须遵循统一的生命周期管理。

#### 规则

1. **任务定义**：
   - 定时作业继承 `BaseJob`（`src/jobs/base.py`），或
   - 定义为 `async def` 协程函数，签名形如 `(session, progress_callback, ...)`

2. **任务提交**：真实签名见 `src/services/task_manager.py:836`

   ```python
   # 第一个参数是 coro_factory：接收 (session, progress_callback) 并返回协程的工厂
   # 返回值是 (task_id, done_event) 元组，不是单个 task_id
   task_id, done_event = await task_manager.submit_task(
       lambda s, cb: refresh_episode_task(episode_id, s, scraper_manager, rate_limiter, cb, config_manager),
       f"刷新分集: {title}",              # title：位置参数，不是 task_title
       unique_key=unique_key,             # 去重键，防同一目标重复排队
       task_type="refresh_episode",       # 任务类型标识
       task_parameters={"episodeId": episode_id},
       queue_type="download",             # download / management / fallback
   )
   ```

   完整参数：`coro_factory`, `title`, `scheduled_task_id`, `unique_key`,
   `run_immediately`, `task_type`, `task_parameters`, `queue_type`。

   ⚠️ **不存在** `func` / `func_kwargs` / `task_title` / `task_description` 这些参数，
   传了会直接 `TypeError`。业务逻辑要用 `lambda s, cb: ...` 包成工厂传入。

3. **进度报告**：
   ```python
   await progress_callback(50, "正在处理第 10/20 集")
   ```

4. **结果标记**（均定义在 `src/utils/task_exceptions.py`）：
   ```python
   # 成功
   raise TaskSuccess("任务完成，共导入 15 集")

   # 失败
   raise TaskFailed("数据源无效，未获取到弹幕")

   # 暂停（速率限制）
   raise TaskPauseForRateLimit(retry_after_seconds=60)
   ```

#### 禁止的做法

❌ 直接使用 `asyncio.create_task()` 启动后台任务
❌ 在任务函数中使用 `return` 返回结果（必须用异常标记）
❌ 不报告进度（用户看不到任务状态）

---

### 2. Webhook 导入流程 (Webhook Import Flow)

Webhook 触发的弹幕导入必须遵循"解析 → 分发 → 搜索 → 导入"的四阶段流程。

#### 流程图

```
媒体库 Webhook 事件
    ↓
[1. 解析阶段] webhook/{emby,plex,jellyfin}.py
    - 提取标题、季度、集数
    - 提取元数据 ID（TMDB/IMDB/TVDB）
    ↓
[2. 分发阶段] webhook/base.py::dispatch_task()
    - 检查全局开关和过滤规则
    - 处理延时导入
    - 提交任务到 TaskManager
    ↓
[3. 搜索阶段] tasks/webhook.py::webhook_search_and_dispatch_task()
    - 调用 unified_search() 搜索所有数据源
    - AI 评分选择最佳匹配
    - 检查库内已有源（+3000分加成）
    ↓
[4. 导入阶段] tasks/import_core.py::edited_import_task()
    - 验证第一集有弹幕
    - 逐集下载弹幕
    - 保存到数据库
```

#### 关键约束

1. **解析与执行分离**：Webhook 处理器只负责解析，不执行下载
2. **搜索锁机制**：同一作品同季度的多个 Webhook 请求会被合并
3. **模糊匹配优先**：优先复用库内已有源（通过 title + season 匹配）
4. **第一集验证**：必须验证第一集有弹幕，否则任务失败

---

### 3. 数据源管理模式 (Scraper Management Pattern)

所有数据源必须实现统一的接口和配置化管理。

#### 数据源结构

```python
class BilibiliScraper:
    # 必须字段
    __version__ = "1.2.1"  # 版本号（修改时必须更新）
    
    configurable_fields = [
        {
            "name": "useProxy",
            "label": "使用代理",
            "type": "switch",
            "default": False
        }
    ]
    
    # 核心接口
    async def search(self, keyword, **kwargs) -> List[SearchResult]:
        """搜索作品"""
        pass
    
    async def get_episode_list(self, media_id) -> List[Episode]:
        """获取分集列表"""
        pass
    
    async def get_comments(self, episode_id, **kwargs) -> List[Comment]:
        """获取弹幕"""
        pass
```

#### 规则

1. **版本管理**：
   - 每次修改数据源，必须更新 `__version__`
   - 格式：`major.minor.patch`
   - 重大 API 变更 → major +1
   - 功能增强 → minor +1
   - Bug 修复 → patch +1

2. **配置化**：
   - 所有可配置项必须在 `configurable_fields` 中声明
   - 不得硬编码配置值
   - 必须提供默认值

3. **错误处理**：
   - 网络错误抛出 `HTTPException` 或 `TimeoutError`
   - 解析错误抛出 `ValueError` 或 `KeyError`
   - 不得静默失败（返回空列表必须有明确原因）

---

### 4. 弹幕导入流程 (Danmaku Import Flow)

弹幕导入必须遵循"验证 → 去重 → 批量写入"的流程。

#### 流程

```python
# 1. 验证第一集
first_episode_comments = await scraper.get_comments(first_episode_id)
if not first_episode_comments:
    raise TaskFailed("数据源验证失败，第一集无弹幕")

# 2. 创建数据库条目
anime_id = await get_or_create_anime(...)
source_id = await link_source_to_anime(anime_id, provider, media_id)

# 3. 逐集导入
for episode in episodes:
    comments = await scraper.get_comments(episode.episodeId)
    
    # 去重检查
    existing_episode = await get_episode_by_index(source_id, episode.index)
    if existing_episode and existing_episode.commentCount > 0:
        if len(comments) <= existing_episode.commentCount:
            logger.info(f"集 {episode.index} 弹幕数量未增加，跳过")
            continue
    
    # 保存弹幕
    episode_db_id = await create_episode_if_not_exists(...)
    await save_danmaku_for_episode(session, episode_db_id, comments)
```

#### 关键约束

1. **第一集验证**：必须先验证第一集有弹幕，避免创建无效条目
2. **去重保护**：已有弹幕且数量未增加时，跳过写入
3. **批量提交**：使用 `session.flush()` 而非每条 `commit()`
4. **错误恢复**：单集失败不影响其他集的导入

---

### 5. 配置管理模式 (Configuration Pattern)

系统配置必须通过 `ConfigManager` 统一管理。

#### 规则

```python
# ✅ 正确：通过 ConfigManager 读取
config_manager = ConfigManager()
proxy_enabled = await config_manager.get("proxyEnabled", "false")

# ❌ 错误：硬编码配置
PROXY_ENABLED = True  # 不可配置
```

#### 配置分类

| 配置类型 | 存储位置 | 示例 |
|---------|---------|------|
| 系统配置 | `system_config` 表 | `webhookEnabled`, `aiMatcherModel` |
| 数据源配置 | `scrapers` 表 | `useProxy`, `cookie` |
| 运行时配置 | 内存缓存 | `rate_limiter` 状态 |

---

### 6. 单剧过滤模式 (Single Series Filter Pattern)

对数据源返回的分集列表应用黑名单和单剧过滤后，**必须重新编号**。

#### 正确流程

```python
# 1. 应用黑名单过滤
filtered = apply_blacklist_filter(episodes)

# 2. 应用单剧过滤（保留前N集或指定范围）
filtered = apply_single_series_filter(filtered, keep_count=12)

# 3. 重新编号（关键步骤！）
for i, episode in enumerate(filtered):
    episode.episodeIndex = i + 1  # 从 1 开始连续编号

return filtered
```

#### 为什么必须重新编号？

- **问题**：过滤后集数不连续（1, 2, 3, 8, 14, 15...）
- **影响**：导入时检测到"已有第 8 集"会跳过，导致重复导入
- **解决**：重新编号为连续序列（1, 2, 3, 4, 5, 6...）

---

### 7. AI 匹配模式 (AI Matching Pattern)

使用 AI 选择最佳数据源时，必须遵循"特征提取 → 评分 → 排序"的流程。

#### 评分规则

```python
score = 0

# 1. 库内已有源 +3000 分（最高优先级）
if is_existing_source(provider, media_id):
    score += 3000

# 2. 标题相似度（0-100）
score += title_similarity * 10

# 3. 年份匹配 +50 分
if year_match:
    score += 50

# 4. 分集数量匹配 +30 分
if episode_count_match:
    score += 30

# 5. 元数据 ID 匹配 +100 分
if tmdb_id_match or imdb_id_match:
    score += 100
```

#### 关键约束

1. **库内源优先**：已存在的源必须获得最高加成
2. **标题模糊匹配**：使用 `fuzz.ratio()` 计算相似度
3. **季度精确匹配**：同名作品不同季度必须区分

---

## 🚨 违规案例

### 案例 1：绕过任务管理器

❌ **错误**：
```python
async def import_anime(title):
    # 直接启动后台任务
    asyncio.create_task(download_danmaku(title))
```

✅ **正确**：
```python
async def import_anime(title, task_manager, scraper_manager, config_manager):
    # coro_factory 形式：lambda 接收 (session, progress_callback)
    task_id, _ = await task_manager.submit_task(
        lambda s, cb: download_danmaku(title, s, scraper_manager, cb, config_manager),
        f"导入: {title}",
        unique_key=f"import-{title}",
        task_type="import",
    )
```

---

### 案例 2：Webhook 直接执行导入

❌ **错误**：
```python
# webhook/emby.py
async def handle_library_new(data):
    title = extract_title(data)
    # ❌ Webhook 不应该直接调用导入逻辑
    await import_anime(title)
```

✅ **正确**：
```python
# webhook/emby.py（继承 BaseWebhook，见 src/webhook/base.py:46）
async def handle_library_new(self, data, webhook_source):
    title = extract_title(data)
    # ✅ 只负责解析和分发；unique_key 与 webhook_source 均为必填
    await self.dispatch_task(
        task_title=f"Webhook导入: {title}",
        unique_key=f"webhook-{title}",
        payload={"animeTitle": title},
        webhook_source=webhook_source,
    )
```

---

### 案例 3：硬编码配置

❌ **错误**：
```python
class BilibiliScraper:
    def __init__(self):
        self.use_proxy = True  # 硬编码
```

✅ **正确**：
```python
class BilibiliScraper:
    configurable_fields = [
        {"name": "useProxy", "type": "switch", "default": False}
    ]
    
    def __init__(self, config):
        self.use_proxy = config.get("useProxy", False)
```

---

## 📝 实现新功能时的检查清单

- [ ] 确认是否有现有模式可以复用
- [ ] 如果是后台任务，必须通过 `TaskManager` 提交
- [ ] 如果是 Webhook，必须继承 `BaseWebhook`（`src/webhook/base.py`）
- [ ] 如果是数据源，必须实现 `BaseScraper` 接口
- [ ] 如果涉及配置，必须通过 `ConfigManager` 读取
- [ ] 如果涉及 AI 匹配，必须遵循评分规则
- [ ] 如果过滤分集，必须重新编号

---

*最后更新：2026-01-08*
