# AGENTS.md

This file is the primary instruction set for all AI agents and LLMs working in this repository. Local documentation takes precedence over general training data. You must follow this file and the rule documents it references.

---

## ⚡ CRITICAL: AUTONOMOUS EXECUTION MODE

You are NOT an advisor. You are an AUTONOMOUS coding agent that DOES the work, not suggests it.

### FORBIDDEN RESPONSE PATTERNS (will be rejected)

❌ "你需要检查 X 文件"
❌ "可能是 Y 有问题"
❌ "建议查看 Z"
❌ "应该修改 A"
❌ "请确认 B"

### REQUIRED RESPONSE PATTERNS

✅ [immediately calls `view` to check X]
✅ [immediately calls `codebase-retrieval` to find Y]
✅ [immediately reads Z, analyzes, reports findings]
✅ [immediately calls `str-replace-editor` to modify A]
✅ [immediately reads and verifies B]

---

## 🏛️ 强制架构遵循与违规优先修复

- **开始任务前必须阅读并严格遵循 [最终架构规范](docs/最终架构规范.md)**。这是本项目唯一权威架构文档；其他提示词、历史示例与之冲突时，以该文档为准。
- **读取、排查或修改代码时发现违反架构的情况，必须先修复已发现的违规，再继续原任务**。不得以“历史代码”“不是本次新增”或“先让功能跑起来”为由跳过。
- 数据库操作必须遵循 `调用方 → DatabaseService → Repository / QueryRepository → ORM`；调用方通过服务层声明事务，禁止绕过统一服务入口直接实例化仓储或操作 ORM/Session。Repository 只 `flush()`，不得 `commit()`，不得反向依赖 Service。
- 修复必须覆盖相关调用链并检查循环依赖；不得用函数内导入、动态导入或新增平行数据库入口掩盖架构问题。架构修复验证通过后，继续完成原任务。

## 📋 Pre-Flight Check (MANDATORY)

Before generating code or proposing changes, you MUST:

1. **Identify the domains** the task actually touches:
   - Task system? → Load `src/ai/assistant/rules/02-design-patterns.md` (Task System Pattern)
   - Database? → Load `src/ai/assistant/rules/01-architecture.md` (Database Access Rules)
   - Data source? → Load `src/ai/assistant/rules/02-design-patterns.md` (Scraper Management Pattern)
   - Webhook? → Load `src/ai/assistant/rules/02-design-patterns.md` (Webhook Import Flow)

2. **Use tools to investigate** before answering:
   ```
   When user reports: "NameError: name 'Anime' is not defined"
   
   Step 1: Extract file + line from traceback
   Step 2: Call `view` on that file (lines 1-50) to check imports
   Step 3: Call `view` on that file (error line ±50) for context
   Step 4: Identify root cause (missing import)
   Step 5: Call `str-replace-editor` to add the import
   Step 6: Report "已修复：在 webhook.py 第 26 行添加了 Anime 导入"
   ```

3. **Load relevant rules** from `src/ai/assistant/rules/`:
   - When modifying task system → Load pattern rules
   - When modifying database → Load architecture rules
   - When adding features → Load design patterns
   - When fixing bugs → Load code styles

4. **Follow existing patterns** strictly:
   - If `02-design-patterns.md` defines a pattern for the scenario, you MUST use it
   - Do not invent new patterns or abstractions
   - Match the style of surrounding code

---

## 📚 Task-to-Documentation Mapping

For work that changes repository behavior, identify domains and load applicable documents:

### Architecture and Module Boundaries
* **Primary Reference:** `src/ai/assistant/rules/01-architecture.md`
* **Required Constraints:**
  - Respect layer boundaries and dependency flow
  - Do not introduce circular dependencies
  - Database access MUST go through `DatabaseService` (via `get_database_service()`)
  - `src/db/crud/*` is DEPRECATED and has been deleted — never reference it
  - Tasks MUST be submitted via `TaskManager.submit_task()`
  - All imports at file top; no delayed imports except TYPE_CHECKING
  - Repository only `flush()`, never `commit()`

### Design Patterns and Business Logic
* **Primary Reference:** `src/ai/assistant/rules/02-design-patterns.md`
* **Required Constraints:**
  - Task system: Use `TaskManager.submit_task()` + `TaskSuccess`/`TaskFailed`
  - Webhook: Inherit `BaseWebhookHandler`, use `dispatch_task()`
  - Scraper: Implement `BaseScraper`, define `configurable_fields`, update `__version__`
  - Import flow: Validate first episode → Create DB entries → Download iteratively

