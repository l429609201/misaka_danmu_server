"""自定义弹幕格式转换，纯解析逻辑不依赖任务层。"""
import xml.etree.ElementTree as ET
from typing import Dict, List

from src.utils.danmaku.p_fields import normalize_p_attr


def parse_xml_content(xml_content: str) -> List[Dict[str, str]]:
    """完整解析 XML 为九段参数，保留已有来源及元数据，拒绝截断内容。"""
    try:
        # 先完成整份 XML 校验，避免部分结果覆盖已有弹幕。
        root = ET.fromstring(xml_content)
    except ET.ParseError as exc:
        raise ValueError(f"XML 弹幕内容不完整或格式错误: {exc}") from exc
    provider = (root.findtext('sourceprovider') or '').strip().lower()
    chat_server = (root.findtext('chatserver') or '').strip().lower()
    input_format = "dandanplay" if (
        provider == "dandanplay" or "dandanplay" in chat_server
    ) else "auto"
    comments = []
    for elem in root.iter('d'):
        p_attr = elem.get('p')
        text = elem.text
        if p_attr is not None and text is not None:
            comments.append({
                'p': normalize_p_attr(
                    p_attr, provider_name="custom_xml", input_format=input_format,
                ),
                'm': text,
            })
    return comments


def convert_text_danmaku_to_xml(text_content: str) -> str:
    """将逐行参数与正文转换为九段 XML，兼容旧四段字号布局并保留来源。"""
    root = ET.Element('i')
    ET.SubElement(root, 'chatserver').text = 'danmu'
    ET.SubElement(root, 'chatid').text = '0'
    ET.SubElement(root, 'mission').text = '0'
    ET.SubElement(root, 'source').text = 'misaka'
    maxlimit = ET.SubElement(root, 'maxlimit')
    count = 0
    for line in text_content.strip().splitlines():
        if '|' not in line:
            continue
        params_str, text = line.split('|', 1)
        if len(params_str.split(',')) < 3:
            continue
        p_attr = normalize_p_attr(params_str, provider_name="custom_text")
        ET.SubElement(root, 'd', p=p_attr).text = text.strip()
        count += 1
    maxlimit.text = str(count)
    return ET.tostring(root, encoding='unicode', xml_declaration=True)
