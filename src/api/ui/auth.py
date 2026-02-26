"""
认证相关的API端点
"""

import logging
from typing import List, Tuple, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import models, crud, get_db_session, ConfigManager
from src import security
from src.api.dependencies import get_config_manager

# 从 crud 导入需要的子模块
user_crud = crud.user
session_crud = crud.session
config_crud = crud.config

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/token", response_model=models.Token, summary="用户登录获取令牌")
async def login_for_access_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_db_session),
    config_manager: ConfigManager = Depends(get_config_manager)
):
    """用户登录,返回访问令牌"""
    user = await user_crud.get_user_by_username(session, form_data.username)
    if not user or not security.verify_password(form_data.password, user["hashedPassword"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token, jti, expire_minutes = await security.create_access_token(
        data={"sub": user["username"]}, session=session
    )
    # 更新用户的登录信息（保留向后兼容）
    await user_crud.update_user_login_info(session, user["username"], access_token)

    # 创建会话记录
    client_ip = await security.get_real_client_ip(request, config_manager)
    user_agent = request.headers.get("user-agent", "")
    await session_crud.create_user_session(
        session=session,
        user_id=user["id"],
        jti=jti,
        ip_address=client_ip,
        user_agent=user_agent,
        expires_minutes=expire_minutes
    )

    return {"accessToken": access_token, "tokenType": "bearer", "expiresIn": expire_minutes}


@router.post("/auto-login", response_model=models.Token, summary="白名单自动登录")
async def auto_login(
    request: Request,
    session: AsyncSession = Depends(get_db_session)
):
    """
    白名单IP自动登录接口

    如果请求来自白名单IP，自动生成JWT token并返回
    如果不在白名单中，返回401错误

    注意：此接口不依赖 check_ip_whitelist，避免 session 回滚问题
    """
    import ipaddress
    import hashlib

    # 获取 IP 白名单配置
    ip_whitelist_str = await config_crud.get_config_value(session, "ipWhitelist", "")
    if not ip_whitelist_str or not ip_whitelist_str.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="IP whitelist is not configured",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 获取受信任代理配置并解析真实 IP
    trusted_proxies_str = await config_crud.get_config_value(session, "trustedProxies", "")
    client_ip_str = security._get_real_client_ip_sync(request, trusted_proxies_str)

    # 解析白名单网段
    whitelist_networks = []
    for entry in ip_whitelist_str.split(','):
        entry = entry.strip()
        if not entry:
            continue
        try:
            network = ipaddress.ip_network(entry, strict=False)
            whitelist_networks.append(network)
        except ValueError:
            pass

    if not whitelist_networks:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="IP whitelist is empty",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 检查客户端 IP 是否在白名单中
    try:
        client_addr = ipaddress.ip_address(client_ip_str)
        is_whitelisted = any(client_addr in network for network in whitelist_networks)
    except ValueError:
        is_whitelisted = False

    if not is_whitelisted:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not in IP whitelist",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 获取管理员用户
    admin_user = await user_crud.get_user_by_username(session, "admin")
    if not admin_user:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Admin user not found",
        )

    # 生成白名单会话的 jti（与 check_ip_whitelist 保持一致）
    user_agent = request.headers.get("user-agent", "")
    ua_hash = hashlib.md5(user_agent.encode()).hexdigest()[:8] if user_agent else "unknown"
    whitelist_jti = f"whitelist_{client_ip_str}_{ua_hash}"

    # 生成 JWT token（使用白名单会话的 jti）
    access_token, _, expire_minutes = await security.create_access_token(
        data={"sub": admin_user["username"]},
        session=session,
        jti=whitelist_jti
    )

    # 尝试创建会话记录（如果已存在则忽略）
    try:
        db_expire_minutes = None if expire_minutes == -1 else expire_minutes
        await session_crud.create_user_session(
            session=session,
            user_id=admin_user["id"],
            jti=whitelist_jti,
            ip_address=client_ip_str,
            user_agent=user_agent[:500] if user_agent else None,
            expires_minutes=db_expire_minutes
        )
    except Exception as e:
        # 如果会话已存在（并发请求），忽略错误
        if "Duplicate entry" in str(e) or "UNIQUE constraint" in str(e):
            # 回滚当前事务，避免 PendingRollback 错误
            await session.rollback()
            # 更新最后使用时间
            try:
                await session_crud.update_session_last_used(session, whitelist_jti)
            except Exception:
                pass
        else:
            # 其他错误也回滚，但记录日志
            await session.rollback()
            logger.error(f"创建白名单会话记录失败: {e}")

    return {"accessToken": access_token, "tokenType": "bearer", "expiresIn": expire_minutes}


@router.get("/users/me", response_model=models.User, summary="获取当前用户信息")
async def read_users_me(current_user: models.User = Depends(security.get_current_user)):
    """获取当前登录用户的信息"""
    return current_user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="用户登出")
