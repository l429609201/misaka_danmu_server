"""根据通知渠道配置选择并应用 Webhook 隧道的业务流程。"""

from typing import Any

from src.services.tunnel_service import TunnelService


async def apply_tunnel_from_notification_manager(
    tunnel_service: TunnelService,
    notification_manager: Any,
    config_service: Any,
    local_port: int,
) -> None:
    """选择第一个启用隧道的 Webhook 渠道，配置改变时应用隧道。"""
    vps_proxy_url = ""
    tunnel_enabled = False

    for channel in notification_manager.get_all_channels().values():
        channel_type = getattr(channel, "channel_type", "")
        config = channel.config
        if str(config.get("tunnel_enabled", "false")).lower() not in ("true", "1", "yes"):
            continue
        if channel_type == "wechat":
            url = config.get("wecom_proxy", "").strip()
        elif channel_type in ("telegram", "serverchan"):
            if config.get("mode", "polling") != "webhook":
                continue
            url = config.get("webhook_base_url", "").strip()
        else:
            continue
        if url:
            vps_proxy_url = url
            tunnel_enabled = True
            break

    webhook_key = await config_service.get("webhookApiKey", "")
    changed = tunnel_service.configure(
        enabled=tunnel_enabled,
        vps_proxy_url=vps_proxy_url,
        webhook_key=webhook_key,
        local_port=local_port,
    )
    if changed:
        await tunnel_service.apply()
