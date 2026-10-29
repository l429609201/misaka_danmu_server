"""OAuth 处理器组合入口，只有启动组合根导入具体平台。"""

from src.oauth.providers.bangumi import BangumiOAuthProvider
from src.oauth.providers.trakt import TraktOAuthProvider
from src.services.oauth_service import OAuthService


def register_oauth_providers(service: OAuthService) -> None:
    """注册应用支持的平台，不在通用服务中自动发现或导入实现。"""
    service.register_provider("bangumi", BangumiOAuthProvider())
    service.register_provider("trakt", TraktOAuthProvider())
