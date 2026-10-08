"""
独立密码重置脚本

使用方法:
    python src/reset_password.py admin
"""
import argparse
import asyncio
import secrets
import string
import sys
from pathlib import Path

# 保留旧脚本的直接执行入口，确保导入的是当前项目而非备份目录。
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db.database import _get_db_url
from src.services.service_container import (
    close_database_service,
    get_database_service,
    init_database_service,
)

# 沿用备份脚本的 bcrypt 配置，不引入 Web 认证模块的依赖链。
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


async def reset_password(username: str, password: str | None = None) -> bool:
    """通过数据库服务重置密码；未指定密码时生成随机密码。"""
    # 使用主程序的配置与 URL 构造，不猜测数据库类型或回退到 SQLite。
    engine = create_async_engine(_get_db_url())
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        init_database_service(session_factory)
        db = get_database_service()
        async with db.transaction():
            user = await db.user.get_by_username(username)
            if user is None:
                print(f"❌ 错误：未找到用户 '{username}'。", file=sys.stderr)
                return False

            # 仅未传入密码时生成随机值，避免悄悄替换用户指定的密码。
            alphabet = string.ascii_letters + string.digits
            new_password = password if password is not None else ''.join(secrets.choice(alphabet) for _ in range(16))
            if not new_password:
                raise ValueError("密码不能为空")
            hashed_password = _pwd_context.hash(new_password)
            if not await db.user.update_password(username, hashed_password):
                raise RuntimeError("用户密码更新失败")

        # 退出事务并提交成功后才输出，避免把回滚的密码误报为有效密码。
        print("\n" + "=" * 60)
        print("✅ 密码重置成功！")
        print(f"   - 用户名: {username}")
        print(f"   - 新密码: {new_password}")
        print("=" * 60)
        print("\n请立即使用新密码登录，并在“设置”页面中修改为您自己的密码。")
        return True
    finally:
        # 会话先退出，再释放独立脚本持有的服务与连接池。
        try:
            await close_database_service()
        finally:
            await engine.dispose()


def main() -> None:
    """解析用户名和可选密码，兼容原有的随机密码重置方式。"""
    parser = argparse.ArgumentParser(description="重置指定用户的密码。")
    parser.add_argument("username", help="要重置密码的用户名。")
    parser.add_argument("password", nargs="?", default=None, help="可选的新密码；省略时生成随机密码。注意：明文参数可能保留在终端历史中。")
    args = parser.parse_args()
    # 参数校验在连接数据库之前完成，不去除空白以免改变密码原值。
    if args.password == "":
        parser.error("密码不能为空")
    try:
        success = asyncio.run(reset_password(args.username, args.password))
    except KeyboardInterrupt:
        print("\n操作已取消", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        # 不直接打印数据库异常详情，避免连接参数或 SQL 中的密码哈希泄露。
        print(f"❌ 密码重置失败（{type(exc).__name__}），请检查当前项目配置、数据库连接及表结构。", file=sys.stderr)
        raise SystemExit(1) from None
    if not success:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