### Coding Standards and Style
* **Primary Reference:** `src/ai/assistant/rules/03-code-styles.md`
* **Required Constraints:**
  - All functions MUST have type annotations
  - Public functions MUST have Chinese docstrings
  - Use `async/await` for I/O operations
  - Import order: stdlib → third-party → project
  - No circular imports (use delayed import if needed)

### Commands and Development Workflow
* **Primary Reference:** `src/ai/assistant/rules/04-commands.md`
* **Required Constraints:**
  - Use `python -m py_compile` to verify syntax
  - Use `alembic revision` for database schema changes
  - Use documented commands for testing and deployment

### 🖥️ 本机运行环境（NAS / Ubuntu 24.04 / Linux）

> 本节描述**当前开发机**的实际环境，优先于文档里的 Windows 示例（`start.bat` 等）。

* **没有 `python` 命令**：Ubuntu 24.04 只提供 `python3`（PEP 394）。项目依赖装在**仓库内 venv**，
  所以请用它自带的解释器：`venv/bin/python`（等价 `venv/bin/python3`）。
* **启动后端**（模块形式，不是 `main.py`）：
  ```bash
  cd ~/项目/DanmuApi/misaka_danmu_server
  venv/bin/python -m src.main            # 或：source venv/bin/activate && python -m src.main
  ```
  ⚠️ `python src.main` 是错的（会被当成文件名）；`python -m src.main` 才对。
* **启动/构建前端**（`web/`，锁文件是 `package-lock.json` ⇒ 用 npm）：
  ```bash
  cd web && npm run dev                  # 开发（vite）
  cd web && npm run build                # 产物 web/dist，由 src/frontend.py 挂载
  ```
* **数据库/缓存不在本机**：MySQL 8.1 与 Redis 都在 `192.168.10.220`（见 `config/config.yml`），
  本机**不需要**安装数据库；表结构由应用启动时的 `create_all` + `src/db/migrations.py` 自动同步。
* **安装依赖**：只在 venv 内安装（`venv/bin/pip install -r requirements.txt`）；
  **禁止** `--break-system-packages` 等破坏系统 Python 的做法。`venv/` 已在 `.gitignore` 中，不要删除。
* **后台任务务必用 PTY 启动**：需要看实时日志的后台任务（常驻服务、构建、pytest）请使用
  `run_pty` 工具，而不是 `bash` + `run_in_background`。
  原因：`bash` 作业通过管道捕获输出，被子进程缓冲（Python 默认块缓冲约 8KB）时**面板和
  `job_output` 都看不到任何日志**；PTY 下子进程行缓冲，日志逐行实时可见。
  `run_pty` 返回普通作业 id，用 `job_output` 读取、`job_kill` 停止。

---

## 🔄 Coupled Update Rules

When modifying the following, you must also update the listed artifacts:

| Changed Content | Must Also Update |
|---|---|
| Database model schema | New Alembic migration under `database/versions/` |
| Scraper configuration | Update `configurable_fields` and `__version__` |
| Task interface | Update related tests and task manager |
| API endpoint | Update frontend code if needed |
| Notification event | Update `notification_scopes` API |

---

## 🎯 Execution Guidelines

### Pattern Adherence

Avoid generic boilerplate. If `02-design-patterns.md` defines a project-level pattern for a scenario, you are REQUIRED to use it.

Examples:
- Task submission → Use `TaskManager.submit_task()`
- Database access → Use `DatabaseService` + `db/repositories/*.py` (NOT `db/crud/*.py`, which is deprecated)
- Configuration → Use `ConfigManager.get()`
- Webhook → Inherit `BaseWebhookHandler`

### Minimal Change Principle

Prefer the smallest correct change:
- Do not perform unrelated refactors
- Do not rename variables unless necessary
- Do not reformat unrelated code
- Focus on the specific problem

### Documentation Standards

- Public or cross-module functions MUST have Chinese docstrings
- Inline comments should explain "why", not "what"
- No redundant comments that restate code

### When Encountering Errors

1. **Extract** file and line number from traceback
2. **View** the file at error location (±50 lines)
3. **View** the file header (lines 1-50) to check imports
4. **Use codebase-retrieval** if the issue location is unclear
5. **Identify** the root cause from actual code
6. **Fix** using `str-replace-editor`
7. **Verify** with `python -m py_compile`
8. **Report** what was fixed and why

### Output Language

All responses, summaries, and documentation default to Chinese unless the user requests otherwise.

---

## 🚨 Common Mistakes to Avoid

### Mistake 1: Suggesting Instead of Doing

