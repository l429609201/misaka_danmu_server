"""
Skill 管理器（参考 MoviePilot v3 设计）
------------------------------------------------------------
技能分两类：

1. **内置技能（builtin）**：定义在 builtin_skills.py 的代码里，随版本发布，
   **不落盘**。启动时直接加载进内存，随代码升级自动生效，用户不可改也不必改。
   避免了"改了代码但磁盘上的旧文件还在，导致修正不生效"的问题。

2. **用户技能（user）**：位于 config/skills/<skill_id>/SKILL.md，由用户或 AI 创建，
   需要持久化。SKILL.md 格式：
       ---
       name: import-anime-batch
       version: 1
       description: 何时该用这个技能（给 LLM 判断触发时机）
       allowed-tools: search_media import_selected
       enabled: true
       ---
       # 正文：详细操作步骤/流程/注意事项（给 LLM 当作业指导书）

功能：
1. 启动时先注册内置技能（内存），再扫描 config/skills/ 加载用户技能
2. 渐进式披露：system prompt 里只放 name+description（省 token），LLM 按需调工具读全文
3. 支持热重载（不重启即可加载新 skill）
4. allowed-tools 仅作提示写进 prompt，不做技术拦截（KISS 原则）
5. 内置技能受保护：不可 update/delete，仅允许 toggle（启停状态存到配置而非文件）

目录约定（仅用户技能）：
- Docker: /app/config/skills/
- 本地: config/skills/
- 每个 skill 独占一个目录，目录名 = skill ID（小写短横线命名）
"""

import logging
import re
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

# 技能目录基础路径（由 app_lifecycle 初始化）
SKILLS_BASE_DIR: Optional[Path] = None


def set_skills_base_dir(base_dir: Path) -> None:
    """设置技能基础目录（由 app_lifecycle 启动时调用）。"""
    global SKILLS_BASE_DIR
    SKILLS_BASE_DIR = base_dir / "config" / "skills"
    SKILLS_BASE_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"技能目录: {SKILLS_BASE_DIR}")


def get_skills_base_dir() -> Optional[Path]:
    """获取技能基础目录。

    必须通过本函数读取而非 `from ... import SKILLS_BASE_DIR`：
    后者在导入时就拷走了值（那时还是 None），set_skills_base_dir 之后拿不到新值。
    """
    return SKILLS_BASE_DIR


@dataclass
class Skill:
    """单个 Skill 的数据模型。

    正文（content）是懒加载的：注册表里只保留元数据，
    调用 `SkillManager.get_content(skill_id)` 时才真正取正文。
    这样常驻内存只有几百字的摘要，而不是全部技能的完整作业指导书。
    """
    skill_id: str  # 目录名（小写短横线）
    name: str  # frontmatter 里的 name
    version: int = 1
    description: str = ""  # 触发时机描述（给 LLM 判断）
    allowed_tools: List[str] = field(default_factory=list)  # 工具白名单（仅提示用）
    enabled: bool = True  # 是否启用
    content: str = ""  # 正文。注册表中的实例此字段为空，需用 get_content() 按需取
    file_path: Optional[Path] = None  # 原始文件路径（内置技能为 None）
    builtin: bool = False  # 是否为代码内置技能（不落盘、不可增删改）


