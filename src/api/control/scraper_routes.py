"""
�ⲿ����API - ��ĻԴ���ù���·��
����: /scrapers, /scrapers/{provider}
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.services.service_container import get_database_service
from src.services.config_service import ConfigService
from src.services.scraper_manager import ScraperManager
from src.schemas.control import ControlActionResponse, ScraperConfigItem, ScraperConfigUpdate
from .dependencies import get_scraper_manager, get_config_service

logger = logging.getLogger(__name__)

router = APIRouter()


# --- �ӿ� ---

@router.get("/scrapers", response_model=List[ScraperConfigItem], summary="��ȡ���е�ĻԴ����")
async def get_all_scraper_configs(
    manager: ScraperManager = Depends(get_scraper_manager),
    config_service: ConfigService = Depends(get_config_service),
):
    """
    ��ȡ����**�Ѽ���**�ĵ�ĻԴ������Ϣ����������״̬���������ء��ּ�����������־���غ�������ʱ��
    ֻ����ʵ�ʼ��سɹ���Դ�����������ݿ��в�������Ч��¼�� 'custom' ����Դ��
    """
    db = get_database_service()
    async with db.transaction():
        # 服务代理同时暴露读写方法，不再访问废弃的独立查询域。
        all_settings = await db.scraper.get_all_scraper_settings()
    # ֻ����ʵ�ʼ�����ʵ����Դ������У�����ݿ� + �ڴ棩
    loaded_providers = set(manager.scrapers.keys())

    result = []
    for s in all_settings:
        name = s['providerName']
        if name == 'custom' or name not in loaded_providers:
            continue

        # �ּ�������
        blacklist = await config_service.get(f"{name}_episode_blacklist_regex", "")

        # ��¼ԭʼ��Ӧ
        log_resp = await config_service.get(f"scraper_{name}_log_responses", "false")
        log_resp_bool = str(log_resp).lower() == "true"

        # ������ʱ
        timeout = await config_service.get(f"scraper_{name}_search_timeout", "30")
        try:
            timeout_int = int(timeout)
        except (ValueError, TypeError):
            timeout_int = 30

        # ��Ϣ��ǿ����
        enrich_enabled = await config_service.get(f"scraper_{name}_enrich_enabled", "false")
        enrich_enabled_bool = str(enrich_enabled).lower() == "true"

        # ��Ϣ��ǿ�ֶ��б�
        enrich_fields = await config_service.get(f"scraper_{name}_enrich_fields", "")

        result.append(ScraperConfigItem(
            providerName=name,
            isEnabled=s.get('isEnabled', True),
            useProxy=s.get('useProxy', False),
            displayOrder=s.get('displayOrder', 0),
            episodeBlacklistRegex=str(blacklist),
            logRawResponses=log_resp_bool,
            searchTimeout=timeout_int,
            enrichEnabled=enrich_enabled_bool,
            enrichFields=str(enrich_fields),
        ))

    return result


@router.put("/scrapers/{provider}", response_model=ControlActionResponse, summary="���µ�����ĻԴ����")
async def update_scraper_config(
    provider: str,
    payload: ScraperConfigUpdate,
    manager: ScraperManager = Depends(get_scraper_manager),
    config_service: ConfigService = Depends(get_config_service),
):
    """
    ����ָ����ĻԴ�����á�ֻ�������������ṩ���ֶΣ�δ�ṩ���ֶα��ֲ��䡣

    ### �ɸ��µ�������
    - **useProxy**: �Ƿ����ô���
    - **episodeBlacklistRegex**: �ּ��������������
    - **logRawResponses**: �Ƿ��¼ԭʼ��Ӧ����־�ļ�
    - **searchTimeout**: ������ʱʱ��(��), ��Χ 1-120
    - **enrichEnabled**: ����ʱ�Ƿ���ȡ���鲹ȫȱʧ�ֶΣ���ݡ������ȣ�
    - **enrichFields**: ����ȫ�ֶ��б������ŷָ���Ϊ����̳�ȫ�����ã�
    """
    # ��֤Դ�Ƿ���ڣ����ݿ� + �ڴ�ʵ��˫У�飩
    db = get_database_service()
    async with db.transaction():
        scraper_setting = await db.scraper.get_scraper_setting_by_name(provider)
    if not scraper_setting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"��ĻԴ '{provider}' �����ڡ�")
    if provider not in manager.scrapers:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"��ĻԴ '{provider}' ���ݿ��м�¼��δ���أ�����Դ�ļ��ѱ��Ƴ���")

    updated_fields = []

    # ���´������ã�д scrapers ����
    if payload.useProxy is not None:
        async with db.transaction():
            await db.scraper.update_proxy(provider, payload.useProxy)
        updated_fields.append(f"useProxy={payload.useProxy}")

    # ���·ּ���������д config ����
    if payload.episodeBlacklistRegex is not None:
        key = f"{provider}_episode_blacklist_regex"
        await config_service.set(key, payload.episodeBlacklistRegex)
        updated_fields.append(f"episodeBlacklistRegex='{payload.episodeBlacklistRegex}'")

    # ������־���أ�д config ����
    if payload.logRawResponses is not None:
        key = f"scraper_{provider}_log_responses"
        await config_service.set(key, str(payload.logRawResponses).lower())
        updated_fields.append(f"logRawResponses={payload.logRawResponses}")

    # ����������ʱ��д config ����
    if payload.searchTimeout is not None:
        key = f"scraper_{provider}_search_timeout"
        await config_service.set(key, str(payload.searchTimeout))
        updated_fields.append(f"searchTimeout={payload.searchTimeout}")

    # ������Ϣ��ǿ���أ�д config ����
    if payload.enrichEnabled is not None:
        key = f"scraper_{provider}_enrich_enabled"
        await config_service.set(key, str(payload.enrichEnabled).lower())
        updated_fields.append(f"enrichEnabled={payload.enrichEnabled}")

    # ������Ϣ��ǿ�ֶ��б���д config ����
    if payload.enrichFields is not None:
        key = f"scraper_{provider}_enrich_fields"
        await config_service.set(key, payload.enrichFields)
        updated_fields.append(f"enrichFields='{payload.enrichFields}'")

    if not updated_fields:
        return {"message": "δ�ṩ�κ���Ҫ���µ��ֶΡ�"}

    logger.info(f"�ⲿAPI�����˵�ĻԴ '{provider}' ������: {', '.join(updated_fields)}")
    return {"message": f"��ĻԴ '{provider}' �����Ѹ���: {', '.join(updated_fields)}"}
