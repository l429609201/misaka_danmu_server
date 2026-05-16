# 更新日志

## v2.7.4

### 🚀 新功能

- **弹幕源 displayName** — 弹幕源支持自定义友好显示名称（`display_name` 属性），前端优先显示 `displayName`，未设置时 fallback 到 `provider_name`。
- **JWT 令牌有效期热加载** — 修改"登录令牌有效期"后，新登录和白名单会话立即使用新值，无需重启服务。

### 🐛 修复

- **弹幕API页面文字** — "dandanplayapi" 改为"弹弹play API"。

### ⚡ 性能优化

- 无

### 🎨 界面优化

- 无

### 🔧 重构

- **一键更新 Docker Compose 支持** — 一键更新自动检测容器是否由 Docker Compose 创建，优先使用 `docker compose up -d` 方式重建，保留 Compose 项目关联；非 Compose 容器走原有 docker run 兜底逻辑。
- **统一 IP 解析函数** — `security.py` 中重复的 `_normalize_ip` 函数改为复用 `middleware.py` 的公共 `normalize_ip`，减少代码重复。
- **修复文件名拼写** — `localstroage.js` 更正为 `localStorage.js`。