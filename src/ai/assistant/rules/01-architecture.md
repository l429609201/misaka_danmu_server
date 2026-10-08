# 架构和模块职责

本文档定义 Misaka Danmu Server 的模块边界、依赖方向和职责划分。

> 📘 **完整架构规范**: 详见 [`docs/最终架构规范.md`](../../../docs/最终架构规范.md)（唯一权威）
> ⚡ **快速参考**: 详见 [`docs/架构规范-快速参考.md`](../../../docs/架构规范-快速参考.md)

---

## 🎯 核心架构原则

### 单向依赖流

```text
API / Tasks / Jobs / Workflows
        ↓
Services（DatabaseService、CacheService、ConfigService、FileStorageService）
        ↓
Repository / QueryRepository
        ↓
ORM Models
```

### 数据库访问唯一入口

所有层级通过 `DatabaseService` 访问数据库：

```python
from src.services.service_container import get_database_service

db = get_database_service()
async with db.transaction():
    anime = await db.anime.get_by_id(anime_id)
```

### 严格禁止

- ❌ API、Tasks、Jobs、Workflows 直接导入 Repository
- ❌ Repository 调用 Service 或直接 `commit()`
- ❌ 使用已废弃的 `src.db.crud`
- ❌ 函数内延迟导入（除 TYPE_CHECKING）
- ❌ Utils 依赖业务层

---

## 🏗️ 模块边界表

| 模块 | 职责范围 | 禁止依赖 | 代表文件 |
|------|---------|---------|---------|
| `src/core/` | 配置加载、启动/关闭生命周期、缓存、默认配置 | 业务逻辑、数据源 | `config.py`, `app_lifecycle.py`, `default_configs.py` |
| `src/db/` | 数据库模型（ORM）、Repository、迁移 | 业务逻辑、HTTP 请求、任务调度 | `orm_models.py`, `repositories/*.py`, `migrations.py` |
| `src/schemas/` | Pydantic 请求/响应模型定义 | 数据库、业务逻辑 | `anime.py`, `comments.py`, `search.py`, `dandan.py`, `metadata.py`, `ui_models.py` |
| `src/scrapers/` | 数据源抓取逻辑、弹幕解析、站点适配 | 任务管理、通知系统、数据库直接操作 | `bilibili.py`, `iqiyi.py` |
| `src/metadata_sources/` | 元数据源适配（TMDB/Bangumi/豆瓣/TVDB/IMDb） | 任务调度、数据库直接操作 | `bangumi.py`, `tmdb.py`, `base.py` |
| `src/media_servers/` | 媒体服务器适配（Emby/Jellyfin/Plex 读取媒体库） | 任务执行细节 | `emby.py`, `jellyfin.py`, `plex.py` |
| `src/tasks/` | 后台任务编排、导入流程、Webhook 任务处理 | 具体数据源实现细节 | `webhook.py`, `import_core.py` |
| `src/jobs/` | 可被调度的定时作业（追更、清理、数据同步） | 数据源实现细节 | `auto_finish.py`, `danmaku_cleanup.py` |
| `src/internal_tasks/` | 内部轮询任务（Token 刷新、趋势统计、日报） | HTTP 层 | `bgm_token_refresh.py`, `daily_summary.py` |
| `src/services/` | 业务服务层、搜索匹配、任务管理、元数据管理 | 数据库直接操作（必须通过 crud） | `search.py`, `task_manager.py`, `scraper_manager.py` |
| `src/notification/` | 通知渠道、事件定义、订阅匹配、模板解析 | 任务执行细节 | `events.py`, `subscription_matcher.py`, `template_resolver.py` |
| `src/api/` | HTTP 接口、路由、请求验证 | 直接操作数据库（必须通过 services/crud） | `control/*.py`, `ui/*.py`, `dandan/*.py` |
| `src/routes/` | 独立挂载的路由（不走 `api_router` 聚合） | 直接操作数据库 | `notification_template.py` |
| `src/webhook/` | Webhook 载荷解析、媒体库事件识别 | 任务执行细节（只负责解析和分发） | `base.py`, `emby.py`, `plex.py` |
| `src/commands/` | 播放器搜索框 @指令实现 | 数据库直接操作 | `clear_cache.py`, `rate_limit_status.py` |
| `src/rate_limit/` | 访问频率限制与配额统计 | 业务逻辑 | — |
| `src/ai/` | AI 匹配、标题识别、御坂助手（工具/技能/知识库） | 具体数据源、任务调度 | `matcher.py`, `assistant/` |
| `src/tools/` | 供 LLM 调用的诊断工具（只读查询、性能指标） | 写操作、业务编排 | `database_tools.py`, `performance_tools.py` |
| `src/utils/` | 工具函数、通用逻辑、格式转换 | 业务规则、数据库、网络请求 | `episode_filter.py`, `filename_parser.py` |

---

## ⚠️ 重要架构变更

### crud 层已完全废弃（2025-01）

