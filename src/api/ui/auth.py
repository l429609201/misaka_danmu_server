"""
认证相关的API端点
"""

import ipaddress
import hashlib
import logging
import time
from typing import AsyncIterator, List, Tuple, Optional, Dict
from fastapi import APIRouter, Depends, HTTPException, status, Request, Form
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.utils.auth import security
from src.services.service_container import get_database_service
from src.services.config_service import ConfigService, get_config_service
from src.utils.misc.otp import verify_otp
from src.api.ui.auth_mfa import create_mfa_token
from src.db import orm_models

logger = logging.getLogger(__name__)
router = APIRouter()


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """在请求期间经服务层获取会话，向鉴权函数传递会话实例而非依赖函数。"""
    session_dependency = get_database_service().get_session_dependency()
    async for session in session_dependency():
        yield session


# ========== 登录暴力破解防护 ==========
# key: client_ip, value: (fail_count, first_fail_time)
_login_fail_tracker: Dict[str, Tuple[int, float]] = {}
# 清理计数器：每 100 次登录尝试清理一次过期记录
_login_cleanup_counter = 0


def _check_login_rate_limit(client_ip: str, max_fail_count: int, lockout_minutes: int):
    """
    检查登录速率限制。如果该 IP 已超过失败上限且在锁定期内，抛出 429 异常。
    """
    if max_fail_count <= 0:
        return  # 功能已禁用

    record = _login_fail_tracker.get(client_ip)
    if not record:
        return

    fail_count, first_fail_time = record
    lockout_seconds = lockout_minutes * 60
    elapsed = time.time() - first_fail_time

    if fail_count >= max_fail_count:
        if elapsed < lockout_seconds:
            remaining = int(lockout_seconds - elapsed)
            remaining_min = remaining // 60
            remaining_sec = remaining % 60
            logger.warning(f"[登录防护] IP {client_ip} 已被锁定，剩余 {remaining_min}分{remaining_sec}秒")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"登录失败次数过多，请 {remaining_min} 分 {remaining_sec} 秒后重试",
                headers={"Retry-After": str(remaining)},
            )
        else:
            # 锁定期已过，清除记录
            del _login_fail_tracker[client_ip]


def _record_login_failure(client_ip: str):
    """记录一次登录失败"""
    global _login_cleanup_counter
    now = time.time()

    record = _login_fail_tracker.get(client_ip)
    if record:
        fail_count, first_fail_time = record
        _login_fail_tracker[client_ip] = (fail_count + 1, first_fail_time)
    else:
        _login_fail_tracker[client_ip] = (1, now)

    # 定期清理过期记录（每 100 次失败清理一次）
    _login_cleanup_counter += 1
    if _login_cleanup_counter >= 100:
        _login_cleanup_counter = 0
        _cleanup_login_tracker()


def _record_login_success(client_ip: str):
    """登录成功后清除该 IP 的失败记录"""
    _login_fail_tracker.pop(client_ip, None)


def _cleanup_login_tracker(max_age_seconds: int = 7200):
    """清理超过 max_age_seconds 的过期记录"""
    now = time.time()
    expired = [ip for ip, (_, ts) in _login_fail_tracker.items() if now - ts > max_age_seconds]
    for ip in expired:
        del _login_fail_tracker[ip]


@router.post("/token", summary="用户登录获取令牌")
async def login_for_access_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    otp_password: Optional[str] = Form(None, description="TOTP 验证码（两步验证时必填）"),
    config_service: ConfigService = Depends(get_config_service)
):
    """
    用户登录,返回访问令牌。

    MFA 流程:
    1. 密码验证通过 → 检查 MFA → 返回 403 + mfaToken + mfaTypes
    2. 前端调 /api/ui/auth/mfa/verify 用 mfaToken + (TOTP验证码 或 PassKey凭证) 完成验证并获取 JWT
    3. 如果提供了 otp_password，则在此接口内直接验证 TOTP（向后兼容简化流程）
    """
    # ===== 暴力破解防护 =====
    client_ip = await security.get_real_client_ip(request, config_service)
    max_fail_count = int(await config_service.get("loginMaxFailCount", "3"))
    lockout_minutes = int(await config_service.get("loginLockoutMinutes", "60"))
    _check_login_rate_limit(client_ip, max_fail_count, lockout_minutes)

    # 获取用户信息
    db = get_database_service()
    async with db.transaction():
        user = await db.user.get_by_username(form_data.username)

    if not user or not security.verify_password(form_data.password, user.hashedPassword):
        _record_login_failure(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # ===== MFA 检查 =====
    has_totp = user.isOtp if hasattr(user, 'isOtp') else False
    async with db.transaction():
        passkey_count = await db.passkey.count_by_user_id(user.id)
    has_passkey = passkey_count > 0
    needs_mfa = has_totp or has_passkey

    if needs_mfa:
        mfa_types = []
        if has_totp:
            mfa_types.append("totp")
        if has_passkey:
            mfa_types.append("passkey")

        # 如果提供了 otp_password 且有 TOTP，尝试直接验证（简化流程）
        if otp_password and has_totp:
            otp_secret = user.otpSecret if hasattr(user, 'otpSecret') else None
            if not (otp_secret and verify_otp(otp_secret, otp_password)):
                _record_login_failure(client_ip)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid OTP code",
                )
            # TOTP 验证通过，继续签发 token
        else:
            # 生成 mfaToken 并返回 403，让前端走 /mfa/verify 流程
            mfa_token = create_mfa_token(user.username, user.id)
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "detail": "MFA required",
                    "mfaRequired": True,
                    "mfaTypes": mfa_types,
                    "mfaToken": mfa_token,
                }
            )

    # ===== 签发 Token =====
    _record_login_success(client_ip)

    # 签发函数自行管理配置读取事务，无需额外创建并提前中断会话生成器。
    access_token, jti, expire_minutes = await security.create_access_token(
        data={"sub": user.username}
    )

    # 更新用户的登录信息（保留向后兼容）
    async with db.transaction():
        await db.user.update_login_info(user.username, access_token)

    # 通过登录会话数据域保存记录，避免与数据库连接会话混淆。
    user_agent = request.headers.get("user-agent", "")
    async with db.transaction():
        await db.session_store.create(
            user_id=user.id,
            jti=jti,
            ip_address=client_ip,
            user_agent=user_agent,
            expires_minutes=expire_minutes
        )

    return {"accessToken": access_token, "tokenType": "bearer", "expiresIn": expire_minutes}


