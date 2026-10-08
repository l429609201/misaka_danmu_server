"""
Auth_extra相关的API端点 - 用户认证和密码管理
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from src.utils.auth import security
from src.schemas.auth import Token, User, PasswordChange
from src.services.service_container import get_database_service

logger = logging.getLogger(__name__)

auth_router = APIRouter()

@auth_router.post("/token", response_model=Token, summary="用户登录获取令牌")
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
):
    db = get_database_service()
    async with db.transaction():
        user = await db.user.get_user_by_username(form_data.username)

    if not user or not security.verify_password(form_data.password, user["hashedPassword"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = await security.create_access_token(
        data={"sub": user["username"]}
    )

    # 更新用户的登录信息
    async with db.transaction():
        await db.user.update_user_login_info(user["username"], access_token)

    return {"accessToken": access_token, "tokenType": "bearer"}




@auth_router.get("/users/me", response_model=User, summary="获取当前用户信息")
async def read_users_me(current_user: User = Depends(security.get_current_user)):
    return current_user



@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="用户登出")
async def logout():
    """
    用户登出。前端应清除本地存储的token。
    """
    return



@auth_router.put("/users/me/password", status_code=status.HTTP_204_NO_CONTENT, summary="修改当前用户密码")
async def change_current_user_password(
    password_data: PasswordChange,
    current_user: User = Depends(security.get_current_user),
):
    db = get_database_service()

    # 1. 从数据库获取完整的用户信息，包括哈希密码
    async with db.transaction():
        user_in_db = await db.user.get_user_by_username(current_user.username)

    if not user_in_db:
        # 理论上不会发生，因为 get_current_user 已经验证过
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # 2. 验证旧密码是否正确
    if not security.verify_password(password_data.oldPassword, user_in_db["hashedPassword"]):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incorrect old password")

    # 3. 更新密码
    new_hashed_password = security.get_password_hash(password_data.newPassword)
    async with db.transaction():
        await db.user.update_user_password(current_user.username, new_hashed_password)

# --- Rate Limiter API ---



