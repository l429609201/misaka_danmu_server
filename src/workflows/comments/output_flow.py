"""统一弹幕输出配置编排，不修改原始缓存或持久化 XML。"""

from typing import Any, Dict, List

from src.schemas.comments import Comment
from src.utils.danmaku.p_fields import normalize_p_attr
from src.utils.misc.common import (
    handle_danmaku_likes, restyle_danmaku_likes, sample_comments_evenly,
    strip_danmaku_likes,
)
from src.utils.misc.converter import convert_comments
from src.utils.parsing.danmaku_color import (
    apply_random_color, apply_repeat_highlight, parse_color_palette,
)
from src.utils.parsing.danmaku_filter import apply_blacklist_filter
from src.utils.parsing.danmaku_mode import convert_danmaku_position
from src.workflows.comments.helpers import process_comments_for_dandanplay


async def apply_output_config(
    comments_data: List[Dict[str, Any]],
    config_service: Any,
    ch_convert: int = 0,
    fire_threshold: int = 1000,
) -> List[Comment]:
    """每次请求应用显示配置并生成 API 弹幕；配置读取错误向调用方传播。"""
    # 点赞和简繁工具会原地修改，必须隔离缓存及调用方持有的数据。
    comments = [dict(item, p=normalize_p_attr(item.get('p', ''))) for item in comments_data]
    limit = int(await config_service.get('danmakuOutputLimitPerSource', '-1'))
    if limit > 0:
        comments = sample_comments_evenly(comments, limit)
    if str(await config_service.get('danmakuBlacklistEnabled', 'false')).lower() == 'true':
        comments = apply_blacklist_filter(
            comments, await config_service.get('danmakuBlacklistPatterns', ''),
        )
    likes_enabled = str(await config_service.get('danmakuLikesOutputEnabled', 'true')).lower() == 'true'
    likes_style = await config_service.get('danmakuLikesStyle', 'heart_white')
    if not likes_enabled or likes_style == 'off':
        comments = handle_danmaku_likes(comments, enabled=False)
        comments = strip_danmaku_likes(comments)
    else:
        comments = restyle_danmaku_likes(comments, likes_style)
        comments = handle_danmaku_likes(comments, fire_threshold, style=likes_style)
    palette = parse_color_palette(await config_service.get('danmakuRandomColorPalette', ''))
    comments = apply_random_color(
        comments, await config_service.get('danmakuRandomColorMode', 'off'), palette,
    )
    comments = apply_repeat_highlight(comments, palette_list=palette)
    # 输出配置不可由转换辅助器捕获后静默回退，读取失败必须传播。
    server_convert = int(await config_service.get('danmakuChConvert', '0'))
    priority = await config_service.get('danmakuChConvertPriority', 'player')
    effective_convert = server_convert if priority == 'server' else (ch_convert or server_convert)
    comments = convert_comments(comments, effective_convert)
    comments = convert_danmaku_position(
        comments,
        await config_service.get('danmakuTopConvertTo', 'none'),
        await config_service.get('danmakuBottomConvertTo', 'none'),
    )
    return process_comments_for_dandanplay(comments)
