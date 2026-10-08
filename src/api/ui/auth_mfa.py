"""
MFA (多因素认证) 管理 API

包含 TOTP 两步验证和 WebAuthn PassKey 的设置、验证、管理接口。
"""

import json
import logging
import secrets
import time
from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status, Request, Form
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from webauthn import (
    generate_registration_options,
    verify_registration_response,
    generate_authentication_options,
    verify_authentication_response,
    options_to_json,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    ResidentKeyRequirement,
    UserVerificationRequirement,
    PublicKeyCredentialDescriptor,
)
from webauthn.helpers import bytes_to_base64url, base64url_to_bytes

from src.services.service_container import get_database_service
from src.utils.auth import security
from src.utils.misc.otp import generate_secret, get_totp_uri, verify_otp
from src.api.dependencies import get_config_service
from src.services.config_service import ConfigService
from src.db import orm_models

logger = logging.getLogger(__name__)
router = APIRouter()

# ======== WebAuthn 配置 ========
RP_ID = "localhost"  # 会在运行时从请求中动态推断
RP_NAME = "Misaka弹幕库"

# ======== Challenge 临时存储 ========
# key: username/session_id, value: (challenge_bytes, timestamp)
_challenge_store: dict[str, tuple[bytes, float]] = {}
CHALLENGE_TTL = 300  # 5分钟过期
CHALLENGE_MAX_SIZE = 10000  # 最大存储条目数，防 DoS

# ======== MFA Pending 临时令牌存储 ========
# key: mfa_token, value: (username, user_id, timestamp)
_mfa_pending_store: dict[str, tuple[str, int, float]] = {}
MFA_PENDING_TTL = 300  # 5分钟过期
MFA_PENDING_MAX_SIZE = 10000  # 最大存储条目数，防 DoS


def _store_challenge(key: str, challenge: bytes):
    """存储 challenge 并自动清理过期条目，限制最大容量"""
    now = time.time()
    expired_keys = [k for k, (_, t) in _challenge_store.items() if now - t > CHALLENGE_TTL]
    for k in expired_keys:
        del _challenge_store[k]
    # 容量限制：如果仍超出上限，清除最旧的条目
    if len(_challenge_store) >= CHALLENGE_MAX_SIZE:
        oldest_key = min(_challenge_store, key=lambda k: _challenge_store[k][1])
        del _challenge_store[oldest_key]
    _challenge_store[key] = (challenge, now)


def _get_and_consume_challenge(key: str) -> Optional[bytes]:
    """获取并消费 challenge"""
    if key not in _challenge_store:
        return None
    challenge, ts = _challenge_store.pop(key)
    if time.time() - ts > CHALLENGE_TTL:
        return None
    return challenge


def create_mfa_token(username: str, user_id: int) -> str:
    """创建 MFA 临时令牌（证明密码已验证通过）"""
    now = time.time()
    # 清理过期令牌
    expired = [k for k, (_, _, t) in _mfa_pending_store.items() if now - t > MFA_PENDING_TTL]
    for k in expired:
        del _mfa_pending_store[k]
    # 容量限制
    if len(_mfa_pending_store) >= MFA_PENDING_MAX_SIZE:
        oldest_key = min(_mfa_pending_store, key=lambda k: _mfa_pending_store[k][2])
        del _mfa_pending_store[oldest_key]
    token = secrets.token_urlsafe(32)
    _mfa_pending_store[token] = (username, user_id, now)
    return token


def consume_mfa_token(token: str) -> Optional[tuple[str, int]]:
    """消费 MFA 临时令牌，返回 (username, user_id) 或 None"""
    if token not in _mfa_pending_store:
        return None
    username, user_id, ts = _mfa_pending_store.pop(token)
    if time.time() - ts > MFA_PENDING_TTL:
        return None
    return username, user_id


def _get_rp_id(request: Request) -> str:
    """从请求中动态推断 RP ID（域名，不含端口）"""
    host = request.headers.get("host", "localhost")
    # 移除端口号
    return host.split(":")[0]