❌ Wrong:
```
User: "I got NameError: 'Anime' is not defined"
Agent: "你需要在 webhook.py 中导入 Anime 模型"
```

✅ Correct:
```
User: "I got NameError: 'Anime' is not defined"
Agent: [calls view on webhook.py lines 1-50]
       [identifies missing import]
       [calls str-replace-editor to add import]
       "已修复：在 webhook.py 第 26 行添加了 `Anime = orm_models.Anime`"
```

### Mistake 2: Not Loading Documentation

❌ Wrong:
```
Agent: "我建议使用 asyncio.create_task() 启动后台任务"
```

✅ Correct:
```
Agent: [loads src/ai/assistant/rules/02-design-patterns.md]
       [reads Task System Pattern]
       "根据项目设计模式，所有后台任务必须通过 TaskManager.submit_task() 提交：
       
       await task_manager.submit_task(
           func=my_task,
           task_title='任务标题'
       )"
```

### Mistake 3: Violating Architecture Rules

❌ Wrong:
```python
# In api/control/import_routes.py
from src.db.repositories.anime import AnimeRepository  # ❌ API 直接导入 Repository

@router.get("/anime/{id}")
async def get_anime(id: int):
    repo = AnimeRepository()  # ❌ 绕过 DatabaseService
    return await repo.get_by_id(id)
```

✅ Correct:
```python
# In api/control/import_routes.py
from src.services.service_container import get_database_service

@router.get("/anime/{id}")
async def get_anime(id: int):
    db = get_database_service()
    async with db.transaction():
        anime = await db.anime.get_by_id(id)  # ✅ 经 DatabaseService → Repository 访问
    return anime
```

---

## 🛠️ Available Internal Tools

This project provides specialized internal tools for LLM to diagnose issues and query system state. You MUST use these tools when relevant.

### 1. Database Tools (`src/tools/database_tools.py`)

**Purpose**: Query database schema and execute safe read-only SQL queries.

**Key Methods**:
```python
from src.tools.database_tools import get_database_tools

db_tools = get_database_tools()

# List all tables
tables = await db_tools.list_tables()
# Returns: [{table_name, model_class, comment, column_count}, ...]

# Get table schema
schema = await db_tools.get_table_schema("anime")
# Returns: {columns, relationships, indexes, ...}

# Search tables by keyword
results = await db_tools.search_tables("弹幕")
# Returns: [{table_name, match_type, matched_columns}, ...]

# Execute safe SQL query (read-only, auto-sanitized)
result = await db_tools.query(
    "SELECT id, title, season FROM anime WHERE title LIKE '%进击%' LIMIT 10"
)
# Returns: {success, columns, rows, row_count, masked_columns, execution_time_ms}

# Quick query (returns only rows)
rows = await db_tools.quick_query("SELECT COUNT(*) as count FROM anime")

# Get row count
count = await db_tools.get_table_row_count("anime")

# Explain query plan
plan = await db_tools.explain("SELECT * FROM anime WHERE season = 1")
# Returns: {success, plan, warnings, suggestions}
```

**When to Use**:
- User asks "有多少个番剧？" → `get_table_row_count("anime")`
- User asks "某个表的结构是什么？" → `get_table_schema(table_name)`
- User reports data inconsistency → Query relevant tables to verify
- Need to check if data exists → `query()` to verify

**Safety Features**:
- ✅ Only SELECT/EXPLAIN/SHOW/DESCRIBE allowed
- ✅ Auto-masks sensitive fields (password, token, secret)
- ✅ Transaction rollback (no side effects)
- ✅ Query timeout protection (default 30s)
- ✅ Max rows limit (default 100, max 1000)

---

## 🔧 Performance Diagnosis Tools

When user reports performance issues (slow tasks, high resource usage, timeouts), you MUST use the built-in performance tools to diagnose:

### Available Tools

1. **PerformanceTools** (`src/tools/performance_tools.py`)
   - Query system metrics (CPU, memory, disk, database pool, task queue)
   - Query task performance history
   - Get active alerts

2. **TaskProfiler Data** (`task_perf_events` table)
   - View step-by-step task execution time
   - Identify slow steps in task flows

3. **System Metrics** (`performance_metrics` table)
   - Historical resource usage trends
   - Database connection pool status
   - Cache hit rates

### Diagnostic Workflow

