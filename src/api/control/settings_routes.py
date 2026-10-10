"""
外部控制API - 设置和配置管理路由
包含: /settings/*, /config
"""

import logging
from typing import Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.services.service_container import get_database_service, get_title_recognition_service
from src.services.config_service import ConfigService

from src.schemas.control import (
    ControlActionResponse,
    ConfigItem,
    ConfigUpdateRequest,
    ConfigResponse,
    HelpResponse,
)
# 明确使用含合并输出字段的模型，避免同名导出覆盖接口契约。
from src.schemas.control.common import DanmakuOutputSettings
from .dependencies import get_config_service

logger = logging.getLogger(__name__)

router = APIRouter()


# --- 弹幕输出设置 ---

@router.get("/settings/danmaku-output", response_model=DanmakuOutputSettings, summary="获取弹幕输出设置")
async def get_danmaku_output_settings() -> DanmakuOutputSettings:
    """获取每个源的弹幕输出上限及合并输出开关。"""
    db = get_database_service()
    async with db.transaction():
        limit = await db.config.get_value('danmakuOutputLimitPerSource', '-1')
        merge_enabled = await db.config.get_value('danmakuMergeOutputEnabled', 'false')
    return DanmakuOutputSettings(limit_per_source=int(limit), merge_output_enabled=(merge_enabled.lower() == 'true'))


@router.put("/settings/danmaku-output", response_model=ControlActionResponse, summary="更新弹幕输出设置")
async def update_danmaku_output_settings(
    payload: DanmakuOutputSettings,
    config_service: ConfigService = Depends(get_config_service)
) -> dict[str, str]:
    """原子更新输出设置，提交成功后再失效配置缓存。"""
    config_values = {
        'danmakuOutputLimitPerSource': str(payload.limitPerSource),
        'danmakuMergeOutputEnabled': str(payload.mergeOutputEnabled).lower(),
    }
    db = get_database_service()
    # 自持短事务保证先提交再清缓存，避免并发请求回填尚未提交的旧值。
    async with db.transaction():
        await db.config.upsert_batch(config_values)
    for key in config_values:
        config_service.invalidate(key)
    return {"message": "弹幕输出设置已更新。"}


# --- 通用配置管理接口 ---

# 定义可通过外部API管理的配置项白名单
ALLOWED_CONFIG_KEYS = {
    # Webhook相关配置
    "webhookEnabled": {"type": "boolean", "description": "是否全局启用 Webhook 功能"},
    "webhookDelayedImportEnabled": {"type": "boolean", "description": "是否为 Webhook 触发的导入启用延时"},
    "webhookDelayedImportHours": {"type": "integer", "description": "Webhook 延时导入的小时数"},
    "webhookFilterMode": {"type": "string", "description": "Webhook 标题过滤模式 (blacklist/whitelist)"},
    "webhookFilterRegex": {"type": "string", "description": "用于过滤 Webhook 标题的正则表达式"},
    # 识别词配置
    "titleRecognition": {"type": "text", "description": "自定义识别词配置内容，支持屏蔽词、替换、集数偏移、季度偏移等规则"},
    # AI配置
    "aiMatchPrompt": {"type": "text", "description": "AI智能匹配提示词"},
    "aiRecognitionPrompt": {"type": "text", "description": "AI辅助识别提示词"},
    "aiAliasValidationPrompt": {"type": "text", "description": "AI别名验证提示词"},
    # 弹幕XML来源标签配置
    "danmakuSourceTagEnabled": {"type": "boolean", "description": "来源标签压缩开关。关闭（默认）：写原始 provider 名如 [bilibili]；开启：写别名（默认 [0]），可减少约10%文件体积"},
    "danmakuSourceTagAlias": {"type": "string", "description": "开关开启时使用的来源标签别名，默认为 0（即写 [0]）"},
}


@router.get("/config", response_model=Union[ConfigResponse, HelpResponse], summary="获取可配置的参数列表或帮助信息")
async def get_allowed_configs(
    type: Optional[str] = Query(None, description="请求类型，使用 'help' 获取可用配置项列表"),
    config_service: ConfigService = Depends(get_config_service),
    title_recognition_service=Depends(get_title_recognition_service)
):
    """
    获取所有可通过外部API管理的配置项及其当前值。

    参数:
    - type: 可选参数
      - 不提供或为空: 返回所有配置项及其当前值
      - "help": 返回所有可用的配置项键名列表
    """
    if type == "help":
        return HelpResponse(
            available_keys=list(ALLOWED_CONFIG_KEYS.keys()),
            description="可通过外部API管理的配置项列表。使用不带 type 参数的请求获取详细配置信息。"
        )

    configs = []

    for key, meta in ALLOWED_CONFIG_KEYS.items():
        if key == "titleRecognition":
            if title_recognition_service:
                current_value = await title_recognition_service.get_content()
            else:
                current_value = ""
        else:
            if meta["type"] == "boolean":
                default_value = "false"
            elif meta["type"] == "integer":
                default_value = "0"
            else:
                default_value = ""

            current_value = await config_service.get(key, default_value)

        configs.append(ConfigItem(
            key=key,
            value=str(current_value),
            type=meta["type"],
            description=meta["description"]
        ))

    return ConfigResponse(configs=configs)


@router.put("/config", status_code=status.HTTP_204_NO_CONTENT, summary="更新指定配置项")
async def update_config(
    request: ConfigUpdateRequest,
    config_service: ConfigService = Depends(get_config_service),
    title_recognition_service=Depends(get_title_recognition_service)
):
    """
    更新指定配置项。
    只允许更新白名单中定义的配置项。
    """
    if request.key not in ALLOWED_CONFIG_KEYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"配置项 '{request.key}' 不在允许的配置列表中。允许的配置项: {list(ALLOWED_CONFIG_KEYS.keys())}"
        )

    config_meta = ALLOWED_CONFIG_KEYS[request.key]

    if config_meta["type"] == "boolean":
        if request.value.lower() not in ["true", "false"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"配置项 '{request.key}' 的值必须是 'true' 或 'false'"
            )
    elif config_meta["type"] == "integer":
        try:
            int(request.value)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"配置项 '{request.key}' 的值必须是整数"
            )

    if request.key == "titleRecognition":
        if title_recognition_service is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="识别词管理器未初始化")

        warnings = await title_recognition_service.update_recognition_rules(request.value)
        if warnings:
            logger.warning(f"外部API更新识别词配置时发现 {len(warnings)} 个警告: {warnings}")

        logger.info(f"外部API更新了识别词配置，共 {len(await title_recognition_service.get_rules_snapshot())} 条规则")
    else:
        await config_service.set(request.key, request.value)
        logger.info(f"外部API更新了配置项 '{request.key}' 为 '{request.value}'")

    return

