# 更新日志

## v2.7.4

### 🚀 新功能

- **弹幕源 displayName** — 弹幕源支持自定义友好显示名称（`display_name` 属性），前端优先显示 `displayName`，未设置时 fallback 到 `provider_name`。
- **JWT 令牌有效期热加载** — 修改"登录令牌有效期"后，新登录和白名单会话立即使用新值，无需重启服务。

### 🐛 修复

- **弹幕API页面文字** — "dandanplayapi" 改为"弹弹play API"。
- **lifespan 文档字符串** — 清理生命周期管理器 docstring 中残留的旧代码片段。
- **流控页面调整** — 主页中流控图标在深色模式下异常的问题。

### ⚡ 性能优化

- **流控调整** —依据弹弹play平台接口调用量分析，流控量比较与其他人应用相对小， 因此翻倍流控量。

### 🎨 界面优化

- **添加数据源弹窗** — 数据源平台下拉列表改为从后端动态获取，不再硬编码，自动展示所有已加载的弹幕源。

### 🔧 重构

- **一键更新 Docker Compose 支持** — 一键更新自动检测容器是否由 Docker Compose 创建，优先使用 `docker compose up -d` 方式重建，保留 Compose 项目关联；非 Compose 容器走原有 docker run 兜底逻辑。
- **统一 IP 解析函数** — `security.py` 中重复的 `_normalize_ip` 函数改为复用 `middleware.py` 的公共 `normalize_ip`，减少代码重复。
- **修复文件名拼写** — `localstroage.js` 更正为 `localStorage.js`。
- **清理前端调试日志** — 移除 20+ 个文件中遗留的 60+ 条 `console.log` 调试日志，避免生产环境信息泄露。