"""
繁简转换工具

提供统一的繁简转换功能，支持：
1. 弹幕内容批量转换
2. 单个文本转换
3. 基于配置的优先级控制
"""

import logging
from typing import List, Dict, Any, Optional
from opencc import OpenCC

logger = logging.getLogger(__name__)

# 全局转换器实例（避免重复创建）
_converters = {
    's2t': None,  # 简转繁
    't2s': None,  # 繁转简
}


def _get_converter(mode: str) -> OpenCC:
    """
    获取转换器实例（懒加载 + 缓存）
    
    :param mode: 's2t' (简转繁) 或 't2s' (繁转简)
    :return: OpenCC 转换器实例
    """
    global _converters
    
    if _converters[mode] is None:
        try:
            _converters[mode] = OpenCC(f'{mode}.json')
        except Exception as e:
            logger.error(f"创建 OpenCC 转换器失败 (mode={mode}): {e}", exc_info=True)
            raise
    
    return _converters[mode]


def convert_text(text: str, mode: int) -> str:
    """
    转换单个文本
    
    :param text: 待转换的文本
    :param mode: 转换模式 (0=不转换, 1=繁转简, 2=简转繁)
    :return: 转换后的文本
    """
    if not text or mode == 0:
        return text
    
    try:
        if mode == 1:
            # 繁转简
            converter = _get_converter('t2s')
        elif mode == 2:
            # 简转繁
            converter = _get_converter('s2t')
        else:
            logger.warning(f"未知的转换模式: {mode}")
            return text
        
        return converter.convert(text)
    except Exception as e:
        logger.error(f"文本转换失败 (mode={mode}): {e}", exc_info=True)
        return text


def convert_comments(
    comments: List[Dict[str, Any]],
    mode: int,
    text_field: str = 'm'
) -> List[Dict[str, Any]]:
    """
    批量转换弹幕内容
    
    :param comments: 弹幕列表
    :param mode: 转换模式 (0=不转换, 1=繁转简, 2=简转繁)
    :param text_field: 弹幕文本字段名（默认 'm'）
    :return: 转换后的弹幕列表（原地修改）
    """
    if not comments or mode == 0:
        return comments
    
    try:
        if mode == 1:
            # 繁转简
            converter = _get_converter('t2s')
        elif mode == 2:
            # 简转繁
            converter = _get_converter('s2t')
        else:
            logger.warning(f"未知的转换模式: {mode}")
            return comments
        
        # 批量转换
        converted_count = 0
        for comment in comments:
            if text_field in comment and comment[text_field]:
                try:
                    comment[text_field] = converter.convert(comment[text_field])
                    converted_count += 1
                except Exception as e:
                    logger.debug(f"单条弹幕转换失败: {e}")
                    continue
        
        if converted_count > 0:
            logger.debug(f"弹幕繁简转换完成: {converted_count}/{len(comments)} 条 (mode={mode})")
        
        return comments
    
    except Exception as e:
        logger.error(f"批量弹幕转换失败 (mode={mode}): {e}", exc_info=True)
        return comments


async def get_effective_convert_mode(
    client_mode: int,
    config_service,
    priority_override: Optional[str] = None
) -> int:
    """
    根据配置和优先级计算最终的转换模式
    
    :param client_mode: 客户端请求的转换模式 (chConvert 参数)
    :param config_service: 配置管理器
    :param priority_override: 优先级覆盖（'player' 或 'server'）
    :return: 最终的转换模式 (0/1/2)
    """
    try:
        # 获取服务端配置
        server_mode = int(await config_service.get('danmakuChConvert', '0'))
        priority = priority_override or await config_service.get('danmakuChConvertPriority', 'player')
        
        # 根据优先级决定
        if priority == 'server':
            final_mode = server_mode
        else:  # priority == 'player'
            final_mode = client_mode if client_mode != 0 else server_mode
        
        logger.debug(
            f"繁简转换模式计算: client={client_mode}, server={server_mode}, "
            f"priority={priority}, final={final_mode}"
        )
        
        return final_mode
    
    except Exception as e:
        logger.error(f"计算转换模式失败: {e}", exc_info=True)
        return client_mode


def convert_title_t2s(title: str) -> str:
    """
    将标题从繁体转为简体（常用于处理港澳台搜索结果）
    
    :param title: 繁体标题
    :return: 简体标题
    """
    if not title:
        return title
    
    try:
        converter = _get_converter('t2s')
        return converter.convert(title)
    except Exception as e:
        logger.debug(f"标题繁转简失败: {e}")
        return title


def convert_title_s2t(title: str) -> str:
    """
    将标题从简体转为繁体
    
    :param title: 简体标题
    :return: 繁体标题
    """
    if not title:
        return title
    
    try:
        converter = _get_converter('s2t')
        return converter.convert(title)
    except Exception as e:
        logger.debug(f"标题简转繁失败: {e}")
        return title
