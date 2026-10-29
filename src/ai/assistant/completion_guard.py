"""
回复完成度护栏：识别“说了要做却没做”的结尾，促使模型继续执行而不是就此结束本轮。

参考 MoviePilot app/agent/guardrails/completion.py 的尾部动作规则，只保留中英文的尾部意图检测，
不引入其余 LangChain 依赖。
"""

import re

# 回复结尾的“接下来我会/让我现在……”一类未执行动作
_TRAILING_ACTION = re.compile(
    r"(?:\blet me now\b|\bi(?:['\u2019])?ll now\b|\bi will now\b"
    r"|\bnow i(?:['\u2019]ll| will)\b|\bnext[,:] i\b"
    # 中文要求“接下来/现在 + 我会/将/要”这类明确的将来时，避免“如需调整请让我知道”之类误判
    r"|(?:接下来|现在)[，,]?我(?:会|将|要)|我(?:现在|接下来)(?:会|将|要)|让我(?:现在|接下来))"
    r"[^.!?\n。！？]{0,100}[.:：。\u2026]?\s*$",
    re.IGNORECASE,
)

# 最多催促次数，防止模型反复只给“我接下来会……”造成空转
MAX_CONTINUATION_NUDGES = 2

CONTINUATION_NUDGE = (
    "[系统提示：你上一条回复以“将要执行的动作”结尾，但没有发起任何工具调用。"
    "请现在直接调用所需工具完成任务，完成后再给出最终答复；"
    "如果任务其实已经完成或无法继续，请直接给出最终结论，不要再描述将要做什么。]"
)


def trailing_continue_intent(text: str) -> bool:
    """判断回复是否以未执行的后续动作结尾。

    只检查 400 字以内回复的最后 160 个字符，避免把长答案中途的步骤描述误判为结束意图。

    Args:
        text: 模型本轮不带工具调用的回复正文。

    Returns:
        bool: 结尾是“将要执行”的动作时返回 True。
    """
    text = (text or "").strip()
    return bool(text and len(text) <= 400 and _TRAILING_ACTION.search(text[-160:]))