async def logout(
    user_jti: Tuple[models.User, Optional[str]] = Depends(security.get_current_user_with_jti),
    session: AsyncSession = Depends(get_db_session)
):
    """用户登出，撤销当前会话"""
    _, jti = user_jti
    if jti:
        await session_crud.revoke_session_by_jti(session, jti)
    return


@router.put("/users/me/password", status_code=status.HTTP_204_NO_CONTENT, summary="修改当前用户密码")
async def change_current_user_password(
    password_data: models.PasswordChange,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session)
):
    """修改当前用户的密码"""
    # 1. 从数据库获取完整的用户信息，包括哈希密码
    user_in_db = await user_crud.get_user_by_username(session, current_user.username)
    if not user_in_db:
        # 理论上不会发生，因为 get_current_user 已经验证过
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # 2. 验证旧密码是否正确
    if not security.verify_password(password_data.oldPassword, user_in_db["hashedPassword"]):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incorrect old password")

    # 3. 更新密码
    new_hashed_password = security.get_password_hash(password_data.newPassword)
    await user_crud.update_user_password(session, current_user.username, new_hashed_password)


# ========== 会话管理 API ==========

@router.get("/sessions", summary="获取当前用户的所有会话")
async def get_user_sessions(
    user_jti: Tuple[models.User, Optional[str]] = Depends(security.get_current_user_with_jti),
    session: AsyncSession = Depends(get_db_session)
):
    """获取当前用户的所有会话列表"""
    user, current_jti = user_jti
    user_in_db = await user_crud.get_user_by_username(session, user.username)
    if not user_in_db:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    sessions = await session_crud.get_user_sessions(session, user_in_db["id"])

    # 标记当前会话和白名单会话
    for s in sessions:
        s["isCurrent"] = (s["jti"] == current_jti)
        # 白名单会话的 jti 以 "whitelist_" 开头
        s["isWhitelist"] = s["jti"].startswith("whitelist_") if s["jti"] else False

    return {"sessions": sessions, "currentJti": current_jti}


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT, summary="踢出指定会话")
async def revoke_session(
    session_id: int,
    user_jti: Tuple[models.User, Optional[str]] = Depends(security.get_current_user_with_jti),
    session: AsyncSession = Depends(get_db_session)
):
    """撤销指定的会话（踢出设备）"""
    user, current_jti = user_jti
    user_in_db = await user_crud.get_user_by_username(session, user.username)
    if not user_in_db:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # 获取目标会话信息
    target_session = await session_crud.get_user_sessions(session, user_in_db["id"])
    target = next((s for s in target_session if s["id"] == session_id), None)

    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    # 不允许踢出当前会话
    if target["jti"] == current_jti:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot revoke current session")

    success = await session_crud.revoke_session(session, session_id, user_in_db["id"])
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")


@router.delete("/sessions/others/all", status_code=status.HTTP_200_OK, summary="踢出所有其他会话")
async def revoke_other_sessions(
    user_jti: Tuple[models.User, Optional[str]] = Depends(security.get_current_user_with_jti),
    session: AsyncSession = Depends(get_db_session)
):
    """撤销当前用户的所有其他会话"""
    user, current_jti = user_jti
    user_in_db = await user_crud.get_user_by_username(session, user.username)
    if not user_in_db:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    count = await session_crud.revoke_other_sessions(session, user_in_db["id"], current_jti)
    return {"revokedCount": count}

