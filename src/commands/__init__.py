"""
指令系统模块
提供命令的自动加载和注册功能
"""
import logging
from typing import Dict, Optional, List, Any

from src.schemas.dandan import DandanSearchAnimeResponse, DandanSearchAnimeItem
from src.workflows.image_public_url import get_custom_domain
from .base import CommandHandler, COMMAND_HANDLERS, parse_command
from .clear_cache import ClearCacheCommand
from .help import HelpCommand
from .rate_limit_status import RateLimitStatusCommand
from .refresh_danmaku import RefreshDanmakuCommand
from .task_status import TaskStatusCommand

logger = logging.getLogger(__name__)

_COMMAND_HANDLERS = COMMAND_HANDLERS


def _load_commands() -> None:
    """注册内建播放器指令。"""
    if _COMMAND_HANDLERS:
        return
    for handler_class in (
        ClearCacheCommand, HelpCommand, RateLimitStatusCommand,
        RefreshDanmakuCommand, TaskStatusCommand,
    ):
        handler = handler_class()
        _COMMAND_HANDLERS[handler.name] = handler
        logger.info("已加载命令处理器: @%s", handler.name)
    logger.info("命令系统初始化完成，共加载 %d 个命令", len(_COMMAND_HANDLERS))


def get_all_handlers() -> Dict[str, CommandHandler]:
    """
    获取所有已注册的命令处理器
    
    Returns:
        命令名称到处理器的映射字典
    """
    if not _COMMAND_HANDLERS:
        _load_commands()
    
    return _COMMAND_HANDLERS


def get_handler(command_name: str) -> Optional[CommandHandler]:
    """
    获取指定名称的命令处理器
    
    Args:
        command_name: 命令名称（大写）
        
    Returns:
        命令处理器实例，如果不存在则返回 None
    """
    handlers = get_all_handlers()
    return handlers.get(command_name.upper())


async def handle_command(search_term: str, token: str, session: object,
                        config_service, **kwargs) -> Optional["DandanSearchAnimeResponse"]:
    """
    处理指令

    Args:
        search_term: 搜索词
        token: 用户token
        session: 保留的兼容参数，命令通过服务管理事务
        config_service: 配置管理器
        **kwargs: 其他依赖

    Returns:
        指令响应 或 None（不是指令）
    """
    # 解析指令
    parsed = parse_command(search_term)
    if not parsed:
        return None
    
    command_name, args = parsed
    
    # 确保命令已加载
    handlers = get_all_handlers()
    handler = handlers.get(command_name)
    
    # 获取自定义域名和图片URL（http/https 均支持，格式不合规时降级）
    custom_domain = await get_custom_domain(config_service)
    image_url = f"{custom_domain}/static/logo.png" if custom_domain else "/static/logo.png"
    
    if not handler:
        # 未知指令
        logger.warning(f"未知指令: @{command_name}, token={token}")
        
        return DandanSearchAnimeResponse(animes=[
            DandanSearchAnimeItem(
                animeId=999999998,
                bangumiId="999999998",
                animeTitle=f"✗ 未知指令: @{command_name}",
                type="other",
                typeDescription=f"该指令不存在\n\n💡 提示：输入 @ 或 @HELP 查看所有可用指令",
                imageUrl=image_url,
                startDate="2025-01-01T00:00:00+08:00",
                year=2025,
                episodeCount=0,
                rating=0.0,
                isFavorited=False
            )
        ])
    
    # 检查频率限制
    can_exec, remaining = await handler.can_execute(token, session)
    if not can_exec:
        logger.info(f"指令 @{command_name} 冷却中, token={token}, 剩余{remaining}秒")
        
        return DandanSearchAnimeResponse(animes=[
            DandanSearchAnimeItem(
                animeId=999999998,
                bangumiId="999999998",
                animeTitle=f"⏱ 指令冷却中",
                type="other",
                typeDescription=f"你已在 {handler.cooldown_seconds} 秒内触发过 @{command_name} 指令，还有 {remaining} 秒才能再次使用",
                imageUrl=image_url,
                startDate="2025-01-01T00:00:00+08:00",
                year=2025,
                episodeCount=0,
                rating=0.0,
                isFavorited=False
            )
        ])
    
    # 执行指令
    return await handler.execute(token, args, session, config_service, **kwargs)


# 导出公共接口
__all__ = [
    'CommandHandler',
    'parse_command',
    'get_all_handlers',
    'get_handler',
    'handle_command',
]

