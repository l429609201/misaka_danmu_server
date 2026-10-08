"""初始管理员用户初始化服务。"""

import logging
import secrets
import string
from collections.abc import Callable
from typing import Optional

from src.services.database_service import DatabaseService

logger = logging.getLogger(__name__)


def _generate_initial_password() -> str:
    """生成兼容旧启动流程的 16 位随机管理员密码。"""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(16))


async def create_initial_admin_user(
    database_service: DatabaseService,
    username: Optional[str],
    password: Optional[str],
    hash_password: Callable[[str], str],
) -> None:
    """按配置创建不存在的初始管理员用户，并由服务事务统一提交。"""
    if not username:
        return

    async with database_service.transaction():
        existing_user = await database_service.user.get_by_username(username)
        if existing_user:
            logger.info("管理员用户 '%s' 已存在，跳过创建。", username)
            return

        generated = not password
        initial_password = password or _generate_initial_password()
        await database_service.user.create(username, hash_password(initial_password))

    logger.info("初始管理员账户已创建 (用户: %s)", username)
    if generated:
        # 随机密码只在首次启动控制台展示一次，不进入日志系统。
        print("\n" + "=" * 60)
        print(f"=== 初始管理员账户已创建 (用户: {username}) ".ljust(56) + "===")
        print(f"=== 请使用以下随机生成的密码登录: {initial_password} ".ljust(56) + "===")
        print("=" * 60 + "\n")