**旧路径（已删除）**：
```python
from src.db import crud  # ❌ 目录已删除
from src.db.crud.anime import get_anime_by_id  # ❌
```

**新路径（必须使用）**：
```python
from src.services.service_container import get_database_service

db = get_database_service()
async with db.transaction():
    anime = await db.anime.get_by_id(anime_id)
```

### 响应模型迁移完成（2025-01）

**旧路径（已删除）**：
```python
from src.db import models  # ❌ 此文件已删除
```

**新路径（必须使用）**：
```python
from src.schemas.comments import Comment, CommentsResponse
from src.schemas.search import AnimeInfo, AnimeSearchResponse
from src.schemas.anime import AnimeCreate, AnimeDetail
from src.schemas.ui_models import TaskInfo, User
```

### ConfigManager 已迁移为 ConfigService（2026-01）

**旧方式（已废弃）**：
```python
from src.db import ConfigManager  # ❌
config_manager = ConfigManager()
```

**新方式（必须使用）**：
```python
from src.services.service_container import get_config_service

config = get_config_service()
value = await config.get("key", "default")
```

---
from src.schemas.anime import AnimeCreate, AnimeDetail
from src.schemas.metadata import MetadataDetailsResponse
from src.schemas.ui_models import TaskInfo, ApiTokenInfo, User
from src.schemas.dandan import DandanSearchAnimeResponse
from src.schemas.import_schemas import EditedImportRequest
```

**迁移映射表**：
| 旧位置 | 新位置 | 说明 |
|--------|--------|------|
| `src/db/models.py` | **已删除** | 所有模型已迁移到 `src/schemas/*` |
| `models.Comment` | `src/schemas/comments.py` | 弹幕相关模型 |
| `models.AnimeInfo` | `src/schemas/search.py` | 搜索相关模型 |
| `models.MatchInfo` | `src/schemas/match.py` | 匹配相关模型 |
| `models.AnimeCreate` | `src/schemas/anime.py` | 动漫管理模型 |
| `models.MetadataDetailsResponse` | `src/schemas/metadata.py` | 元数据相关模型 |
| `models.TaskInfo` | `src/schemas/ui_models.py` | UI/任务/用户模型 |
| `models.DandanSearchAnimeResponse` | `src/schemas/dandan.py` | 弹弹Play API 模型 |
| `models.EditedImportRequest` | `src/schemas/import_schemas.py` | 导入流程模型 |

**注意事项**：
1. `CommentResponse` 已重命名为 `CommentsResponse`（更语义化）
2. ORM 模型仍在 `src/db/orm_models.py`（不要与响应模型混淆）
3. 所有 API 响应模型必须从 `src/schemas/*` 导入
4. 旧代码中的 `from src.db import models` 必须修改为对应的 schema 导入

---

## 🔄 依赖方向规则

### 允许的调用链

```text
API / Tasks / Jobs / Workflows
        ↓
DatabaseService / CacheService / ConfigService / FileStorageService
        ↓
Repository / QueryRepository
        ↓
ORM Models
```

### 禁止的依赖

❌ `Repository` → `Service` （Repository 不得调用业务服务）
❌ `Scrapers` → `Tasks` （数据源不得依赖任务系统）
❌ `API/Tasks/Jobs/Workflows` → `Repository` （必须通过 DatabaseService）
❌ `Utils` → `Service/Repository/ORM` （工具函数不得依赖业务逻辑）

---

## 🎯 关键约束

### 1. 数据库访问规则

- ✅ **通过 DatabaseService 访问**：所有数据库操作必须通过 `get_database_service()`
- ❌ **禁止直接导入 Repository**：业务代码不得 `from src.db.repositories import XXX`
- ✅ **事务由调用方管理**：使用 `async with db.transaction()` 管理事务边界

**正确示例**：
```python
# api/control/anime.py
from src.services.service_container import get_database_service

@router.get("/anime/{id}")
async def get_anime(id: int):
    db = get_database_service()
    async with db.transaction():
        return await db.anime.get_by_id(id)
```

**错误示例**：
```python
# ❌ api/control/anime.py
from src.db.repositories.anime import AnimeRepository  # ❌ 禁止

@router.get("/anime/{id}")
async def get_anime(id: int):
    repo = AnimeRepository()  # ❌ 绕过 DatabaseService
    return await repo.get_by_id(id)
```
```

---

### 2. Repository 事务规则

- ✅ **Repository 只 flush()**：Repository 内只调用 `await session.flush()`
- ❌ **Repository 不 commit()**：事务提交由上层（DatabaseService/API）负责
- ✅ **通过 DatabaseService 管理事务**：使用 `async with db.transaction()` 自动管理

**正确示例**：
```python
# db/repositories/anime.py
class AnimeRepository(BaseRepository[Anime]):
    async def update(self, anime_id: int, **data):
        anime = await self.get_by_id(anime_id)
        for key, value in data.items():
            setattr(anime, key, value)
        await self._session.flush()  # ✅ 只 flush
        return anime
```

**错误示例**：
```python
# ❌ db/repositories/anime.py
class AnimeRepository(BaseRepository[Anime]):
    async def update(self, anime_id: int, **data):
        anime = await self.get_by_id(anime_id)
        for key, value in data.items():
            setattr(anime, key, value)
        await self._session.commit()  # ❌ Repository 不能 commit
        return anime
```

---

### 3. 配置访问规则

- ✅ **通过 ConfigService**：所有配置访问通过 `get_config_service()`
- ❌ **禁止直接读取环境变量**：不在业务代码中使用 `os.getenv()`

**正确示例**：
```python
from src.services.service_container import get_config_service

config = get_config_service()
proxy_enabled = await config.get("scrapers.bilibili.useProxy", False)
```

---

### 4. 文件操作规则

- ✅ **通过 FileStorageService**：所有文件操作通过 `get_file_storage_service()`
- ❌ **禁止直接操作文件系统**：Repository/Utils 不直接使用 `Path.open()` 或 `aiofiles`

**正确示例**：
```python
from src.services.service_container import get_file_storage_service

file_storage = get_file_storage_service()
await file_storage.save_danmaku(episode_id, content)
```

---

### 5. 任务系统规则

- ✅ **统一调度**：所有后台任务必须通过 `TaskManager.submit_task()`
- ✅ **进度报告**：使用 `progress_callback` 报告进度
- ✅ **结果标记**：使用 `TaskSuccess` / `TaskFailed` 标记结束
- ❌ **禁止直接启动**：不得使用 `asyncio.create_task()` 绕过管理器

---

### 3. 数据源管理规则

- ✅ **注册机制**：所有数据源必须在 `scrapers/` 下注册
- ✅ **接口统一**：实现 `BaseScraper` 或兼容接口
- ✅ **配置化**：使用 `configurable_fields` 定义配置项
- ✅ **版本管理**：每次修改必须更新 `__version__` 字段

---

### 4. Webhook 处理规则

- ✅ **解析与执行分离**：Webhook 只负责解析，任务执行由 `tasks/webhook.py` 处理
- ✅ **统一入口**：所有 Webhook 继承 `BaseWebhook`（`src/webhook/base.py`）
- ✅ **异步分发**：使用 `dispatch_task()` 异步提交任务

---

## 🔍 模块定位决策树

创建新模块或移动现有模块时，按以下顺序判断：

1. **是数据库模型或 CRUD 操作？** → `src/db/`
2. **是数据源抓取逻辑？** → `src/scrapers/`
3. **是后台任务编排？** → `src/tasks/`
4. **是业务服务逻辑（搜索、匹配、管理）？** → `src/services/`
5. **是 HTTP 接口？** → `src/api/`
6. **是 Webhook 解析？** → `src/webhook/`
7. **是通用工具函数？** → `src/utils/`
8. **是 AI 相关逻辑？** → `src/ai/`

---

## 🚨 常见违规案例

### 案例 1：业务逻辑写在 CRUD 层

❌ **错误**：
```python
# src/db/crud/anime.py
async def create_anime_with_notification(session, title):
    anime = Anime(title=title)
    session.add(anime)
    await session.flush()
    # ❌ CRUD 层不应该发送通知
    await notification_service.notify("新番剧创建")
    return anime
```

✅ **正确**：
```python
# src/db/crud/anime.py
async def create_anime(session, title):
    anime = Anime(title=title)
    session.add(anime)
    await session.flush()
    return anime

# src/services/anime_service.py
async def create_anime_with_notification(session, title):
    anime = await crud.create_anime(session, title)
    await notification_service.notify("新番剧创建")
    return anime
```

---

### 案例 2：数据源直接调用任务系统

❌ **错误**：
```python
# src/scrapers/bilibili.py
async def get_comments(self, video_id):
    comments = await self._fetch_comments(video_id)
    # ❌ 数据源不应该触发任务
    await task_manager.submit_task("process_comments", comments)
    return comments
```

✅ **正确**：
```python
# src/scrapers/bilibili.py
async def get_comments(self, video_id):
    # 只负责抓取和返回
    return await self._fetch_comments(video_id)

# src/tasks/import_core.py
async def import_episode(scraper, video_id):
    comments = await scraper.get_comments(video_id)
    # 任务层负责后续处理
    await save_comments_to_db(comments)
```

---

## 📝 修改模块时的检查清单

- [ ] 确认新功能属于哪个模块的职责范围
- [ ] 确认没有违反依赖方向规则
- [ ] 确认没有跨层直接调用（必须通过服务接口）
- [ ] 如果修改数据库模型，创建新的 Alembic 迁移
- [ ] 如果修改数据源，更新 `__version__` 字段
- [ ] 如果修改 API，确认前端对应代码是否需要更新
- [ ] 如果涉及事务，确认 Repository 只 flush()，不 commit()
- [ ] 所有导入都在文件顶部，无函数内延迟导入（除 TYPE_CHECKING）

---

*最后更新：2026-01-09*