def _get_origin(request: Request) -> str:
    """
    从请求中推断客户端真实 origin。

    优先级:
    1. Origin 请求头（浏览器自动发送，最可靠）
    2. Referer 请求头（取 scheme://host 部分）
    3. X-Forwarded-Proto + Host（反向代理场景）
    4. request.url.scheme + Host（直连场景）
    """
    # 1. 优先使用浏览器发送的 Origin 头
    origin_header = request.headers.get("origin")
    if origin_header:
        return origin_header.rstrip("/")

    # 2. 尝试从 Referer 推断
    referer = request.headers.get("referer")
    if referer:
        # 取 scheme://host 部分
        from urllib.parse import urlparse
        parsed = urlparse(referer)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"

    # 3. X-Forwarded-Proto（反代终结 SSL 时）
    host = request.headers.get("host", "localhost")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)

    return f"{proto}://{host}"



# ======== MFA 状态查询 ========

@router.get("/status", summary="获取当前用户的 MFA 状态")
async def get_mfa_status(
    current_user: orm_models.User = Depends(security.get_current_user)
):
    """获取当前用户的 MFA 配置状态（TOTP 是否开启、PassKey 列表）"""
    db = get_database_service()

    async with db.transaction():
        user = await db.user.get_by_username(current_user.username)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    async with db.transaction():
        passkeys = await db.passkey.get_by_user_id(user.id)

    passkey_list = [
        {
            "id": pk.id,
            "credentialId": pk.credentialId,
            "deviceName": pk.deviceName,
            "createdAt": pk.createdAt.isoformat() if pk.createdAt else None,
            "lastUsedAt": pk.lastUsedAt.isoformat() if pk.lastUsedAt else None
        }
        for pk in passkeys
    ]

    return {
        "totpEnabled": user.isOtp if hasattr(user, 'isOtp') else False,
        "passkeyCount": len(passkeys),
        "passkeys": passkey_list,
    }


# ======== TOTP 两步验证 ========

@router.post("/totp/setup", summary="生成 TOTP 密钥")
async def setup_totp(
    current_user: orm_models.User = Depends(security.get_current_user)
):
    """
    生成 TOTP 密钥和二维码 URI。
    用户需要用验证器 App 扫描二维码，然后调用 verify-setup 确认。
    """
    db = get_database_service()

    async with db.transaction():
        user = await db.user.get_by_username(current_user.username)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    is_otp_enabled = user.isOtp if hasattr(user, 'isOtp') else False
    if is_otp_enabled:
        raise HTTPException(status_code=400, detail="TOTP 已启用，请先关闭后再重新设置")

    secret = generate_secret()
    uri = get_totp_uri(secret, current_user.username)

    # 临时存储 secret，等待用户确认验证码
    _store_challenge(f"totp_setup_{current_user.username}", secret.encode())

    return {"secret": secret, "uri": uri}


@router.post("/totp/verify-setup", status_code=status.HTTP_200_OK, summary="确认 TOTP 设置")
async def verify_totp_setup(
    data: Dict[str, str],
    current_user: orm_models.User = Depends(security.get_current_user)
):
    """用户扫码后输入验证码，确认 TOTP 设置"""
    secret_bytes = _get_and_consume_challenge(f"totp_setup_{current_user.username}")
    if not secret_bytes:
        raise HTTPException(status_code=400, detail="TOTP 设置已过期，请重新生成")

    code = data.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="缺少验证码")

    secret = secret_bytes.decode()
    if not verify_otp(secret, code):
        # 验证失败，重新存回 challenge 让用户重试
        _store_challenge(f"totp_setup_{current_user.username}", secret_bytes)
        raise HTTPException(status_code=400, detail="验证码错误，请重试")

    # 验证成功，保存到数据库
    db = get_database_service()
    async with db.transaction():
        await db.user.enable_otp(current_user.username, secret)

    return {"message": "TOTP 两步验证已启用"}


