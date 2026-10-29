"""
通知渠道管理相关的 Pydantic 模型

从 src/api/ui/notification_routes.py 迁移而来
"""

from typing import Any, Dict, Optional
from pydantic import BaseModel


class ChannelCreate(BaseModel):
    """创建通知渠道的请求模型"""
    name: str
    channelType: str
    isEnabled: bool = True
    useProxy: bool = False
    config: Dict[str, Any] = {}
    eventsConfig: Dict[str, Any] = {}


class ChannelUpdate(BaseModel):
    """更新通知渠道的请求模型"""
    name: Optional[str] = None
    channelType: Optional[str] = None
    isEnabled: Optional[bool] = None
    useProxy: Optional[bool] = None
    config: Optional[Dict[str, Any]] = None
    eventsConfig: Optional[Dict[str, Any]] = None
