# Misaka Danmu Server 全项目代码审查报告

> 审查日期：2026-07-16
> 范围：后端、前端、API/安全、事务/并发、性能、部署与工程质量。结论均来自当前工作区真实代码与静态验证；未覆盖编译后的第三方 `.so` 内部实现。

## 1. 执行摘要

项目功能覆盖广，但复杂度和质量门禁已明显落后于功能增长。当前最优先事项不是继续增加抽象，而是先修复可被利用的文件路径问题、前端确定性运行错误、并发 ID 分配竞态及事务边界混乱。

| 等级 | 数量 | 说明 |
|---|---:|---|
| P0 严重 | 2 | 已认证管理员可越界访问文件；前端存在确定性未定义符号 |
| P1 高 | 7 | 上传资源耗尽、竞态、任务锁泄漏、TLS、审计日志泄密等 |
| P2 中 | 8 | 超大函数、重复实现、事务分散、缓存全量扫描等 |

验证结果：
- 后端：`python -m compileall -q src` **通过**。
- 前端：`npm run build` **通过**，但 Vite 构建不执行 ESLint。
- 前端质量门禁：`npm run check` **失败**，当前共 **194 项（104 errors / 90 warnings）**。
- 测试：按用户要求，工作区内新增的自动化测试源码已全部删除；当前仅保留编译、导入、Lint 与生产构建检查。
- 规模：后端约 **301 个 Python 文件 / 108,971 行**；前端约 **142 个 JS/JSX 文件 / 58,384 行**。

## 2. P0：必须立即处理

### [x] SEC-01 备份文件接口存在目录穿越

**证据**：
- `src/api/ui/backup.py:326` 将路由参数直接拼成 `backup_path / filename`，详情接口没有文件名校验。
- `src/jobs/database_backup.py:364` 的恢复逻辑同样直接拼接文件名，且没有约束解析后路径必须位于备份目录。
- `src/jobs/database_backup.py:339-343` 删除接口虽检查前后缀，但 `danmuapi_backup_../../x.json.gz` 仍满足字符串规则；检查发生在路径拼接后，也未使用 `resolve()`/`relative_to()`。

**影响**：已登录管理员可尝试读取、解析、恢复或删除备份目录外的匹配文件；恢复接口还可能将任意可解析 gzip JSON 当作数据库备份处理。

**最小修复**：统一新增 `resolve_backup_file(backup_dir, filename)`：仅接受 `Path(filename).name == filename` 且匹配严格正则；对 `resolve()` 后路径执行 `relative_to(backup_dir.resolve())`。下载、详情、删除、比较、恢复全部复用。

### [x] FE-01 前端存在构建未发现的确定性运行错误

**证据**：
- `web/src/pages/subscription/CalendarView.jsx:253` 使用未定义的 `getDisplayYear`。
- `web/src/pages/subscription/SubscriptionSearchBar.jsx:251` 使用 `Tooltip`，但 `:2` 的 antd 导入没有它。
- `web/src/pages/episode/[id].jsx:1371-1373` 使用未定义的 `lastSelectedIndex`。
- `web/src/pages/library/index.jsx:1448` 调用未定义的 `fetchList`。
- `web/src/pages/setting/components/DanmakuStorage.jsx:1600,1675-1676` 使用 `InputNumber`，但 `:2` 未导入。

**影响**：相应页面或交互分支触发 `ReferenceError`，页面功能中断。`npm run build` 仍会成功，因此发布流程无法阻止此类缺陷。

**最小修复**：先修复所有 `no-undef`/`jsx-no-undef`，再将 `npm run check && npm run build` 加入 PR/发布 CI。

## 3. P1：高优先级风险

### [x] SEC-02 弹幕源离线包上传可造成越界写入与资源耗尽

**证据**：`src/api/ui/parameters.py:129` 使用客户端 `file.filename` 拼临时路径；`:131` 一次性 `await file.read()`；`:149,167` 直接全量解压。虽检查成员路径和 tar 链接，但没有上传大小、压缩后总大小、文件数、压缩比限制，字符串 `startswith` 也不如 `relative_to()` 稳健。