@router.post("/totp/disable", status_code=status.HTTP_200_OK, summary="关闭 TOTP 两步验证")
async def disable_totp(
    data: Dict[str, str],
    current_user: orm_models.User = Depends(security.get_current_user)
):
    """关闭 TOTP 两步验证（需要输入当前密码确认）"""
    db = get_database_service()

    async with db.transaction():
        user = await db.user.get_by_username(current_user.username)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    is_otp_enabled = user.isOtp if hasattr(user, 'isOtp') else False
    if not is_otp_enabled:
        raise HTTPException(status_code=400, detail="TOTP 未启用")

    # 验证密码
    password = data.get("password")
    if not password:
        raise HTTPException(status_code=400, detail="缺少密码")

    if not security.verify_password(password, user.hashedPassword):
        raise HTTPException(status_code=400, detail="密码错误")

    # 关闭 TOTP 时连带删除所有 PassKey（TOTP 是 PassKey 的前置条件）
    async with db.transaction():
        passkey_count = await db.passkey.delete_all_by_user_id(user.id)
        await db.user.disable_otp(current_user.username)

    msg = "TOTP 两步验证已关闭"
    if passkey_count > 0:
        msg += f"，同时已清除 {passkey_count} 个 PassKey"
    return {"message": msg}


# ======== WebAuthn PassKey ========

@router.post("/passkey/register/options", summary="生成 PassKey 注册选项")
async def passkey_register_options(
    request: Request,
    current_user: orm_models.User = Depends(security.get_current_user)
):
    """
    生成 WebAuthn 注册选项，前端调用 navigator.credentials.create() 时使用。
    前提条件：用户必须先启用 TOTP 两步验证，才能注册 PassKey。
    """
    db = get_database_service()

    async with db.transaction():
        user = await db.user.get_by_username(current_user.username)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # 必须先启用 TOTP 才能注册 PassKey（防止用户失联）
    is_otp_enabled = user.isOtp if hasattr(user, 'isOtp') else False
    if not is_otp_enabled:
        raise HTTPException(status_code=400, detail="请先启用 TOTP 两步验证后再注册 PassKey")

    rp_id = _get_rp_id(request)

    # 获取用户已有的凭证，用于排除重复注册
    async with db.transaction():
        existing_passkeys = await db.passkey.get_by_user_id(user.id)

    exclude_credentials = [
        PublicKeyCredentialDescriptor(
            id=base64url_to_bytes(pk.credentialId),
            transports=pk.transports.split(",") if pk.transports else [],
        )
        for pk in existing_passkeys
    ]

    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=RP_NAME,
        user_id=str(user.id).encode("utf-8"),
        user_name=user.username,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=exclude_credentials,
    )

    # 存储 challenge 供验证时使用
    _store_challenge(f"passkey_reg_{current_user.username}", options.challenge)

    return {"options": options_to_json(options)}


@router.post("/passkey/register/verify", summary="验证 PassKey 注册")
async def passkey_register_verify(
    data: Dict[str, Any],
    request: Request,
    current_user: orm_models.User = Depends(security.get_current_user)
):
    """验证浏览器返回的 WebAuthn 注册凭证并保存"""
    db = get_database_service()

    async with db.transaction():
        user = await db.user.get_by_username(current_user.username)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    challenge = _get_and_consume_challenge(f"passkey_reg_{current_user.username}")
    if not challenge:
        raise HTTPException(status_code=400, detail="注册已过期，请重新开始")

    rp_id = _get_rp_id(request)
    origin = _get_origin(request)

    credential_data = data.get("credential")
    device_name = data.get("deviceName", "未命名设备")

    try:
        verification = verify_registration_response(
            credential=credential_data,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
        )
    except Exception as e:
        logger.warning(f"PassKey 注册验证失败: {e}")
        raise HTTPException(status_code=400, detail=f"PassKey 注册验证失败: {str(e)}")

    # 存储凭证
    credential_id_b64 = bytes_to_base64url(verification.credential_id)
    public_key_b64 = bytes_to_base64url(verification.credential_public_key)

    # 获取传输方式（如果有）
    transports_str = None
    if hasattr(verification, 'credential_device_type'):
        # py_webauthn v2.x
        pass

    async with db.transaction():
        passkey_id = await db.passkey.create(
            user_id=user.id,
            credential_id=credential_id_b64,
            public_key=public_key_b64,
            sign_count=verification.sign_count,
            device_name=device_name,
            transports=transports_str,
        )

    return {"message": "PassKey 注册成功", "id": passkey_id}


