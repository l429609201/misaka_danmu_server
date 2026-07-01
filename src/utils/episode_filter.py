"""
单剧分集过滤工具 - 共享模块
从 search.py 提取，供 import_core / predownload / search 等多处复用
"""
import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _parse_metadata_block(block: str) -> Dict[str, str]:
    """解析类似 {[rules=xxx;provider=xxx;mediaId=xxx]} 的配置块。"""
    text = block.strip()
    if not (text.startswith("{[") and text.endswith("]}")):
        return {}
    text = text[2:-2].strip()
    data: Dict[str, str] = {}
    for part in text.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            data[key] = value
    return data


def parse_single_episode_filter_rules(content: str) -> List[Dict[str, str]]:
    """解析单剧过滤文本配置。格式：作品匹配词 => {[rules=正则;provider=可选;mediaId=可选]}"""
    rules: List[Dict[str, str]] = []
    if not content:
        return rules
    for line_num, raw_line in enumerate(content.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if " => " not in line:
            logger.warning(f"单剧过滤配置第{line_num}行缺少 =>，已跳过: {line}")
            continue
        title_pattern, block = line.split(" => ", 1)
        title_pattern = title_pattern.strip()
        meta = _parse_metadata_block(block)
        rule_pattern = meta.get("rules", "").strip()
        if not title_pattern or not rule_pattern:
            logger.warning(f"单剧过滤配置第{line_num}行缺少作品匹配词或 rules，已跳过: {line}")
            continue
        rules.append({
            "title": title_pattern,
            "rules": rule_pattern,
            "provider": meta.get("provider", "").strip(),
            "mediaId": meta.get("mediaId", "").strip(),
        })
    return rules


def apply_single_episode_filter(
    episodes,
    rules: List[Dict[str, str]],
    title: Optional[str],
    provider: str,
    media_id: str,
):
    """根据单剧过滤规则过滤分集列表。

    Args:
        episodes: 分集列表 (List[ProviderEpisodeInfo])
        rules: 解析后的规则列表
        title: 当前作品标题
        provider: 数据源名称
        media_id: 媒体ID

    Returns:
        过滤后的分集列表
    """
    if not rules or not title:
        return episodes
    filtered = episodes
    for rule in rules:
        if rule["title"].lower() not in title.lower():
            continue
        if rule.get("provider") and rule["provider"] != provider:
            continue
        if rule.get("mediaId") and rule["mediaId"] != media_id:
            continue
        pattern = rule["rules"]
        before_count = len(filtered)
        kept = []
        removed_titles = []
        for episode in filtered:
            episode_title = episode.title or ""
            try:
                matched = re.search(pattern, episode_title, re.IGNORECASE) is not None
            except re.error as e:
                logger.warning(f"单剧过滤规则正则无效，已跳过: {pattern} ({e})")
                matched = False
            if matched:
                removed_titles.append(episode_title)
            else:
                kept.append(episode)
        if removed_titles:
            logger.info(
                f"单剧过滤命中: title={title}, provider={provider}, mediaId={media_id}, "
                f"rule={rule['title']}，过滤 {len(removed_titles)}/{before_count} 集: {removed_titles[:20]}"
            )
        filtered = kept
    return filtered


async def get_and_apply_single_episode_filter(
    episodes,
    config_manager,
    title: Optional[str],
    provider: str,
    media_id: str,
):
    """从配置中读取单剧过滤规则并应用。便捷函数。

    Args:
        episodes: 分集列表
        config_manager: 配置管理器
        title: 当前作品标题
        provider: 数据源名称
        media_id: 媒体ID

    Returns:
        过滤后的分集列表
    """
    if not episodes or not title:
        return episodes
    filter_content = await config_manager.get("singleEpisodeFilterRules", "")
    if not filter_content:
        return episodes
    rules = parse_single_episode_filter_rules(filter_content)
    if not rules:
        return episodes
    return apply_single_episode_filter(episodes, rules, title, provider, media_id)


async def apply_global_episode_title_filter(
    episodes,
    config_manager,
    provider: str = "",
    media_id: str = "",
):
    """兜底全局分集标题过滤（不依赖作品标题，按全局正则过滤分集标题）。

    读取配置：
        - globalEpisodeTitleFilterEnabled: 开关（"true" 才生效）
        - globalEpisodeTitleFilterRegex: 全局过滤正则

    与单剧过滤的区别：单剧过滤需匹配作品标题/源/mediaId 后再过滤；
    本函数是全局兜底，只要开关开启就对所有分集标题应用同一条正则。
    正则无效时保留该分集（不误删）。

    Args:
        episodes: 分集列表 (List[ProviderEpisodeInfo])
        config_manager: 配置管理器
        provider: 数据源名称（仅用于日志）
        media_id: 媒体ID（仅用于日志）

    Returns:
        过滤后的分集列表
    """
    if not episodes:
        return episodes
    enabled = await config_manager.get("globalEpisodeTitleFilterEnabled", "false")
    if str(enabled).lower() != "true":
        return episodes
    pattern = (await config_manager.get("globalEpisodeTitleFilterRegex", "") or "").strip()
    if not pattern:
        return episodes

    before_count = len(episodes)
    kept = []
    for ep in episodes:
        ep_title = getattr(ep, "title", "") or ""
        try:
            if re.search(pattern, ep_title, re.IGNORECASE):
                continue  # 命中过滤规则，丢弃
        except re.error as e:
            logger.warning(f"兜底全局分集标题过滤正则无效，已跳过过滤: {pattern} ({e})")
            return episodes  # 正则无效时整体不过滤，避免误删
        kept.append(ep)

    removed = before_count - len(kept)
    if removed > 0:
        logger.info(
            f"兜底全局分集标题过滤: provider={provider}, mediaId={media_id}, "
            f"过滤 {removed}/{before_count} 集"
        )
    return kept
