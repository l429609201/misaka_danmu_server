"""统一生成九段 p 属性的弹幕 XML，兼容旧格式输入。"""

import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from src.utils.danmaku.p_fields import normalize_p_attr


def generate_xml_from_comments(
    comments: List[Dict[str, Any]],
    episode_id: int,
    provider_name: Optional[str] = 'misaka',
    chat_server: Optional[str] = 'danmaku.misaka.org',
    source_tag: Optional[str] = None,
) -> str:
    """生成九段 XML：时间、模式、字号、颜色、发送时间、池、来源、ID、权重。

    第七段按项目约定保存来源标签。source_tag=None 仅保留输入来源；
    空串表示缺来源时使用 provider_name；非空标签显式替换原来源。
    """
    root = ET.Element('i')
    ET.SubElement(root, 'chatserver').text = chat_server
    ET.SubElement(root, 'chatid').text = str(episode_id)
    ET.SubElement(root, 'mission').text = '0'
    ET.SubElement(root, 'maxlimit').text = '2000'
    ET.SubElement(root, 'source').text = 'k-v'
    ET.SubElement(root, 'sourceprovider').text = provider_name
    ET.SubElement(root, 'datasize').text = str(len(comments))
    input_format = 'dandanplay' if provider_name == 'dandanplay' else 'auto'
    fallback = provider_name if source_tag == '' else None
    for comment in comments:
        p_attr = normalize_p_attr(
            str(comment.get('p') or ''), fallback,
            input_format=input_format, comment_id=comment.get('cid'),
        )
        if source_tag:
            # 别名覆盖已有标签，但不能破坏九段结构。
            fields = p_attr.split(',')
            if any(char in source_tag for char in ',[]\r\n'):
                raise ValueError('弹幕来源别名包含非法分隔符')
            fields[6] = f'[{source_tag}]'
            p_attr = ','.join(fields)
        d = ET.SubElement(root, 'd', p=p_attr)
        d.text = comment.get('m', '')
    return ET.tostring(root, encoding='unicode', xml_declaration=True)