@router.post("/passkey/authenticate/options", summary="生成 PassKey 认证选项")
async def passkey_authenticate_options(
    request: Request,
    username: str = ""
):
    """
    生成 WebAuthn 认证选项，前端调用 navigator.credentials.get() 时使用。
    注意：此接口不需要认证（用于登录流程中的 MFA 验证）。
    """
    rp_id = _get_rp_id(request)
    allow_credentials = []

    if username:
        db = get_database_service()
        async with db.transaction():
            user = await db.user.get_by_username(username)

        if user:
            async with db.transaction():
                existing_passkeys = await db.passkey.get_by_user_id(user.id)

            allow_credentials = [
                PublicKeyCredentialDescriptor(
                    id=base64url_to_bytes(pk.credentialId),
                    transports=pk.transports.split(",") if pk.transports else [],
                )
                for pk in existing_passkeys
            ]

    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=allow_credentials if allow_credentials else None,
        user_verification=UserVerificationRequirement.PREFERRED,
    )

    # 存储 challenge
    challenge_key = f"passkey_auth_{username}" if username else f"passkey_auth__anon"
    _store_challenge(challenge_key, options.challenge)

    return {"options": options_to_json(options)}


@router.post("/passkey/authenticate/verify", summary="验证 PassKey 认证")
async def passkey_authenticate_verify(
    data: Dict[str, Any],
    request: Request,
    username: str = ""
):
    """
    验证 WebAuthn 认证断言。
    注意：此接口不需要认证（用于登录流程中的 MFA 验证）。
    返回验证是否成功。
    """
    challenge_key = f"passkey_auth_{username}" if username else "passkey_auth__anon"
    challenge = _get_and_consume_challenge(challenge_key)
    if not challenge:
        raise HTTPException(status_code=400, detail="认证已过期，请重新开始")

    rp_id = _get_rp_id(request)
    origin = _get_origin(request)

    # 解析凭证以获取 credential_id，查找对应的 passkey
    credential_data = data.get("credential")
    if not credential_data:
        raise HTTPException(status_code=400, detail="缺少凭证数据")

    try:
        cred_data = json.loads(credential_data) if isinstance(credential_data, str) else credential_data
        raw_id = cred_data.get("rawId") or cred_data.get("id", "")
    except (json.JSONDecodeError, AttributeError):
        raise HTTPException(status_code=400, detail="无效的凭证数据")

    db = get_database_service()
    async with db.transaction():
        passkey_record = await db.passkey.get_by_credential_id(raw_id)

    if not passkey_record:
        raise HTTPException(status_code=400, detail="未找到对应的 PassKey")

    try:
        verification = verify_authentication_response(
            credential=credential_data,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=base64url_to_bytes(passkey_record.publicKey),
            credential_current_sign_count=passkey_record.signCount,
        )
    except Exception as e:
        logger.warning(f"PassKey 认证验证失败: {e}")
        raise HTTPException(status_code=400, detail=f"PassKey 认证验证失败: {str(e)}")

    # 更新签名计数器
    async with db.transaction():
        await db.passkey.update_sign_count(raw_id, verification.new_sign_count)

    return {"verified": True, "userId": passkey_record.userId}


# ======== PassKey 管理 ========

