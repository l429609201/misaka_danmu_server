"""
内置技能定义（参考 MoviePilot v3 设计）
------------------------------------------------------------
这里定义的技能是**代码内置、随版本发布的**，启动时由 SkillManager 直接加载进内存，
**不落盘到 config/skills/**。好处：
- 修正技能内容只需改这个文件，升级即生效，不会被磁盘上的旧文件挡住
- 用户目录只存用户自建技能，职责清晰

内置技能对用户只读：不可 update/delete，可以 toggle 停用（内存态，重启恢复）。
用户想定制某个内置流程时，用 create_skill 新建自己的技能。
"""

import logging
import shutil
from typing import Dict

from .skill_manager import get_skills_base_dir, Skill

logger = logging.getLogger(__name__)

# 内置技能定义（skill_id → Skill对象，不含 file_path）
BUILTIN_SKILLS: Dict[str, Skill] = {
    "import-variety-batch": Skill(
        skill_id="import-variety-batch",
        name="批量导入综艺",
        version=1,
        description="用户要批量导入综艺（尤其需要配合单剧过滤规则过滤加更/纯享/预告版本）时使用此技能。",
        allowed_tools=[
            "search_media", "get_provider_episodes", "import_edited",
            "get_single_episode_filter", "set_single_episode_filter"
        ],
        enabled=True,
        content="""
## 触发时机
- 用户说"导入xxx综艺"、"帮我把xx的所有集都导进来"
- 综艺往往有「加更」「纯享」「会员版」等多版本，需配合单剧过滤

## 工作流程
1. **搜索媒体**：调用 `search_media` 找到该综艺（传 season 确保是综艺系列）
2. **检查过滤规则**：调用 `get_single_episode_filter` 看该综艺是否已有过滤规则
   - 若无或规则不够准确，引导用户用 `set_single_episode_filter` 添加
   - 规则格式：`综艺名 => {[rules=加更|纯享|会员版;provider=可选源;mediaId=可选]}`
3. **获取分集列表**：调用 `get_provider_episodes` 获取该源的所有分集
4. **过滤与确认**：根据规则过滤掉不需要的版本，向用户展示最终列表
5. **执行导入**：调用 `import_edited` 提交编辑后的分集列表

## 关键注意
- 综艺往往有重复版本，单剧过滤是关键环节，不能跳过
- 过滤规则写好后，后续导入同一综艺都会自动生效
- 如果用户不确定要过滤哪些关键词，建议先展示原始分集列表让用户挑选
""",
    ),
    "diagnose-danmaku-missing": Skill(
        skill_id="diagnose-danmaku-missing",
        name="诊断弹幕缺失",
        version=1,
        description="用户反馈某作品或某集弹幕缺失、数量异常少时使用此技能，定位根因（是否过滤、是否导入失败）。",
        allowed_tools=[
            "search_library", "get_anime_sources", "get_source_episodes",
            "get_global_filter", "get_global_episode_title_filter",
            "get_single_episode_filter", "get_source_episode_blacklist",
            "list_tasks", "get_task_status"
        ],
        enabled=True,
        content="""
## 触发时机
- 用户说"为什么这集没弹幕"、"弹幕怎么这么少"、"xx的弹幕是不是导入失败了"

## 诊断路径（按此顺序排查）
1. **确认作品存在**：`search_library` 确认该作品已入库
2. **检查数据源**：`get_anime_sources` 看有几个源、每个源的分集数
   - 如果分集数为 0 或缺少目标集，说明导入时就没抓到
3. **排查过滤规则**（弹幕导入的三层过滤链）：
   - 第 1 层（单源黑名单）：`get_source_episode_blacklist` 检查该源是否配置了黑名单正则
   - 第 2 层（兜底全局分集过滤）：`get_global_episode_title_filter` 看是否全局启用且命中
   - 第 3 层（单剧过滤）：`get_single_episode_filter` 看该作品是否有专属过滤规则
   - 作品级过滤（影响搜索）：`get_global_filter` 检查是否在搜索阶段就被过滤掉
4. **检查导入任务状态**：`list_tasks` 找到相关任务，`get_task_status` 看是否失败/被取消
5. **给出结论**：
   - 若命中过滤规则，告知用户"这集标题命中了xxx过滤规则，弹幕被有意过滤"
   - 若任务失败，告知用户失败原因（如超时、源不可用）
   - 若分集根本未导入，建议用户重新搜索或用 URL 导入补全

## 关键注意
- 弹幕少不一定是 bug，很多时候是过滤规则生效（尤其综艺的加更/纯享版）
- 给用户解释清楚三层过滤链，帮助用户理解系统设计
""",
    ),
    "configure-recognition-rules": Skill(
        skill_id="configure-recognition-rules",
        name="配置识别词规则",
        version=1,
        description="用户需要添加/测试识别词规则（自定义标题识别、季度/集数偏移）时使用此技能。",
        allowed_tools=[
            "get_recognition_rules", "test_recognition",
            "check_recognition_conflicts", "set_recognition_rules"
        ],
        enabled=True,
        content="""
## 触发时机
- 用户说"帮我加个识别词"、"这个标题识别不对"、"季度/集数对不上"、"测试一下识别规则"

## 五种语法（务必按实际格式写，不要臆造）

### 1. 屏蔽词
```
内地版
```
单独一行写词本身，搜索时自动从标题中去除。

### 2. 简单替换
```
被替换词 => 替换词
```
注意 `=>` 两侧各有一个空格，这是解析的硬要求。

### 3. 集数偏移
```
前定位词 <> 后定位词 >> 集偏移量
```
示例 `第 <> 期 >> -1`：把「第20期」识别为第 19 集。
偏移量带符号：源站集数比实际小用 `+`，比实际大用 `-`。

### 4. 复合格式（替换 + 偏移一起做）
```
被替换词 => 替换词 && 前定位词 <> 后定位词 >> 集偏移量
```
示例：`爱豆VS剧 => 爱·回家之开心速递 && 第 <> 集 >> -370`

### 5. 元数据块（季度映射 / 强制入库名 / 绑定 ID）
```
源标题 => {[key=value;key=value]}
```
支持的键（**只有这些，其他键会被忽略**）：

| 键 | 说明 | 示例 |
|---|---|---|
| `source` | 弹幕源标识，省略时默认 `all`（对所有源生效） | `source=iqiyi` |
| `title` | 强制入库名 | `title=中国新说唱 第九季` |
| `season_offset` | 季度映射，格式 `源季>入库季`（不是偏移量） | `season_offset=1>9` |
| `ep_offset` | 集数偏移量，带符号 | `ep_offset=+12` |
| `ep_range` | 限定生效集数范围，`*` 表示到末尾 | `ep_range=1-12` / `ep_range=13-*` |
| `search_season` | 搜索时强制拼接的季度号 | `search_season=8` |
| `tmdbid` / `doubanid` | 强制绑定元数据 ID（整数） | `tmdbid=12345` |
| `type` | 强制媒体类型 | `type=tv` |
| `s` / `e` | 季度/集数关键词，用于特殊标题格式解析 | `s=S` |

**实战示例（综艺换名 + 季度错位）**
```
说唱巅峰对决2026 => {[source=iqiyi;title=中国新说唱 第九季;season_offset=1>9]}
```
这条规则同时做三件事：
- 反向映射：用户搜「中国新说唱 第九季」时，系统改用「说唱巅峰对决2026」去源站搜
- 强制改名：入库统一为「中国新说唱 第九季」
- 季度映射：源站的第 1 季入库为第 9 季

## 工作流程
1. **读取现有规则**：`get_recognition_rules` 查看当前所有规则，避免重复和冲突
2. **判断该用哪种语法**：
   - 搜索时标题不对 → 屏蔽词 / 简单替换
   - 集数编号错位 → 集数偏移 / 复合格式
   - 季度错位、要统一入库名、要绑定 ID → 元数据块
3. **干跑验证**：`test_recognition(title=..., season=..., episode=...)` 确认输出符合预期
4. **追加写入**：`set_recognition_rules(content=..., mode="append")`
5. **冲突检测**：写入后 `check_recognition_conflicts` 检查重复/空规则

## 关键注意
- 识别词全局生效，影响所有作品与所有搜索路径，务必谨慎
- **永远用 mode=append**，除非用户明确说"清空重来"
- `=>`、`<>`、`>>` 两侧的空格是解析硬要求，缺空格规则不生效
- `season_offset` 是映射关系（`1>9` 表示源第1季=入库第9季），不是加减偏移量
- 元数据块省略 `source` 时默认对所有源生效，范围可能超出用户预期

## 停止条件（不要写库，先问用户）
- 干跑结果与用户预期不符（集数/季度不对、标题没改对）
- 用户给的 metadata 键不在上表中
- 规则会覆盖已有的同名映射（`get_recognition_rules` 里已存在相同左侧词）
- 用户要求 `mode="replace"` 但没明确说清空全部
- 一次要改多部作品且规则复杂，应拆分逐条确认
""",
    ),
    "config-episode-filter": Skill(
        skill_id="config-episode-filter",
        name="配置分集与搜索过滤",
        version=1,
        description="用户询问分集过滤怎么配、预告/花絮/加更为什么没被过滤、四层过滤链该用哪一层，或搜索结果出现伪条目时使用此技能。",
        allowed_tools=[
            "get_global_filter", "set_global_filter",
            "get_global_episode_title_filter", "set_global_episode_title_filter",
            "get_single_episode_filter", "set_single_episode_filter",
            "get_source_episode_blacklist", "set_source_episode_blacklist",
            "test_regex",
        ],
        enabled=True,
        content="""
## 触发时机
- 用户说"预告没过滤掉"、"加更版怎么去掉"、"搜索结果里有奇怪的条目"、"过滤规则配了没生效"

## 四层过滤链（选错层会范围过大或完全不生效）

| 层 | 作用对象 | 生效范围 | 典型用途 |
|---|---|---|---|
| 作品级 | 搜索结果条目标题 | 全局 | 剔除「XX预告合集」这类伪条目 |
| 第1层 | 分集标题 | 单个弹幕源 | 某源特有的命名怪癖 |
| 第2层 | 分集标题 | 所有源（兜底） | 通用垃圾：预告/花絮/彩蛋 |
| 第3层 | 分集标题 | 特定作品 | 综艺的加更/纯享/会员版 |

执行顺序：作品级在搜索阶段剔除整条结果；分集三层在获取分集列表时按 1→2→3 叠加生效。

## 各层格式与工具

### 作品级（搜索结果标题）
- `get_global_filter()` / `set_global_filter(cn=..., eng=..., mode="append")`
- 格式：`|` 分隔的正则，如 `特典|预告|花絮|彩蛋`
- 英文黑名单按独立词匹配，避免 `CD` 误伤 `CDrama`

### 第1层（单源分集黑名单）
- `get_source_episode_blacklist(provider="bilibili")`
- `set_source_episode_blacklist(provider="bilibili", regex="预告", mode="append")`
- provider 取值：`tencent` / `bilibili` / `iqiyi` / `youku` / `mgtv` / `gamer` 等

### 第2层（兜底全局分集过滤）
- `get_global_episode_title_filter()`
- `set_global_episode_title_filter(enabled=True, regex="预告|花絮", mode="append")`
- **开关默认关闭**。用户说"配了正则没生效"时先查 `globalEpisodeTitleFilterEnabled`

### 第3层（单剧过滤）
- `get_single_episode_filter()` / `set_single_episode_filter(content=..., mode="append")`
- 格式：`作品匹配词 => {[rules=正则;provider=可选;mediaId=可选]}`
- `rules` 内部是正则，多个用 `|` 分隔；`provider` 限定源；`mediaId` 限定具体媒体
- 示例：`奔跑吧 => {[rules=加更|纯享|未播;provider=tencent]}`

## 工作流程
1. **定位范围**（选层唯一依据）：问清脏数据出现在哪些源、哪些作品上
2. **读现有配置**：调对应 `get_*`，避免重复添加
3. **验证正则**：`test_regex` 既要测命中目标标题，**也要测一条正片标题确认不误伤**
4. **追加写入**：`mode="append"`
5. **汇总确认**：用哪层、为什么、正则验证结果、生效范围

## 排查"配了没生效"的顺序
1. 第2层开关是否为 true
2. 正则能否真命中目标标题（test_regex 实测）
3. 层级是否选对（给 B 站的问题配到了 tencent 黑名单上）
4. 单剧过滤的作品匹配词是否与库内标题一致
5. 分集是否在配规则**之前**就已导入（过滤只作用于新获取的分集，已入库需重新导入）

## 停止条件
- `test_regex` 显示正则会误伤正片标题
- 用户需求跨多层，需拆成多次操作分别确认
- 用户要 `mode="replace"` 但没明确说清空
- 第2层开关关闭而用户只要求改正则（需提示同时开启）
- provider 名称不在已加载源列表中
""",
    ),
    "config-danmaku-output": Skill(
        skill_id="config-danmaku-output",
        name="配置弹幕输出与文件路径",
        version=1,
        description="用户询问弹幕输出配置（输出上限、合并输出、简繁转换、弹幕位置转换、点赞样式、随机染色、弹幕内容黑名单、自动刷新）或弹幕文件保存路径与命名模板时使用此技能。",
        allowed_tools=["get_config", "set_config"],
        enabled=True,
        content="""
## 触发时机
- 用户说"弹幕太密/太少"、"繁简不对"、"弹幕挡画面"、"点赞没显示"、"弹幕颜色单调"、
  "有垃圾弹幕"、"弹幕文件存哪"、"想改文件名格式"

## 配置项速查（键名区分大小写，值统一传字符串）

### 输出数量与合并
- `danmakuOutputLimitPerSource`（默认 `-1`）：单源输出上限，`-1` 无限制。
  设正整数后超出部分按时间段**均匀采样**，不是简单截断。
- `danmakuMergeOutputEnabled`（默认 `false`）：合并所有源后再统一采样输出。

### 简繁转换
- `danmakuChConvert`（默认 `0`）：`0` 不转换 / `1` 转简体 / `2` 转繁体
- `danmakuChConvertPriority`（默认 `player`）：`player` 播放器优先 / `server` 服务端优先
  用户说"服务端设了简体还是繁体" → 多半是播放器传参覆盖，改 `server`

### 弹幕位置转换（仅输出时转换，不改已存储弹幕）
- `danmakuTopConvertTo`（默认 `none`）：`none` / `bottom` / `scroll`
- `danmakuBottomConvertTo`（默认 `none`）：`none` / `top` / `scroll`
- 顶部弹幕是 `mode=5`，底部是 `mode=4`；基于原始类型一次性映射，不连锁转换

### 点赞显示
- `danmakuLikesFetchEnabled`（默认 `true`）：下载时是否**存**点赞数据
- `danmakuLikesOutputEnabled`（默认 `true`）：输出时是否**显示**
- `danmakuLikesStyle`（默认 `heart_white`）：六种取值
  `heart_white`(🤍/🔥) / `heart_red`(❤️/🔥) / `heart_outline`(♡/🔥) /
  `like_bracket`([👍]/[🔥]) / `text`(点赞/热门) / `num_only`(+数字)

**Fetch 与 Output 的区别**：关掉 Fetch 后新下载的弹幕永久没有点赞数据（重开也补不回，
需重新下载）；关掉 Output 只是不显示，数据仍在文件里。

### 随机染色
- `danmakuRandomColorMode`（默认 `off`）：`off` / `white_to_random`（只染白色弹幕） /
  `all_random` / `all_white`
- `danmakuRandomColorPalette`：色板，**逗号分隔的十进制颜色值**（不是十六进制）
  `#FFFFFF` = `16777215`。默认值重复 8 次白色来加大白色概率。
  用户给十六进制时先换算再写入，并告知换算结果。
- 一般推荐 `white_to_random`：保留原本有颜色的弹幕，只把单调白色染开

### 弹幕内容黑名单
- `danmakuBlacklistEnabled`（默认 `false`）：开关
- `danmakuBlacklistPatterns`：`|` 分隔的正则，匹配弹幕内容（`m` 字段），不区分大小写
- 系统内置数百条默认规则。用户要加词时**必须先读后拼**：`get_config` 取现值 →
  拼 `|新词` → 写回。直接覆盖会丢掉全部内置规则。

### 自动刷新（两者是与关系）
- `danmakuAutoRefreshDays`（默认 `0`）：超过多少天自动重抓，`0` 禁用
- `danmakuRefreshThreshold`（默认 `5000`）：仅当该集现有弹幕**低于**此值才重抓，`0` 不限
- 设计意图：避免对已抓全的热门剧反复刷新

### 文件保存路径与命名
- `customDanmakuPathEnabled`（默认 `false`）：**关闭时下面 4 项全部无效**
- `movieDanmakuDirectoryPath`（默认 `/app/config/danmaku/movies`）
- `movieDanmakuFilenameTemplate`（默认 `${title}/${episodeId}`）
- `tvDanmakuDirectoryPath`（默认 `/app/config/danmaku/tv`）
- `tvDanmakuFilenameTemplate`（默认 `${animeId}/${episodeId}`）

**10 个模板变量**：`${title}`、`${titleBase}`（标准化标题，去除季度信息）、`${season}`、
`${episode}`、`${year}`、`${provider}`、`${animeId}`、`${episodeId}`、`${sourceId}`、`${tmdbId}`

模板支持子目录（`/` 分隔），`.xml` 后缀自动添加不要手写。
改模板**不会**重命名已有文件，只影响后续新增。

## 工作流程
1. **定位配置项**：按用户描述的现象对应到上面的键
2. **先读现有值**：尤其黑名单这类需要拼接的长文本，不读就写等于清空
3. **检查依赖**（依赖不满足时改了不生效，必须主动提示）
   - `danmakuLikesStyle` ← `danmakuLikesOutputEnabled=true`
   - 4 个路径/模板项 ← `customDanmakuPathEnabled=true`
   - `danmakuBlacklistPatterns` ← `danmakuBlacklistEnabled=true`
   - `danmakuRefreshThreshold` ← `danmakuAutoRefreshDays>0`
4. **汇总确认**：改哪个键、从什么改成什么、预期效果、是否影响已有弹幕

## 停止条件
- 枚举值不在允许列表中（如 `danmakuLikesStyle` 给了 `heart_blue`）
- 要改黑名单但没说清追加还是替换（替换会丢内置数百条规则）
- 色板给的是十六进制且换算结果未经用户确认
- 文件名模板去掉了所有唯一标识（`${episodeId}` / `${animeId}` 都没有），文件会互相覆盖
- 依赖开关关闭而用户只要求改子配置项
""",
    ),
    "config-ai-params": Skill(
        skill_id="config-ai-params",
        name="配置AI功能参数",
        version=1,
        description="用户询问AI相关配置（提供商与模型、各AI功能开关、温度/token/超时等LLM参数、AI缓存、思考模式）该怎么填时使用此技能。",
        allowed_tools=["get_config", "set_config"],
        enabled=True,
        content="""
## 触发时机
- 用户说"AI匹配怎么开"、"temperature 填多少"、"AI 超时了"、"该用哪个模型"、
  "AI 调用太贵想省钱"、"思考模式是什么"

## 基础连接配置
- `aiProvider`（默认 `deepseek`）：`deepseek` / `siliconflow` / `openai` / `gemini`
- `aiApiKey`：API 密钥。写入时**绝不回显明文**，只确认「已写入」
- `aiBaseUrl`：自定义接口地址，留空则用提供商默认值
- `aiModel`：模型名，如 `deepseek-chat` / `gpt-4o` / `gemini-2.5-flash`
  可通过提供商接口刷新获取可用列表，不要凭印象编造模型名

## AI 功能开关（各自独立，按需开启）
- `aiMatchEnabled`（默认 `false`）：AI 智能匹配。在外部API/Webhook/匹配后备场景中用 AI 选最佳搜索结果
- `aiFallbackEnabled`（默认 `true`）：AI 匹配失败时降级到传统算法。**建议保持开启**
- `aiRecognitionEnabled`（默认 `false`）：AI 辅助识别标题与季度（TMDB 刮削任务中）
- `aiAliasCorrectionEnabled`（默认 `false`）：AI 验证与修正别名
- `aiAliasExpansionEnabled`（默认 `false`）：元数据源返回非中文标题时，AI 生成可能的中文别名
- `aiNameConversionEnabled`（默认 `false`）：元数据源查询失败时用 AI 做名称转换（兜底）
- `aiEpisodeGroupEnabled`（默认 `false`）：有 TMDB ID 但缺剧集组时，AI 自动选最佳剧集组
- `aiThinkingEnabled`（默认 `false`）：DeepSeek 思考模式。提升准确性但**显著增加耗时与 token 消耗**，
  且**仅对 DeepSeek 生效**

每个功能都有对应的 `*Prompt` 配置项可自定义提示词，留空用内置默认值。

## 御坂助手的 LLM 参数（对话质量调优）
- `assistantTemperature`（默认 `0.7`，范围 0-2）：`0` 精确 / `0.7` 平衡 / `2` 创意。
  管理类操作建议 0.3-0.7，不要调高（会增加瞎编风险）
- `assistantMaxTokens`（默认 `2000`，范围 100-8000）：单次回答最大输出长度
- `assistantTopP`（默认 `0.9`，范围 0-1）：词汇多样性。一般不动，与 temperature 二选一调
- `assistantPresencePenalty`（默认 `0.0`，范围 -2~2）：抑制重复话题
- `assistantFrequencyPenalty`（默认 `0.0`，范围 -2~2）：抑制重复用词
- `assistantTimeout`（默认 `120`，范围 10-300）：请求超时秒数。
  慢速推理模型（o3/o4）建议 180-300
- `assistantProxyEnabled`（默认 `false`）：是否为助手启用代理（复用全局 `proxyUrl`）

## 性能与成本控制
- `aiCacheEnabled`（默认 `true`）：相同查询直接返回缓存，**显著降低 API 成本**。建议保持开启
- `aiCacheTtl`（默认 `3600` 秒）：缓存过期时间
- `aiCallTimeout`（默认 `60` 秒）：AI API 单次请求超时。o3/o4 等慢模型建议 120-300
- `aiLogRawResponse`（默认 `false`）：记录 AI 原始交互到 `ai_responses.log`。
  排查 AI 行为异常时开启，日志会包含完整请求与响应，排查完建议关掉

## 用户抱怨对应关系
- "AI 超时" → 调高 `aiCallTimeout` 与 `assistantTimeout`；确认模型是否为慢速推理模型
- "AI 调用太贵" → 确认 `aiCacheEnabled=true`、关掉 `aiThinkingEnabled`、
  调低 `assistantMaxTokens`、只开真正需要的功能开关
- "AI 回答太发散/瞎编" → 调低 `assistantTemperature`（0.3 左右）
- "AI 匹配错了" → 确认 `aiFallbackEnabled=true` 有兜底；开 `aiLogRawResponse` 查原始响应
- "AI 回答被截断" → 调高 `assistantMaxTokens`

## 工作流程
1. **先读现有配置**：`get_config` 确认当前值与已开启的功能
2. **区分是哪一类问题**：连接配置 / 功能开关 / 对话参数 / 成本性能
3. **检查依赖与生效范围**
   - `aiThinkingEnabled` 仅 DeepSeek 生效，其他提供商设了无效
   - 各 `*Prompt` 只在对应功能开关打开时才被使用
   - 所有 AI 功能都依赖 `aiApiKey` / `aiModel` 已正确配置
4. **汇总确认后写入**：说明改哪项、新值、预期影响（尤其涉及成本变化时要讲清）

## 停止条件
- 数值超出允许范围（如 temperature 给了 3、timeout 给了 5）
- 提供商与模型不匹配（如 `aiProvider=deepseek` 却填 `gpt-4o`）
- 用户要开 `aiThinkingEnabled` 但提供商不是 DeepSeek（需说明不生效）
- 用户要关 `aiFallbackEnabled`（会失去兜底，AI 失败即全盘失败，需明确风险后确认）
- 要写 `aiApiKey` 但用户是在公共场合/共享会话中提供的（提示注意密钥安全）
- 模型名不确定是否存在（应让用户通过刷新模型列表确认，不要猜）
""",
    ),
}


def cleanup_legacy_builtin_files() -> None:
    """清理早期版本落盘到 config/skills/ 的内置技能残留目录。

    旧实现会把内置技能写成 SKILL.md 并"仅当不存在时写入"，导致代码里修正了内容
    但磁盘旧文件仍在、修正不生效。现在内置技能改为纯内存加载，这些残留需要删除，
    否则会与内置技能同名冲突（SkillManager 会忽略并告警，但目录留着没有意义）。

    只删除确认属于内置技能 ID 的目录，不碰用户自建技能。
    """
    skills_base_dir = get_skills_base_dir()
    if skills_base_dir is None or not skills_base_dir.exists():
        return

    for skill_id in BUILTIN_SKILLS:
        legacy_dir = skills_base_dir / skill_id
        if not legacy_dir.is_dir():
            continue
        try:
            shutil.rmtree(legacy_dir)
            logger.info(f"已清理旧版内置技能残留目录: {skill_id}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"清理旧版内置技能目录 {skill_id} 失败: {e}")
