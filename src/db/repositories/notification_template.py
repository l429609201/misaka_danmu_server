"""
NotificationTemplateRepository - 通知模板数据访问层

通知模板使用 config 表存储，本 Repository 通过 ConfigRepository 访问底层数据。
"""

import json
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from .config import ConfigRepository
from src.schemas.notification_template import DEFAULT_PROGRESS_BODY, DEFAULT_PROGRESS_TITLE, TemplateID

logger = logging.getLogger(__name__)

# Config 表中的键前缀
TEMPLATE_KEY_PREFIX = "notification_template_"


def _make_template_key(template_id: str) -> str:
    """生成模板的 config key"""
    return f"{TEMPLATE_KEY_PREFIX}{template_id}"


class NotificationTemplateRepository:
    """
    通知模板 Repository
    
    职责：
    - 管理通知模板的增删改查
    - 通过 ConfigRepository 访问底层配置表
    - 处理模板的 JSON 序列化/反序列化
    """
    
    def __init__(self, session: AsyncSession):
        """
        初始化 NotificationTemplateRepository
        
        Args:
            session: 数据库会话
        """
        self._session = session
        self._config_repo = ConfigRepository(session)
    
    async def get_by_id(self, template_id: str) -> Optional[Dict[str, Any]]:
        """
        获取单个通知模板
        
        Args:
            template_id: 模板 ID
            
        Returns:
            模板字典，包含 templateId, title, body, updatedAt；不存在返回 None
        """
        key = _make_template_key(template_id)
        value_str = await self._config_repo.get_value(key, None)
        
        if value_str:
            try:
                data = json.loads(value_str)
                return {
                    "templateId": template_id,
                    "title": data.get("title", ""),
                    "body": data.get("body", ""),
                    "imageEnabled": data.get("imageEnabled", True),
                    "updatedAt": data.get("updatedAt"),
                }
            except json.JSONDecodeError:
                logger.error(f"模板配置解析失败: {template_id}")
                return None
        return None
    
    async def get_all(self) -> List[Dict[str, Any]]:
        """
        获取所有通知模板
        
        Returns:
            模板字典列表
        """

        
        templates = []
        for template_id in TemplateID.ALL:
            template = await self.get_by_id(template_id)
            if template:
                templates.append(template)
        
        return templates
    
    async def upsert(
        self, 
        template_id: str, 
        title: str, 
        body: str,
        image_enabled: bool = True,
    ) -> bool:
        """
        插入或更新通知模板
        
        Args:
            template_id: 模板 ID
            title: 模板标题
            body: 模板正文
            
        Returns:
            是否成功
        """
        key = _make_template_key(template_id)
        data = {
            "title": title,
            "body": body,
            "imageEnabled": image_enabled,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        value_str = json.dumps(data, ensure_ascii=False)
        
        await self._config_repo.upsert(key, value_str)
        return True
    
    async def ensure_defaults(self) -> None:
        """
        确保默认模板存在（首次启动时初始化）
        
        如果数据库中不存在某个默认模板，则创建它。
        """

        
        default_templates = {
            TemplateID.TASK_PROGRESS: {
                "title": DEFAULT_PROGRESS_TITLE,
                "body": DEFAULT_PROGRESS_BODY,
                "imageEnabled": False,
            },
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

        # 仅创建不存在的默认模板
        for template_id, content in default_templates.items():
            existing = await self.get_by_id(template_id)
            if not existing:
                await self.upsert(
                    template_id,
                    content["title"],
                    content["body"],
                    content.get("imageEnabled", True),
                )
                logger.info(f"已创建默认模板: {template_id}")
