"""
御坂助手 · 提示词素材加载
------------------------------------------------------------
职责单一：把 prompts/*.md 读出来拼成 system prompt 的一段。

与 knowledge_base.py 的区别（两者都是 md，用途完全不同，故分目录存放）：
- prompts/*.md   → 常驻注入 system prompt，全文进上下文，本模块负责
- knowledge/*.md → 按需检索（search_docs），只有命中章节才进上下文

why 外置成 md 而非继续写在 personas.py 的字符串常量里：
1. 领域知识/行为准则/工具清单原先塞在一个 400 行的 Python 常量里，
   与人设、排版格式混在同一文件，改一处容易漏改另一处；
2. md 可直接被人阅读与 diff，新增工具时改文档即可，无需碰 Python；
3. reload() 支持改完 md 不重启进程即生效，便于调优提示词。

装载顺序即注入顺序，由 _PROMPT_FILES 显式声明（不依赖文件名排序，避免改名即改行为）。
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 提示词目录：随代码发布，用户不可改
PROMPTS_DIR = Path(__file__).parent / "prompts"

# 注入顺序：领域知识 → 行为准则 → 工具地图
# 顺序有意义：先让模型知道"这是什么系统"，再约束"怎么做"，最后给"有什么工具"
_PROMPT_FILES: List[str] = [
    "domain.md",
    "conventions.md",
    "tools.md",
]

# md 文件头部的说明区（一级标题与编辑边界说明）不进 prompt，
# 用分隔线 --- 作为正文起点标记，避免把"给维护者看的话"喂给模型浪费 token。
_BODY_SEPARATOR = "\n---\n"


def _load_body(path: Path) -> str:
    """读取单个 md 的正文（首个 --- 之后的内容）。失败返回空串，不打断对话。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.error(f"读取提示词文件失败 {path.name}: {e}")
        return ""

    idx = raw.find(_BODY_SEPARATOR)
    body = raw[idx + len(_BODY_SEPARATOR):] if idx >= 0 else raw
    return body.strip()


class PromptLoader:
    """提示词素材加载器：懒加载 + 常驻内存 + 支持热重载。"""

    def __init__(self) -> None:
        self._cache: Optional[str] = None

    def _build(self) -> str:
        parts: List[str] = []
        for name in _PROMPT_FILES:
            path = PROMPTS_DIR / name
            if not path.is_file():
                logger.warning(f"提示词文件缺失：{path}")
                continue
            body = _load_body(path)
            if body:
                parts.append(body)
        if not parts:
            logger.error(f"提示词目录为空或全部读取失败：{PROMPTS_DIR}")
        return "\n\n".join(parts)

    def get(self) -> str:
        """获取拼接后的提示词正文（首次调用时加载并缓存）。"""
        if self._cache is None:
            self._cache = self._build()
            logger.info(
                f"助手提示词素材加载完成：{len(_PROMPT_FILES)} 个文件，"
                f"共 {len(self._cache)} 字符"
            )
        return self._cache

    def reload(self) -> int:
        """强制重新读取（改完 md 后无需重启进程），返回字符数。"""
        self._cache = None
        return len(self.get())

    def describe(self) -> List[Dict[str, object]]:
        """列出各文件的加载状态与体量，供诊断使用。"""
        info: List[Dict[str, object]] = []
        for name in _PROMPT_FILES:
            path = PROMPTS_DIR / name
            exists = path.is_file()
            info.append({
                "file": name,
                "exists": exists,
                "chars": len(_load_body(path)) if exists else 0,
            })
        return info


_loader: Optional[PromptLoader] = None


def get_prompt_loader() -> PromptLoader:
    """获取全局提示词加载器单例。"""
    global _loader
    if _loader is None:
        _loader = PromptLoader()
    return _loader


def get_system_knowledge() -> str:
    """获取系统领域知识 + 行为准则 + 工具地图的拼接文本。

    供 personas.get_persona_prompt() 组装 system prompt 时调用。
    """
    return get_prompt_loader().get()