@router.put("/passkey/{passkey_id}/rename", summary="重命名 PassKey")
async def rename_passkey(
    passkey_id: int,
    data: Dict[str, str],
    current_user: orm_models.User = Depends(security.get_current_user)
):
    """重命名指定的 PassKey"""
    db = get_database_service()

    async with db.transaction():
        user = await db.user.get_by_username(current_user.username)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    device_name = data.get("deviceName")
    if not device_name:
        raise HTTPException(status_code=400, detail="缺少设备名称")

    async with db.transaction():
        success = await db.passkey.rename(passkey_id, user.id, device_name)

    if not success:
        raise HTTPException(status_code=404, detail="PassKey not found")
    return {"message": "重命名成功"}


@router.delete("/passkey/{passkey_id}", status_code=status.HTTP_200_OK, summary="删除 PassKey")
async def delete_passkey(
    passkey_id: int,
    current_user: orm_models.User = Depends(security.get_current_user)
):
    """删除指定的 PassKey"""
    db = get_database_service()

    async with db.transaction():
        user = await db.user.get_by_username(current_user.username)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    async with db.transaction():
        success = await db.passkey.delete(passkey_id, user.id)

    if not success:
        raise HTTPException(status_code=404, detail="PassKey not found")
    return {"message": "PassKey 已删除"}


# ======== 统一 MFA 验证（签发 JWT） ========

async def _issue_jwt_for_user(
    request: Request,
    config_service: ConfigService,
    username: str,
    user_id: int,
):
    """MFA 验证通过后签发 JWT 并创建会话，返回 Token 响应体"""
    db = get_database_service()

    async with db.transaction() as session:
        access_token, jti, expire_minutes = await security.create_access_token(
            data={"sub": username}, session=session
        )
        # 更新用户登录信息
        await db.user.update_login_info(username, access_token)

        # 使用登录会话数据域，而非数据库连接会话。
        client_ip = await security.get_real_client_ip(request, config_service)
        user_agent = request.headers.get("user-agent", "")
        await db.session_store.create(
            user_id=user_id,
            jti=jti,
            ip_address=client_ip,
            user_agent=user_agent,
            expires_minutes=expire_minutes,
        )

    return {"accessToken": access_token, "tokenType": "bearer", "expiresIn": expire_minutes}


@router.post("/verify", summary="统一 MFA 验证并签发 JWT")
async def mfa_verify(
    request: Request,
    mfa_token: str = Form(..., description="密码验证后获得的临时 MFA 令牌"),
    otp_code: Optional[str] = Form(None, description="TOTP 验证码（6位）"),
    passkey_credential: Optional[str] = Form(None, description="PassKey WebAuthn 凭证 JSON"),
    config_service: ConfigService = Depends(get_config_service),
):
    """
    统一 MFA 验证接口。密码验证通过后，前端用 mfaToken 调此接口完成二步验证。
    支持 TOTP 验证码 或 PassKey 凭证，验证通过后直接签发 JWT。
    """
    # 验证 mfa_token
    result = consume_mfa_token(mfa_token)
    if not result:
        raise HTTPException(status_code=401, detail="MFA 令牌无效或已过期")
    username, user_id = result

    db = get_database_service()
    async with db.transaction():
        user = await db.user.get_by_username(username)

    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")

    # 尝试 TOTP 验证
    if otp_code:
        otp_secret = user.otpSecret if hasattr(user, 'otpSecret') else None
        if otp_secret and verify_otp(otp_secret, otp_code):
            return await _issue_jwt_for_user(request, config_service, username, user_id)
        raise HTTPException(status_code=401, detail="验证码错误")

    # 尝试 PassKey 验证
    if passkey_credential:
        rp_id = _get_rp_id(request)
        origin = _get_origin(request)

        # 获取 challenge
        challenge_key = f"passkey_auth_{username}"
        challenge = _get_and_consume_challenge(challenge_key)
        if not challenge:
            raise HTTPException(status_code=400, detail="PassKey 认证已过期，请重新开始")

        try:
            cred_data = json.loads(passkey_credential)
            raw_id = cred_data.get("rawId") or cred_data.get("id", "")
        except (json.JSONDecodeError, AttributeError):
            raise HTTPException(status_code=400, detail="无效的凭证数据")

        async with db.transaction():
            passkey_record = await db.passkey.get_by_credential_id(raw_id)

        if not passkey_record or passkey_record.userId != user_id:
            raise HTTPException(status_code=400, detail="未找到对应的 PassKey")

        try:
            verification = verify_authentication_response(
                credential=passkey_credential,
                expected_challenge=challenge,
                expected_rp_id=rp_id,
                expected_origin=origin,
                credential_public_key=base64url_to_bytes(passkey_record.publicKey),
                credential_current_sign_count=passkey_record.signCount,
            )
        except Exception as e:
            logger.warning(f"PassKey MFA 验证失败: {e}")
            raise HTTPException(status_code=400, detail=f"PassKey 验证失败: {str(e)}")

        async with db.transaction():
            await db.passkey.update_sign_count(raw_id, verification.new_sign_count)

        return await _issue_jwt_for_user(request, config_service, username, user_id)

    raise HTTPException(status_code=400, detail="请提供 TOTP 验证码或 PassKey 凭证")


