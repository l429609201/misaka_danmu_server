import asyncio
import argparse
import secrets
import string
import sys
from pathlib import Path

# 将项目根目录添加到Python路径，以便可以从 src 导入模块
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# 现在可以安全地从 src 导入
from src import crud, security
from src.config import settings

async def reset_password(username: str):
    """
    为指定用户重置密码。
    """
    # 1. 设置数据库连接 (与 main.py 类似，但更简化)
    db_type = settings.database.type.lower()
    if db_type == "mysql":
        db_url = f"mysql+aiomysql://{settings.database.user}:{settings.database.password}@{settings.database.host}:{settings.database.port}/{settings.database.name}?charset=utf8mb4"
    elif db_type == "postgresql":
        db_url = f"postgresql+asyncpg://{settings.database.user}:{settings.database.password}@{settings.database.host}:{settings.database.port}/{settings.database.name}"
    else:
        print(f"❌ 不支持的数据库类型: '{db_type}'。请使用 'mysql' 或 'postgresql'。")
        return

    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as session:
        # 2. 检查用户是否存在
        user = await crud.get_user_by_username(session, username)
        if not user:
            print(f"❌ 错误: 未找到用户 '{username}'。")
            await engine.dispose()
            return

        # 3. 生成一个新的16位随机密码
        alphabet = string.ascii_letters + string.digits
        new_password = ''.join(secrets.choice(alphabet) for _ in range(16))
        
        # 4. 对新密码进行哈希处理
        hashed_password = security.get_password_hash(new_password)

        # 5. 更新数据库中的用户密码
        await crud.update_user_password(session, username, hashed_password)
        
        print("\n" + "="*60)
        print("✅ 密码重置成功！")
        print(f"   - 用户名: {username}")
        print(f"   - 新密码: {new_password}")
        print("="*60)
        print("\n请立即使用新密码登录，并在“设置”页面中修改为您自己的密码。")

    await engine.dispose()

def main():
    parser = argparse.ArgumentParser(description="重置指定用户的密码。")
    parser.add_argument("username", help="要重置密码的用户名。")
    args = parser.parse_args()
    
    asyncio.run(reset_password(args.username))

if __name__ == "__main__":
    main()
