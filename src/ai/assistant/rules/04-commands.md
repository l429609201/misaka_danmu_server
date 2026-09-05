# 开发命令参考

本文档提供 Misaka Danmu Server 的常用开发命令。所有 AI Agent 在执行操作时，应优先使用这些命令。

---

## 🚀 启动和运行

### 启动开发服务器

⚠️ 根目录**没有** `main.py`，入口是 `src/main.py`，必须以模块方式启动。

```bash
# 启动后端（与容器内 run.sh 完全一致的方式）
python -m src.main
```

默认监听端口 **7768**（见 `Dockerfile` 的 `EXPOSE 7768`），
健康检查端点 `http://127.0.0.1:7768/api/health`。
端口由配置读取，不要在命令行硬编码 `--port`。

Windows 下若 `import src` 失败，先设置 PYTHONPATH：

```powershell
$env:PYTHONPATH = (Get-Location).Path
python -m src.main
```

### 启动前端开发服务器

```bash
cd web
npm install
npm run dev
```

### 构建前端

```bash
cd web
npm run build
```

---

## 🧪 测试和验证

### 编译检查

```bash
# 检查单个文件语法
python -m py_compile src/tasks/webhook.py

# 检查所有 Python 文件
python -m compileall src/
```

### 运行测试（如果有）

```bash
# 运行所有测试
pytest

# 运行特定测试文件
pytest tests/test_webhook.py

# 运行特定测试函数
pytest tests/test_webhook.py::test_emby_parsing
```

---

## 🗄️ 数据库管理

### 数据库迁移（自研标志位机制，非 Alembic）

⚠️ 本项目**没有** `alembic.ini`，`migrations/versions/` 为空，
`alembic revision` / `alembic upgrade` 等命令**全部不可用**。

迁移由 `src/db/migrations.py` 在启动时自动执行，机制如下：

1. 每个迁移有唯一 `migration_id`（如 `reset_notification_subscriptions_v1`）
2. 执行前查 `config` 表是否已有该 id 的标志位，有则跳过
3. 执行成功后写入标志位，确保只跑一次

**新增迁移的步骤**（三处都要改，漏一处就会重复执行或永不执行）：

```python
# 1. 在 src/db/migrations.py 定义迁移函数
async def _migrate_xxx_v1(conn: AsyncConnection):
    """迁移说明：为什么需要这次迁移。"""
    await conn.execute(text("UPDATE ..."))

# 2. 登记到 run_migrations() 的 migrations 列表
migrations = [
    ...
    ("migrate_xxx_v1", _migrate_xxx_v1, ()),
]

# 3. 登记到模块级 ALL_MIGRATION_IDS 列表
ALL_MIGRATION_IDS = [
    ...
    "migrate_xxx_v1",
]
```

**只是加字段或扩类型时无需手写迁移** —— `db_maintainer.py` 会在启动时
自动比对 ORM 模型并补齐，仅「数据转换 / 重命名 / 回填 / 删列」才需要写迁移函数。

### 数据库备份和恢复

```bash
# 默认数据库是 MySQL（src/core/config.py: type = "mysql"），也支持 PostgreSQL。
# 备份不要手工拷文件，项目已有专用作业：src/jobs/database_backup.py，
# 界面入口：设置 → 数据库备份与还原（可立即备份 / 上传还原）。

# MySQL 手工备份（容器外）
mysqldump -h <host> -u <user> -p <dbname> > backup.sql

# PostgreSQL 手工备份
pg_dump -h <host> -U <user> <dbname> > backup.sql
```

⚠️ 旧版文档写的 `cp config/user.db` 是错的：该文件不存在，
且本项目主力使用 MySQL/PostgreSQL，不能靠拷贝单文件备份。

---

## 📦 依赖管理

### Python 依赖

```bash
# 安装依赖
pip install -r requirements.txt

# 更新依赖
pip install --upgrade package_name

# 冻结当前依赖
pip freeze > requirements.txt
```

### 前端依赖

```bash
cd web

# 安装依赖
npm install

# 添加新依赖
npm install package_name

# 更新依赖
npm update package_name
```

---

## 🔍 代码检查

⚠️ 本项目**未配置** black / pylint / mypy / ruff
（`requirements.txt` 中无这些依赖，也没有 `pyproject.toml`、`.pylintrc`、`mypy.ini`）。
不要建议或执行这些命令，改动后的验证手段只有：

```bash
# 语法检查（改完 Python 文件必做）
python -m py_compile src/path/to/changed_file.py

# 批量检查
python -m compileall src/
```

代码风格靠人工遵循 `03-code-styles.md`，没有自动化工具兜底，
因此改动时更要严格对齐周边既有代码的写法。

---

## 🐳 Docker 命令

### 构建镜像

```bash
# 构建 Docker 镜像
docker build -t misaka-danmu-server .

# 指定版本号
docker build -t misaka-danmu-server:1.2.0 .
```

### 运行容器

```bash
# 运行容器（端口 7768，与 Dockerfile 的 EXPOSE 一致）
docker run -d \
  --name misaka-danmu \
  -p 7768:7768 \
  -v $(pwd)/config:/app/config \
  misaka-danmu-server

# 查看日志
docker logs -f misaka-danmu

# 进入容器
docker exec -it misaka-danmu bash

# 停止容器
docker stop misaka-danmu

# 重启容器
docker restart misaka-danmu
```

---

## 🔧 调试命令

### 查看运行状态

```bash
# 查看进程
ps aux | grep python

# 查看端口占用
lsof -i :3000
# 或 Windows
netstat -ano | findstr :3000

# 查看日志
tail -f config/logs/app.log
```

### 清理缓存

```bash
# 清理 Python 缓存
find . -type d -name "__pycache__" -exec rm -r {} +
find . -type f -name "*.pyc" -delete

# 清理前端构建产物
cd web
rm -rf dist node_modules
```

---

## 📝 Git 工作流

### 提交代码

```bash
# 查看状态
git status

# 添加修改
git add src/tasks/webhook.py

# 提交
git commit -m "fix: 修复 Webhook 导入时 Anime 模型未导入的问题"

# 推送
git push origin main
```

### 创建分支

```bash
# 创建新分支
git checkout -b feature/add-bilibili-overseas

# 切换分支
git checkout main

# 合并分支
git merge feature/add-bilibili-overseas
```

---

## 🛠️ 常用工具命令

### 查看代码统计

```bash
# 统计代码行数
find src -name "*.py" | xargs wc -l

# 查看文件数量
find src -name "*.py" | wc -l
```

### 搜索代码

```bash
# 搜索关键字
grep -r "TaskSuccess" src/

# 搜索并显示行号
grep -rn "async def" src/tasks/
```

---

## ⚠️ 注意事项

1. **生产环境操作**：
   - 数据库迁移前必须备份
   - 重启服务前确认无正在运行的任务
   - 修改配置后需要重启容器

2. **开发环境操作**：
   - 修改数据库模型：加字段/扩类型由 `db_maintainer.py` 自动处理；
     需数据转换时在 `src/db/migrations.py` 追加迁移（本项目不用 Alembic）
   - 修改前端代码后需要重新构建
   - 修改数据源后必须更新版本号

3. **调试技巧**：
   - 查看日志是第一步
   - 使用 `python -m py_compile` 快速检查语法
   - 使用 `docker logs` 查看容器内错误

---

*最后更新：2026-01-08*
