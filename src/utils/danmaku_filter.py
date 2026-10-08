"""
弹幕过滤模块
提供弹幕黑名单过滤功能
"""

import logging
import re
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


def apply_blacklist_filter(comments_data: List[Dict[str, Any]], patterns_text: str) -> List[Dict[str, Any]]:
    """
    应用黑名单正则表达式过滤弹幕。

    支持两种格式：
    1. 单行格式：用 | 分隔的正则表达式（如：广告|推广|666）
    2. 多行格式：每行一个正则表达式

    Args:
        comments_data: 弹幕数据列表，每个元素是包含 'p' 和 'm' 字段的字典
                      - 'p': 弹幕参数（时间,模式,颜色等）
                      - 'm': 弹幕内容文本
        patterns_text: 黑名单正则表达式文本
                      - 单行格式：用 | 分隔多个规则
                      - 多行格式：每行一个正则表达式，以 # 开头的行为注释

    Returns:
        过滤后的弹幕列表，移除了匹配黑名单的弹幕

    Examples:
        >>> comments = [
        ...     {'p': '10.5,1,25,16777215', 'm': '这是一条正常弹幕'},
        ...     {'p': '20.0,1,25,16777215', 'm': '广告：点击领取'},
        ...     {'p': '30.5,1,25,16777215', 'm': '666'},
        ... ]
        >>> # 单行格式
        >>> patterns = "广告|领取|666"
        >>> filtered = apply_blacklist_filter(comments, patterns)
        >>> len(filtered)
        1
    """
    if not patterns_text or not patterns_text.strip():
        return comments_data

    patterns_text = patterns_text.strip()

    # 判断是单行格式还是多行格式
    # 如果包含 | 且没有换行符，或者只有一行，则视为单行格式
    is_single_line = '\n' not in patterns_text or (patterns_text.count('\n') == 0 and '|' in patterns_text)

    try:
        if is_single_line:
            # 单行格式：直接编译为一个大正则
            pattern = re.compile(patterns_text, re.IGNORECASE)
            patterns = [pattern]
            logger.debug(f"使用单行黑名单规则: {len(patterns_text)} 个字符")
        else:
            # 多行格式：逐行解析
            patterns = []
            for line in patterns_text.split('\n'):
                line = line.strip()
                # 忽略空行和注释行
                if not line or line.startswith('#'):
                    continue

                try:
                    # 编译正则表达式，忽略大小写
                    patterns.append(re.compile(line, re.IGNORECASE))
                except re.error as e:
                    logger.warning(f"无效的黑名单正则表达式 '{line}': {e}")

            logger.debug(f"使用多行黑名单规则: {len(patterns)} 条")
    except re.error as e:
        logger.error(f"编译黑名单正则表达式失败: {e}")
        return comments_data

    if not patterns:
        logger.debug("黑名单规则为空，跳过过滤")
        return comments_data

    # 过滤弹幕
    filtered_comments = []
    blocked_count = 0

    for comment in comments_data:
        message = comment.get('m', '')
        is_blocked = False

        # 检查是否匹配任一黑名单规则
        for pattern in patterns:
            if pattern.search(message):
                is_blocked = True
                blocked_count += 1
                logger.debug(f"弹幕已拦截: '{message}'")
                break

        if not is_blocked:
            filtered_comments.append(comment)

    if blocked_count > 0:
        logger.info(f"黑名单过滤完成: 拦截 {blocked_count} 条弹幕，保留 {len(filtered_comments)} 条")
    else:
        logger.debug(f"黑名单过滤完成: 未拦截任何弹幕 (共 {len(comments_data)} 条)")

    return filtered_comments


def validate_regex_pattern(pattern: str) -> tuple[bool, str]:
    """
    验证正则表达式是否有效。
    
    Args:
        pattern: 要验证的正则表达式字符串
        
    Returns:
        (是否有效, 错误信息)
        - 如果有效: (True, "")
        - 如果无效: (False, "错误描述")
        
    Examples:
        >>> validate_regex_pattern("广告|推广")
        (True, '')
        >>> validate_regex_pattern("[invalid")
        (False, 'unterminated character set at position 0')
    """
    try:
        re.compile(pattern, re.IGNORECASE)
        return True, ""
    except re.error as e:
        return False, str(e)


def parse_blacklist_patterns(patterns_text: str) -> List[str]:
    """
    解析黑名单文本，返回有效的正则表达式列表。
    
    Args:
        patterns_text: 黑名单文本，每行一个正则表达式
        
    Returns:
        有效的正则表达式列表（去除注释和空行）
        
    Examples:
        >>> text = '''
        ... # 这是注释
        ... 广告
        ... 
        ... 推广|营销
        ... '''
        >>> parse_blacklist_patterns(text)
        ['广告', '推广|营销']
    """
    if not patterns_text:
        return []
    
    patterns = []
    for line in patterns_text.strip().split('\n'):
        line = line.strip()
        if line and not line.startswith('#'):
            patterns.append(line)
    
    return patterns

