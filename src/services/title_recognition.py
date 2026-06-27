"""
标题识别词功能模块
用于解析和应用标题识别词转换规则
参考MoviePilot的识别词格式实现
"""
import os
import logging
import re
from typing import Dict, Tuple, Optional, List, Any, Union
from sqlalchemy.orm import Session
from sqlalchemy import delete, select

from src.db import get_db_session, orm_models
from src.core import get_now

# ORM 模型别名
TitleRecognition = orm_models.TitleRecognition

logger = logging.getLogger(__name__)

class TitleRecognitionRule:
    """识别词规则类"""

    def __init__(self, rule_type: str, stage: str, **kwargs):
        self.rule_type = rule_type  # 'block', 'replace', 'offset', 'complex', 'metadata_replace', 'season_offset'
        self.stage = stage  # 'preprocess' (搜索预处理) 或 'postprocess' (入库后处理)
        self.data = kwargs

class TitleRecognitionManager:
    """标题识别词管理器 - 参考MoviePilot格式"""

    def __init__(self, session_factory):
        """
        初始化识别词管理器

        Args:
            session_factory: 数据库会话工厂
        """
        self.session_factory = session_factory
        self.recognition_rules: List[TitleRecognitionRule] = []
        self._rules_loaded = False

    async def _ensure_rules_loaded(self):
        """
        确保识别词规则已加载
        """
        if self._rules_loaded:
            return

        try:
            async with self.session_factory() as session:
                # 获取最新的识别词配置（只有一条记录）
                result = await session.execute(
                     select(TitleRecognition).limit(1)
                 )
                title_recognition = result.scalar_one_or_none()

                if title_recognition is None:
                    logger.info("数据库中未找到识别词配置，使用空规则集")
                    self.recognition_rules = []
                else:
                    self.recognition_rules, warnings = self._parse_recognition_content(title_recognition.content)
                    logger.info(f"从数据库加载了 {len(self.recognition_rules)} 条识别词规则")
                    if warnings:
                        logger.warning(f"加载识别词规则时发现 {len(warnings)} 个警告")
                        for warning in warnings:
                            logger.warning(f"识别词规则警告: {warning}")

                self._rules_loaded = True

        except Exception as e:
            logger.error(f"从数据库加载识别词规则失败: {e}")
            self.recognition_rules = []
    
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

    async def update_recognition_rules(self, content: str) -> List[str]:
        """
        更新识别词规则，使用全量替换模式

        Args:
            content: 新的识别词配置内容

        Returns:
            List[str]: 解析过程中的警告信息列表
        """
        try:
            # 先解析内容，获取警告信息
            new_rules, warnings = self._parse_recognition_content(content)

            async with self.session_factory() as session:
                # 删除所有现有记录
                await session.execute(delete(TitleRecognition))

                # 插入新的配置记录
                new_recognition = TitleRecognition(content=content)
                session.add(new_recognition)

                await session.commit()

                # 重新加载规则到内存
                self.recognition_rules = new_rules

                logger.info(f"成功更新识别词规则，共 {len(self.recognition_rules)} 条规则")
                if warnings:
                    logger.warning(f"更新过程中发现 {len(warnings)} 个警告")
                    for warning in warnings:
                        logger.warning(f"识别词规则警告: {warning}")

                return warnings

        except Exception as e:
            logger.error(f"更新识别词规则失败: {e}")
            raise

    async def apply_search_preprocessing(self, text: str, episode: Optional[int] = None, season: Optional[int] = None) -> Tuple[str, Optional[int], Optional[int], bool]:
        """
        应用搜索预处理规则（在搜索前执行）

        Args:
            text: 原始搜索关键词
            episode: 原始集数
            season: 原始季数

        Returns:
            Tuple[处理后的文本, 处理后的集数, 处理后的季数, 是否发生了转换]
        """
        await self._ensure_rules_loaded()

        if not text:
            return text, episode, False

        processed_text = text
        processed_episode = episode
        processed_season = season
        has_changed = False

        # 只应用预处理阶段的规则
        for rule in self.recognition_rules:
            if rule.stage != 'preprocess':
                continue

            if rule.rule_type == 'block':
                # 屏蔽词：从文本中移除
                if rule.data['word'] in processed_text:
                    processed_text = processed_text.replace(rule.data['word'], '').strip()
                    has_changed = True
                    logger.debug(f"搜索预处理 - 应用屏蔽词规则: 移除 '{rule.data['word']}'")

            elif rule.rule_type == 'replace':
                # 简单替换
                if self._exact_match(processed_text, rule.data['source']):
                    processed_text = processed_text.replace(rule.data['source'], rule.data['target'])
                    has_changed = True
                    logger.debug(f"搜索预处理 - 应用替换规则: '{rule.data['source']}' => '{rule.data['target']}'")

            elif rule.rule_type == 'offset':
                # 集数偏移
                new_episode = self._apply_episode_offset(processed_text, processed_episode, rule)
                if new_episode != processed_episode:
                    processed_episode = new_episode
                    has_changed = True

            elif rule.rule_type == 'complex':
                # 复合规则：先替换，再偏移
                if rule.data['source'] in processed_text:
                    processed_text = processed_text.replace(rule.data['source'], rule.data['target'])
                    new_episode = self._apply_episode_offset_with_locators(
                        processed_text, processed_episode,
                        rule.data['before_locator'],
                        rule.data['after_locator'],
                        rule.data['offset']
                    )
                    if new_episode != processed_episode:
                        processed_episode = new_episode
                    has_changed = True
                    logger.debug(f"搜索预处理 - 应用复合规则: '{rule.data['source']}' => '{rule.data['target']}'")

            elif rule.rule_type == 'search_season':
                # 季度预处理：指定搜索时使用的季度
                if self._exact_match(processed_text, rule.data['source']):
                    processed_season = rule.data['search_season']
                    has_changed = True
                    logger.debug(f"搜索预处理 - 应用季度预处理规则: '{rule.data['source']}' => 季度 {processed_season}")

        return processed_text, processed_episode, processed_season, has_changed

    async def apply_search_title_mapping(self, original_keyword: str) -> Optional[Dict[str, Any]]:
        """
        识别词"反向映射"（搜索期最高优先级）。

        用户写的 postprocess 规则形如：
            说唱巅峰对决2026 => {[source=iqiyi;title=中国新说唱 第九季;season_offset=1>9]}
        其中 `source`(规则左侧匹配词)=源站真实标题，`title`=用户希望的入库名。

        本方法做反向匹配：如果"用户搜索的原始完整关键词"命中了规则的 `title` 值，
        说明用户其实想找的是源站上叫 `source` 的那部，于是返回实际应该拿去搜索的词。

        Args:
            original_keyword: 用户输入的原始完整关键词（未经 parse 拆解，如 "中国新说唱 第九季"）

        Returns:
            命中时返回 {
                "search_title": 实际拿去搜索的词（规则 source 值，如 "说唱巅峰对决2026"）,
                "recognition_title": 识别词指定的入库正确名（规则 title 值，如 "中国新说唱 第九季"）,
                "rule_source_restriction": 规则的源限定（如 "iqiyi"，无则 "all"）,
            }
            未命中返回 None
        """
        await self._ensure_rules_loaded()

        if not original_keyword:
            return None

        # 仅扫描带 title 的 postprocess 规则（season_offset / metadata_replace）
        for rule in self.recognition_rules:
            if rule.stage != 'postprocess':
                continue
            if rule.rule_type not in ('season_offset', 'metadata_replace'):
                continue
            recognition_title = rule.data.get('title')
            rule_match_source = rule.data.get('source')  # 规则左侧匹配词 = 源站真实标题
            if not recognition_title or not rule_match_source:
                continue

            # 反向匹配：用户原始词 == 规则的 title 值（即用户用"入库名"来搜）
            if self._exact_match(original_keyword, recognition_title):
                rule_source_restriction = rule.data.get('source_restriction', 'all')
                logger.info(
                    f"✓ 识别词反向映射命中: 用户搜索 '{original_keyword}' 匹配规则入库名 "
                    f"'{recognition_title}'，实际改用 '{rule_match_source}' 搜索"
                    f"（源限定={rule_source_restriction}）"
                )
                return {
                    "search_title": rule_match_source,
                    "recognition_title": recognition_title,
                    "rule_source_restriction": rule_source_restriction,
                }

        return None

    async def apply_storage_postprocessing(self, text: str, season: Optional[int] = None, source: Optional[str] = None, episode: Optional[int] = None) -> Tuple[str, Optional[int], bool, Optional[Dict[str, Any]], Optional[int]]:
        """
        应用入库后处理规则（在选择最佳匹配后执行）

        Args:
            text: 选择的最佳匹配标题
            season: 原始季数
            source: 数据源名称
            episode: 当前集数（用于 partial_offset 规则）

        Returns:
            Tuple[处理后的标题, 处理后的季数, 是否发生了转换, 元数据信息, 处理后的集数]
        """
        await self._ensure_rules_loaded()

        if not text:
            return text, season, False, None, episode

        processed_text = text
        processed_season = season
        processed_episode = episode
        has_changed = False
        metadata_info = None

        # 只应用后处理阶段的规则
        for rule in self.recognition_rules:
            if rule.stage != 'postprocess':
                continue

            if rule.rule_type == 'season_offset':
                # 季度偏移规则
                if self._exact_match(processed_text, rule.data['source']):
                    # 检查source限制（如果规则指定了source）
                    rule_source = rule.data.get('source_restriction')
                    if rule_source and rule_source != 'all' and source and rule_source != source:
                        logger.debug(f"跳过季度偏移规则（源不匹配）: 规则源={rule_source}, 当前源={source}")
                        continue

                    # 应用标题替换（如果有）
                    if 'title' in rule.data:
                        processed_text = rule.data['title']
                        has_changed = True
                        logger.debug(f"入库后处理 - 应用标题替换: '{rule.data['source']}' => '{processed_text}'")

                    # 应用季度偏移
                    new_season = self._apply_season_offset(processed_season, rule.data['season_offset'])
                    if new_season != processed_season:
                        processed_season = new_season
                        has_changed = True
                        logger.debug(f"入库后处理 - 应用季度偏移: {season} => {processed_season}")

            elif rule.rule_type == 'partial_offset':
                # 部分集数偏移规则：标题匹配 + 集数在范围内才偏移
                if self._exact_match(processed_text, rule.data['source']):
                    # 检查source限制
                    rule_source = rule.data.get('source_restriction')
                    if rule_source and rule_source != 'all' and source and rule_source != source:
                        logger.debug(f"入库后处理 - 跳过部分集数偏移规则（源不匹配）: 规则源={rule_source}, 当前源={source}")
                        continue

                    new_episode = self._apply_partial_episode_offset(
                        processed_episode,
                        rule.data['ep_range'],
                        rule.data['ep_offset']
                    )
                    if new_episode != processed_episode:
                        logger.info(f"入库后处理 - 部分集数偏移: '{rule.data['source']}' 第{processed_episode}集 => 第{new_episode}集 (范围: {rule.data['ep_range']}, 偏移: {rule.data['ep_offset']})")
                        processed_episode = new_episode
                        has_changed = True

            elif rule.rule_type == 'metadata_replace':
                # 元数据替换规则
                if self._exact_match(processed_text, rule.data['source']):
                    # 检查source限制（如果规则指定了source）
                    rule_source = rule.data.get('source_restriction')
                    if rule_source and rule_source != 'all' and source and rule_source != source:
                        logger.debug(f"跳过元数据替换规则（源不匹配）: 规则源={rule_source}, 当前源={source}")
                        continue

                    metadata_info = {k: v for k, v in rule.data.items() if k not in ['source', 'source_restriction']}
                    has_changed = True
                    logger.debug(f"入库后处理 - 应用元数据替换规则: '{rule.data['source']}' => 元数据")

        return processed_text, processed_season, has_changed, metadata_info, processed_episode

    async def reverse_episode_offset(self, text: str, stored_episode: int, source: Optional[str] = None) -> int:
        """
        将已偏移存储的集数反向还原为源站原始集数（用于预下载场景）

        例如：规则 ep_range=60-99, ep_offset=+7
        存储的第68集 → 源站第61集

        Args:
            text: 番剧标题
            stored_episode: 数据库中存储的集数（已偏移后）
            source: 数据源名称

        Returns:
            源站实际集数，如果无匹配规则则返回 stored_episode 原值
        """
        await self._ensure_rules_loaded()

        if not text:
            return stored_episode

        for rule in self.recognition_rules:
            if rule.stage != 'postprocess' or rule.rule_type != 'partial_offset':
                continue

            if not self._exact_match(text, rule.data['source']):
                continue

            rule_source = rule.data.get('source_restriction')
            if rule_source and rule_source != 'all' and source and rule_source != source:
                continue

            ep_offset = rule.data['ep_offset'].strip()
            ep_range = rule.data['ep_range']

            # 计算反向偏移量
            try:
                if ep_offset.upper().startswith('EP'):
                    # EP 表达式无法简单反向，跳过
                    continue
                elif ep_offset.startswith('+'):
                    delta = int(ep_offset[1:])
                    source_episode = stored_episode - delta
                elif ep_offset.startswith('-'):
                    delta = int(ep_offset[1:])
                    source_episode = stored_episode + delta
                else:
                    delta = int(ep_offset)
                    source_episode = stored_episode - delta

                source_episode = max(1, source_episode)

                # 验证反向还原后的源站集数是否在规则范围内
                ep_start, ep_end = ep_range
                in_range = source_episode >= ep_start and (ep_end is None or source_episode <= ep_end)
                if in_range:
                    logger.debug(f"预下载反向偏移: '{text}' 存储第{stored_episode}集 => 源站第{source_episode}集")
                    return source_episode
            except Exception as e:
                logger.warning(f"预下载反向偏移计算失败: stored_episode={stored_episode}, offset={ep_offset}, 错误: {e}")

        return stored_episode


    async def apply_title_recognition(self, text: str, episode: Optional[int] = None, season: Optional[int] = None, source: Optional[str] = None) -> Tuple[str, Optional[int], Optional[int], bool, Optional[Dict[str, Any]]]:
        """
        应用标题识别词转换 - 参考MoviePilot格式

        Args:
            text: 原始文本（标题或文件名）
            episode: 原始集数
            season: 原始季数
            source: 数据源名称（用于source限制规则）

        Returns:
            Tuple[转换后的文本, 转换后的集数, 转换后的季数, 是否发生了转换, 元数据信息]
        """
        # 确保规则已加载
        await self._ensure_rules_loaded()

        if not text:
            return text, episode, season, False, None

        processed_text = text
        processed_episode = episode
        processed_season = season
        has_changed = False
        metadata_info = None

        # 按顺序应用所有规则
        for rule in self.recognition_rules:
            if rule.rule_type == 'block':
                # 屏蔽词：从文本中移除
                if rule.data['word'] in processed_text:
                    processed_text = processed_text.replace(rule.data['word'], '').strip()
                    has_changed = True
                    logger.debug(f"应用屏蔽词规则: 移除 '{rule.data['word']}'")

            elif rule.rule_type == 'replace':
                # 简单替换 - 使用完全匹配避免误匹配
                if self._exact_match(processed_text, rule.data['source']):
                    processed_text = processed_text.replace(rule.data['source'], rule.data['target'])
                    has_changed = True
                    logger.debug(f"应用替换规则: '{rule.data['source']}' => '{rule.data['target']}'")

            elif rule.rule_type == 'metadata_replace':
                # 元数据替换 - 使用完全匹配避免误匹配
                if self._exact_match(processed_text, rule.data['source']):
                    # 检查source限制（如果规则指定了source）
                    rule_source = rule.data.get('source_restriction')
                    if rule_source and rule_source != 'all' and source and rule_source != source:
                        logger.debug(f"跳过元数据替换规则（源不匹配）: 规则源={rule_source}, 当前源={source}")
                        continue

                    processed_text = processed_text.replace(rule.data['source'], '')
                    metadata_info = {k: v for k, v in rule.data.items() if k not in ['source', 'source_restriction']}
                    has_changed = True
                    logger.debug(f"应用元数据替换规则: '{rule.data['source']}' => 元数据")

            elif rule.rule_type == 'offset':
                # 集数偏移
                new_episode = self._apply_episode_offset(processed_text, processed_episode, rule)
                if new_episode != processed_episode:
                    processed_episode = new_episode
                    has_changed = True

            elif rule.rule_type == 'complex':
                # 复合规则：先替换，再偏移
                if rule.data['source'] in processed_text:
                    processed_text = processed_text.replace(rule.data['source'], rule.data['target'])
                    new_episode = self._apply_episode_offset_with_locators(
                        processed_text, processed_episode,
                        rule.data['before_locator'],
                        rule.data['after_locator'],
                        rule.data['offset']
                    )
                    if new_episode != processed_episode:
                        processed_episode = new_episode
                    has_changed = True
                    logger.debug(f"应用复合规则: '{rule.data['source']}' => '{rule.data['target']}' + 集数偏移")

            elif rule.rule_type == 'season_offset':
                # 季度偏移规则 - 使用完全匹配避免误匹配
                logger.debug(f"检查季度偏移规则匹配: 文本='{processed_text}' vs 规则='{rule.data['source']}'")
                if self._exact_match(processed_text, rule.data['source']):
                    logger.info(f"✓ 季度偏移规则匹配成功: '{processed_text}' 匹配 '{rule.data['source']}'")

                    # 检查source限制（如果规则指定了source）
                    rule_source = rule.data.get('source_restriction')  # 避免与rule.data['source']冲突
                    if rule_source and rule_source != 'all' and source and rule_source != source:
                        logger.debug(f"跳过季度偏移规则（源不匹配）: 规则源={rule_source}, 当前源={source}")
                        continue

                    # 应用标题替换（如果有）
                    old_text = processed_text
                    if 'title' in rule.data:
                        processed_text = processed_text.replace(rule.data['source'], rule.data['title'])
                        logger.info(f"✓ 标题替换: '{old_text}' -> '{processed_text}'")
                    else:
                        processed_text = processed_text.replace(rule.data['source'], '').strip()
                        logger.info(f"✓ 标题清理: '{old_text}' -> '{processed_text}'")

                    # 应用季度偏移
                    old_season = processed_season
                    new_season = self._apply_season_offset(processed_season, rule.data['season_offset'])
                    if new_season != processed_season:
                        processed_season = new_season
                        has_changed = True
                        logger.info(f"✓ 季度偏移: {old_season} -> {new_season} (规则: {rule.data['season_offset']})")
                else:
                    logger.debug(f"○ 季度偏移规则不匹配: '{processed_text}' 不匹配 '{rule.data['source']}'")

            elif rule.rule_type == 'partial_offset':
                # 部分集数偏移规则：标题匹配 + 集数在范围内才偏移
                if self._exact_match(processed_text, rule.data['source']):
                    # 检查source限制
                    rule_source = rule.data.get('source_restriction')
                    if rule_source and rule_source != 'all' and source and rule_source != source:
                        logger.debug(f"跳过部分集数偏移规则（源不匹配）: 规则源={rule_source}, 当前源={source}")
                        continue

                    new_episode = self._apply_partial_episode_offset(
                        processed_episode,
                        rule.data['ep_range'],
                        rule.data['ep_offset']
                    )
                    if new_episode != processed_episode:
                        processed_episode = new_episode
                        has_changed = True
                        logger.info(f"✓ 部分集数偏移: '{rule.data['source']}' 第{episode}集 => 第{processed_episode}集 (范围: {rule.data['ep_range']}, 偏移: {rule.data['ep_offset']})")

        return processed_text, processed_episode, processed_season, has_changed, metadata_info

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
        import re
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
                new_episode = eval(calc_expr)
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
                new_episode = eval(offset_expr)
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