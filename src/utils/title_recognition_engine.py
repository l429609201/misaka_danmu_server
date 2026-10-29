"""无状态标题规则解析、匹配和偏移计算，不访问数据库或业务服务。"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from src.utils.arithmetic import evaluate_arithmetic

logger = logging.getLogger(__name__)

class TitleRecognitionRule:
    """识别词规则类"""

    def __init__(self, rule_type: str, stage: str, **kwargs: Any) -> None:
        self.rule_type = rule_type  # 'block', 'replace', 'offset', 'complex', 'metadata_replace', 'season_offset'
        self.stage = stage  # 'preprocess' (搜索预处理) 或 'postprocess' (入库后处理)
        self.data = kwargs

class TitleRecognitionEngine:
    """标题识别规则纯计算引擎。"""

    def _parse_recognition_content(self, content: str) -> Tuple[List[TitleRecognitionRule], List[str]]:
        """
        解析识别词配置内容 - 参考MoviePilot格式

        支持的格式：
        1. 屏蔽词: 屏蔽词
        2. 简单替换: 被替换词 => 替换词
        3. 集数偏移: 前定位词 <> 后定位词 >> 集偏移量
        4. 复合格式: 被替换词 => 替换词 && 前定位词 <> 后定位词 >> 集偏移量
        5. 季度偏移: 被替换词 => {[source=源名称;season_offset=偏移规则]}

        Args:
            content: 识别词配置文本内容

        Returns:
            Tuple[List[TitleRecognitionRule], List[str]]: 解析后的识别词规则列表和警告信息列表
        """
        rules = []
        warnings = []

        if not content:
            return rules, warnings

        lines = content.split('\n')

        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            try:
                rule = self._parse_single_rule(line, line_num)
                if rule:
                    rules.append(rule)
            except Exception as e:
                warning_msg = f"第{line_num}行解析失败: {line} (错误: {e})"
                warnings.append(warning_msg)
                logger.warning(f"识别词配置{warning_msg}")
                continue

        return rules, warnings

    def _parse_single_rule(self, line: str, line_num: int) -> Optional[TitleRecognitionRule]:
        """
        解析单个识别词规则

        Args:
            line: 规则行内容
            line_num: 行号

        Returns:
            TitleRecognitionRule: 解析后的规则对象，解析失败返回None
        """
        # 检查是否是屏蔽词格式
        if line.startswith('BLOCK:'):
            return self._parse_block_rule(line, line_num)

        # 检查是否包含复合格式标识符
        has_replace = ' => ' in line
        has_offset = ' <> ' in line and ' >> ' in line
        has_complex = has_replace and ' && ' in line

        if has_complex:
            # 复合格式: 被替换词 => 替换词 && 前定位词 <> 后定位词 >> 集偏移量
            return self._parse_complex_rule(line, line_num)
        elif has_replace:
            # 简单替换: 被替换词 => 替换词
            return self._parse_replace_rule(line, line_num)
        elif has_offset:
            # 集数偏移: 前定位词 <> 后定位词 >> 集偏移量
            return self._parse_offset_rule(line, line_num)
        else:
            # 如果没有特殊格式，跳过（避免误解析）
            logger.warning(f"识别词配置第{line_num}行格式不明确，跳过: {line}")
            return None

    def _parse_block_rule(self, line: str, line_num: int) -> Optional[TitleRecognitionRule]:
        """解析屏蔽词规则"""
        if line.startswith('BLOCK:'):
            block_word = line[6:].strip()  # 移除 'BLOCK:' 前缀
        else:
            block_word = line.strip()

        if not block_word:
            logger.warning(f"识别词配置第{line_num}行屏蔽词为空，跳过: {line}")
            return None

        logger.debug(f"解析屏蔽词规则: {block_word}")
        return TitleRecognitionRule('block', 'preprocess', word=block_word)

    def _parse_replace_rule(self, line: str, line_num: int) -> Optional[TitleRecognitionRule]:
        """解析简单替换规则"""
        if ' => ' not in line:
            return None

        parts = line.split(' => ', 1)
        if len(parts) != 2:
            logger.warning(f"识别词配置第{line_num}行替换格式错误，跳过: {line}")
            return None

        source = parts[0].strip()
        target = parts[1].strip()

        if not source or not target:
            logger.warning(f"识别词配置第{line_num}行替换词为空，跳过: {line}")
            return None

        # 检查是否是搜索预处理格式 {<search_season=8>}
        if target.startswith('{<') and target.endswith('>}'):
            search_info = self._parse_search_target(target)
            if search_info:
                logger.debug(f"解析搜索预处理规则: {source} => {target}")
                search_copy = search_info.copy()
                search_copy['source'] = source  # 这是匹配的文本
                return TitleRecognitionRule('search_season', 'preprocess', **search_copy)

        # 检查是否是特殊格式 {[tmdbid/doubanid=xxx;type=movie/tv;s=xxx;e=xxx]} 或季度偏移格式
        elif target.startswith('{[') and target.endswith(']}'):
            metadata_info = self._parse_metadata_target(target)
            if metadata_info:
                # 检查是否包含部分集数偏移信息（ep_range + ep_offset）
                if 'ep_range' in metadata_info and 'ep_offset' in metadata_info:
                    logger.debug(f"解析部分集数偏移规则: {source} => {target}")
                    metadata_copy = metadata_info.copy()
                    if 'source' in metadata_copy:
                        metadata_copy['source_restriction'] = metadata_copy.pop('source')
                    metadata_copy['source'] = source
                    return TitleRecognitionRule('partial_offset', 'postprocess', **metadata_copy)
                # 检查是否包含季度偏移信息
                elif 'season_offset' in metadata_info:
                    logger.debug(f"解析季度偏移规则: {source} => {target}")
                    # 重命名source参数为source_restriction以避免冲突
                    metadata_copy = metadata_info.copy()
                    if 'source' in metadata_copy:
                        metadata_copy['source_restriction'] = metadata_copy.pop('source')
                    metadata_copy['source'] = source  # 这是匹配的文本
                    return TitleRecognitionRule('season_offset', 'postprocess', **metadata_copy)
                else:
                    logger.debug(f"解析元数据替换规则: {source} => {target}")
                    # 重命名source参数为source_restriction以避免冲突
                    metadata_copy = metadata_info.copy()
                    if 'source' in metadata_copy:
                        metadata_copy['source_restriction'] = metadata_copy.pop('source')
                    metadata_copy['source'] = source  # 这是匹配的文本
                    return TitleRecognitionRule('metadata_replace', 'postprocess', **metadata_copy)

        logger.debug(f"解析简单替换规则: {source} => {target}")
        return TitleRecognitionRule('replace', 'preprocess', source=source, target=target)

    def _parse_offset_rule(self, line: str, line_num: int) -> Optional[TitleRecognitionRule]:
        """解析集数偏移规则"""
        if ' <> ' not in line or ' >> ' not in line:
            return None

        # 分割: 前定位词 <> 后定位词 >> 集偏移量
        parts = line.split(' >> ', 1)
        if len(parts) != 2:
            logger.warning(f"识别词配置第{line_num}行偏移格式错误，跳过: {line}")
            return None

        locator_part = parts[0].strip()
        offset_part = parts[1].strip()

        if ' <> ' not in locator_part:
            logger.warning(f"识别词配置第{line_num}行定位词格式错误，跳过: {line}")
            return None

        locator_parts = locator_part.split(' <> ', 1)
        before_locator = locator_parts[0].strip()
        after_locator = locator_parts[1].strip()

        logger.debug(f"解析集数偏移规则: {before_locator} <> {after_locator} >> {offset_part}")
        return TitleRecognitionRule('offset', 'preprocess',
                                   before_locator=before_locator,
                                   after_locator=after_locator,
                                   offset=offset_part)

    def _parse_complex_rule(self, line: str, line_num: int) -> Optional[TitleRecognitionRule]:
        """解析复合规则"""
        if ' && ' not in line:
            return None

        # 分割: 被替换词 => 替换词 && 前定位词 <> 后定位词 >> 集偏移量
        parts = line.split(' && ', 1)
        if len(parts) != 2:
            logger.warning(f"识别词配置第{line_num}行复合格式错误，跳过: {line}")
            return None

        replace_part = parts[0].strip()
        offset_part = parts[1].strip()

        # 解析替换部分
        replace_rule = self._parse_replace_rule(replace_part, line_num)
        if not replace_rule:
            return None

        # 解析偏移部分
        offset_rule = self._parse_offset_rule(offset_part, line_num)
        if not offset_rule:
            return None

        logger.debug(f"解析复合规则: {line}")
        return TitleRecognitionRule('complex', 'preprocess',
                                   source=replace_rule.data['source'],
                                   target=replace_rule.data['target'],
                                   before_locator=offset_rule.data['before_locator'],
                                   after_locator=offset_rule.data['after_locator'],
                                   offset=offset_rule.data['offset'])

    def _parse_metadata_target(self, target: str) -> Optional[Dict[str, Any]]:
        """
        解析元数据目标格式: {[tmdbid/doubanid=xxx;type=movie/tv;s=xxx;e=xxx]}

        Args:
            target: 目标字符串

        Returns:
            Dict: 解析后的元数据信息
        """
        if not target.startswith('{[') or not target.endswith(']}'):
            return None

        content = target[2:-2]  # 移除 {[ 和 ]}
        metadata = {}

        for part in content.split(';'):
            if '=' not in part:
                continue
            key, value = part.split('=', 1)
            key = key.strip()
            value = value.strip()

            if key in ['tmdbid', 'doubanid']:
                try:
                    metadata[key] = int(value)
                except ValueError:
                    continue
            elif key == 'ep_range':
                # 解析集数范围，格式：1-12
                range_parts = value.split('-', 1)
                if len(range_parts) == 2:
                    try:
                        ep_start = int(range_parts[0].strip())
                        ep_end_raw = range_parts[1].strip()
                        ep_end = None if ep_end_raw == '*' else int(ep_end_raw)
                        metadata['ep_range'] = (ep_start, ep_end)
                    except ValueError:
                        continue
            elif key == 'ep_offset':
                # 集数偏移量，如 +12、-12、EP+5 等，保留字符串交由计算方法处理
                metadata['ep_offset'] = value
            elif key in ['type', 's', 'e', 'source', 'season_offset', 'title', 'search_season']:
                if key == 'search_season':
                    try:
                        metadata[key] = int(value)
                    except ValueError:
                        continue
                else:
                    metadata[key] = value

        # 如果没有指定source，默认为'all'
        if 'source' not in metadata:
            metadata['source'] = 'all'

        return metadata if metadata else None

    def _parse_search_target(self, target: str) -> Optional[Dict[str, Any]]:
        """
        解析搜索预处理目标格式: {<search_season=8>}

        Args:
            target: 目标字符串

        Returns:
            Dict: 解析后的搜索信息
        """
        if not target.startswith('{<') or not target.endswith('>}'):
            return None

        content = target[2:-2]  # 移除 {< 和 >}
        search_info = {}

        for part in content.split(';'):
            if '=' not in part:
                continue
            key, value = part.split('=', 1)
            key = key.strip()
            value = value.strip()

            if key == 'search_season':
                try:
                    search_info[key] = int(value)
                except ValueError:
                    continue

        return search_info if search_info else None

    def _parse_source_season_from_offset(self, season_offset: Optional[str]) -> Optional[int]:
        """从 season_offset 规则解析出"源站季度"（搜索期过滤用）。

        season_offset 语法约定：左侧=源站季度，右侧=入库季度。
        - "1>9"  直接映射：源季=1
        - "1+8"  加法：源季=1
        - "9-8"  减法：源季=9
        - "*+4" / "*>1"  通配：源季不确定，返回 None（不按季过滤）
        无法解析时返回 None。
        """
        if not season_offset:
            return None
        s = str(season_offset).strip()
        if s.startswith('*'):
            return None
        for op in ('>', '+', '-'):
            if op in s:
                left = s.split(op, 1)[0].strip()
                try:
                    return int(left)
                except (ValueError, TypeError):
                    return None
        # 没有运算符时尝试整体解析为季度
        try:
            return int(s)
        except (ValueError, TypeError):
            return None

    def reverse_partial_episode_offset(
        self, stored_episode: int, ep_range: Tuple[int, Optional[int]], ep_offset: str,
    ) -> Optional[int]:
        """反算可逆的常量集数偏移，表达式或范围不匹配时返回空值。"""
        offset = ep_offset.strip()
        if offset.upper().startswith("EP"):
            return None
        try:
            if offset.startswith("-"):
                delta = -int(offset[1:])
            elif offset.startswith("+"):
                delta = int(offset[1:])
            else:
                delta = int(offset)
            source_episode = max(1, stored_episode - delta)
            start, end = ep_range
            if source_episode >= start and (end is None or source_episode <= end):
                return source_episode
        except (ValueError, TypeError):
            logger.warning("预下载反向偏移计算失败: stored_episode=%s, offset=%s", stored_episode, offset)
        return None

    def exact_match(self, text: str, pattern: str) -> bool:
        """提供调用方共享的标题边界匹配纯计算入口。"""
        return self._exact_match(text, pattern)

    def _exact_match(self, text: str, pattern: str) -> bool:
        """
        精确匹配检查，避免子字符串误匹配

        Args:
            text: 要检查的文本
            pattern: 匹配模式

        Returns:
            bool: 是否精确匹配
        """
        # 完全匹配
        if text == pattern:
            return True

        # 检查是否作为独立词汇存在（前后有分隔符或边界）
        # 创建正则表达式，确保前后有边界
        escaped_pattern = re.escape(pattern)
        # 使用词边界或常见分隔符作为边界
        boundary_pattern = r'(?:^|[\s\-_\[\]()（）【】]){pattern}(?:$|[\s\-_\[\]()（）【】])'.format(pattern=escaped_pattern)

        return bool(re.search(boundary_pattern, text))

    def _apply_episode_offset(self, text: str, episode: Optional[int], rule: TitleRecognitionRule) -> Optional[int]:
        """应用集数偏移规则"""
        return self._apply_episode_offset_with_locators(
            text, episode,
            rule.data['before_locator'],
            rule.data['after_locator'],
            rule.data['offset']
        )

    def _apply_partial_episode_offset(self, episode: Optional[int], ep_range: Tuple, ep_offset: str) -> Optional[int]:
        """
        应用部分集数偏移规则

        Args:
            episode: 当前集数
            ep_range: 集数范围元组 (start, end)，end 为 None 表示无上限
            ep_offset: 偏移表达式，支持 +N、-N、EP+N、EP-N 等

        Returns:
            偏移后的集数，若当前集数不在范围内则返回原值
        """
        if episode is None:
            return episode

        ep_start, ep_end = ep_range
        # 检查集数是否在范围内
        in_range = episode >= ep_start and (ep_end is None or episode <= ep_end)
        if not in_range:
            return episode

        try:
            offset_expr = ep_offset.strip()
            if offset_expr.upper().startswith('EP'):
                # EP 变量表达式，如 EP+5、EP-12
                calc_expr = offset_expr.upper().replace('EP', str(episode))
                new_episode = evaluate_arithmetic(calc_expr)
            elif offset_expr.startswith('+'):
                new_episode = episode + int(offset_expr[1:])
            elif offset_expr.startswith('-'):
                new_episode = episode - int(offset_expr[1:])
            else:
                new_episode = episode + int(offset_expr)

            result = max(1, int(new_episode))
            logger.debug(f"部分集数偏移: 第{episode}集 ({ep_offset}) => 第{result}集")
            return result
        except Exception as e:
            logger.warning(f"部分集数偏移计算失败: episode={episode}, offset={ep_offset}, 错误: {e}")
            return episode

    def _apply_episode_offset_with_locators(self, text: str, episode: Optional[int],
        before_locator: str, after_locator: str,
        offset: str) -> Optional[int]:
        """
        使用定位词应用集数偏移

        Args:
            text: 文本内容
            episode: 当前集数
            before_locator: 前定位词
            after_locator: 后定位词
            offset: 偏移表达式

        Returns:
            计算后的集数
        """
        # 查找定位词之间的内容
        pattern = re.escape(before_locator) + r'(.*?)' + re.escape(after_locator)
        match = re.search(pattern, text)

        if not match:
            return episode

        content = match.group(1)

        # 提取数字（包括中文小写数字）
        numbers = self._extract_numbers(content)
        if not numbers:
            return episode

        # 使用第一个数字作为集数
        ep = numbers[0]

        # 计算偏移
        try:
            if offset.startswith('EP'):
                # 使用EP变量的表达式
                offset_expr = offset.replace('EP', str(ep))
                new_episode = evaluate_arithmetic(offset_expr)
            else:
                # 简单的数字偏移
                offset_value = int(offset)
                new_episode = ep + offset_value

            logger.debug(f"集数偏移计算: {ep} + ({offset}) = {new_episode}")
            return max(1, int(new_episode))  # 确保集数不小于1

        except Exception as e:
            logger.warning(f"集数偏移计算失败: {offset}, 错误: {e}")
            return episode

    def _extract_numbers(self, text: str) -> List[int]:
        """
        从文本中提取数字（包括中文小写数字）

        Args:
            text: 文本内容

        Returns:
            提取到的数字列表
        """
        numbers = []

        # 提取阿拉伯数字
        for match in re.finditer(r'\d+', text):
            numbers.append(int(match.group()))

        # 提取中文小写数字
        chinese_numbers = {
            '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
            '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
            '零': 0
        }

        for char in text:
            if char in chinese_numbers:
                numbers.append(chinese_numbers[char])

        return numbers

    def _apply_season_offset(self, season: Optional[int], offset_rule: str) -> Optional[int]:
        """
        应用季度偏移规则

        Args:
            season: 当前季度
            offset_rule: 偏移规则，支持格式：
                - "9>13" - 第9季改为第13季
                - "9+4" - 第9季加4变成第13季
                - "9-1" - 第9季减1变成第8季
                - "*+4" - 所有季度都加4
                - "*>1" - 所有季度都改为第1季

        Returns:
            计算后的季度
        """
        if not offset_rule or season is None:
            return season

        try:
            # 处理直接映射格式：9>13
            if '>' in offset_rule:
                parts = offset_rule.split('>', 1)
                if len(parts) == 2:
                    source_season = parts[0].strip()
                    target_season = int(parts[1].strip())

                    if source_season == '*' or int(source_season) == season:
                        logger.debug(f"季度偏移计算: {season} => {target_season} (直接映射)")
                        return max(1, target_season)

            # 处理偏移计算格式：9+4, 9-1, *+4
            elif any(op in offset_rule for op in ['+', '-', '*']):
                # 提取季度条件和偏移表达式
                if offset_rule.startswith('*'):
                    # 通用规则，适用于所有季度
                    offset_expr = offset_rule[1:]  # 移除 *
                    current_season = season
                else:
                    # 特定季度规则
                    for op in ['+', '-']:
                        if op in offset_rule:
                            parts = offset_rule.split(op, 1)
                            if len(parts) == 2:
                                source_season = int(parts[0].strip())
                                if source_season != season:
                                    return season  # 不匹配当前季度，不应用偏移
                                offset_expr = op + parts[1].strip()
                                current_season = season
                                break
                    else:
                        return season

                # 计算偏移
                if offset_expr.startswith('+'):
                    offset_value = int(offset_expr[1:])
                    new_season = current_season + offset_value
                elif offset_expr.startswith('-'):
                    offset_value = int(offset_expr[1:])
                    new_season = current_season - offset_value
                else:
                    return season

                logger.debug(f"季度偏移计算: {season} {offset_expr} = {new_season}")
                return max(1, int(new_season))  # 确保季度不小于1

        except (ValueError, IndexError) as e:
            logger.warning(f"季度偏移计算失败: {offset_rule}, 错误: {e}")

        return season