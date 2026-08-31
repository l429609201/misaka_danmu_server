"""
内置技能模板（参考 MoviePilot v3 设计）
------------------------------------------------------------
启动时自动同步到 config/skills/，只在目标不存在时写入，不覆盖用户修改。
提供 3 个示例：批量导入综艺、诊断弹幕缺失、配置识别词。
"""

import logging
from typing import Dict

import yaml

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
- 用户说"帮我加个识别词"、"这个标题识别不对"、"测试一下识别规则"

## 工作流程
1. **读取现有规则**：`get_recognition_rules` 查看当前所有规则（避免重复）
2. **理解用户需求**：
   - 如果是标题映射（如"某平台把A剧叫B"），用 `A剧 -> B剧` 格式
   - 如果是季度偏移（如"某源第1季对应实际第2季"），用 `[S:1->2]` 格式
   - 如果是集数偏移（如"某源集数比实际少1"），用 `[E+1]` 格式
3. **先测试再写入**：
   - 调用 `test_recognition` 用用户提供的测试标题干跑验证效果
   - 测试通过后，再调用 `set_recognition_rules` 追加（**mode 务必用 append，不要 replace**）
4. **冲突检测**：写入后调用 `check_recognition_conflicts` 检查是否有重复/冲突

## 关键注意
- 识别词规则全局生效，影响所有作品，务必谨慎
- **永远用 mode=append 追加**，除非用户明确说"清空重来"
- 测试环节不可跳过，避免写入错误规则导致全局识别混乱
- 如果用户提供的规则语法有问题，先解释 DSL 规则再让用户修正
""",
    ),
}


def sync_builtin_skills() -> None:
    """同步内置技能到持久化目录（仅当目标不存在时写入）。"""
    # 通过函数获取而非导入变量副本，确保拿到 set_skills_base_dir 之后的最新值
    skills_base_dir = get_skills_base_dir()
    if skills_base_dir is None:
        logger.warning("技能目录未初始化，跳过内置技能同步")
        return

    for skill_id, skill_template in BUILTIN_SKILLS.items():
        skill_dir = skills_base_dir / skill_id
        skill_file = skill_dir / "SKILL.md"
        if skill_file.exists():
            logger.debug(f"内置技能 {skill_id} 已存在，跳过（保护用户修改）")
            continue

        try:
            skill_dir.mkdir(parents=True, exist_ok=True)
            # 写入 SKILL.md（拼接 YAML frontmatter）
            frontmatter = {
                "name": skill_template.name,
                "version": skill_template.version,
                "description": skill_template.description,
                "allowed-tools": " ".join(skill_template.allowed_tools),
                "enabled": skill_template.enabled,
            }
            yaml_text = yaml.dump(frontmatter, allow_unicode=True, sort_keys=False)
            full_text = f"---\n{yaml_text}---\n{skill_template.content}\n"
            skill_file.write_text(full_text, encoding="utf-8")
            logger.info(f"同步内置技能: {skill_id}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"同步内置技能 {skill_id} 失败: {e}", exc_info=True)
