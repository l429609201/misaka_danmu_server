from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, Dict
import uuid
import ipaddress
import logging
import time
import hashlib

from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from src.db import crud, models, get_db_session
from src.core import settings, get_now
from src.db.crud import session as session_crud

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/ui/auth/token")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """生成密码哈希"""
    return pwd_context.hash(password)


async def get_real_client_ip(request: Request, config_manager) -> str:
    """
    获取真实客户端IP，支持信任反代。

    :param request: FastAPI Request 对象
    :param config_manager: 配置管理器
    :return: 客户端真实IP
    """
    trusted_proxies_str = await config_manager.get("trustedProxies", "")
    trusted_networks = []
    if trusted_proxies_str:
        for proxy_entry in trusted_proxies_str.split(','):
            try:
                trusted_networks.append(ipaddress.ip_network(proxy_entry.strip()))
            except ValueError:
                logger.warning(f"无效的受信任代理IP或CIDR: '{proxy_entry.strip()}'，已忽略。")

    client_ip_str = request.client.host if request.client else "127.0.0.1"
    is_trusted = False
    if trusted_networks:
        try:
            client_addr = ipaddress.ip_address(client_ip_str)
            is_trusted = any(client_addr in network for network in trusted_networks)
        except ValueError:
            logger.warning(f"无法将客户端IP '{client_ip_str}' 解析为有效的IP地址。")

    if is_trusted:
        x_forwarded_for = request.headers.get("x-forwarded-for")
        if x_forwarded_for:
            client_ip_str = x_forwarded_for.split(',')[0].strip()
        else:
            client_ip_str = request.headers.get("x-real-ip", client_ip_str)

    return client_ip_str


# IP 白名单会话缓存
# key: (client_ip, ua_hash), value: (user, timestamp, ttl_seconds, jti)
_whitelist_session_cache: Dict[Tuple[str, str], Tuple[models.User, float, int, str]] = {}


def _get_real_client_ip_sync(request: Request, trusted_proxies_str: str) -> str:
    """
    同步获取真实客户端 IP（用于白名单检查）
    """
    trusted_networks = []
    if trusted_proxies_str:
        for proxy_entry in trusted_proxies_str.split(','):
            try:
                trusted_networks.append(ipaddress.ip_network(proxy_entry.strip(), strict=False))
            except ValueError:
                pass

    client_ip_str = request.client.host if request.client else "127.0.0.1"
    original_client_ip = client_ip_str  # 保存原始 IP 用于调试

    if trusted_networks:
        try:
            client_addr = ipaddress.ip_address(client_ip_str)
            is_trusted = any(client_addr in network for network in trusted_networks)
            if is_trusted:
                x_forwarded_for = request.headers.get("x-forwarded-for")
                x_real_ip = request.headers.get("x-real-ip")
                if x_forwarded_for:
                    client_ip_str = x_forwarded_for.split(',')[0].strip()
                    logger.debug(f"[IP解析] 原始IP={original_client_ip}, 受信任=True, X-Forwarded-For={x_forwarded_for}, 解析后IP={client_ip_str}")
                elif x_real_ip:
                    client_ip_str = x_real_ip
                    logger.debug(f"[IP解析] 原始IP={original_client_ip}, 受信任=True, X-Real-IP={x_real_ip}, 解析后IP={client_ip_str}")
                else:
                    logger.debug(f"[IP解析] 原始IP={original_client_ip}, 受信任=True, 但无X-Forwarded-For或X-Real-IP头")
            else:
                logger.debug(f"[IP解析] 原始IP={original_client_ip}, 受信任=False, 配置的受信任网段={[str(n) for n in trusted_networks]}")
        except ValueError as e:
            logger.warning(f"[IP解析] 无法解析IP地址: {client_ip_str}, 错误: {e}")

    return client_ip_str


# 危险的 CIDR 网段（会匹配所有 IP）
_DANGEROUS_NETWORKS = [
    "0.0.0.0/0",      # 所有 IPv4
    "::/0",           # 所有 IPv6
    "0.0.0.0/1",      # 一半 IPv4
    "128.0.0.0/1",    # 另一半 IPv4
]


async def check_ip_whitelist(request: Request, session: AsyncSession) -> Optional[models.User]:
    """
    检查客户端 IP 是否在白名单中。
    如果在白名单中，返回系统管理员用户；否则返回 None。
    使用内存缓存减少重复检查和日志输出。

    :param request: FastAPI Request 对象
    :param session: 数据库会话
    :return: 如果 IP 在白名单中返回管理员用户，否则返回 None
    """
    global _whitelist_session_cache

    # 获取 IP 白名单配置
    ip_whitelist_str = await crud.get_config_value(session, "ipWhitelist", "")
    if not ip_whitelist_str or not ip_whitelist_str.strip():
        # 白名单为空，清除所有缓存的白名单会话
        if _whitelist_session_cache:
            logger.info("IP 白名单已清空，清除所有缓存的白名单会话")
            _whitelist_session_cache.clear()
        return None

    # 获取受信任代理配置并解析真实 IP
    trusted_proxies_str = await crud.get_config_value(session, "trustedProxies", "")
    client_ip_str = _get_real_client_ip_sync(request, trusted_proxies_str)

    logger.debug(f"[IP白名单检查] 客户端IP={client_ip_str}, 白名单配置={ip_whitelist_str}, 受信任代理={trusted_proxies_str}")

    # 解析白名单网段（每次都解析，以便检测配置变更）
    whitelist_networks = []
    for entry in ip_whitelist_str.split(','):
        entry = entry.strip()
        if not entry:
            continue
        # 安全检查：阻止危险的 CIDR 配置
        if entry in _DANGEROUS_NETWORKS:
            logger.error(f"危险的 IP 白名单配置被阻止: '{entry}'（会匹配所有 IP）")
            continue
        try:
            network = ipaddress.ip_network(entry, strict=False)
            # 额外检查：阻止过大的网段（/8 以下，即超过 1600 万个 IP）
            if network.version == 4 and network.prefixlen < 8:
                logger.error(f"危险的 IP 白名单配置被阻止: '{entry}'（网段过大，包含超过 1600 万个 IP）")
                continue
            if network.version == 6 and network.prefixlen < 32:
                logger.error(f"危险的 IP 白名单配置被阻止: '{entry}'（IPv6 网段过大）")
                continue
            whitelist_networks.append(network)
        except ValueError:
            logger.warning(f"无效的 IP 白名单条目: '{entry}'，已忽略。")

    if not whitelist_networks:
        logger.debug(f"[IP白名单检查] 解析后白名单网段为空")
        return None

    logger.debug(f"[IP白名单检查] 解析后白名单网段={[str(n) for n in whitelist_networks]}")

    # 获取 User-Agent 并生成哈希（用于区分同 IP 不同浏览器）
    user_agent = request.headers.get("user-agent", "")
    ua_hash = hashlib.md5(user_agent.encode()).hexdigest()[:8] if user_agent else "unknown"
    cache_key = (client_ip_str, ua_hash)

    # 检查缓存：如果该 IP + UA 已经验证过且未过期
    current_time = time.time()
    if cache_key in _whitelist_session_cache:
        cached_user, cached_time, cached_ttl, cached_jti = _whitelist_session_cache[cache_key]

        # 【安全检查】验证该 IP 是否仍在当前白名单中
        try:
            client_addr = ipaddress.ip_address(client_ip_str)
            still_whitelisted = any(client_addr in network for network in whitelist_networks)
        except ValueError:
            still_whitelisted = False

        if not still_whitelisted:
            # IP 已从白名单移除，立即撤销会话
            logger.warning(f"IP {client_ip_str} 已从白名单移除，撤销其会话")
            del _whitelist_session_cache[cache_key]
            try:
                await session_crud.revoke_session_by_jti(session, cached_jti)
            except Exception as e:
                logger.warning(f"撤销白名单会话失败: {e}")
            return None

        if current_time - cached_time < cached_ttl:
            # 缓存有效，直接返回（不打印日志）
            return cached_user
        else:
            # 缓存过期，删除缓存并撤销数据库中的会话
            del _whitelist_session_cache[cache_key]
            try:
                await session_crud.revoke_session_by_jti(session, cached_jti)
            except Exception as e:
                logger.warning(f"撤销过期白名单会话失败: {e}")

    # 检查客户端 IP 是否在白名单中
    try:
        client_addr = ipaddress.ip_address(client_ip_str)
        is_whitelisted = any(client_addr in network for network in whitelist_networks)

        if is_whitelisted:
            # 获取管理员用户（必须存在，否则不允许白名单登录）
            admin_user = await crud.get_user_by_username(session, "admin")
            if not admin_user:
                logger.error("IP 白名单功能需要 admin 用户存在，但未找到 admin 用户")
                return None

            user = models.User.model_validate(admin_user)
            user_id = admin_user["id"]

            # 获取 JWT 有效期配置（与正常登录一致）
            expire_minutes_str = await crud.get_config_value(session, 'jwtExpireMinutes', str(settings.jwt.access_token_expire_minutes))
            expire_minutes = int(expire_minutes_str)
            # 如果是 -1（永不过期），使用一个较大的值（7天）
            if expire_minutes == -1:
                ttl_seconds = 7 * 24 * 60 * 60  # 7 天
                db_expire_minutes = None  # 数据库中不设置过期时间
            else:
                ttl_seconds = expire_minutes * 60  # 转换为秒
                db_expire_minutes = expire_minutes

            # 使用 IP + UA哈希 作为会话 ID，区分同 IP 不同浏览器
            jti = f"whitelist_{client_ip_str}_{ua_hash}"

            # 检查数据库中是否已存在该 jti 的会话
            existing_session = await session_crud.get_session_by_jti(session, jti)
            if existing_session:
                if not existing_session.get("isRevoked"):
                    # 检查是否过期
                    expires_at = existing_session.get("expiresAt")
                    if expires_at is None or expires_at > get_now():
                        # 会话有效，更新最后使用时间并复用
                        await session_crud.update_session_last_used(session, jti)
                        _whitelist_session_cache[cache_key] = (user, current_time, ttl_seconds, jti)
                        logger.debug(f"IP {client_ip_str} 复用已有的白名单会话")
                        return user

                # 会话已过期或被撤销，删除旧会话以便重建
                await session_crud.delete_session_by_jti(session, jti)

            # 创建新的数据库会话记录
            try:
                await session_crud.create_user_session(
                    session=session,
                    user_id=user_id,
                    jti=jti,
                    ip_address=client_ip_str,
                    user_agent=user_agent[:500] if user_agent else None,
                    expires_minutes=db_expire_minutes
                )
            except Exception as e:
                # 可能是并发请求导致的重复键错误，尝试复用已存在的会话
                if "Duplicate entry" in str(e) or "UNIQUE constraint" in str(e):
                    logger.debug(f"白名单会话已被其他请求创建，尝试复用: {jti}")
                    # 更新最后使用时间
                    try:
                        await session_crud.update_session_last_used(session, jti)
                    except Exception:
                        pass
                    _whitelist_session_cache[cache_key] = (user, current_time, ttl_seconds, jti)
                    return user
                else:
                    logger.error(f"创建白名单会话记录失败: {e}")
                # 即使数据库记录失败，仍然允许访问（但不缓存）
                return user

            # 缓存结果并打印一次日志
            _whitelist_session_cache[cache_key] = (user, current_time, ttl_seconds, jti)
            logger.info(f"IP {client_ip_str} 在白名单中，已建立免登录会话（有效期 {expire_minutes if expire_minutes != -1 else '永久'} 分钟）")
            return user
    except ValueError:
        logger.warning(f"无法解析客户端 IP '{client_ip_str}'")

    return None


def clear_whitelist_session_cache(ip: Optional[str] = None):
    """
    清除白名单会话缓存。

    :param ip: 指定要清除的 IP，如果为 None 则清除所有缓存
    """
    global _whitelist_session_cache
    if ip:
        # 清除该 IP 的所有会话（不同 UA 的）
        keys_to_remove = [k for k in _whitelist_session_cache if k[0] == ip]
        for key in keys_to_remove:
            del _whitelist_session_cache[key]
    else:
        _whitelist_session_cache.clear()


async def check_ip_whitelist_with_jti(request: Request, session: AsyncSession) -> Optional[Tuple[models.User, Optional[str]]]:
    """
    检查客户端 IP 是否在白名单中，并返回用户和 jti。
    用于需要 jti 的场景（如会话管理）。

    :param request: FastAPI Request 对象
    :param session: 数据库会话
    :return: 如果 IP 在白名单中返回 (用户, jti)，否则返回 None
    """
    global _whitelist_session_cache

    # 获取 IP 白名单配置
    ip_whitelist_str = await crud.get_config_value(session, "ipWhitelist", "")
    if not ip_whitelist_str or not ip_whitelist_str.strip():
        return None

    # 获取受信任代理配置并解析真实 IP
    trusted_proxies_str = await crud.get_config_value(session, "trustedProxies", "")
    client_ip_str = _get_real_client_ip_sync(request, trusted_proxies_str)

    # 获取 User-Agent 并生成哈希
    user_agent = request.headers.get("user-agent", "")
    ua_hash = hashlib.md5(user_agent.encode()).hexdigest()[:8] if user_agent else "unknown"
    cache_key = (client_ip_str, ua_hash)

    # 检查缓存
    current_time = time.time()
    if cache_key in _whitelist_session_cache:
        cached_user, cached_time, cached_ttl, cached_jti = _whitelist_session_cache[cache_key]
        if current_time - cached_time < cached_ttl:
            return cached_user, cached_jti
        else:
            del _whitelist_session_cache[cache_key]
            try:
                await session_crud.revoke_session_by_jti(session, cached_jti)
            except Exception as e:
                logger.warning(f"撤销过期白名单会话失败: {e}")

    # 调用 check_ip_whitelist 来创建会话（如果在白名单中）
    user = await check_ip_whitelist(request, session)
    if user:
        # 从缓存中获取 jti
        if cache_key in _whitelist_session_cache:
            _, _, _, jti = _whitelist_session_cache[cache_key]
            return user, jti
        return user, None

    return None


async def _get_user_from_token(token: str, session: AsyncSession, validate_session: bool = True) -> Tuple[models.User, Optional[str]]:
    """
    核心逻辑：解码JWT，验证其有效性，并获取当前用户。
    这是一个不带FastAPI依赖的辅助函数。

    :param token: JWT 令牌
    :param session: 数据库会话
    :param validate_session: 是否验证会话有效性（检查 jti 是否在会话表中且未撤销）
    :return: (用户对象, jti)
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise credentials_exception
    try:
        secret_key = await crud.get_config_value(session, 'jwtSecretKey', settings.jwt.secret_key)
        payload = jwt.decode(token, secret_key, algorithms=[settings.jwt.algorithm])
        username: str = payload.get("sub")
        jti: str = payload.get("jti")
        if username is None:
            raise credentials_exception
        token_data = models.TokenData(username=username)
    except JWTError:
        # 这将捕获过期的令牌、无效的签名等
        raise credentials_exception

    # 验证会话是否有效（未撤销且未过期）
    if validate_session and jti:
        is_valid = await session_crud.validate_session(session, jti)
        if not is_valid:
            raise credentials_exception

    user = await crud.get_user_by_username(session, username=token_data.username)
    if user is None:
        raise credentials_exception

    return models.User.model_validate(user), jti


