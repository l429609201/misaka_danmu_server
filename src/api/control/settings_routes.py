"""
外部控制API - 设置和配置管理路由
包含: /settings/*, /config
"""

import logging
from typing import List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import crud, get_db_session, ConfigManager

from .models import ControlActionResponse, DanmakuOutputSettings
from .dependencies import get_config_manager, get_title_recognition_manager

logger = logging.getLogger(__name__)

router = APIRouter()


# --- 设置管理 ---

@router.get("/settings/danmaku-output", response_model=DanmakuOutputSettings, summary="获取弹幕输出设置")
async def get_danmaku_output_settings(session: AsyncSession = Depends(get_db_session)):
    """获取全局的弹幕输出设置，如输出上限和是否合并输出。"""
    limit = await crud.get_config_value(session, 'danmakuOutputLimitPerSource', '-1')
    merge_enabled = await crud.get_config_value(session, 'danmakuMergeOutputEnabled', 'false')
    return DanmakuOutputSettings(limit_per_source=int(limit), merge_output_enabled=(merge_enabled.lower() == 'true'))


@router.put("/settings/danmaku-output", response_model=ControlActionResponse, summary="更新弹幕输出设置")
async def update_danmaku_output_settings(
    payload: DanmakuOutputSettings,
    session: AsyncSession = Depends(get_db_session),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """更新全局的弹幕输出设置，包括输出上限和合并输出选项。"""
    config_values = {
        'danmakuOutputLimitPerSource': str(payload.limitPerSource),
        'danmakuMergeOutputEnabled': str(payload.mergeOutputEnabled).lower(),
    }
    await crud.update_config_values_atomic(session, config_values)
    for key in config_values:
        config_manager.invalidate(key)
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
}


class ConfigItem(BaseModel):
    key: str
    value: str
    type: str
    description: str


class ConfigUpdateRequest(BaseModel):
    key: str
    value: str


class ConfigResponse(BaseModel):
    configs: List[ConfigItem]


class HelpResponse(BaseModel):
    available_keys: List[str]
    description: str


@router.get("/config", response_model=Union[ConfigResponse, HelpResponse], summary="获取可配置的参数列表或帮助信息")
async def get_allowed_configs(
    type: Optional[str] = Query(None, description="请求类型，使用 'help' 获取可用配置项列表"),
    config_manager: ConfigManager = Depends(get_config_manager),
    title_recognition_manager=Depends(get_title_recognition_manager),
    session: AsyncSession = Depends(get_db_session)
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
            if title_recognition_manager:
                from src.db import orm_models
                result = await session.execute(select(orm_models.TitleRecognition).limit(1))
                title_recognition = result.scalar_one_or_none()
                current_value = title_recognition.content if title_recognition else ""
            else:
                current_value = ""
        else:
            if meta["type"] == "boolean":
                default_value = "false"
            elif meta["type"] == "integer":
                default_value = "0"
            else:
                default_value = ""

            current_value = await config_manager.get(key, default_value)

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
    config_manager: ConfigManager = Depends(get_config_manager),
    title_recognition_manager=Depends(get_title_recognition_manager)
):
    """
    更新指定的配置项。
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
        if title_recognition_manager is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="识别词管理器未初始化")

        warnings = await title_recognition_manager.update_recognition_rules(request.value)
        if warnings:
            logger.warning(f"外部API更新识别词配置时发现 {len(warnings)} 个警告: {warnings}")

        logger.info(f"外部API更新了识别词配置，共 {len(title_recognition_manager.recognition_rules)} 条规则")
    else:
        await config_manager.setValue(request.key, request.value)
        logger.info(f"外部API更新了配置项 '{request.key}' 为 '{request.value}'")

    return

