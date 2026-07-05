"""
通用别名语言识别工具。

只负责「识别文本语言」这一纯逻辑，不含任何源相关的挑选/偏好策略。
各弹幕源（如 gamer）若需按语言挑选搜索关键词，应在自身实现里调用本模块，
再叠加自己的偏好顺序，避免通用工具被特定源的业务逻辑污染。

语言标签约定：
    "zh"    —— 中文（CJK 主导且不含日文假名，含简体/繁体）
    "ja"    —— 日文（含平假名或片假名）
    "en"    —— 非 CJK（英文/罗马字等）
    "other" —— 空串或无法判定
"""
from typing import List, Tuple


def _has_jp_kana(text: str) -> bool:
    """是否包含日文假名（平假名 U+3040-309F / 片假名 U+30A0-30FF）。"""
    return any('\u3040' <= c <= '\u30ff' for c in text)


def _is_cjk_dominant(text: str) -> bool:
    """文本是否以 CJK（汉字/假名/韩文）为主，用于区分中日文与英文。"""
    if not text:
        return False
    cjk_count = sum(
        1 for c in text
        if '\u4e00' <= c <= '\u9fff'    # CJK 统一汉字
        or '\u3040' <= c <= '\u30ff'    # 日文假名
        or '\uac00' <= c <= '\ud7af'    # 韩文音节
    )
    return cjk_count > len(text) * 0.3


def detect_language(text: str) -> str:
    """识别单个文本的语言标签，返回 "zh" / "ja" / "en" / "other"。"""
    s = (text or "").strip()
    if not s:
        return "other"
    # 含假名优先判为日文（假名是日文独有）
    if _has_jp_kana(s):
        return "ja"
    if _is_cjk_dominant(s):
        return "zh"
    return "en"


def classify_aliases(keywords: List[str]) -> List[Tuple[str, str]]:
    """批量识别别名语言，保序返回 [(别名, 语言标签), ...]。

    会去除空白项并按原始顺序去重（首次出现为准）。
    """
    result: List[Tuple[str, str]] = []
    seen = set()
    for kw in keywords:
        s = (kw or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        result.append((s, detect_language(s)))
    return result
