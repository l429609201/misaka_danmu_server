from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from . import crud, models
from .config import settings
from .database import get_db_session

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/ui/auth/token")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    """生成密码哈希"""
    return pwd_context.hash(password)

async def _get_user_from_token(token: str, session: AsyncSession) -> models.User:
    """
    核心逻辑：解码JWT，验证其有效性，并获取当前用户。
    这是一个不带FastAPI依赖的辅助函数。
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise credentials_exception

    try:
        payload = jwt.decode(token, settings.jwt.secret_key, algorithms=[settings.jwt.algorithm])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = models.TokenData(username=username)
    except JWTError:
        # 这将捕获过期的令牌、无效的签名等
        raise credentials_exception
    
    user = await crud.get_user_by_username(session, username=token_data.username)
    if user is None:
        raise credentials_exception

    return models.User.model_validate(user)

async def create_access_token(data: dict, session: AsyncSession, expires_delta: Optional[timedelta] = None):
    """创建JWT访问令牌"""
    to_encode = data.copy()
    
    # 新增：添加标准声明以增强安全性和互操作性
    now = datetime.now(timezone.utc)
    to_encode.update({
        "iat": now,  # Issued At: 令牌签发时间
        "jti": str(uuid.uuid4()), # JWT ID: 每个令牌的唯一标识符，可用于防止重放攻击
    })

    expire_minutes_str = await crud.get_config_value(session, 'jwt_expire_minutes', str(settings.jwt.access_token_expire_minutes))
    expire_minutes = int(expire_minutes_str)
    # 如果有效期不为-1，则设置过期时间
    if expire_minutes != -1:
        expire = now + timedelta(minutes=expire_minutes)
        to_encode.update({"exp": expire})
    # 如果是-1，则不添加 "exp" 字段，令牌将永不过期
    encoded_jwt = jwt.encode(to_encode, settings.jwt.secret_key, algorithm=settings.jwt.algorithm)
    return encoded_jwt

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_db_session)
) -> models.User:
    """
    依赖项：解码JWT，验证其有效性，并获取当前用户。
    """
    return await _get_user_from_token(token, session)