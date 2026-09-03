"""
通知模板 CRUD 操作 — 使用 config 表存储
"""
import json
import logging
from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.crud.config import get_config_value, set_config_value

logger = logging.getLogger(__name__)

# Config 表中的键前缀
TEMPLATE_KEY_PREFIX = "notification_template_"


def _make_template_key(template_id: str) -> str:
    """生成模板的 config key"""
    return f"{TEMPLATE_KEY_PREFIX}{template_id}"


async def get_notification_template(
    session: AsyncSession,
    template_id: str
) -> Optional[Dict[str, Any]]:
    """获取单个通知模板"""
    key = _make_template_key(template_id)
    value_str = await get_config_value(session, key, None)

    if value_str:
        try:
            data = json.loads(value_str)
            return {
                "templateId": template_id,
                "title": data.get("title", ""),
                "body": data.get("body", ""),
                "updatedAt": data.get("updatedAt"),
            }
        except json.JSONDecodeError:
            logger.error(f"模板配置解析失败: {template_id}")
            return None
    return None


async def get_all_notification_templates(
    session: AsyncSession
) -> List[Dict[str, Any]]:
    """获取所有通知模板"""
    from src.notification.template_resolver import TemplateResolver

    templates = []
    for template_id in TemplateResolver.get_all_template_ids():
        template = await get_notification_template(session, template_id)
        if template:
            templates.append(template)

    return templates


async def update_notification_template(
    session: AsyncSession,
    template_id: str,
    title: str,
    body: str
) -> bool:
    """更新通知模板"""
    from datetime import datetime, timezone

    key = _make_template_key(template_id)
    data = {
        "title": title,
        "body": body,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    value_str = json.dumps(data, ensure_ascii=False)

    await set_config_value(session, key, value_str)
    return True


async def ensure_default_templates(session: AsyncSession):
    """确保默认模板存在（首次启动时初始化）"""
    from src.notification.template_resolver import TemplateID

    default_templates = {
        TemplateID.DANMAKU_IMPORT: {
            "title": "{{ status_icon }} {{ action_name }}{{ status_name }}",
            "body": """**作品**: {{ anime_title }}
{% if season %}**季**: {{ season }}{% endif %}
{% if episode %}**集**: {{ episode }}{% endif %}
{% if provider %}**来源**: {{ provider }}{% endif %}
{% if comment_count %}**弹幕数**: {{ comment_count }}{% endif %}
{% if added_count %}**新增**: {{ added_count }}{% endif %}
{% if duration %}**耗时**: {{ duration }}秒{% endif %}
{% if error %}**错误**: {{ error }}{% endif %}""",
        },
        TemplateID.DANMAKU_REFRESH: {
            "title": "{{ status_icon }} {{ action_name }}{{ status_name }}",
            "body": """**作品**: {{ anime_title }}
{% if season %}**季**: {{ season }}{% endif %}
{% if episode_range %}**集数**: {{ episode_range }}{% endif %}
{% if trigger_name %}**触发**: {{ trigger_name }}{% endif %}
{% if comment_count %}**获取弹幕**: {{ comment_count }}{% endif %}
{% if added_count %}**新增**: {{ added_count }}{% endif %}
{% if success_count %}**成功**: {{ success_count }}{% endif %}
{% if failed_count %}**失败**: {{ failed_count }}{% endif %}
{% if duration %}**耗时**: {{ duration }}秒{% endif %}
{% if error %}**错误**: {{ error }}{% endif %}""",
        },
        TemplateID.FALLBACK_PROCESSING: {
            "title": "{{ status_icon }} {{ action_name }}{{ status_name }}",
            "body": """**作品**: {{ anime_title }}
{% if season %}**季**: {{ season }}{% endif %}
{% if episode %}**集**: {{ episode }}{% endif %}
**阶段**: {{ action_name }}
{% if success_count %}**成功**: {{ success_count }}{% endif %}
{% if failed_count %}**失败**: {{ failed_count }}{% endif %}
{% if message %}**说明**: {{ message }}{% endif %}
{% if error %}**错误**: {{ error }}{% endif %}""",
        },
        TemplateID.MEDIA_SCAN: {
            "title": "{{ status_icon }} {{ action_name }}{{ status_name }}",
            "body": """**媒体服务器**: {{ provider }}
**扫描数量**: {{ comment_count }}
{% if added_count %}**新增**: {{ added_count }}{% endif %}
{% if success_count %}**更新**: {{ success_count }}{% endif %}
{% if failed_count %}**失败**: {{ failed_count }}{% endif %}
{% if duration %}**耗时**: {{ duration }}秒{% endif %}
{% if error %}**错误**: {{ error }}{% endif %}""",
        },
        TemplateID.SYSTEM_NOTICE: {
            "title": "{{ status_icon }} 系统通知",
            "body": """{% if message %}{{ message }}{% endif %}
{% if error %}**错误信息**: {{ error }}{% endif %}""",
        },
    }

    for template_id, content in default_templates.items():
        existing = await get_notification_template(session, template_id)
        if not existing:
            await update_notification_template(
                session,
                template_id,
                content["title"],
                content["body"]
            )
            logger.info(f"已创建默认模板: {template_id}")