@router.post("/auto-login", summary="白名单自动登录")
async def auto_login(request: Request):
    """
    白名单IP自动登录接口

    如果请求来自白名单IP，自动生成JWT token并返回
    如果不在白名单中，返回401错误

    注意：此接口不依赖 check_ip_whitelist，避免 session 回滚问题
    """
    db = get_database_service()

    # 获取 IP 白名单配置
    async with db.transaction():
        ip_whitelist_str = await db.config.get_value("ipWhitelist", "")

    if not ip_whitelist_str or not ip_whitelist_str.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="IP whitelist is not configured",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 获取受信任代理配置并解析真实 IP
    async with db.transaction():
        trusted_proxies_str = await db.config.get_value("trustedProxies", "")

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
    async with db.transaction():
        admin_user = await db.user.get_by_username("admin")

    if not admin_user:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Admin user not found",
        )

    # 生成白名单会话的 jti（与 check_ip_whitelist 保持一致）
    user_agent = request.headers.get("user-agent", "")
    ua_hash = hashlib.md5(user_agent.encode()).hexdigest()[:8] if user_agent else "unknown"
    whitelist_jti = f"whitelist_{client_ip_str}_{ua_hash}"

    # 复用签发函数的独立事务，保留白名单会话标识。
    access_token, _, expire_minutes = await security.create_access_token(
        data={"sub": admin_user.username}, jti=whitelist_jti
    )

    # 尝试创建会话记录（如果已存在则更新最后使用时间）
    try:
        db_expire_minutes = None if expire_minutes == -1 else expire_minutes
        async with db.transaction():
            await db.session_store.create(
                user_id=admin_user.id,
                jti=whitelist_jti,
                ip_address=client_ip_str,
                user_agent=user_agent[:500] if user_agent else None,
                expires_minutes=db_expire_minutes
            )
    except Exception as e:
        # 如果会话已存在（并发请求），更新最后使用时间
        if "Duplicate entry" in str(e) or "UNIQUE constraint" in str(e):
            try:
                async with db.transaction():
                    await db.session_store.update_last_used(whitelist_jti)
            except Exception:
                pass
        else:
            logger.error(f"创建白名单会话记录失败: {e}")

    return {"accessToken": access_token, "tokenType": "bearer", "expiresIn": expire_minutes}


@router.get("/users/me", summary="获取当前用户信息")
async def read_users_me(current_user: orm_models.User = Depends(security.get_current_user)):
    """获取当前登录用户的信息"""
    return current_user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="用户登出")
async def logout(
    user_jti: Tuple[orm_models.User, Optional[str]] = Depends(security.get_current_user_with_jti)
):
    """用户登出，撤销当前会话"""
    _, jti = user_jti
    if jti:
        db = get_database_service()
        async with db.transaction():
            await db.session_store.revoke_by_jti(jti)
    return


@router.put("/users/me/password", status_code=status.HTTP_204_NO_CONTENT, summary="修改当前用户密码")
async def change_current_user_password(
    password_data: Dict[str, str],
    current_user: orm_models.User = Depends(security.get_current_user)
):
    """修改当前用户的密码"""
    db = get_database_service()

    # 1. 从数据库获取完整的用户信息，包括哈希密码
    async with db.transaction():
        user_in_db = await db.user.get_by_username(current_user.username)

    if not user_in_db:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # 2. 验证旧密码是否正确
    old_password = password_data.get("oldPassword")
    new_password = password_data.get("newPassword")

    if not old_password or not new_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing password fields")

    if not security.verify_password(old_password, user_in_db.hashedPassword):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incorrect old password")

    # 3. 更新密码
    new_hashed_password = security.get_password_hash(new_password)
    async with db.transaction():
        await db.user.update_password(current_user.username, new_hashed_password)