**影响**：恶意管理员/被盗账号可通过文件名或 zip bomb 消耗内存、磁盘和 CPU。

**建议**：上传文件名只取 `Path(name).name`；分块写入并限制总字节；逐成员解压，限制成员数、单文件/总展开大小和压缩比。

### [x] DB-01 自增计数器存在并发分配重复 ID 的竞态

**证据**：`src/db/crud/config.py:89-103` 读取计数器、计算再写回，仅 `flush()`，没有 `SELECT ... FOR UPDATE` 或数据库原子自增；`src/api/dandan/helpers.py:311-319` 依赖它分配真实 animeId。

**影响**：两个并发后备导入可读到同一计数值并返回相同 ID，导致唯一键冲突或 episodeId 串台；这会破坏该计数器原本要保证的“不重用”。

**建议**：在同一事务中锁定 Config 行；首次创建需处理并发插入冲突后重试。更简单可靠的方案是使用数据库原生序列/自增主键，不手工分配 Anime.id。

### [x] TASK-01 任务去重键可能永久泄漏

**证据**：`src/services/task_manager.py:810` 在内存集合中先占用 unique key；之后 `:835` 写任务历史、`:881` 入队。若序列化、数据库写入、队列类型校验或入队失败，没有异常补偿清除 key/title；正常清理只在任务包装器 finally 中发生。

**影响**：一次提交失败后，同资源后续任务持续返回 409，直到服务重启。

**建议**：将占位后的提交阶段包在 `try/except`，失败时在锁内回滚 `_active_unique_keys/_pending_titles`；先验证 queue_type 和 JSON 可序列化性。

### DB-02 CRUD 内部提交导致业务操作无法原子化

**证据**：`src/db/crud/config.py:43-69` 的通用更新函数内部直接 commit；全 CRUD 共检出约 **138 次** `session.commit()`。例如代理设置在 `src/api/ui/config_extra.py:120-149` 连续调用五次，形成五个独立事务。

**影响**：中途失败会留下半套配置；调用层无法统一回滚。类似问题广泛存在于任务、源、用户和日历 CRUD。

**建议**：CRUD 默认只 `flush()`，事务由 API/任务用 `async with session.begin()` 管理；仅为确需独立提交的操作保留显式命名函数。分模块渐进迁移，勿一次性重写。

### [x] SEC-03 外部控制 API 将完整请求体写入审计日志

**证据**：`src/api/control/dependencies.py:141-147` 无大小限制读取并回灌完整 body，`:156-184` 对成功和失败请求都持久化，且只过滤请求头/查询参数中的 key，没有递归脱敏 JSON body。

**影响**：导入 XML、大批弹幕或带 token/password 的配置会造成内存与数据库膨胀，并把敏感字段长期落盘。

**建议**：只记录前 8–16 KiB；按 Content-Type 解析并递归脱敏 password/token/secret/apiKey/cookie；二进制仅记哈希、长度和类型。

### SEC-04 JWT 暴露面偏大

**证据**：`web/src/pages/login/index.jsx:124-129` 由 JS 写可读 Cookie，无法设置 HttpOnly；`src/security.py:450-456` 允许 `-1` 永不过期；`src/metadata_sources/bangumi.py:536` 还把 JWT 密钥前 8 位放入对外 User-Agent。

**影响**：一旦出现 XSS，长期 token 可直接被窃取；密钥前缀会泄露给目标站和各级代理/日志。

**建议**：立即移除 User-Agent 中的密钥片段；长期改为后端 HttpOnly/Secure/SameSite Cookie + 短期访问令牌/可撤销会话，禁用无限有效期默认路径。

### SEC-05 代理请求关闭 TLS 校验

**证据**：`src/scrapers/bilibili.py:794,1193` 在 Clash 代理分支硬编码 `verify=False`。

**影响**：代理链路可被中间人篡改，搜索/分集结果可能被污染。

**建议**：默认校验证书；仅显式配置允许关闭，并在 UI/日志显示高风险警告。

### [x] OPS-01 Docker socket 权限扩大为 0666

