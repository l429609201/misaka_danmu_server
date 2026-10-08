"""
�ⲿ����API - ���ú����ù���·��
����: /settings/*, /config
"""

import logging
from typing import Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.services.service_container import get_database_service
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
from .dependencies import get_config_service, get_title_recognition_manager

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


# --- ͨ�����ù����ӿ� ---

# �����ͨ���ⲿAPI�����������������
ALLOWED_CONFIG_KEYS = {
    # Webhook�������
    "webhookEnabled": {"type": "boolean", "description": "�Ƿ�ȫ������ Webhook ����"},
    "webhookDelayedImportEnabled": {"type": "boolean", "description": "�Ƿ�Ϊ Webhook �����ĵ���������ʱ"},
    "webhookDelayedImportHours": {"type": "integer", "description": "Webhook ��ʱ�����Сʱ��"},
    "webhookFilterMode": {"type": "string", "description": "Webhook �������ģʽ (blacklist/whitelist)"},
    "webhookFilterRegex": {"type": "string", "description": "���ڹ��� Webhook ������������ʽ"},
    # ʶ�������
    "titleRecognition": {"type": "text", "description": "�Զ���ʶ����������ݣ�֧�����δʡ��滻������ƫ�ơ�����ƫ�Ƶȹ���"},
    # AI����
    "aiMatchPrompt": {"type": "text", "description": "AI����ƥ����ʾ��"},
    "aiRecognitionPrompt": {"type": "text", "description": "AI����ʶ����ʾ��"},
    "aiAliasValidationPrompt": {"type": "text", "description": "AI������֤��ʾ��"},
    # ��ĻXML��Դ��ǩ����
    "danmakuSourceTagEnabled": {"type": "boolean", "description": "��Դ��ǩѹ�����ء��رգ�Ĭ�ϣ���дԭʼ provider ���� [bilibili]��������д������Ĭ�� [0]�����ɼ���Լ10%�ļ����"},
    "danmakuSourceTagAlias": {"type": "string", "description": "���ؿ���ʱʹ�õ���Դ��ǩ������Ĭ��Ϊ 0����д [0]��"},
}


@router.get("/config", response_model=Union[ConfigResponse, HelpResponse], summary="��ȡ�����õĲ����б��������Ϣ")
async def get_allowed_configs(
    type: Optional[str] = Query(None, description="�������ͣ�ʹ�� 'help' ��ȡ�����������б�"),
    config_service: ConfigService = Depends(get_config_service),
    title_recognition_manager=Depends(get_title_recognition_manager)
):
    """
    ��ȡ���п�ͨ���ⲿAPI������������䵱ǰֵ��

    ����:
    - type: ��ѡ����
      - ���ṩ��Ϊ��: ��������������䵱ǰֵ
      - "help": �������п��õ�����������б�
    """
    if type == "help":
        return HelpResponse(
            available_keys=list(ALLOWED_CONFIG_KEYS.keys()),
            description="��ͨ���ⲿAPI�������������б���ʹ�ò��� type �����������ȡ��ϸ������Ϣ��"
        )

    configs = []

    for key, meta in ALLOWED_CONFIG_KEYS.items():
        if key == "titleRecognition":
            if title_recognition_manager:
                # 通过服务层读取识别词，保留未配置时返回空字符串的语义。
                db = get_database_service()
                async with db.transaction():
                    current_value = await db.title_recognition.get_content()
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


@router.put("/config", status_code=status.HTTP_204_NO_CONTENT, summary="����ָ��������")
async def update_config(
    request: ConfigUpdateRequest,
    config_service: ConfigService = Depends(get_config_service),
    title_recognition_manager=Depends(get_title_recognition_manager)
):
    """
    ����ָ���������
    ֻ�������°������ж���������
    """
    if request.key not in ALLOWED_CONFIG_KEYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"������ '{request.key}' ���������������б��С�������������: {list(ALLOWED_CONFIG_KEYS.keys())}"
        )

    config_meta = ALLOWED_CONFIG_KEYS[request.key]

    if config_meta["type"] == "boolean":
        if request.value.lower() not in ["true", "false"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"������ '{request.key}' ��ֵ������ 'true' �� 'false'"
            )
    elif config_meta["type"] == "integer":
        try:
            int(request.value)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"������ '{request.key}' ��ֵ����������"
            )

    if request.key == "titleRecognition":
        if title_recognition_manager is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="ʶ��ʹ�����δ��ʼ��")

        warnings = await title_recognition_manager.update_recognition_rules(request.value)
        if warnings:
            logger.warning(f"�ⲿAPI����ʶ�������ʱ���� {len(warnings)} ������: {warnings}")

        logger.info(f"�ⲿAPI������ʶ������ã��� {len(title_recognition_manager.recognition_rules)} ������")
    else:
        await config_service.set(request.key, request.value)
        logger.info(f"�ⲿAPI������������ '{request.key}' Ϊ '{request.value}'")

    return

