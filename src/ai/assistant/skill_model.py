"""技能共享模型及基础目录状态，避免内置技能与管理器循环依赖。"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

SKILLS_BASE_DIR: Optional[Path] = None


def set_skills_base_dir_value(base_dir: Path) -> None:
    """记录用户技能目录供管理器和内置技能迁移使用。"""
    global SKILLS_BASE_DIR
    SKILLS_BASE_DIR = base_dir


def get_skills_base_dir() -> Optional[Path]:
    """读取启动后设置的技能目录。"""
    return SKILLS_BASE_DIR


@dataclass
class Skill:
    """技能元数据；用户技能正文按需从文件读取。"""

    skill_id: str
    name: str
    version: int = 1
    description: str = ""
    allowed_tools: List[str] = field(default_factory=list)
    enabled: bool = True
    content: str = ""
    file_path: Optional[Path] = None
    builtin: bool = False