**证据**：`exec.sh:21-29` 在权限不足时对 `/var/run/docker.sock` 执行 `chmod 666`；`docker-compose.yml:53` 默认挂载 socket。

**影响**：容器内任意进程都可控制宿主 Docker，等价于高权限宿主访问。应用或插件被攻破后影响扩大。

**建议**：默认不挂载；需要时按宿主 docker group GID 授权，禁止 0666；将更新能力拆到最小权限 sidecar 是后续可选项。

## 4. P2：架构、性能与可维护性

### ARCH-01 超大函数与职责混杂严重

`src/api/dandan/comments.py:221` 单函数约 **1316 行**，`match.py:112` 约 **1154 行**，`tasks/auto_import.py:57` 约 **949 行**，`tasks/webhook.py:94` 约 **884 行**。前端也有 `Scrapers.jsx` 2962 行、`library/index.jsx` 2580 行、`SearchResult.jsx` 2237 行。

建议按现有业务阶段拆成纯函数/小服务，不引入新框架：输入解析、缓存查找、DB 兜底、源站抓取、落库、通知各自独立，并先补回归测试。

### ARCH-02 宽泛异常吞噬过多

静态检出 `except Exception` **1188 处**。例如 `src/api/ui/cache.py:63-68,79-91` 对后端异常直接吞掉，UI 会把故障伪装成空缓存或“读取失败”。建议只在边界层兜底，至少记录结构化 warning；业务层捕获明确异常。

### [x] PERF-01 缓存列表先全量扫描再内存分页

`src/api/ui/cache.py:55-75` 对多个 region 执行通配 keys，汇总、排序后才分页；`:79-82` 再逐项串行 get。Redis 大缓存下会阻塞/高延迟。

建议后端提供游标扫描；列表仅返回 key/TTL/大小，详情按需加载；预览用有限并发批量获取。

### [x] FE-02 API GET 参数约定被六处违反

`web/src/apis/fetch.js:61-68` 已把第二参数直接设为 axios `params`，但 `web/src/apis/index.js:1277,1287,1322,1328,1333,1349` 又传 `{ params: ... }`，最终请求会变为 `?params[...]`，相关缓存、历史、任务时间线、审计、扫描详情接口可能收不到参数。

### FE-03 Hook 依赖与死代码积累

ESLint 报告含大量 `react-hooks/exhaustive-deps`、未使用变量、空 catch、缺 key。缺失依赖会造成闭包读取旧状态、重复轮询或漏刷新。应先只修 error 和行为相关 warning，再逐页清理，避免全局自动格式化产生大 diff。

### [x] ARCH-03 Docker 环境判断重复

已有 `src/core/env.py:12`，但至少还有 9 个 `_is_docker_environment` 重复定义（parameters、scraper_resources、bootstrap、多个 CRUD、image_utils）。统一调用现有函数即可，不需要新增抽象。

### SEC-06 CORS 配置语义不清

`src/main.py:65-71` 配置 `allow_origins=["*"]` 与 `allow_credentials=True`。Starlette 会按请求 Origin 回显部分带凭据请求；即使当前 Bearer token 主要由 JS 加头，也会扩大跨域调用面。建议配置允许域名列表；单机默认仅同源。

### OPS-02 依赖与供应链不可复现

`requirements.txt` 大量依赖无上限/无精确版本；Docker 基础镜像 `l429609201/su-exec:3.12` 无 digest；构建阶段从 GitHub `raw` 分支下载二进制 `.so`，未校验 SHA256/签名（`Dockerfile:76-94`）。建议生成锁文件/哈希，固定镜像 digest，并校验二进制清单。

## 5. 推荐修复顺序

1. **当天**：修 SEC-01、FE-01；CI 加前端 lint。
2. **本周**：修 SEC-02、DB-01、TASK-01、SEC-03；移除 JWT 密钥前缀和 `verify=False`。
3. **两周内**：以配置更新为试点，把事务边界移到业务层；补备份、计数器、任务提交、GET 参数的测试。
4. **持续治理**：按热点修改超大函数；将 `except Exception`、重复环境判断、前端 Hook 警告纳入“修改到哪清到哪”。

## 6. 当前工作区修复进度

