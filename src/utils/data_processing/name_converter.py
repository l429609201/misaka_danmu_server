"""
名称转换模块 - 将非中文标题转换为中文标题

用于搜索时自动将日文、英文等非中文标题转换为中文，以提高弹幕源搜索的匹配率。
"""

import asyncio
import json
import logging
from typing import Any, Optional, Tuple

from src.schemas import User
from src.utils.parsing.filename_parser import is_chinese_title
try:
    from opencc import OpenCC
except ImportError:
    OpenCC = None

# 服务由调用方注入，避免工具模块反向导入服务而形成循环依赖。

logger = logging.getLogger(__name__)


async def convert_to_chinese_title(
    title: str,
    config_service: Any,
    metadata_manager: Any,
    ai_service: Optional[Any],
    user: User
) -> Tuple[str, bool]:
    """
    将非中文标题转换为中文标题
    
    Args:
        title: 原始标题
        config_service: 配置管理器
        metadata_manager: 元数据管理器
        ai_service: 共享 AI 服务（可选）
        user: 当前用户
        
    Returns:
        Tuple[str, bool]: (转换后的标题, 是否成功转换)
    """
    # 检查是否启用名称转换
    name_conversion_enabled_str = await config_service.get("nameConversionEnabled", "false")
    name_conversion_enabled = name_conversion_enabled_str.lower() == "true"

    # 检查繁体转简体开关（独立于名称转换开关）
    t2s_enabled_str = await config_service.get("nameConversionT2SEnabled", "false")
    t2s_enabled = t2s_enabled_str.lower() == "true"

    logger.info(f"名称转换配置检查: nameConversionEnabled='{name_conversion_enabled_str}', t2sEnabled='{t2s_enabled_str}'")

    # 如果已经是中文标题
    if is_chinese_title(title):
        # 检查繁体转简体
        if t2s_enabled and OpenCC:
            try:
                converter = OpenCC('t2s')
                converted = converter.convert(title)
                if converted != title:
                    logger.info(f"? 繁体转简体: '{title}' → '{converted}'")
                    return converted, True
            except Exception as e:
                logger.warning(f"繁体转简体失败: {e}")
        return title, False

    # 非中文标题，检查名称转换开关
    if not name_conversion_enabled:
        logger.info(f"○ 名称转换功能未启用，跳过: '{title}'")
        return title, False
    
    logger.info(f"检测到非中文标题: '{title}'，尝试名称转换...")
    
    try:
        # 1. 尝试通过元数据源转换
        converted = await _convert_via_metadata_sources(
            title, config_service, metadata_manager, user
        )
        if converted:
            logger.info(f"? 名称转换成功 ({converted[0]}): '{title}' → '{converted[1]}'")
            return converted[1], True
        
        # 2. 元数据源失败，尝试AI兜底
        ai_converted = await _convert_via_ai(
            title, config_service, ai_service
        )
        if ai_converted:
            logger.info(f"? AI名称转换成功: '{title}' → '{ai_converted}'")
            return ai_converted, True
        
        logger.info(f"○ 名称转换未找到中文名: '{title}'")
        return title, False
        
    except Exception as e:
        logger.warning(f"名称转换过程出错: {e}")
        return title, False


async def _convert_via_metadata_sources(
    title: str,
    config_service: Any,
    metadata_manager: Any,
    user: User
) -> Optional[Tuple[str, str]]:
    """
    通过元数据源转换标题
    
    Returns:
        Optional[Tuple[str, str]]: (源名称, 中文标题) 或 None
    """
    # 获取元数据源优先级配置
    priority_config_str = await config_service.get(
        "nameConversionSourcePriority",
        '[{"key":"bangumi","enabled":true},{"key":"tmdb","enabled":true},{"key":"tvdb","enabled":true},{"key":"douban","enabled":true},{"key":"imdb","enabled":true}]'
    )
    try:
        priority_config = json.loads(priority_config_str)
    except json.JSONDecodeError:
        priority_config = [{"key": "bangumi", "enabled": True}, {"key": "tmdb", "enabled": True}]
    
    # 按优先级顺序获取启用的元数据源
    enabled_sources = [item["key"] for item in priority_config if item.get("enabled", True)]
    
    if not enabled_sources:
        return None
    
    # 定义单个源的搜索函数
    async def search_source(source_name: str) -> Optional[Tuple[str, str]]:
        try:
            media_type = 'multi' if source_name == 'tmdb' else None
            results = await metadata_manager.search(source_name, title, user, mediaType=media_type)
            if results:
                for result in results:
                    # 检查标题是否有中文
                    if result.title and is_chinese_title(result.title):
                        return (source_name, result.title)
                    # 检查别名
                    if result.aliases:
                        for alias in result.aliases:
                            if is_chinese_title(alias):
                                return (source_name, alias)

                    # ?? 如果搜索结果标题不是中文，尝试获取详情以获取中文别名
                    # 这对 TMDB 特别重要，因为搜索结果可能返回原始语言标题
                    if result.id and source_name in ['tmdb', 'tvdb', 'imdb']:
                        try:
                            # 确定媒体类型用于 get_details
                            detail_media_type = result.type if hasattr(result, 'type') and result.type else 'tv'
                            details = await metadata_manager.get_details(
                                source_name, result.id, user, mediaType=detail_media_type
                            )
                            if details:
                                # 检查详情中的标题
                                if details.title and is_chinese_title(details.title):
                                    return (source_name, details.title)
                                # 检查中文别名列表
                                if hasattr(details, 'aliasesCn') and details.aliasesCn:
                                    for alias in details.aliasesCn:
                                        if is_chinese_title(alias):
                                            return (source_name, alias)
                                # 检查通用别名
                                if details.aliases:
                                    for alias in details.aliases:
                                        if is_chinese_title(alias):
                                            return (source_name, alias)
                        except Exception as detail_err:
                            logger.debug(f"名称转换 - {source_name} 获取详情失败: {detail_err}")
            return None
        except Exception as e:
            logger.debug(f"名称转换 - {source_name} 查询失败: {e}")
            return None
    
    # 并行执行所有查询
    tasks = [search_source(source) for source in enabled_sources]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 按优先级顺序检查结果
    for result in results:
        if result and not isinstance(result, Exception):
            return result

    return None


async def _convert_via_ai(
    title: str,
    config_service: Any,
    ai_service: Optional[Any]
) -> Optional[str]:
    """
    通过共享 AI 服务转换标题（兜底方案）

    Returns:
        Optional[str]: 中文标题 或 None
    """
    # 检查是否启用AI名称转换
    ai_enabled_str = await config_service.get("aiNameConversionEnabled", "false")
    if ai_enabled_str.lower() != "true":
        return None

    if not ai_service or not await ai_service.is_available():
        return None

    logger.info("元数据源名称转换失败，尝试AI兜底...")

    try:
        # 由共享服务管理提示词和供应商调用，不再依赖不存在的 query 接口。
        ai_response = await ai_service.convert_title(title)

        if ai_response and is_chinese_title(ai_response):
            return ai_response.strip()

        return None

    except Exception as e:
        logger.warning(f"AI名称转换失败: {e}")
        return None

