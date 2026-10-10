# 工具地图与调用边界

> 组织原则参考 MoviePilot：按能力分组、保留后续工具链、以运行时 schema 为准，不照搬本项目未实现的权限和能力。参考：[工具工厂](https://github.com/jxxghp/MoviePilot/blob/v2/app/agent/tools/factory.py)、[工具筛选](https://github.com/jxxghp/MoviePilot/blob/v2/app/agent/middleware/tool_selection.py)、[运行时提示词](https://github.com/jxxghp/MoviePilot/blob/v2/app/agent/prompt/__init__.py)。

---

本节描述静态 `registry` 中实际注册的工具；会话导出的工具仍由宿主按权限、是否允许写入和是否允许代码动态筛选。每次调用以当次 function schema 为准，不凭本文件猜参数。`★` 表示写入或外部副作用，须通过当前会话确认（代码补丁另有管理员授权规则）。

## 只读业务工具

- `search_library(keyword?)`：本地作品检索；返回 `animeId`。
- `get_anime_sources(animeId)`、`get_source_episodes(sourceId)`、`get_anime_detail(animeId)`：按作品→源→分集逐级核查。
- `list_tasks(status?, search?)`、`get_task_status(taskId)`：任务列表与详情。
- `search_media(keyword, season?)`：搜索外部弹幕源候选，返回 `searchId` 与 `resultIndex`。
- `get_provider_episodes(searchId, resultIndex, includeFiltered?: 0|1)`：查看候选分集及可选过滤项。

## 写入和外部副作用

- `import_selected★(searchId, resultIndex, episode?)`：整季或 `episode` 字符串指定单集。
- `import_edited★(searchId, resultIndex, episodeIndexes[])`：导入指定集号数组。
- `refresh_episode_danmaku★(episodeId)`、`run_scheduled_task★(taskId)`。
- `delete_anime★(animeId)`、`delete_source★(sourceId)`：不可逆，先核对 ID 并明确告知后果。

导入必须遵循 `search_media` → 展示候选 → 用户选定 → 导入。写工具返回 `taskId` 后，用 `ask_user_choice` 提供查看进度、等待汇报、暂不查看，再用 `get_task_status` 报告真实状态。`ask_user_choice(title, prompt, options)` 只是澄清，不是写授权。

## 元数据

- `list_metadata_sources()`。
- `get_metadata_source_config(provider)`、`search_metadata(provider, keyword, mediaType?)`、`get_metadata_details(provider, itemId, mediaType?)`。
- `get_key_status(provider)` 只返回掩码状态；`verify_metadata_source_key(provider)` 会真实探测外部服务但不写本地。
- `set_metadata_source_key★(provider, configKey, value)`：写入并验证；绝不复述明文。

## 配置、识别词和过滤

- `get_config(keys?)` / `set_config★(key, value)`：`keys`/`key` 仅可用当次 schema 白名单；依赖不满足时如实转达 `warning`。
- `get_recognition_rules()`、`test_recognition(title, season?, episode?, stage?)`、`check_recognition_conflicts()`、`set_recognition_rules★(content, mode: append|replace)`。
- `get_global_filter()` / `set_global_filter★(cn?, eng?, mode)`。
- `get_source_episode_blacklist(provider)` / `set_source_episode_blacklist★(provider, regex, mode)`。
- `get_global_episode_title_filter()` / `set_global_episode_title_filter★(enabled?, regex?, mode)`。
- `get_single_episode_filter()` / `set_single_episode_filter★(content, mode)`。
- `test_regex(text, patterns[])`：纯计算。过滤层依次为作品级、单源、第2层全局分集、第3层单剧；修改前先读取并优先 `append`。

## 技能、UI 手册和受限数据库

- `list_skills(enabledOnly?)`、`read_skill(skillId)`；写入：`create_skill★(skillId,name,description,content,allowedTools?)`、`update_skill★(skillId, name?, description?, content?, allowedTools?)`、`delete_skill★(skillId)`、`toggle_skill★(skillId, enabled)`。
- `search_docs(query, limit?)`、`list_doc_sections()`：仅查询 `knowledge/ui_guide.md`；界面问题先查原文，配置 key 不等于页面名称。
- `list_danmaku_tables()`、`get_danmaku_table_schema(tableName)`、`get_danmaku_config_metadata()`、`count_danmaku_table(tableName)`、`list_recent_anime()`：仅固定白名单业务表/安全摘要；不是任意 SQL、不是配置/令牌/流控查询。

## API 网关与代码工具

- `list_api_operations(keyword?)` 仅列当前白名单操作；`call_api(operation_id, path_params?, query?, body?)` 只接受清单中的结构化 `operation_id`，不可传 URL/HTTP 方法。具体操作及其读写级别以该工具返回的运行时清单为准，不能把网关概括成“所有 API 可调用”；写操作仍需确认。通知模板、媒库/订阅/日历/存储等接口是否可用不得凭路由或名称推断；未出现在当前清单中的能力不可调用，界面问题改用 `search_docs` 查操作路径。
- 受认证管理员会话且宿主导出时才有：`code_capabilities()`、`code_search(query, prefix?)`、`code_read(path, start_line?)`、`code_prepare_patch(changes[])`、`code_validate_patch(draft_id, profile)`、`code_patch_status(draft_id)`、`code_apply_patch★(draft_id)`、`code_rollback_patch★(draft_id)`。验证 profile 仅 `python_syntax|python_tests|frontend_build|frontend_lint`；草稿不等于已修改，应用不等于部署。

`log_tools` 当前不注册任何工具；`list_tokens`、`list_log_files`、`search_logs`、`read_log_file` 被注册表禁用。`host_tools` 只提供未注册的拒绝实现，不能执行 shell、任意代码或文件请求。不得把导入模块、函数名或历史文档当作可调用能力。

## 调用原则

先用窄范围只读工具取得 ID、候选和证据，再调用写工具；破坏性操作复述对象与不可逆后果。不要输出密钥、令牌、流控、原始日志或未经工具返回的内部状态。调用次数、确认卡和实际可用工具由宿主结构化事件呈现，不在正文虚构。

调用只使用宿主提供的结构化 function calling；不要在正文输出 DSML `calls/invoke/parameter`、历史工具 JSON 或伪执行记录。最终回答只汇报已执行工具的结果，不能把下一步工具请求写成已完成。
