"""认证业务服务：JWT、用户校验与白名单会话，不依赖 HTTP 接入层。"""

import hashlib
import uuid
from datetime import timedelta
from typing import Any, Dict, Optional, Tuple

from jose import JWTError, jwt
from sqlalchemy.exc import IntegrityError

from src.core import get_now, settings
from src.schemas.auth import User
from src.services.config_service import get_config_service
from src.services.service_container import get_database_service
from src.utils.auth.ip import is_ip_whitelisted


class AuthenticationError(Exception):
    """凭据无效或客户端不满足白名单认证条件。"""


class AuthUnavailableError(Exception):
    """认证所需的配置、用户或会话持久化暂时不可用。"""


def _expire_minutes(value: Any) -> int:
    """将配置约束为有限有效期，避免无效配置生成永久令牌。"""
    default = settings.jwt.access_token_expire_minutes
    if default <= 0:
        default = 3 * 24 * 60
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        minutes = default
    return min(minutes if minutes > 0 else default, 30 * 24 * 60)


def _encode_token(data: Dict[str, Any], secret: str, minutes: int, jti: str) -> str:
    """按现有 JWT 算法生成带固定有效期和会话标识的令牌。"""
    now = get_now()
    payload = data.copy()
    payload.update(iat=now, jti=jti, exp=now + timedelta(minutes=minutes))
    return jwt.encode(payload, secret, algorithm=settings.jwt.algorithm)


def _is_duplicate_key(error: IntegrityError) -> bool:
    """仅识别唯一键冲突，不能把外键等完整性错误当作并发创建。"""
    original = error.orig
    code = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if code == "23505":
        return True
    if getattr(original, "sqlite_errorcode", None) in (1555, 2067):
        return True
    args = getattr(original, "args", ())
    return bool(args and args[0] == 1062)


async def create_access_token(
    data: Dict[str, Any],
    jti: Optional[str] = None,
) -> Tuple[str, str, int]:
    """使用当前配置签发令牌，配置读取事务结束后返回。"""
    try:
        db = get_database_service()
        async with db.transaction():
            secret = await get_config_service().get("jwtSecretKey", settings.jwt.secret_key)
            value = await get_config_service().get("jwtExpireMinutes", str(settings.jwt.access_token_expire_minutes))
            minutes = _expire_minutes(value)
            token_jti = jti if jti is not None else str(uuid.uuid4())
            token = _encode_token(data, secret, minutes, token_jti)
        return token, token_jti, minutes
    except Exception as error:
        raise AuthUnavailableError("访问令牌签发失败") from error


async def create_channel_access_token(username: str) -> str:
    """为通知渠道确认操作建立可撤销会话，提交后才返回令牌。"""
    token, jti, minutes = await create_access_token({"sub": username})
    db = get_database_service()
    async with db.transaction():
        user = await db.user.get_by_username(username)
        if user is None:
            raise AuthenticationError("Could not validate credentials")
        await db.session_store.create(
            user_id=user.id, jti=jti, ip_address=None,
            user_agent="notification-channel", expires_minutes=minutes,
        )
    return token


async def get_user_from_token(
    token: str,
) -> Tuple[User, str]:
    """解码令牌并验证用户与会话；提供凭据时失败不得回退白名单。"""
    if not token:
        raise AuthenticationError("Could not validate credentials")
    try:
        db = get_database_service()
        async with db.transaction():
            secret = await get_config_service().get("jwtSecretKey", settings.jwt.secret_key)
            try:
                payload = jwt.decode(token, secret, algorithms=[settings.jwt.algorithm])
            except JWTError as error:
                raise AuthenticationError("Could not validate credentials") from error
            username = payload.get("sub")
            token_jti = payload.get("jti")
            if not isinstance(username, str) or not username:
                raise AuthenticationError("Could not validate credentials")
            if not isinstance(token_jti, str) or not token_jti or token_jti.startswith("whitelist_") or "exp" not in payload:
                raise AuthenticationError("Could not validate credentials")
            # 每个令牌必须对应可撤销的持久化会话，不接受无会话旧凭据。
            if not await db.session_store.validate(token_jti):
                raise AuthenticationError("Could not validate credentials")
            record = await db.user.get_by_username(username)
            if record is None:
                raise AuthenticationError("Could not validate credentials")
            user = User.model_validate(record)
        return user, token_jti
    except AuthenticationError:
        raise
    except Exception as error:
        raise AuthUnavailableError("用户认证暂时不可用") from error


async def _whitelist_login(
    client_ip: str,
    user_agent: str,
    *,
    issue_token: bool,
) -> Optional[Tuple[User, str, Optional[str], int]]:
    """每次从数据库确认授权，并在会话提交成功后返回认证结果。"""
    try:
        db = get_database_service()
        for attempt in range(2):
            try:
                # 唯一键并发冲突必须离开失败事务后重试，不能复用已回滚会话。
                async with db.transaction():
                    whitelist = await get_config_service().get("ipWhitelist", "")
                    if not is_ip_whitelisted(client_ip, whitelist or ""):
                        result = None
                    else:
                        admin = await db.user.get_by_username("admin")
                        if admin is None:
                            raise AuthUnavailableError("白名单认证所需的管理员用户不存在")
                        user = User.model_validate(admin)
                        value = await get_config_service().get("jwtExpireMinutes", str(settings.jwt.access_token_expire_minutes))
                        minutes = _expire_minutes(value)
                        agent = user_agent or ""
                        agent_hash = hashlib.md5(agent.encode()).hexdigest()[:8] if agent else "unknown"
                        # 自动登录令牌使用独立会话，撤销后不会被白名单免登录重新激活。
                        token_jti = str(uuid.uuid4()) if issue_token else f"whitelist_{client_ip}_{agent_hash}"
                        await db.session_store.ensure_active_by_jti(
                            user_id=user.id,
                            jti=token_jti,
                            ip_address=client_ip,
                            user_agent=agent[:500] if agent else None,
                            expires_minutes=minutes,
                            fresh=issue_token,
                        )
                        token = None
                        if issue_token:
                            secret = await get_config_service().get("jwtSecretKey", settings.jwt.secret_key)
                            token = _encode_token({"sub": user.username}, secret, minutes, token_jti)
                        result = user, token_jti, token, minutes
                return result
            except IntegrityError as error:
                if attempt == 0 and _is_duplicate_key(error):
                    continue
                raise AuthUnavailableError("白名单会话持久化失败") from error
        raise AuthUnavailableError("白名单会话持久化失败")
    except AuthUnavailableError:
        raise
    except Exception as error:
        raise AuthUnavailableError("白名单认证暂时不可用") from error


async def check_ip_whitelist(client_ip: str, user_agent: str) -> Optional[Tuple[User, str]]:
    """检查统一安全策略下的 IP 白名单，每次校验授权并持久化有效会话。"""
    result = await _whitelist_login(client_ip, user_agent, issue_token=False)
    if result is None:
        return None
    user, token_jti, _, _ = result
    return user, token_jti


async def auto_login(client_ip: str, user_agent: str) -> Dict[str, Any]:
    """白名单自动登录，刷新会话有效期；提交失败时不得返回成功令牌。"""
    result = await _whitelist_login(client_ip, user_agent, issue_token=True)
    if result is None:
        raise AuthenticationError("Not in IP whitelist")
    _, _, token, minutes = result
    return {"accessToken": token, "tokenType": "bearer", "expiresIn": minutes}
