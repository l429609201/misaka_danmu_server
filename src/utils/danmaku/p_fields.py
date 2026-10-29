"""弹幕 p 属性的九段编码及旧格式兼容，来源标签占用第七段。"""

import re
from typing import Any, Optional

_SOURCE_TAG = re.compile(r"\[([^\[\],]+)\]")
_DEFAULT_FIELDS = ('0', '1', '25', '16777215', '0', '0', '0', '0', '0')


def comment_source(p_attr: str, fallback: Optional[str] = None) -> Optional[str]:
    """提取新第七段或旧尾部来源标签，不把用户 HASH 当作来源。"""
    match = _SOURCE_TAG.search(p_attr or '')
    if match:
        return match.group(1)
    if fallback:
        return fallback.strip('[]')
    return None


def _integer(value: Any, default: str, minimum: int = 0, maximum: Optional[int] = None) -> str:
    """无效数值回退默认值，避免旧数据产生不合法的九段元信息。"""
    try:
        number = int(str(value).strip())
    except (ValueError, TypeError):
        return default
    if number < minimum or (maximum is not None and number > maximum):
        return default
    return str(number)


def normalize_p_attr(
    p_attr: str,
    provider_name: Optional[str] = None,
    input_format: str = 'auto',
    comment_id: Any = None,
) -> str:
    """转为九段 p，兼容旧三/四段、尾部来源标签和 B 站八/九段。

    第七段来源按项目约定替代用户 HASH。未知四段布局保持历史判断；
    已知弹弹源须指定 dandanplay，避免深色值与数字 UID 的歧义。
    """
    raw = p_attr or ''
    # 修复旧生成器空属性时颜色与来源粘连的存量格式。
    raw = re.sub(r'(?<=\d)(?=\[)', ',', raw)
    parts = [part.strip() for part in raw.split(',')] if raw else []
    source = comment_source(raw, provider_name)
    fields = list(_DEFAULT_FIELDS)
    # 新标签在第七段，不得按旧尾部标签切断后面的 ID 和权重。
    tag_index = next((index for index, part in enumerate(parts) if _SOURCE_TAG.search(part)), None)
    canonical = len(parts) >= 8 and (tag_index is None or tag_index >= 6)
    if canonical:
        for index, value in enumerate(parts[:9]):
            if value:
                fields[index] = value
    else:
        core = []
        for part in parts:
            if _SOURCE_TAG.search(part):
                break
            core.append(part)
        legacy_tagged = len(core) == 4 and len(parts) > 4 and _SOURCE_TAG.search(parts[4]) is not None
        dandan = len(core) == 3 or (input_format == 'dandanplay' and len(core) >= 3 and not legacy_tagged)
        if input_format in ('auto', 'dandanplay') and len(core) >= 4:
            third, fourth = core[2], core[3]
            dandan = dandan or ((third.isdigit() and int(third) > 1000)
                                or not fourth.isdigit()
                                or (fourth.isdigit() and int(fourth) > 16777215))
        if dandan:
            fields[:4] = [core[0], core[1], '25', core[2]]
            if len(core) > 3:
                fields[6] = core[3] or '0'
            if len(core) > 4:
                fields[4] = core[4] or '0'
        else:
            for index, value in enumerate(core[:9]):
                if value:
                    fields[index] = value
    fields[1] = _integer(fields[1], '1', minimum=1, maximum=9)
    fields[2] = _integer(fields[2], '25', minimum=1)
    fields[3] = _integer(fields[3], '16777215', maximum=16777215)
    for index in (4, 5, 8):
        fields[index] = _integer(fields[index], '0')
    if fields[7] == '0' and comment_id is not None:
        fields[7] = str(comment_id).strip() or '0'
    # 第三方源 ID 可能是 UUID；保留原值，管理接口另按数值能力降级 cid。
    if not fields[7] or any(char in fields[7] for char in ',\r\n'):
        raise ValueError('弹幕标识包含非法分隔符')
    if source:
        # 来源不参与逗号分段；非法分隔符拒绝写入，而非偷偷改变字段数量。
        if any(char in source for char in ',[]\r\n'):
            raise ValueError('弹幕来源标签包含非法分隔符')
        fields[6] = f'[{source}]'
    return ','.join(fields)