async def create_access_token(data: dict, session: AsyncSession, expires_delta: Optional[timedelta] = None, jti: Optional[str] = None) -> Tuple[str, str, int]:
    """
    创建JWT访问令牌

    :param data: JWT payload 数据
    :param session: 数据库会话
    :param expires_delta: 过期时间增量（未使用，保留向后兼容）
    :param jti: 可选的会话ID，如果不提供则自动生成
    :return: (token, jti, expire_minutes)
    """
    to_encode = data.copy()

    # 新增：添加标准声明以增强安全性和互操作性
    now = get_now() # 使用服务器本地时间的 naive datetime
    if jti is None:
        jti = str(uuid.uuid4())
    to_encode.update({
        "iat": now,  # Issued At: 令牌签发时间
        "jti": jti,  # JWT ID: 每个令牌的唯一标识符，可用于防止重放攻击
    })

    secret_key = await crud.get_config_value(session, 'jwtSecretKey', settings.jwt.secret_key) # type: ignore
    expire_minutes_str = await crud.get_config_value(session, 'jwtExpireMinutes', str(settings.jwt.access_token_expire_minutes)) # type: ignore
    expire_minutes = int(expire_minutes_str)
    # 如果有效期不为-1，则设置过期时间
    if expire_minutes != -1:
        expire = now + timedelta(minutes=expire_minutes)
        to_encode.update({"exp": expire})
    # 如果是-1，则不添加 "exp" 字段，令牌将永不过期
    encoded_jwt = jwt.encode(to_encode, secret_key, algorithm=settings.jwt.algorithm)
    return encoded_jwt, jti, expire_minutes