class SkillManager:
    """Skill 加载器和管理器。"""

    def __init__(self):
        self._skills: Dict[str, Skill] = {}
        self._loaded = False
        # 内置技能启停状态（内存态，重启回到默认值；如需持久化可另存配置项）
        self._builtin_disabled: set = set()

    def load_all(self) -> None:
        """加载全部技能：先注册代码内置技能，再扫描 config/skills/ 加载用户技能。"""
        self._skills.clear()

        # 1. 内置技能：直接从代码加载，不读磁盘
        self._load_builtin_skills()

        # 2. 用户技能：扫描持久化目录
        base_dir = get_skills_base_dir()
        if base_dir is None:
            logger.warning("技能目录未初始化，仅加载内置技能")
        elif not base_dir.exists():
            logger.info("用户技能目录不存在，创建空目录")
            base_dir.mkdir(parents=True, exist_ok=True)
        else:
            for skill_dir in base_dir.iterdir():
                if not skill_dir.is_dir():
                    continue
                skill_file = skill_dir / "SKILL.md"
                if not skill_file.exists():
                    logger.debug(f"跳过无 SKILL.md 的目录: {skill_dir.name}")
                    continue
                # 用户目录里若存在与内置同名的技能，以内置为准（避免旧版残留文件覆盖新代码）
                if skill_dir.name in self._skills and self._skills[skill_dir.name].builtin:
                    logger.warning(
                        f"用户目录中的 {skill_dir.name} 与内置技能同名，已忽略磁盘版本（内置优先）"
                    )
                    continue
                try:
                    skill = self._parse_skill_file(skill_dir.name, skill_file)
                    self._skills[skill.skill_id] = skill
                    logger.info(f"加载用户技能: {skill.skill_id} v{skill.version} ({skill.name})")
                except Exception as e:  # noqa: BLE001
                    logger.error(f"解析 {skill_file} 失败: {e}", exc_info=True)

        self._loaded = True
        builtin_count = sum(1 for s in self._skills.values() if s.builtin)
        logger.info(
            f"技能加载完成，共 {len(self._skills)} 个"
            f"（内置 {builtin_count}，用户 {len(self._skills) - builtin_count}）"
        )

    def _load_builtin_skills(self) -> None:
        """把 builtin_skills.py 里定义的技能注册到内存。

        延迟导入避免与 builtin_skills 形成循环依赖（后者需要导入本模块的 Skill）。
        """
        try:
            from .builtin_skills import BUILTIN_SKILLS
        except Exception as e:  # noqa: BLE001
            logger.error(f"加载内置技能失败: {e}", exc_info=True)
            return

        for skill_id, template in BUILTIN_SKILLS.items():
            self._skills[skill_id] = Skill(
                skill_id=skill_id,
                name=template.name,
                version=template.version,
                description=template.description,
                allowed_tools=list(template.allowed_tools),
                # 内置技能默认启用，除非本次运行中被用户手动停用
                enabled=template.enabled and skill_id not in self._builtin_disabled,
                # 正文不复制进注册表，需要时由 get_content() 从 BUILTIN_SKILLS 取
                content="",
                file_path=None,
                builtin=True,
            )
            logger.debug(f"注册内置技能: {skill_id} v{template.version}")

    def _parse_skill_file(self, skill_id: str, file_path: Path, with_content: bool = False) -> Skill:
        """解析单个 SKILL.md 文件（YAML frontmatter + Markdown 正文）。

        Args:
            with_content: 是否解析正文。加载注册表时传 False（只要元数据，省内存）；
                          读取全文时传 True。
        """
        text = file_path.read_text(encoding="utf-8")
        # 匹配 YAML frontmatter: --- ... ---
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
        if not match:
            # 无 frontmatter，整个当正文
            return Skill(
                skill_id=skill_id,
                name=skill_id,
                content=text.strip() if with_content else "",
                file_path=file_path,
            )

        yaml_text, content = match.groups()
        try:
            meta = yaml.safe_load(yaml_text) or {}
        except yaml.YAMLError as e:
            logger.warning(f"{file_path} frontmatter 解析失败: {e}，使用空元数据")
            meta = {}

        return Skill(
            skill_id=skill_id,
            name=meta.get("name", skill_id),
            version=int(meta.get("version", 1)),
            description=meta.get("description", "").strip(),
            allowed_tools=[t.strip() for t in meta.get("allowed-tools", "").split() if t.strip()],
            enabled=meta.get("enabled", True),
            content=content.strip() if with_content else "",
            file_path=file_path,
        )

    def reload(self) -> None:
        """热重载：重新扫描目录并加载所有 skill。"""
        logger.info("开始热重载技能")
        self.load_all()

    def list_skills(self, enabled_only: bool = False) -> List[Skill]:
        """列出所有技能（可选：仅已启用）。"""
        if not self._loaded:
            self.load_all()
        skills = list(self._skills.values())
        if enabled_only:
            skills = [s for s in skills if s.enabled]
        return sorted(skills, key=lambda s: s.skill_id)

    def get_skill(self, skill_id: str) -> Optional[Skill]:
        """获取单个技能的元数据（不含正文，正文请用 get_content）。"""
        if not self._loaded:
            self.load_all()
        return self._skills.get(skill_id)

    def get_content(self, skill_id: str) -> Optional[str]:
        """按需取技能正文。

        - 内置技能：从 builtin_skills.BUILTIN_SKILLS 取（本来就在代码里，无额外开销）
        - 用户技能：此刻才读磁盘文件，读完即用不缓存

        Returns:
            正文字符串；技能不存在或读取失败时返回 None
        """
        skill = self.get_skill(skill_id)
        if not skill:
            return None

        if skill.builtin:
            try:
                from .builtin_skills import BUILTIN_SKILLS

                template = BUILTIN_SKILLS.get(skill_id)
                return template.content if template else None
            except Exception as e:  # noqa: BLE001
                logger.error(f"读取内置技能 {skill_id} 正文失败: {e}")
                return None

        if not skill.file_path or not skill.file_path.exists():
            logger.warning(f"技能 {skill_id} 的文件不存在: {skill.file_path}")
            return None
        try:
            parsed = self._parse_skill_file(skill_id, skill.file_path, with_content=True)
            return parsed.content
        except Exception as e:  # noqa: BLE001
            logger.error(f"读取技能 {skill_id} 正文失败: {e}")
            return None

    def get_skills_summary(self) -> List[Dict[str, str]]:
        """获取技能摘要（skillId + name + description），用于注入 system prompt。

        渐进式披露：只给摘要不给正文，LLM 判断需要时再调 read_skill 取全文，节省 token。
        """
        skills = self.list_skills(enabled_only=True)
        return [
            {
                "skillId": s.skill_id,
                "name": s.name,
                "description": s.description or "（无描述）",
            }
            for s in skills
        ]

    def create_skill(
        self,
        skill_id: str,
        name: str,
        description: str,
        content: str,
        allowed_tools: Optional[List[str]] = None,
        version: int = 1,
    ) -> Skill:
        """创建新技能（写入 SKILL.md 文件）。不能占用内置技能的 ID。"""
        base_dir = get_skills_base_dir()
        if base_dir is None:
            raise RuntimeError("技能目录未初始化")
        existing = self._skills.get(skill_id)
        if existing and existing.builtin:
            raise ValueError(f"{skill_id} 是内置技能的 ID，请换一个名字")
        if existing:
            raise ValueError(f"技能 {skill_id} 已存在，请用 update 更新")
        if not re.match(r"^[a-z0-9-]+$", skill_id):
            raise ValueError("skill_id 只能包含小写字母、数字、短横线")
        skill_dir = base_dir / skill_id
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"

        skill = Skill(
            skill_id=skill_id,
            name=name,
            version=version,
            description=description,
            allowed_tools=allowed_tools or [],
            enabled=True,
            content=content,
            file_path=skill_file,
        )
        self._write_skill_file(skill)
        # 注册表只存元数据：正文已落盘，副本清空避免常驻内存
        self._skills[skill_id] = replace(skill, content="")
        logger.info(f"创建技能: {skill_id}")
        return skill

    def update_skill(
        self,
        skill_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        content: Optional[str] = None,
        allowed_tools: Optional[List[str]] = None,
        version: Optional[int] = None,
    ) -> Skill:
        """更新现有技能（覆盖 SKILL.md 文件）。内置技能不可修改。"""
        skill = self._skills.get(skill_id)
        if not skill:
            raise ValueError(f"技能 {skill_id} 不存在")
        if skill.builtin:
            raise ValueError(
                f"{skill_id} 是内置技能，随版本发布不可修改。"
                f"如需定制，请用 create_skill 新建一个自己的技能"
            )

        # 更新字段（None 表示不变）
        if name is not None:
            skill.name = name
        if description is not None:
            skill.description = description
        if allowed_tools is not None:
            skill.allowed_tools = allowed_tools
        if version is not None:
            skill.version = version
        else:
            skill.version += 1  # 自动递增版本号

        # 注册表里不存正文，写盘时必须补齐：
        # 未传 content 表示保持原样，需先从磁盘读回，否则会把用户正文清空
        write_content = content if content is not None else (self.get_content(skill_id) or "")
        self._write_skill_file(replace(skill, content=write_content))
        logger.info(f"更新技能: {skill_id} v{skill.version}")
        return skill

    def delete_skill(self, skill_id: str) -> None:
        """删除技能（删除目录和 SKILL.md 文件）。内置技能不可删除，可用 toggle 停用。"""
        skill = self._skills.get(skill_id)
        if not skill:
            raise ValueError(f"技能 {skill_id} 不存在")
        if skill.builtin:
            raise ValueError(
                f"{skill_id} 是内置技能不可删除。如不想让它生效，请用 toggle_skill 停用"
            )
        base_dir = get_skills_base_dir()
        if base_dir is None:
            raise RuntimeError("技能目录未初始化")

        skill_dir = base_dir / skill_id
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
        self._skills.pop(skill_id, None)
        logger.info(f"删除技能: {skill_id}")

    def toggle_skill(self, skill_id: str, enabled: bool) -> Skill:
        """启用/停用技能。

        用户技能写回 SKILL.md 的 frontmatter；
        内置技能不落盘，仅记录在内存（进程重启后回到默认启用状态）。
        """
        skill = self._skills.get(skill_id)
        if not skill:
            raise ValueError(f"技能 {skill_id} 不存在")

        skill.enabled = enabled
        if skill.builtin:
            # 内置技能无文件可写，用内存集合记录停用状态
            if enabled:
                self._builtin_disabled.discard(skill_id)
            else:
                self._builtin_disabled.add(skill_id)
            logger.info(f"内置技能 {skill_id} 已{'启用' if enabled else '停用'}（本次运行有效）")
        else:
            # 用户技能写盘：注册表里无正文，需先取回
            content = self.get_content(skill_id) or ""
            self._write_skill_file(replace(skill, content=content))
            logger.info(f"技能 {skill_id} 已{'启用' if enabled else '停用'}")
        return skill

    def _write_skill_file(self, skill: Skill) -> None:
        """写入 SKILL.md 文件（YAML frontmatter + Markdown 正文）。"""
        if not skill.file_path:
            raise RuntimeError(f"技能 {skill.skill_id} 无文件路径")

        frontmatter = {
            "name": skill.name,
            "version": skill.version,
            "description": skill.description,
            "allowed-tools": " ".join(skill.allowed_tools),
            "enabled": skill.enabled,
        }
        yaml_text = yaml.dump(frontmatter, allow_unicode=True, sort_keys=False)
        full_text = f"---\n{yaml_text}---\n{skill.content}\n"
        skill.file_path.write_text(full_text, encoding="utf-8")


# 全局单例
_skill_manager = SkillManager()


def get_skill_manager() -> SkillManager:
    """获取全局 SkillManager 实例。"""
    return _skill_manager
