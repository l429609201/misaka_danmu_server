"""
通知配置 V2 迁移脚本

将旧版 eventsConfig 迁移到新版结构：
- 旧版：{"event_name": true/false}
- 新版：{"version": 2, "scopes": {"scope_key": true/false}}
"""
import logging
from typing import Dict, Any

from src.notification.subscription_matcher import ScopeKey

logger = logging.getLogger(__name__)


def migrate_notification_event_config_v2(old_config: Dict[str, Any]) -> Dict[str, Any]:
    """迁移旧版通知配置到 V2
    
    Args:
        old_config: 旧版 eventsConfig 字典
        
    Returns:
        新版配置字典 {"version": 2, "scopes": {...}}
    """
    # 如果已经是 V2 配置，直接返回
    if isinstance(old_config, dict) and old_config.get("version") == 2:
        return old_config
    
    # 如果配置为空或格式错误，返回默认配置
    if not old_config or not isinstance(old_config, dict):
        logger.warning("空配置或非法 JSON，迁移为全部关闭")
        return {
            "version": 2,
            "scopes": {key: False for key in _get_all_scope_keys()}
        }
    
    # 开始迁移映射
    new_scopes = {}
    
    # 弹幕导入：import_success, import_failed, auto_import_*, webhook_import_*
    new_scopes[ScopeKey.IMPORT_SUCCESS] = (
        old_config.get("import_success", False) or
        old_config.get("auto_import_success", False) or
        old_config.get("webhook_import_success", False)
    )
    new_scopes[ScopeKey.IMPORT_FAILED] = (
        old_config.get("import_failed", False) or
        old_config.get("auto_import_failed", False) or
        old_config.get("webhook_import_failed", False)
    )
    
    # 弹幕刷新：refresh_success, refresh_failed
    new_scopes[ScopeKey.REFRESH_SUCCESS] = old_config.get("refresh_success", False)
    new_scopes[ScopeKey.REFRESH_FAILED] = old_config.get("refresh_failed", False)
    
    # 自动追更：incremental_refresh_*
    new_scopes[ScopeKey.INCREMENTAL_REFRESH_SUCCESS] = old_config.get("incremental_refresh_success", False)
    new_scopes[ScopeKey.INCREMENTAL_REFRESH_NO_CHANGE] = False  # 旧版没有此项，默认关闭
    new_scopes[ScopeKey.INCREMENTAL_REFRESH_FAILED] = old_config.get("incremental_refresh_failed", False)
    
    # 后备处理：download_fallback_*, fallback_search_*, predownload_*, match_fallback_*
    new_scopes[ScopeKey.FALLBACK_SUCCESS] = (
        old_config.get("download_fallback_success", False) or
        old_config.get("fallback_search_success", False) or
        old_config.get("predownload_success", False) or
        old_config.get("match_fallback_success", False) or
        old_config.get("download_fallback_complete", False) or  # 兼容旧合并键
        old_config.get("fallback_search_complete", False) or
        old_config.get("predownload_complete", False) or
        old_config.get("match_fallback_complete", False)
    )
    new_scopes[ScopeKey.FALLBACK_FAILED] = (
        old_config.get("download_fallback_failed", False) or
        old_config.get("fallback_search_failed", False) or
        old_config.get("predownload_failed", False) or
        old_config.get("match_fallback_failed", False)
    )
    
    # 媒体库扫描：media_scan_complete
    new_scopes[ScopeKey.MEDIA_SCAN_SUCCESS] = old_config.get("media_scan_complete", False)
    new_scopes[ScopeKey.MEDIA_SCAN_FAILED] = False  # 旧版只有成功通知
    
    # 系统通知：system_start
    new_scopes[ScopeKey.SYSTEM_STARTUP] = old_config.get("system_start", False)
    new_scopes[ScopeKey.SYSTEM_EXCEPTION] = True  # 默认开启系统异常通知
    
    # 定时任务按实际业务归类（scheduled_task_* 根据内容可能属于多种业务）
    # 这里无法精确分类，暂不迁移，保持关闭状态
    
    # webhook_triggered 已取消，不迁移
    # task_progress 已取消，Telegram 常态化实时进度
    
    logger.info(f"通知配置迁移完成: {len([v for v in new_scopes.values() if v])} 项已启用")
    
    return {
        "version": 2,
        "scopes": new_scopes
    }


def _get_all_scope_keys() -> list:
    """获取所有发送范围键"""
    return [
        ScopeKey.IMPORT_SUCCESS,
        ScopeKey.IMPORT_FAILED,
        ScopeKey.REFRESH_SUCCESS,
        ScopeKey.REFRESH_FAILED,
        ScopeKey.INCREMENTAL_REFRESH_SUCCESS,
        ScopeKey.INCREMENTAL_REFRESH_NO_CHANGE,
        ScopeKey.INCREMENTAL_REFRESH_FAILED,
        ScopeKey.FALLBACK_SUCCESS,
        ScopeKey.FALLBACK_FAILED,
        ScopeKey.MEDIA_SCAN_SUCCESS,
        ScopeKey.MEDIA_SCAN_FAILED,
        ScopeKey.SYSTEM_STARTUP,
        ScopeKey.SYSTEM_EXCEPTION,
    ]


async def migrate_all_channels_to_v2(session):
    """将旧渠道订阅配置一次性重置为 V2 全关闭。

    why：旧事件名与新发送范围并非一一对应，继续维护映射只会引入歧义。
    已经是 V2 的配置必须跳过，避免应用每次启动都清空用户的新设置。
    """
    from src.db.crud import notification as crud_notification

    channels = await crud_notification.get_all_notification_channels(session)
    disabled_config = {
        "version": 2,
        "scopes": {key: False for key in _get_all_scope_keys()},
    }
    reset_count = 0

    for channel in channels:
        current_config = channel.get("eventsConfig")
        if isinstance(current_config, dict) and current_config.get("version") == 2:
            continue

        await crud_notification.update_notification_channel(
            session,
            channel["id"],
            events_config=disabled_config,
        )
        reset_count += 1
        logger.info(
            "渠道 %s (%s) 的旧订阅配置已重置为 V2 全关闭",
            channel["id"],
            channel.get("name", "Unknown"),
        )

    if reset_count:
        await session.commit()
    logger.info("通知订阅配置重置完成: %s 个渠道已更新", reset_count)