```python
# Step 1: Get system health overview
from src.tools.performance_tools import get_performance_tools

perf_tools = get_performance_tools()

# Check database health
db_health = await perf_tools.get_database_health()
# Returns: {pool_size, pool_usage_rate, active_connections, status}

# Check task queue status
task_status = await perf_tools.get_task_queue_status()
# Returns: {pending_tasks, running_tasks, queue_utilization, status}

# Check system resources
resources = await perf_tools.get_system_resources()
# Returns: {cpu_usage, memory_usage, disk_usage, status}

# Step 2: Get active alerts
alerts = await perf_tools.get_active_alerts()
for alert in alerts:
    print(f"[{alert['alert_level']}] {alert['alert_message']}")

# Step 3: Query specific metrics history
history = await perf_tools.get_metric_history(
    category="database",
    metric_name="db_pool_usage_rate",
    hours=24
)

# Step 4: Get metric aggregation
agg = await perf_tools.get_metric_aggregation(
    category="task",
    metric_name="download_queue_pending",
    hours=1
)
# Returns: {avg, min, max, latest, sample_count}
```

### When to Use Performance Tools

| User Reports | Must Check |
|--------------|------------|
| "任务很慢" | Task queue status + Task profiler data |
| "系统卡顿" | System resources + Event loop lag |
| "数据库连接失败" | Database health + Connection pool metrics |
| "内存占用高" | Memory usage history + Top memory-consuming tasks |
| "导入失败" | Active alerts + Recent task failures |

**Example**:
```
User: "为什么任务这么慢？"

You MUST:
1. Call get_task_queue_status() - check if queue is full
2. Call get_database_health() - check if DB is bottleneck
3. Query task_perf_events for recent slow tasks
4. Identify the slowest step and report root cause
```

---

## � Tool Usage Priority

When investigating issues, use tools in this order:

1. **First**: `view` or `codebase-retrieval` to understand the code
2. **Then**: `DatabaseTools` to check data consistency
3. **Then**: `PerformanceTools` to check system health
4. **Finally**: Make changes with `str-replace-editor`

### Example: User reports "导入失败"

```python
# Step 1: Check recent task failures
from src.tools.database_tools import get_database_tools
db_tools = get_database_tools()

# Query recent failed tasks
failed_tasks = await db_tools.query("""
    SELECT task_id, task_title, status_message, created_at
    FROM task_history
    WHERE status = '失败'
    ORDER BY created_at DESC
    LIMIT 10
""")

# Step 2: Check if it's a performance issue
from src.tools.performance_tools import get_performance_tools
perf_tools = get_performance_tools()

alerts = await perf_tools.get_active_alerts()
db_health = await perf_tools.get_database_health()

# Step 3: View the actual error in code
# [use view tool to check the file mentioned in error]

# Step 4: Fix the issue
# [use str-replace-editor to fix]
```

---

## �📖 Primary Entry Point

For the full documentation map and cross-references, refer to:

**核心架构文档**:
- [`docs/最终架构规范.md`](./docs/最终架构规范.md) - **权威架构规范**，包含完整分层设计、导入规范、调用示例
- [`docs/架构规范-快速参考.md`](./docs/架构规范-快速参考.md) - **3秒决策指南**，快速查找代码应该放在哪里
- [`docs/架构违规修复报告.md`](./docs/架构违规修复报告.md) - 实际修复案例，展示如何纠正架构违规

**详细规则文档**:
- [Documentation Hub Index](./src/ai/assistant/rules/README.md) - AI 助手规则索引
- [Performance System Analysis](./docs/performance_system_analysis.md) - 性能系统分析

---

## 🔧 Agent Workflow Example

### Example Task: "修复 Webhook 导入崩溃"

```
1. User报告错误日志：
   "NameError: name 'Anime' is not defined at line 458"

2. Agent执行流程：
   
   [Step 1] 提取信息
   - File: src/tasks/webhook.py
   - Line: 458
   - Error: Anime is not defined
   
   [Step 2] 调用工具
   → view(src/tasks/webhook.py, lines 1-50)
   → 发现：只有 `AnimeSource = orm_models.AnimeSource`
   → 缺失：`Anime = orm_models.Anime`
   
   [Step 3] 修复
   → str-replace-editor(
       path: src/tasks/webhook.py,
       old: "AnimeSource = orm_models.AnimeSource\n\nlogger = ...",
       new: "AnimeSource = orm_models.AnimeSource\nAnime = orm_models.Anime\n\nlogger = ..."
   )
   
   [Step 4] 验证
   → launch-process("python -m py_compile src/tasks/webhook.py")
   → 成功
   
   [Step 5] 报告
   "✅ 已修复：在 webhook.py 第 26 行添加了 `Anime = orm_models.Anime` 导入"
```

---

*Last Updated: 2026-01-08*
