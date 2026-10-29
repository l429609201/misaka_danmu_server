"""纯密码哈希工具，不依赖数据库或应用服务。"""

from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证明文密码与哈希值是否匹配。"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """生成密码哈希。"""
    return pwd_context.hash(password)
