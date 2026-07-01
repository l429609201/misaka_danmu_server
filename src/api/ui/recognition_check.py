"""
识别词规则冲突检测器 (12)
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db_session, crud
from src.api.dependencies import get_config_manager
from src.db import ConfigManager
from src.services.title_recognition import TitleRecognitionManager

logger = logging.getLogger(__name__)
router = APIRouter()


class RuleConflictItem(BaseModel):
    ruleIndex: int = 0
    ruleContent: str = ""
    issueType: str = ""  # empty/duplicate/too_short/overlap/unreachable
    severity: str = "info"
    detail: str = ""
    relatedRules: List[int] = []


class RuleTestResult(BaseModel):
    originalTitle: str = ""
    matchedRules: List[Dict[str, Any]] = []
    transformedTitle: str = ""
    seasonOffset: Optional[str] = None


@router.get("/recognition-check/conflicts", summary="识别词规则冲突检测")
async def check_rule_conflicts(
    session: AsyncSession = Depends(get_db_session),
):
    """扫描识别词规则，检测空规则、重复、过短关键词、潜在冲突"""
    recognition = await crud.get_title_recognition(session)
    if not recognition or not recognition.content:
        return []

    lines = recognition.content.strip().split("\n")
    results = []
    seen_rules = {}
    
    for i, line in enumerate(lines):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # 空规则
        if len(line) < 2:
            results.append(RuleConflictItem(
                ruleIndex=i, ruleContent=line, issueType="empty",
                severity="warning", detail="规则内容过短或为空"
            ))
            continue

        # 重复检测
        if line in seen_rules:
            results.append(RuleConflictItem(
                ruleIndex=i, ruleContent=line, issueType="duplicate",
                severity="warning", detail=f"与第{seen_rules[line]+1}行重复",
                relatedRules=[seen_rules[line]]
            ))
        seen_rules[line] = i

        # 过短关键词检测（替换类规则 A||B 中A太短）
        if "||" in line:
            parts = line.split("||")
            source = parts[0].strip()
            if 0 < len(source) <= 1:
                results.append(RuleConflictItem(
                    ruleIndex=i, ruleContent=line, issueType="too_short",
                    severity="warning", detail=f"替换源关键词'{source}'过短，可能误匹配"
                ))

        # 结构化规则检测
        if line.startswith("{") and "source=" in line:
            try:
                inner = line.strip("{}")
                if "season_offset=" in inner:
                    offset_part = inner.split("season_offset=")[1].split(";")[0].split("}")[0]
                    if not offset_part.strip():
                        results.append(RuleConflictItem(
                            ruleIndex=i, ruleContent=line, issueType="empty",
                            severity="warning", detail="结构化规则的season_offset为空"
                        ))
            except Exception:
                results.append(RuleConflictItem(
                    ruleIndex=i, ruleContent=line, issueType="parse_error",
                    severity="error", detail="结构化规则解析失败"
                ))

    return results


@router.post("/recognition-check/test", summary="识别词规则测试")
async def test_recognition_rule(
    body: dict,
    session: AsyncSession = Depends(get_db_session),
):
    """输入样例标题，展示命中的规则链路"""
    title = body.get("title", "")
    if not title:
        return {"matchedRules": [], "transformedTitle": title}

    recognition = await crud.get_title_recognition(session)
    if not recognition or not recognition.content:
        return {"matchedRules": [], "transformedTitle": title}

    lines = recognition.content.strip().split("\n")
    matched = []
    current = title

    for i, line in enumerate(lines):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "||" in line:
            parts = line.split("||")
            source = parts[0].strip()
            target = parts[1].strip() if len(parts) > 1 else ""
            if source and source in current:
                old = current
                current = current.replace(source, target)
                matched.append({"ruleIndex": i, "rule": line, "before": old, "after": current})

    return RuleTestResult(
        originalTitle=title,
        matchedRules=matched,
        transformedTitle=current,
    )
