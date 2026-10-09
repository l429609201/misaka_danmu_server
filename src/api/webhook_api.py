import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.api.dependencies import get_webhook_service
from src.services.config_service import ConfigService
from src.services.webhook_service import UnknownWebhookTypeError, WebhookService
from src.workflows.webhook_dispatch import WebhookDispatchContext
from src.workflows.webhook_receive import receive_webhook_event

# 新增：获取专用的 webhook_raw 日志记录器
webhook_raw_logger = logging.getLogger("webhook_raw")

logger = logging.getLogger(__name__)
router = APIRouter()


async def get_config_service(request: Request) -> ConfigService:
    """从应用状态获取配置服务。"""
    return request.app.state.config_service


@router.post("/{webhook_type}", status_code=status.HTTP_202_ACCEPTED, summary="接收外部服务的Webhook通知")
async def handle_webhook(
    webhook_type: str,
    request: Request,
    api_key: str = Query(..., description="Webhook安全密钥"),
    config_service: ConfigService = Depends(get_config_service),
    webhook_service: WebhookService = Depends(get_webhook_service),
):
    """统一的Webhook入口，用于接收来自Sonarr, Radarr等服务的通知。"""
    # 通过 ConfigService 获取 webhookApiKey
    stored_key = await config_service.get("webhookApiKey", "")
    # 使用 secrets.compare_digest 防止时序攻击，并处理 stored_key 为空的情况
    if not stored_key or not secrets.compare_digest(api_key, stored_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的Webhook API Key")

    # 记录原始请求体
    log_raw_request = (await config_service.get("webhookLogRawRequest", "false")).lower() == 'true'
    if log_raw_request:
        # 为了让后续的 request.json() 能够工作，我们需要读取body后，再通过一个技巧将其“放回”请求流中
        raw_body = await request.body()
        webhook_raw_logger.info(f"Webhook 原始请求体 ({webhook_type}):\n{raw_body.decode(errors='ignore')}")
        
        async def receive():
            return {"type": "http.request", "body": raw_body}
        request._receive = receive

    try:
        state = request.app.state
        await receive_webhook_event(
            webhook_service, webhook_type, request,
            WebhookDispatchContext(
                task_manager=state.task_manager,
                scraper_manager=state.scraper_manager,
                metadata_manager=state.metadata_service,
                config_service=config_service,
                ai_service=state.ai_service,
                rate_limiter=state.rate_limiter,
                title_recognition_manager=state.title_recognition_manager,
                notification_service=state.notification_workflow,
            ),
        )
    except UnknownWebhookTypeError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except HTTPException:
        # 重新抛出处理器中产生的 HTTPException
        raise
    except Exception as e:
        # 捕获处理器中任何其他未预料到的错误
        logger.error(f"处理 Webhook '{webhook_type}' 时发生未知错误: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="处理 Webhook 时发生内部错误。")

    return {"message": "Webhook received and is being processed."}