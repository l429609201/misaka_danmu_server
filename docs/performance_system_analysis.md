# 性能采集系统完善方案

## 📊 当前系统现状

### 已有的性能采集工具（2套）

#### 1. 系统级性能采集（PerformanceCollector）
**文件**：`src/services/performance_collector.py`

**采集内容**：
- ✅ 数据库连接池状态（大小、使用率、溢出连接数）
- ✅ 任务队列状态（排队数、运行数、利用率）
- ✅ 缓存状态（Redis 内存、连接数、键总数）
- ✅ 系统资源（CPU、内存、磁盘使用率）

**采集频率**：每 60 秒定时轮询  
**存储表**：`performance_metrics`

#### 2. 任务执行性能采集（TaskProfiler）
**文件**：`src/utils/task_profiler.py`

**采集内容**：
- ✅ 任务内部各步骤耗时（通过 `async with profiler.step()` 包裹）
- ✅ 步骤成功/失败状态
- ✅ 总耗时

**触发方式**：在任务代码中手动调用  
**存储表**：`task_perf_events`

---

## ❌ 当前存在的问题

### 问题 1：系统数据采集不全面
**现象**：只采集了 CPU、内存、磁盘三类系统资源

**缺失的关键指标**：
- ❌ 网络 I/O（接收/发送速率）
- ❌ 进程级资源（打开的文件描述符数、线程数）
- ❌ 事件循环延迟（asyncio event loop lag）
- ❌ 数据库慢查询统计（P50/P95/P99 耗时）
- ❌ API 响应时间分布

### 问题 2：任务执行性能采集不完整
**现象**：很多任务没有接入 `TaskProfiler`

**原因**：
1. 需要在每个任务函数中手动添加 `profiler.step()` 代码
2. 老任务代码未改造
3. 新任务开发时容易遗漏

**影响**：
- 无法分析"某个任务为什么慢"
- 无法定位"哪个数据源响应慢"
- 无法追踪"AI 匹配耗时趋势"

### 问题 3：任务性能数据和系统数据割裂
**现象**：
- `task_perf_events` 表：任务步骤耗时
- `performance_metrics` 表：系统资源使用
- **两者没有关联**，无法分析"任务运行时的系统资源消耗"

**影响**：
- 无法回答："为什么这个任务跑的时候 CPU 飙升？"
- 无法回答："内存占用高是哪个任务导致的？"

### 问题 4：缺少关键业务指标
**缺失的任务级指标**：
- ❌ 数据源搜索耗时（每个 scraper 的响应时间）
- ❌ AI 匹配耗时（包括每次 API 调用）
- ❌ 网络请求耗时（HTTP 请求统计）
- ❌ 数据库查询耗时（每条 SQL 的耗时）
- ❌ 缓存命中率（按任务类型统计）

---

## 🔧 完善方案（分 3 个阶段）

### 阶段 1：完善系统级性能采集（高优先级）

**目标**：补充缺失的系统指标，让系统健康状态更全面。

**新增采集指标**：

1. **事件循环延迟**（asyncio event loop lag）
   ```python
   # 在 PerformanceCollector 中添加
   async def _collect_eventloop_metrics(self, session):
       """采集事件循环延迟"""
       import asyncio
       start = asyncio.get_event_loop().time()
       await asyncio.sleep(0)  # 让出控制权
       lag_ms = (asyncio.get_event_loop().time() - start) * 1000
       
       await perf_crud.record_metric(
           session, "system", "eventloop", "eventloop_lag",
           "事件循环延迟", value_float=lag_ms, unit="ms",
           threshold_warning=50.0, threshold_critical=100.0
       )
   ```

2. **进程资源**
   ```python
   # 打开的文件描述符数
   open_fds = len(psutil.Process().open_files())
   
   # 线程数
   thread_count = psutil.Process().num_threads()
   ```

3. **网络 I/O**
   ```python
   net_io = psutil.net_io_counters()
   bytes_sent_mb = net_io.bytes_sent / (1024 * 1024)
   bytes_recv_mb = net_io.bytes_recv / (1024 * 1024)
   ```

**实施方式**：修改 `src/services/performance_collector.py`

---

### 阶段 2：自动接入任务性能采集（中优先级）

**目标**：让所有任务自动记录性能数据，无需手动添加代码。

**方案**：在 `TaskManager._run_task_wrapper` 中自动包装

**实施步骤**：

1. 在 `_run_task_wrapper` 中自动创建 `TaskProfiler`
2. 在任务执行前后记录时间
3. 捕获异常时记录失败信息

**伪代码**：
```python
async def _run_task_wrapper(self, task: Task, queue_type: str = "download"):
    # 自动创建 profiler
    profiler = TaskProfiler(
        flow_type=task.task_type or "未分类任务",
        correlation_id=task.task_id
    )
    
    async with profiler.step("任务执行"):
        # 原有的任务执行逻辑
        ...
    
    # 自动 flush
    await profiler.flush(session)
```

**优势**：
- ✅ 所有任务自动接入，无遗漏
- ✅ 无需修改现有任务代码
- ✅ 统一的性能数据格式

---

### 阶段 3：关联任务性能和系统性能（低优先级）

**目标**：在任务执行前后采集系统资源快照，建立关联。

**方案**：在 `task_perf_events` 表添加系统资源字段

**新增字段**：
```sql
ALTER TABLE task_perf_events ADD COLUMN cpu_usage_before DECIMAL(5,2);
ALTER TABLE task_perf_events ADD COLUMN cpu_usage_after DECIMAL(5,2);
ALTER TABLE task_perf_events ADD COLUMN memory_usage_before DECIMAL(5,2);
ALTER TABLE task_perf_events ADD COLUMN memory_usage_after DECIMAL(5,2);
```

**采集逻辑**：
```python
# 任务执行前
cpu_before = psutil.cpu_percent(interval=0.1)
memory_before = psutil.virtual_memory().percent

# 执行任务
...

# 任务执行后
cpu_after = psutil.cpu_percent(interval=0.1)
memory_after = psutil.virtual_memory().percent

# 写入 task_perf_events
```

**分析能力**：
- ✅ 可以分析"哪个任务导致 CPU 飙升"
- ✅ 可以分析"内存占用高的任务类型"
- ✅ 可以发现"资源泄漏"的任务

---

## 📋 实施优先级

| 阶段 | 工作量 | 优先级 | 价值 |
|------|--------|--------|------|
| 阶段 1：完善系统采集 | 1-2 小时 | 🔴 高 | 立即提升监控全面性 |
| 阶段 2：自动接入任务采集 | 2-3 小时 | 🟡 中 | 解决"任务为何慢"的问题 |
| 阶段 3：关联任务和系统 | 3-4 小时 | 🟢 低 | 深度分析能力 |

---

## 🚀 立即可做的改进（5分钟）

在 `AGENTS.md` 中添加性能工具使用说明，让 AI 知道如何使用性能工具诊断问题。
