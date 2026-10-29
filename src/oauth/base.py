"""OAuth 平台处理器契约；具体协议不依赖 HTTP 路由或服务实例。"""

from abc import ABC, abstractmethod
from typing import Any


class OAuthProvider(ABC):
    """平台注册后由统一服务调度，不管理数据库事务。"""

    name: str
    refresh_days: int = 3

    @abstractmethod
    async def authorize_url(self, redirect_uri: str, state: str) -> str:
        """返回平台授权地址。"""

    @abstractmethod
    async def exchange(self, payload: dict[str, Any]) -> dict[str, Any]:
        """验证远端授权并返回凭证快照。"""

    async def remaining_seconds(self, credentials: dict[str, Any]) -> int | None:
        """可选远端有效期查询；默认交由服务使用本地到期时间。"""
        return None

    @abstractmethod
    async def refresh(self, credentials: dict[str, Any]) -> dict[str, Any]:
        """轮换远端凭证并返回更新字段。"""