# ========== 会话管理 API ==========

@router.get("/sessions", summary="获取当前用户的所有会话")
async def get_user_sessions(
    user_jti: Tuple[orm_models.User, Optional[str]] = Depends(security.get_current_user_with_jti)
):
    """获取当前用户的所有会话列表"""
    user, current_jti = user_jti
    db = get_database_service()

    async with db.transaction():
        user_in_db = await db.user.get_by_username(user.username)

    if not user_in_db:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    async with db.transaction():
        sessions = await db.session_store.get_by_user_id(user_in_db.id)

    # 转换为字典并标记当前会话和白名单会话
    session_list = []
    for s in sessions:
        session_dict = {
            "id": s.id,
            "jti": s.jti,
            "ipAddress": s.ipAddress,
            "userAgent": s.userAgent,
            "createdAt": s.createdAt.isoformat() if s.createdAt else None,
            "lastUsedAt": s.lastUsedAt.isoformat() if s.lastUsedAt else None,
            "expiresAt": s.expiresAt.isoformat() if s.expiresAt else None,
            "isCurrent": (s.jti == current_jti),
            "isWhitelist": s.jti.startswith("whitelist_") if s.jti else False
        }
        session_list.append(session_dict)

    return {"sessions": session_list, "currentJti": current_jti}


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT, summary="踢出指定会话")
async def revoke_session(
    session_id: int,
    user_jti: Tuple[orm_models.User, Optional[str]] = Depends(security.get_current_user_with_jti)
):
    """撤销指定的会话（踢出设备）"""
    user, current_jti = user_jti
    db = get_database_service()

    async with db.transaction():
        user_in_db = await db.user.get_by_username(user.username)

    if not user_in_db:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # 获取目标会话信息
    async with db.transaction():
        target_session = await db.session_store.get_by_id(session_id)

    if not target_session or target_session.userId != user_in_db.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    # 不允许踢出当前会话
    if target_session.jti == current_jti:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot revoke current session")

    async with db.transaction():
        success = await db.session_store.revoke(session_id, user_in_db.id)

    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")


@router.delete("/sessions/others/all", status_code=status.HTTP_200_OK, summary="踢出所有其他会话")
async def revoke_other_sessions(
    user_jti: Tuple[orm_models.User, Optional[str]] = Depends(security.get_current_user_with_jti)
):
    """撤销当前用户的所有其他会话"""
    user, current_jti = user_jti
    db = get_database_service()

    async with db.transaction():
        user_in_db = await db.user.get_by_username(user.username)

    if not user_in_db:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    async with db.transaction():
        count = await db.session_store.revoke_others(user_in_db.id, current_jti)

    return {"revokedCount": count}



# ========== 登录锁定管理 API ==========

async def _authorize_login_lockout_management(
    request: Request,
    token: Optional[str] = Depends(security.oauth2_scheme_optional),
    session: AsyncSession = Depends(get_db_session),
) -> Optional[orm_models.User]:
    """仅允许本机维护脚本免登录调用，远程请求仍走完整鉴权。"""
    client_host = request.client.host if request.client else ""
    try:
        if ipaddress.ip_address(client_host).is_loopback:
            return None
    except ValueError:
        pass
    return await security.get_current_user(request, token, session)


@router.delete("/login-lockout", summary="清除登录锁定")
async def clear_login_lockout(
    ip: Optional[str] = None,
    current_user: Optional[orm_models.User] = Depends(_authorize_login_lockout_management),
):
    """
    清除登录暴力破解防护的 IP 锁定记录。

    - 不传 ip 参数：清除所有 IP 的锁定
    - 传 ip 参数：仅清除指定 IP 的锁定
    """
    operator = current_user.username if current_user else "本机维护脚本"
    if ip:
        removed = _login_fail_tracker.pop(ip, None)
        if removed:
            logger.info(f"[登录防护] {operator} 清除了 IP {ip} 的登录锁定")
            return {"message": f"已清除 IP {ip} 的登录锁定", "cleared": 1}
        return {"message": f"IP {ip} 无锁定记录", "cleared": 0}
    else:
        count = len(_login_fail_tracker)
        _login_fail_tracker.clear()
        logger.info(f"[登录防护] {operator} 清除了所有登录锁定（{count} 条）")
        return {"message": "已清除所有登录锁定", "cleared": count}


@router.get("/login-lockout", summary="查看登录锁定状态")
async def get_login_lockout_status(
    current_user: orm_models.User = Depends(security.get_current_user),
):
    """
    查看当前所有被锁定的 IP 及其失败次数。
    """
    now = time.time()
    result = []
    for ip, (fail_count, first_fail_time) in _login_fail_tracker.items():
        elapsed = now - first_fail_time
        result.append({
            "ip": ip,
            "failCount": fail_count,
            "firstFailTime": int(first_fail_time),
            "elapsedSeconds": int(elapsed),
        })
    return {"lockedIps": result, "total": len(result)}