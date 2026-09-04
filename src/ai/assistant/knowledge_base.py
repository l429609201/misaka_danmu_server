"""
御坂助手 · UI 知识库检索（Knowledge Base）
------------------------------------------------------------
职责单一：把 knowledge/*.md 解析成「章节」列表，并按关键词 + 别名打分检索。

设计取舍：
- 不引入 embedding / 向量库（YAGNI）：文档量级只有 ~30 章节，关键词 + 别名
  已足够；引入向量检索会带来模型依赖、索引落盘、冷启动等一堆新问题。
- 章节即检索单元：`## 标题` 起一段，`> 别名:` 行提供口语化同义词，
  显著提升「重整数据源」「不导入」这类用户口语提问的命中率。
- 首次检索时懒加载并常驻内存（文档随代码发布，体积可控，无需反复读盘）。
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 知识库目录：随代码发布，用户不可改（与 skills 的用户目录形成互补）
KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"

# 章节标题行：## 开头（一级 # 是文件总标题，不作为检索单元）
_SECTION_PATTERN = re.compile(r"^##\s+(.+?)\s*$")
# 别名声明行：> 别名: a, b, c
_ALIAS_PATTERN = re.compile(r"^>\s*别名\s*[:：]\s*(.+?)\s*$")
# 分词：保留中文连续片段与英文数字单词，其余作为分隔符
_TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]+|[a-zA-Z0-9_@]+")

# 中文最短切片长度：把「弹幕库管理」切成 2-gram，便于与查询词部分匹配
_NGRAM = 2


@dataclass
class DocSection:
    """知识库中的一个章节（检索与返回的最小单元）。"""

    title: str
    aliases: List[str] = field(default_factory=list)
    body: str = ""
    source_file: str = ""

    @property
    def searchable_text(self) -> str:
        """用于关键词匹配的全文（标题权重靠打分体现，不在此处重复）。"""
        return f"{self.title}\n{' '.join(self.aliases)}\n{self.body}"


def _cn_ngrams(text: str) -> set:
    """把中文串切成 2-gram 集合，解决「弹幕库」与「弹幕库管理」无法直接相等的问题。"""
    grams = set()
    for token in _TOKEN_PATTERN.findall(text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            if len(token) <= _NGRAM:
                grams.add(token)
            else:
                for i in range(len(token) - _NGRAM + 1):
                    grams.add(token[i : i + _NGRAM])
        else:
            grams.add(token)
    return grams


def _parse_markdown(path: Path) -> List[DocSection]:
    """把单个 md 文件解析成章节列表。解析失败返回空列表，不抛异常打断对话。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.error(f"读取知识库文件失败 {path.name}: {e}")
        return []

    sections: List[DocSection] = []
    current: Optional[DocSection] = None
    body_lines: List[str] = []

    def _flush() -> None:
        if current is not None:
            current.body = "\n".join(body_lines).strip()
            sections.append(current)

    for line in raw.splitlines():
        m = _SECTION_PATTERN.match(line)
        if m:
            _flush()
            current = DocSection(title=m.group(1), source_file=path.name)
            body_lines = []
            continue
        if current is None:
            continue  # 文件头部（一级标题与说明）不进检索
        alias_m = _ALIAS_PATTERN.match(line)
        if alias_m:
            current.aliases = [
                a.strip() for a in re.split(r"[,，]", alias_m.group(1)) if a.strip()
            ]
            continue
        body_lines.append(line)

    _flush()
    return sections


class KnowledgeBase:
    """UI 知识库：懒加载解析 + 关键词/别名打分检索。"""

    def __init__(self) -> None:
        self._sections: Optional[List[DocSection]] = None

    def _ensure_loaded(self) -> List[DocSection]:
        if self._sections is not None:
            return self._sections
        sections: List[DocSection] = []
        if KNOWLEDGE_DIR.is_dir():
            for md in sorted(KNOWLEDGE_DIR.glob("*.md")):
                sections.extend(_parse_markdown(md))
        else:
            logger.warning(f"知识库目录不存在：{KNOWLEDGE_DIR}")
        self._sections = sections
        logger.info(f"UI 知识库加载完成：{len(sections)} 个章节")
        return sections

    def reload(self) -> int:
        """强制重新解析（改完 md 后无需重启进程即可生效）。"""
        self._sections = None
        return len(self._ensure_loaded())

    def list_titles(self) -> List[Dict[str, str]]:
        """列出所有章节标题与别名，供 LLM 概览可查范围。"""
        return [
            {"title": s.title, "aliases": "、".join(s.aliases)}
            for s in self._ensure_loaded()
        ]

    def _score(self, section: DocSection, query: str, grams: set) -> float:
        """对单个章节打分。命中位置越靠前（标题>别名>正文）权重越高。"""
        title_low = section.title.lower()
        alias_low = [a.lower() for a in section.aliases]
        body_low = section.body.lower()
        score = 0.0

        # 整串直接命中：最强信号
        if query and query in title_low:
            score += 30.0
        if query and any(query in a or a in query for a in alias_low):
            score += 25.0
        if query and query in body_low:
            score += 8.0

        # 分片命中：解决口语化与部分词
        title_grams = _cn_ngrams(section.title)
        alias_grams = _cn_ngrams(" ".join(section.aliases))
        body_grams = _cn_ngrams(section.body)
        score += 4.0 * len(grams & title_grams)
        score += 3.0 * len(grams & alias_grams)
        score += 0.6 * len(grams & body_grams)
        return score

    def search(self, query: str, limit: int = 3) -> List[Tuple[DocSection, float]]:
        """按查询词检索最相关的章节，返回 [(章节, 得分)]，得分降序。"""
        sections = self._ensure_loaded()
        q = (query or "").strip().lower()
        if not q or not sections:
            return []
        grams = _cn_ngrams(q)
        scored = [(s, self._score(s, q, grams)) for s in sections]
        scored = [item for item in scored if item[1] > 0]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[: max(1, limit)]


_kb: Optional[KnowledgeBase] = None


def get_knowledge_base() -> KnowledgeBase:
    """获取全局知识库单例。"""
    global _kb
    if _kb is None:
        _kb = KnowledgeBase()
    return _kb