# 可选的 OAuth2 scheme，允许没有 token 的请求通过（用于 IP 白名单场景）
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/ui/auth/token", auto_error=False)


async def get_current_user(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme_optional),
    session: AsyncSession = Depends(get_db_session)
) -> models.User:
    """
    依赖项：解码JWT，验证其有效性，并获取当前用户。
    支持 IP 白名单：如果客户端 IP 在白名单中，可以免登录访问。

    优先级：
    1. 如果有有效的 token，优先使用 token（避免重复创建白名单会话）
    2. 如果没有 token 或 token 无效，再检查 IP 白名单
    """
    # 如果有 token，优先尝试使用 token
    if token:
        try:
            user, _ = await _get_user_from_token(token, session)
            return user
        except HTTPException:
            # token 无效，继续检查白名单
            pass

    # 检查 IP 白名单
    whitelist_user = await check_ip_whitelist(request, session)
    if whitelist_user:
        return whitelist_user

    # 既没有有效 token，也不在白名单中
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user_with_jti(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme_optional),
    session: AsyncSession = Depends(get_db_session)
) -> Tuple[models.User, Optional[str]]:
    """
    依赖项：解码JWT，验证其有效性，并获取当前用户和 jti。
    用于需要知道当前会话 jti 的场景（如会话管理）。
    支持 IP 白名单：如果客户端 IP 在白名单中，可以免登录访问。

    优先级：
    1. 如果有有效的 token，优先使用 token（避免重复创建白名单会话）
    2. 如果没有 token 或 token 无效，再检查 IP 白名单
    """
    # 如果有 token，优先尝试使用 token
    if token:
        try:
            return await _get_user_from_token(token, session)
        except HTTPException:
            # token 无效，继续检查白名单
            pass

    # 检查 IP 白名单（需要获取 jti）
    whitelist_result = await check_ip_whitelist_with_jti(request, session)
    if whitelist_result:
        return whitelist_result

    # 既没有有效 token，也不在白名单中
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )