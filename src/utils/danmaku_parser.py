"""
弹幕 XML 解析工具

why：本模块原位于 src/api/dandan/ 下，但它是零业务依赖的纯解析工具
（仅依赖 src.utils.clean_xml_string）。放在 api 层会导致 src.db.crud
需要它时触发 src.api 包初始化，进而形成
    db.crud.episode -> src.api -> api.control -> src.db(未初始化完)
的循环导入，迫使调用方全部改用函数内延迟导入。

迁到 src/utils/ 后，db/crud、api/ui、api/dandan 均可直接在文件顶部导入，
符合 PEP 8「导入置于文件顶部」的要求。
"""
import logging
import re
from typing import Dict, List
from xml.etree import ElementTree

from src.utils.common import clean_xml_string

logger = logging.getLogger(__name__)


def _normalize_p_attr_to_internal_format(p_attr: str, source_tag: str = "[xml]") -> str:
    """
    将各种格式的 p 属性标准化为内部存储格式。

    内部存储格式: "时间,模式,字体大小,颜色,[来源]" (4个核心参数 + 来源标签)

    支持的输入格式:
    1. Bilibili XML: "时间,模式,字号,颜色,时间戳,弹幕池,用户ID,弹幕ID" (8个参数)
    2. Dandanplay API: "时间,模式,颜色,用户ID,..." (第3个是颜色，没有字号)
    3. 已标准化格式: "时间,模式,字号,颜色,[来源]" (4个核心参数 + 来源标签)
    """
    if not p_attr:
        return f"0,1,25,16777215,{source_tag}"

    parts = p_attr.split(',')

    # 检查是否已有来源标签
    existing_source_tag = None
    core_parts_end = len(parts)
    for i, part in enumerate(parts):
        if '[' in part and ']' in part:
            existing_source_tag = part
            core_parts_end = i
            break

    core_parts = parts[:core_parts_end]

    # 根据核心参数数量判断格式并标准化
    if len(core_parts) >= 4:
        time_val = core_parts[0]
        mode_val = core_parts[1]
        fontsize_candidate = core_parts[2].strip()
        color_or_userid = core_parts[3].strip()

        # 区分两种4参数格式：
        #   - 标准/Bilibili格式: 时间,模式,字号,颜色 (第3个是字号，通常 < 100)
        #   - Dandanplay API格式: 时间,模式,颜色,用户ID (第3个是颜色，通常 > 1000；第4个是用户ID哈希)
        is_dandanplay_format = False

        if fontsize_candidate.isdigit() and int(fontsize_candidate) > 1000:
            # 第3个参数 > 1000，不可能是字号，实际是颜色值 → dandanplay格式
            is_dandanplay_format = True
        elif not color_or_userid.isdigit():
            # 第4个参数包含非数字字符（如十六进制哈希 0d3ed9dd）→ 不是颜色值 → dandanplay格式
            is_dandanplay_format = True
        elif color_or_userid.isdigit() and int(color_or_userid) > 16777215:
            # 第4个参数超过RGB最大值(16777215)，不可能是颜色 → dandanplay格式
            is_dandanplay_format = True

        if is_dandanplay_format:
            # Dandanplay格式: 时间,模式,颜色,用户ID → 丢弃用户ID，插入默认字号
            fontsize_val = '25'
            color_val = fontsize_candidate  # 第3个参数实际是颜色
        else:
            # 标准格式: 时间,模式,字号,颜色
            fontsize_val = fontsize_candidate if fontsize_candidate.isdigit() else '25'
            color_val = color_or_userid

        final_source = existing_source_tag if existing_source_tag else source_tag
        return f"{time_val},{mode_val},{fontsize_val},{color_val},{final_source}"

    elif len(core_parts) == 3:
        # Dandanplay API 格式: 时间,模式,颜色 (没有字号)
        # 需要插入默认字号 25
        time_val = core_parts[0]
        mode_val = core_parts[1]
        color_val = core_parts[2]

        final_source = existing_source_tag if existing_source_tag else source_tag
        return f"{time_val},{mode_val},25,{color_val},{final_source}"

    else:
        # 参数不足，补全默认值
        while len(core_parts) < 4:
            if len(core_parts) == 0:
                core_parts.append('0')       # 时间
            elif len(core_parts) == 1:
                core_parts.append('1')       # 模式
            elif len(core_parts) == 2:
                core_parts.append('25')      # 字号
            elif len(core_parts) == 3:
                core_parts.append('16777215')  # 颜色（白色）

        final_source = existing_source_tag if existing_source_tag else source_tag
        return f"{core_parts[0]},{core_parts[1]},{core_parts[2]},{core_parts[3]},{final_source}"


def parse_dandan_xml_to_comments(xml_content: str, source_tag: str = "[xml]") -> List[Dict]:
    """
    解析 XML 弹幕内容，并标准化为内部存储格式。

    支持的 XML 格式:
    1. Bilibili XML: p="时间,模式,字号,颜色,时间戳,弹幕池,用户ID,弹幕ID"
    2. Dandanplay XML: p="时间,模式,颜色,用户ID,..."
    3. 其他标准 XML 格式

    输出的内部存储格式: p="时间,模式,字号,颜色,[来源]"
    """
    comments = []
    try:
        # 关键修复：在解析之前，先清理XML内容，移除所有非法字符。
        # 这可以防止因弹幕内容包含无效控制字符（如退格符）而导致的解析失败。
        xml_content = clean_xml_string(xml_content)
        # Remove any XML declaration that might cause issues
        xml_content = re.sub(r'<\?xml.*?\?>', '', xml_content, count=1).strip()
        root = ElementTree.fromstring(xml_content)
        for comment_node in root.findall('d'):
            try:
                p_attr = comment_node.attrib.get('p', '0,1,25,16777215')
                text = comment_node.text or ''

                # 标准化 p 属性为内部存储格式
                normalized_p = _normalize_p_attr_to_internal_format(p_attr, source_tag)

                # 解析时间用于排序
                parts = p_attr.split(',')
                time_sec = float(parts[0]) if parts else 0.0

                # 尝试获取弹幕ID (bilibili格式的第8个参数)
                comment_id = 0
                if len(parts) > 7:
                    try:
                        comment_id = int(parts[7])
                    except ValueError:
                        pass

                comment_dict = {
                    'p': normalized_p,
                    'm': text,
                    't': time_sec,
                    'cid': comment_id
                }
                comments.append(comment_dict)
            except (IndexError, ValueError) as e:
                logger.warning(f"Skipping malformed comment node: {ElementTree.tostring(comment_node, 'unicode')}. Error: {e}")
                continue
    except ElementTree.ParseError as e:
        logger.error(f"Failed to parse XML content: {e}")
        # Return empty list if XML is invalid
        return []

    return comments
