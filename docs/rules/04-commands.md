# 开发命令参考

本文档提供 Misaka Danmu Server 的常用开发命令。所有 AI Agent 在执行操作时，应优先使用这些命令。

---

## 🚀 启动和运行

### 启动开发服务器

```bash
# 启动后端（FastAPI）
python main.py

# 或使用 uvicorn
uvicorn main:app --reload --host 0.0.0.0 --port 3000
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

### Alembic 迁移

```bash
# 创建新迁移
alembic revision --autogenerate -m "Add new column to anime table"

# 应用迁移（升级到最新版本）
alembic upgrade head

# 回退一个版本
alembic downgrade -1

# 查看当前版本
alembic current

# 查看迁移历史
alembic history
```

### 数据库备份和恢复

```bash
# 备份数据库（SQLite）
cp config/user.db config/user.db.backup

# 恢复数据库
cp config/user.db.backup config/user.db
```

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

### 格式化和 Lint

```bash
# 格式化 Python 代码（如果使用 black）
black src/

# Lint 检查（如果使用 pylint）
pylint src/

# 类型检查（如果使用 mypy）
mypy src/
```

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
# 运行容器
docker run -d \
  --name misaka-danmu \
  -p 3000:3000 \
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
   - 修改数据库模型后必须创建 Alembic 迁移
   - 修改前端代码后需要重新构建
   - 修改数据源后必须更新版本号

3. **调试技巧**：
   - 查看日志是第一步
   - 使用 `python -m py_compile` 快速检查语法
   - 使用 `docker logs` 查看容器内错误

---

*最后更新：2026-01-08*