本轮已按最小改动完成以下问题修复，尚未提交：

- **SEC-01**：备份下载、详情、删除、恢复、预检统一使用严格文件名正则与 `resolve()/relative_to()` 路径约束。
- **FE-01**：修复报告列出的 5 处确定性未定义符号；相关文件已无 `no-undef/jsx-no-undef`。
- **SEC-02**：离线包改为分块上传，并限制上传大小、成员数、单文件/总展开大小、压缩比；逐成员安全解压，拒绝路径穿越与链接成员。
- **DB-01**：计数器行先用方言原生 upsert 确保存在，再以 `SELECT ... FOR UPDATE` 串行分配并立即提交，避免并发返回重复 animeId。
- **TASK-01**：任务提交先校验队列与参数序列化；占用去重标记后若历史写入、立即执行注册或入队失败，统一释放 key/title 并标记历史任务失败。
- **SEC-03**：外部控制 API、MCP 与 Token API 统一使用安全审计工具；仅在 `Content-Length <= 16 KiB` 时读取请求体，JSON/XML/表单递归脱敏，文本做常见凭据遮罩，二进制只记录类型、长度和 SHA256。
- **DB-02（第一至四批）**：新增可复用的无提交配置 upsert 与原子批量提交；代理配置、自定义弹幕路径配置、元数据源配置均改为单次事务。弹幕源设置与 `scraperOrder` 顺序快照也改为同一事务提交。手动同步播出日程时，元数据 ID 绑定与日程更新分别按阶段统一提交，不再逐作品提交；默认参数保持其他定时任务原行为。其余 CRUD 内部提交继续按模块渐进迁移。
- **SEC-04**：移除 Bangumi User-Agent 中的 JWT 密钥前缀；JWT 强制有限期且最长 30 天，配置入口与 Schema 同步限制。
- **排除项**：按用户要求，本轮不实施 `SEC-05`、`SEC-06`、`OPS-02`，报告保留原始风险记录但不纳入修复计划。
- **OPS-01**：启动脚本不再对 Docker socket 执行 `chmod 666`。
- **FE-02**：修复 6 处 GET 参数二次包裹 `{params}` 的调用。
- **PERF-01**：缓存管理列表不再跨 region 构建全量键数组；数据库后端使用 `COUNT + ORDER BY + OFFSET/LIMIT`，Redis 使用 `SCAN` 并在收满当前页后停止，Memory 按受限容量切片；值预览改为每批最多 10 项并发读取。
- **ARCH-03**：普通运行路径统一复用 `src/core/env.py:is_docker_environment`，删除 API、核心配置、CRUD、服务、日志、限流和更新任务中的重复实现；保留 `docker_utils.is_running_in_docker` 的 LXC/cgroup 强检测语义。
- **FE-03（第一至三批）**：订阅日历页与 `Scrapers.jsx` 定向 ESLint 均已清零；修复 `Scrapers.jsx` 构建语法错误并清理死代码/空异常块。`episode/[id].jsx` 的未使用 API、无效局部变量和冗余布尔转换已清除，定向检查为 0 error / 2 个待单独验证的 Hook warning。

验证：后端 `compileall` 与 `import src.main` 通过，前端生产构建通过；按项目标准命令 `npm run check`，全项目 ESLint 当前仍有 **194 项（104 errors / 90 warnings）**，均为报告已记录的既有质量债，尚未清零。按用户要求，工作区自动化测试源码已删除。

## 7. 最小测试基线

- 后端：pytest 覆盖备份路径穿越、压缩包限额、计数器并发、任务提交失败补偿、配置事务回滚。
- 前端：ESLint 必须 0 error；为 GET 参数封装和上述五个未定义符号所在交互补 Vitest/React Testing Library 用例。
- CI：`python -m compileall -q src`、后端测试、`npm ci`、`npm run check`、`npm run build`；任一步失败禁止发布镜像。

## 8. 说明

本报告是静态审查，不等价于渗透测试或完整运行时压测。优先级按“可利用性 × 数据影响 × 发生概率”排序；修复时应遵循 KISS，优先复用现有模块，不建议进行一次性全项目重构。
