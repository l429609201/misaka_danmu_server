"""标题识别业务流程：应用规则、映射分集季度并构造匹配提示。"""
import logging
from typing import Dict, Tuple, Optional, List, Any

from src.services.title_recognition import TitleRecognitionService
from src.utils.title_recognition_engine import TitleRecognitionEngine, TitleRecognitionRule

logger = logging.getLogger(__name__)


class TitleRecognitionWorkflow:
    """通过规则快照编排标题识别，不持有数据库或规则缓存。"""

    def __init__(self, service: TitleRecognitionService) -> None:
        """注入基础规则服务，纯匹配与算术由无状态引擎执行。"""
        self.service = service
        self._engine = TitleRecognitionEngine()

    async def test_legacy_rule_trace(self, title: str) -> Dict[str, Any]:
        """编排旧格式规则诊断，保留诊断接口的逐条命中返回结构。"""
        content = await self.service.get_content()
        matched: List[Dict[str, Any]] = []
        current = title
        for index, line in enumerate(content.strip().split("\n")):
            line = line.strip()
            if not line or line.startswith("#") or "||" not in line:
                continue
            parts = line.split("||")
            source, target = parts[0].strip(), parts[1].strip()
            if source and source in current:
                old = current
                current = current.replace(source, target)
                matched.append({"ruleIndex": index, "rule": line, "before": old, "after": current})
        return {"originalTitle": title, "matchedRules": matched, "transformedTitle": current}

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
        rules = await self.service.get_rules_snapshot()

        if not text:
            return text, episode, season, False

        processed_text = text
        processed_episode = episode
        processed_season = season
        has_changed = False

        # 只应用预处理阶段的规则
        for rule in rules:
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
                if self._engine._exact_match(processed_text, rule.data['source']):
                    processed_text = processed_text.replace(rule.data['source'], rule.data['target'])
                    has_changed = True
                    logger.debug(f"搜索预处理 - 应用替换规则: '{rule.data['source']}' => '{rule.data['target']}'")

            elif rule.rule_type == 'offset':
                # 集数偏移
                new_episode = self._engine._apply_episode_offset(processed_text, processed_episode, rule)
                if new_episode != processed_episode:
                    processed_episode = new_episode
                    has_changed = True

            elif rule.rule_type == 'complex':
                # 复合规则：先替换，再偏移
                if rule.data['source'] in processed_text:
                    processed_text = processed_text.replace(rule.data['source'], rule.data['target'])
                    new_episode = self._engine._apply_episode_offset_with_locators(
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
                if self._engine._exact_match(processed_text, rule.data['source']):
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
        rules = await self.service.get_rules_snapshot()

        if not original_keyword:
            return None

        # 仅扫描带 title 的 postprocess 规则（season_offset / metadata_replace）
        for rule in rules:
            if rule.stage != 'postprocess':
                continue
            if rule.rule_type not in ('season_offset', 'metadata_replace'):
                continue
            recognition_title = rule.data.get('title')
            rule_match_source = rule.data.get('source')  # 规则左侧匹配词 = 源站真实标题
            if not recognition_title or not rule_match_source:
                continue

            # 反向匹配：用户原始词 == 规则的 title 值（即用户用"入库名"来搜）
            if self._engine._exact_match(original_keyword, recognition_title):
                rule_source_restriction = rule.data.get('source_restriction', 'all')
                # why：规则 season_offset 形如 "1>9"（源站第1季 = 入库第9季）。
                # 用户用"入库名"搜索时解析出的季度是目标季(9)，但源站结果实际是源季(1)，
                # 若仍用目标季去过滤会把正确结果全部删光。这里解析出"源站季度"一并返回，
                # 供搜索期季度过滤使用，确保用源季匹配源站结果。
                source_season = self._engine._parse_source_season_from_offset(
                    rule.data.get('season_offset')
                )
                logger.info(
                    f"✓ 识别词反向映射命中: 用户搜索 '{original_keyword}' 匹配规则入库名 "
                    f"'{recognition_title}'，实际改用 '{rule_match_source}' 搜索"
                    f"（源限定={rule_source_restriction}，源站季度={source_season}）"
                )
                return {
                    "search_title": rule_match_source,
                    "recognition_title": recognition_title,
                    "rule_source_restriction": rule_source_restriction,
                    "search_season": source_season,  # 源站季度（None 表示不限定/无法解析）
                }

        return None

    async def get_recognition_hint_for_result(
        self, source_title: str, provider: Optional[str] = None, source_season: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """AI 认知校正：判断某条搜索结果(源站标题+provider)是否命中带 title 的识别词规则。

        与 apply_search_title_mapping(反向匹配 title) 相反，本方法做"正向匹配"：
        拿源站结果的真实标题去匹配规则左侧 source，命中则返回该规则的转换意图，
        供 AI 匹配时理解"这条结果经识别词转换后实际是哪部作品"，避免相似度误判。
        注意：仅用于认知校正，不参与/不改变 AI 的排序优先级。

        Args:
            source_title: 搜索结果的源站标题（如 "说唱巅峰对决2026"）
            provider: 该结果的弹幕源（如 "iqiyi"），用于校验规则的 source_restriction
            source_season: 该结果的季度（可选，预留）

        Returns:
            命中时返回 {
                "source": 规则左侧源站标题,
                "source_restriction": 规则限定源（"all" 表示不限定）,
                "recognition_title": 识别词指定的入库名,
                "season_offset": 季度偏移规则原文（无则 None）,
            }
            未命中返回 None
        """
        rules = await self.service.get_rules_snapshot()
        return self._get_recognition_hint(source_title, provider, rules)

    def _get_recognition_hint(
        self, source_title: str, provider: Optional[str],
        rules: Tuple[TitleRecognitionRule, ...],
    ) -> Optional[Dict[str, Any]]:
        """在同一流程快照内选择匹配规则，避免批量提示跨版本混用。"""
        if not source_title:
            return None

        for rule in rules:
            if rule.stage != 'postprocess':
                continue
            if rule.rule_type not in ('season_offset', 'metadata_replace'):
                continue
            recognition_title = rule.data.get('title')
            rule_match_source = rule.data.get('source')  # 规则左侧 = 源站真实标题
            if not recognition_title or not rule_match_source:
                continue

            # 正向匹配：搜索结果标题 == 规则左侧源站标题
            if not self._engine._exact_match(source_title, rule_match_source):
                continue

            # why：规则带 source=iqiyi 表示仅对该源生效；provider 不匹配则视为未命中，
            # 避免把 renren 等无关源也标成"命中识别词"。
            rule_source_restriction = rule.data.get('source_restriction', 'all') or 'all'
            if rule_source_restriction != 'all' and provider and provider != rule_source_restriction:
                continue

            return {
                "source": rule_match_source,
                "source_restriction": rule_source_restriction,
                "recognition_title": recognition_title,
                "season_offset": rule.data.get('season_offset'),
            }

        return None

    async def build_recognition_context_for_results(
        self, results: List[Any]
    ) -> Tuple[Dict[str, bool], Optional[str]]:
        """批量为一组搜索结果构建"识别词认知校正"上下文，供 AI 匹配统一调用。

        收口 webhook/全自动/match 三条 AI 路径里重复的"遍历结果→逐条判定命中→
        拼装 recognition_info 标记 map + recognition_hint 文案"逻辑，避免多处复制。

        Args:
            results: 搜索结果列表，每个元素需含 .provider / .mediaId / .title 属性

        Returns:
            (recognition_info, recognition_hint)
            - recognition_info: {f"{provider}:{mediaId}" -> True}，命中识别词规则的结果
            - recognition_hint: 给 AI 的认知校正文案；无命中时为 None
        """
        rules = await self.service.get_rules_snapshot()
        recognition_info: Dict[str, bool] = {}
        hint_parts: List[str] = []

        for r in results:
            try:
                rec = self._get_recognition_hint(
                    getattr(r, "title", None), getattr(r, "provider", None), rules
                )
            except Exception as e:
                logger.debug(f"识别词命中判定失败: {e}")
                continue
            if not rec:
                continue
            recognition_info[f"{r.provider}:{r.mediaId}"] = True
            hint_line = (
                f"源站标题'{rec['source']}'(源:{rec['source_restriction']})"
                f"经识别词规则对应入库作品'{rec['recognition_title']}'"
            )
            if hint_line not in hint_parts:
                hint_parts.append(hint_line)

        if not hint_parts:
            return recognition_info, None

        recognition_hint = (
            "用户配置了识别词规则: " + "; ".join(hint_parts)
            + "。命中规则(matchesRecognitionRule=true)的结果即用户想找的作品，请勿因字面标题差异排除。"
        )
        return recognition_info, recognition_hint

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
        rules = await self.service.get_rules_snapshot()

        if not text:
            return text, season, False, None, episode

        processed_text = text
        processed_season = season
        processed_episode = episode
        has_changed = False
        metadata_info = None

        # 只应用后处理阶段的规则
        for rule in rules:
            if rule.stage != 'postprocess':
                continue

            if rule.rule_type == 'season_offset':
                # 季度偏移规则
                if self._engine._exact_match(processed_text, rule.data['source']):
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
                    new_season = self._engine._apply_season_offset(processed_season, rule.data['season_offset'])
                    if new_season != processed_season:
                        processed_season = new_season
                        has_changed = True
                        logger.debug(f"入库后处理 - 应用季度偏移: {season} => {processed_season}")

            elif rule.rule_type == 'partial_offset':
                # 部分集数偏移规则：标题匹配 + 集数在范围内才偏移
                if self._engine._exact_match(processed_text, rule.data['source']):
                    # 检查source限制
                    rule_source = rule.data.get('source_restriction')
                    if rule_source and rule_source != 'all' and source and rule_source != source:
                        logger.debug(f"入库后处理 - 跳过部分集数偏移规则（源不匹配）: 规则源={rule_source}, 当前源={source}")
                        continue

                    new_episode = self._engine._apply_partial_episode_offset(
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
                if self._engine._exact_match(processed_text, rule.data['source']):
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
        rules = await self.service.get_rules_snapshot()

        if not text:
            return stored_episode

        for rule in rules:
            if rule.stage != 'postprocess' or rule.rule_type != 'partial_offset':
                continue

            if not self._engine._exact_match(text, rule.data['source']):
                continue

            rule_source = rule.data.get('source_restriction')
            if rule_source and rule_source != 'all' and source and rule_source != source:
                continue

            source_episode = self._engine.reverse_partial_episode_offset(
                stored_episode, rule.data['ep_range'], rule.data['ep_offset']
            )
            if source_episode is not None:
                logger.debug(f"预下载反向偏移: '{text}' 存储第{stored_episode}集 => 源站第{source_episode}集")
                return source_episode

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
        rules = await self.service.get_rules_snapshot()

        if not text:
            return text, episode, season, False, None

        processed_text = text
        processed_episode = episode
        processed_season = season
        has_changed = False
        metadata_info = None

        # 按顺序应用所有规则
        for rule in rules:
            if rule.rule_type == 'block':
                # 屏蔽词：从文本中移除
                if rule.data['word'] in processed_text:
                    processed_text = processed_text.replace(rule.data['word'], '').strip()
                    has_changed = True
                    logger.debug(f"应用屏蔽词规则: 移除 '{rule.data['word']}'")

            elif rule.rule_type == 'replace':
                # 简单替换 - 使用完全匹配避免误匹配
                if self._engine._exact_match(processed_text, rule.data['source']):
                    processed_text = processed_text.replace(rule.data['source'], rule.data['target'])
                    has_changed = True
                    logger.debug(f"应用替换规则: '{rule.data['source']}' => '{rule.data['target']}'")

            elif rule.rule_type == 'metadata_replace':
                # 元数据替换 - 使用完全匹配避免误匹配
                if self._engine._exact_match(processed_text, rule.data['source']):
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
                new_episode = self._engine._apply_episode_offset(processed_text, processed_episode, rule)
                if new_episode != processed_episode:
                    processed_episode = new_episode
                    has_changed = True

            elif rule.rule_type == 'complex':
                # 复合规则：先替换，再偏移
                if rule.data['source'] in processed_text:
                    processed_text = processed_text.replace(rule.data['source'], rule.data['target'])
                    new_episode = self._engine._apply_episode_offset_with_locators(
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
                if self._engine._exact_match(processed_text, rule.data['source']):
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
                    new_season = self._engine._apply_season_offset(processed_season, rule.data['season_offset'])
                    if new_season != processed_season:
                        processed_season = new_season
                        has_changed = True
                        logger.info(f"✓ 季度偏移: {old_season} -> {new_season} (规则: {rule.data['season_offset']})")
                else:
                    logger.debug(f"○ 季度偏移规则不匹配: '{processed_text}' 不匹配 '{rule.data['source']}'")

            elif rule.rule_type == 'partial_offset':
                # 部分集数偏移规则：标题匹配 + 集数在范围内才偏移
                if self._engine._exact_match(processed_text, rule.data['source']):
                    # 检查source限制
                    rule_source = rule.data.get('source_restriction')
                    if rule_source and rule_source != 'all' and source and rule_source != source:
                        logger.debug(f"跳过部分集数偏移规则（源不匹配）: 规则源={rule_source}, 当前源={source}")
                        continue

                    new_episode = self._engine._apply_partial_episode_offset(
                        processed_episode,
                        rule.data['ep_range'],
                        rule.data['ep_offset']
                    )
                    if new_episode != processed_episode:
                        processed_episode = new_episode
                        has_changed = True
                        logger.info(f"✓ 部分集数偏移: '{rule.data['source']}' 第{episode}集 => 第{processed_episode}集 (范围: {rule.data['ep_range']}, 偏移: {rule.data['ep_offset']})")

        return processed_text, processed_episode, processed_season, has_changed, metadata_info
