"""
紧急重置 MFA（两步验证 / PassKey）工具

用于用户无法通过 MFA 验证登录时的紧急恢复：
  python -m src.reset_mfa admin          # 同时清空 TOTP 和 PassKey
  python -m src.reset_mfa admin --totp   # 仅清空 TOTP
  python -m src.reset_mfa admin --passkey # 仅清空 PassKey
"""

import asyncio
import argparse
import sys
from pathlib import Path

# 将项目根目录添加到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from src.db import _get_db_url
from src.db.orm_models import UserPassKey
from src.db.repositories import UserRepository
from src.core import settings


async def reset_mfa(username: str, reset_totp: bool = True, reset_passkey: bool = True):
    """
    为指定用户清空 MFA 设置。
    """
    try:
        db_url = _get_db_url()
    except ValueError as e:
        print(f"❌ {e}")
        return

    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as session:
        # 本脚本自建 engine，不走 DatabaseService，故直接实例化仓储并显式提交
        user_repo = UserRepository(session)

        # 检查用户是否存在
        user = await user_repo.get_by_username(username)
        if not user:
            print(f"❌ 错误: 未找到用户 '{username}'。")
            await engine.dispose()
            return

        actions = []

        # 清空 TOTP
        if reset_totp:
            await user_repo.disable_otp(username)
            actions.append("TOTP 两步验证")

        # 清空 PassKey
        if reset_passkey:
            # 仓储返回 ORM 对象（原 crud 返回字典），故改用属性访问
            stmt = delete(UserPassKey).where(UserPassKey.userId == user.id)
            result = await session.execute(stmt)
            count = result.rowcount
            actions.append(f"PassKey ({count} 个)")

        await session.commit()

        print("\n" + "=" * 60)
        print("✅ MFA 重置成功！")
        print(f"   - 用户名: {username}")
        for action in actions:
            print(f"   - 已清空: {action}")
        print("=" * 60)
        print("\n现在可以仅使用密码登录了。")

    await engine.dispose()


def main():
    parser = argparse.ArgumentParser(
        description="紧急重置用户的 MFA（两步验证 / PassKey）设置。"
    )
    parser.add_argument("username", help="要重置 MFA 的用户名。")
    parser.add_argument("--totp", action="store_true", help="仅清空 TOTP 两步验证")
    parser.add_argument("--passkey", action="store_true", help="仅清空 PassKey")
    args = parser.parse_args()

    # 如果都没指定，则全部清空
    if not args.totp and not args.passkey:
        reset_totp = True
        reset_passkey = True
    else:
        reset_totp = args.totp
        reset_passkey = args.passkey

    asyncio.run(reset_mfa(args.username, reset_totp, reset_passkey))


if __name__ == "__main__":
    main()