# ======== PassKey 无密码直接登录 ========

@router.post("/passkey/login/options", summary="PassKey 无密码登录 - 获取认证选项")
async def passkey_login_options(
    request: Request,
):
    """
    PassKey 无密码登录第一步：生成 WebAuthn 认证选项。
    不需要输入用户名，浏览器会自动匹配已注册的 PassKey。
    返回中包含 sessionId，前端验证时需回传。
    """
    rp_id = _get_rp_id(request)

    options = generate_authentication_options(
        rp_id=rp_id,
        user_verification=UserVerificationRequirement.PREFERRED,
    )

    # 使用随机 session_id 作为 challenge key，避免并发冲突
    session_id = secrets.token_urlsafe(16)
    _store_challenge(f"passkey_login_{session_id}", options.challenge)
    return {"options": options_to_json(options), "sessionId": session_id}


@router.post("/passkey/login/verify", summary="PassKey 无密码登录 - 验证并签发 JWT")
async def passkey_login_verify(
    request: Request,
    credential: str = Form(..., description="WebAuthn 凭证 JSON"),
    session_id: str = Form(..., description="获取认证选项时返回的 sessionId"),
    config_service: ConfigService = Depends(get_config_service),
):
    """
    PassKey 无密码登录第二步：验证凭证并直接签发 JWT。
    """
    challenge = _get_and_consume_challenge(f"passkey_login_{session_id}")
    if not challenge:
        raise HTTPException(status_code=400, detail="登录已过期，请重新开始")

    rp_id = _get_rp_id(request)
    origin = _get_origin(request)

    try:
        cred_data = json.loads(credential)
        raw_id = cred_data.get("rawId") or cred_data.get("id", "")
    except (json.JSONDecodeError, AttributeError):
        raise HTTPException(status_code=400, detail="无效的凭证数据")

    db = get_database_service()
    async with db.transaction():
        passkey_record = await db.passkey.get_by_credential_id(raw_id)

    if not passkey_record:
        raise HTTPException(status_code=400, detail="未注册的 PassKey")

    try:
        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=base64url_to_bytes(passkey_record.publicKey),
            credential_current_sign_count=passkey_record.signCount,
        )
    except Exception as e:
        logger.warning(f"PassKey 直接登录验证失败: {e}")
        raise HTTPException(status_code=400, detail=f"PassKey 验证失败: {str(e)}")

    async with db.transaction():
        await db.passkey.update_sign_count(raw_id, verification.new_sign_count)

    # 查找用户
    async with db.transaction():
        user = await db.user.get_by_id(passkey_record.userId)

    if not user:
        raise HTTPException(status_code=400, detail="用户不存在")

    return await _issue_jwt_for_user(
        request, config_service,
        user.username, user.id,
    )