"""弹幕 XML 解析工具，兼容历史参数并输出统一九段存储格式。"""
import logging
import re
from typing import Dict, List
from xml.etree import ElementTree

from src.utils.danmaku.p_fields import normalize_p_attr
from src.utils.misc.common import clean_xml_string

logger = logging.getLogger(__name__)


def _normalize_p_attr_to_internal_format(
    p_attr: str, source_tag: str = "[xml]", input_format: str = "auto",
) -> str:
    """兼容原调用入口，将来源放在第七段并保留时间戳、弹幕 ID 和权重。"""
    return normalize_p_attr(
        p_attr, provider_name=source_tag, input_format=input_format,
    )


def parse_dandan_xml_to_comments(
    xml_content: str, source_tag: str = "[xml]", input_format: str = "auto",
) -> List[Dict]:
    """读取历史三、四、五、八、九段参数，统一为九段弹幕字典。

    自动模式使用 XML 头识别弹弹格式；无法区分的四段参数可由调用方
    显式传入 input_format="dandanplay"，避免低颜色值被当成字号。
    已有来源标签优先于 XML 头和默认来源，原始用户 HASH 不作为来源。
    """
    comments = []
    try:
        xml_content = clean_xml_string(xml_content)
        xml_content = re.sub(r'<\?xml.*?\?>', '', xml_content, count=1).strip()
        root = ElementTree.fromstring(xml_content)
        provider = (root.findtext('sourceprovider') or '').strip()
        chat_server = (root.findtext('chatserver') or '').strip().lower()
        effective_format = input_format
        if input_format == "auto" and (
            provider.lower() == "dandanplay" or "dandanplay" in chat_server
        ):
            effective_format = "dandanplay"
        fallback_source = provider or source_tag
        for comment_node in root.findall('d'):
            try:
                p_attr = comment_node.attrib.get('p', '0,1,25,16777215')
                normalized_p = _normalize_p_attr_to_internal_format(
                    p_attr, fallback_source, effective_format,
                )
                parts = normalized_p.split(',')
                time_sec = float(parts[0])
                comment_id = 0
                try:
                    comment_id = int(parts[7])
                except ValueError:
                    pass
                comments.append({
                    'p': normalized_p,
                    'm': comment_node.text or '',
                    't': time_sec,
                    'cid': comment_id,
                })
            except (IndexError, ValueError) as exc:
                logger.warning(
                    "跳过格式错误的弹幕节点: %s，错误: %s",
                    ElementTree.tostring(comment_node, 'unicode'), exc,
                )
    except ElementTree.ParseError as exc:
        logger.error("XML 弹幕解析失败: %s", exc)
        return []
    return comments
