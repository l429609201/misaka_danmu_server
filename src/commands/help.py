"""
帮助指令模块
提供 @HELP 或 @ 指令，展示所有可用指令
"""
import logging
from typing import List

from src.schemas.dandan import DandanSearchAnimeResponse
from .base import CommandHandler, COMMAND_HANDLERS

logger = logging.getLogger(__name__)


class HelpCommand(CommandHandler):
    """帮助指令 - 展示所有可用指令"""

    def __init__(self):
        super().__init__(
            name="HELP",
            description="展示所有可用指令及说明",
            cooldown_seconds=0,  # 无冷却
            usage="@ 或 @HELP (支持大小写)",
            examples=["@", "@HELP", "@help"]
        )

    async def execute(self, token: str, args: List[str], session: object,
                     config_service, **kwargs) -> "DandanSearchAnimeResponse":
        """展示所有可用指令"""
        image_url = await self.get_image_url(config_service)
        all_handlers = COMMAND_HANDLERS
        
        # 收集所有指令（排除 HELP 自己）
        commands_list = []
        for cmd_name, handler in all_handlers.items():
            if cmd_name == "HELP":
                continue
            commands_list.append({
                "name": cmd_name,
                "description": handler.description,
                "cooldown": handler.cooldown_seconds,
                "usage": handler.usage,
                "examples": handler.examples
            })
        
        # 第一条：引导说明
        anime_items = [
            self.build_response_item(
                anime_id=999999900,
                title="📖 可用指令列表",
                description=f"当前系统共有 {len(commands_list)} 个可用指令:\n\n"
                           f"✅ 所有指令支持大小写 (@SXDM = @sxdm)\n"
                           f"💡 直接在搜索框输入 @指令名 即可使用\n"
                           f"💡 例如: @sxdm 或 @SXDM 刷新弹幕",
                image_url=image_url,
                episodeCount=len(commands_list)
            )
        ]
        
        # 第二条开始：每个指令一个独立的 item
        for cmd in commands_list:
            cooldown_text = f"⏱ 冷却: {cmd['cooldown']}秒" if cmd['cooldown'] > 0 else "⚡ 无冷却限制"
            
            # 构建详细描述
            description_parts = [cmd['description'], "", cooldown_text]
            
            # 添加使用说明
            if cmd['usage']:
                description_parts.append(f"📝 用法: {cmd['usage']}")
            
            # 添加示例
            if cmd['examples']:
                description_parts.append("📌 示例:")
                for example in cmd['examples']:
                    description_parts.append(f"  • {example}")
            
            anime_items.append(
                self.build_response_item(
                    anime_id=999999901,
                    title=f"@{cmd['name']}",
                    description="\n".join(description_parts),
                    image_url=image_url
                )
            )
        
        logger.info(f"@HELP 返回指令列表: 共 {len(commands_list)} 个指令")
        
        return self.build_response(anime_items)

