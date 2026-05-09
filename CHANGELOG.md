# 更新日志

## v2.7.1

### 🚀 新功能

- **分集补全扫描定时任务** — 新增 `fillMissingEpisodes` 模式，自动检测并补全缺失分集弹幕
- **E站弹幕网 (ezdmw) 搜索源** — 新增 ezdmw 弹幕搜索源
- **IPv4+IPv6 双栈监听** — 服务器默认监听地址改为 `::`，同时支持 IPv4 和 IPv6 连接
- **弹幕黑名单默认配置** — 新增"填充默认配置"按钮，内置 TG 群分享的推荐规则
- **弹幕源单源超时配置** — 支持为每个弹幕源独立设置超时时间（Slider + config 表存储）
- **UI 导航下拉菜单** — 有子分页的导航项（弹幕、媒体获取、搜索源、外部控制、设置）增加 Dropdown 下拉子菜单，快速跳转到对应分页
- **Swagger 汉化** — API 文档页面汉化，独立模块 `swagger_cn.py` 通过 MutationObserver 注入中文翻译
- **番剧详情分集列表** — 番剧详情 Tab 改为列表模式，展示封面、分集数、可搜索可分页的完整分集列表
- **整季匹配缓存** — 同标题同季的不同集数复用匹配结果（1小时TTL），避免重复搜索
- **后备搜索结果缓存复用** — 修复 `fallback_result_` 缓存只存不读的问题，同标题10分钟内复用搜索结果
- **match 接口延迟写库** — 匹配后备不再在 match 时写入数据库，改由 comment 接口下载弹幕成功后才创建记录
- **搜索性能全面优化** — 补充源架构重构 + 计时报告分组显示（弹幕源/补充源/辅助源分组框框格式）
- **辅助源类型修正** — 360/ezdmw 日志整合优化

### 🐛 修复

- **fallback 占位符 ID 穿透** — 修复匹配后备流程生成的占位符 `provider_episode_id` 穿透到弹幕下载环节导致 0 弹幕的问题，新增二次校验兜底
- **补充源 URL 弹幕下载** — comment 接口下载弹幕时，补充源返回的 URL 格式 episode_id 通过基类 `get_id_from_url` + `format_episode_id_for_comments` 正确解析
- **后备搜索缓存导入路径** — `crud.utility` → `crud.cache`
- **闭包作用域冲突** — 删除函数内重复的 `from .bangumi import generate_episode_id` 导入
- **任务日志缺少 mediaId** — 全量刷新、增量刷新、单集刷新、补全任务的标题和日志中补充 mediaId 信息，方便排查
- **容器重启策略** — `container.restart()` 替代 `stop()`，避免 restart policy 不生效；后续参考 MoviePilot 改造为 SIGTERM 优先 + Docker API 兜底
- **弹幕源超时统一控制** — 基类忽略源内硬编码 timeout，统一由配置表管理
- **API 接口测试页面暗色模式** — 全量适配暗色主题
- **搜索作品结果海报显示** — 补上被遗漏的海报图片
- **目录浏览器移动端适配** — 文件名截断、隐藏日期列、文件名占满宽度
- **ezdmw 代理方法名修正** — `_get_proxy_url` → `_get_proxy_for_provider`
- **scrapers 框架文件恢复** — `base.py` + `__init__.py` 恢复到 git 追踪
- **黑名单默认配置修正** — 替换为 hills TG 群分享规则
- **requests 版本兼容性警告过滤**
- **IPv6 双栈 monkey-patch** — 改用 `socket.bind` 实现，开发/生产模式均可用
- **misaka-relay 子模块误添加** — 移除嵌套 git 仓库引用，添加 `.gitignore` 排除

### ⚡ 性能优化

- **搜索全并行化** — 弹幕源 + 补充源 + 辅助源(别名) 全并行请求
- **后备匹配并行优化** — TMDB 搜索与弹幕源搜索并行启动，config 读取改为 `asyncio.gather` 批量获取
- **TMDB 刮削增量优化** — 已有别名的作品跳过重复刮削
- **计时报告分组统一** — 所有搜索入口（主页/后备搜索/后备匹配/Webhook/外部控制）统一分组框框格式

### 🎨 界面优化

- 批量管理界面优化（去粗边框 / 紧凑筛选 / 简化源项 / banner 提示）
- 弹幕源启用状态改为 Switch 切换按钮 + 文字标签（已启用/未启用）
- 元信息搜索源启用开关改为 Switch 样式
- TMDB 启用状态改为 disabled Switch
- 搜索超时 Slider 与 InputNumber 对齐优化
- 测试页面 Card 右上角增加 Tab 下拉选择器

### 🔧 重构

- 容器重启策略参考 MoviePilot 改造（SIGTERM 优先 + Docker API 兜底）
- 黑名单默认规则改为从后端 API 获取，去掉前端硬编码
- 外部控制接口响应统一 — `ControlActionResponse` / `ControlTaskResponse` 加 `status` 字段，创建接口返回简洁状态+ID
- 定时任务 `get_available_jobs` 补充 `isSystemTask` 字段
