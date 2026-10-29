"""
TOTP (Time-Based One-Time Password) 工具模块

基于 pyotp 库实现 RFC 6238 TOTP 两步验证功能。
包含 OTP Secret 的加解密，防止数据库泄露时 MFA 失效。
"""

import base64
import logging

import pyotp
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

# 应用名称，显示在验证器 App 中
APP_ISSUER = "Misaka弹幕库"

# PBKDF2 固定盐值（改变会导致已加密数据无法解密）
_OTP_KDF_SALT = b"misaka-otp-secret-encryption-v1"

# 模块级缓存，避免每次调用都派生密钥
_fernet_cache: dict[str, Fernet] = {}


def _get_fernet(master_key: str) -> Fernet:
    """从 master_key（jwt.secret_key）派生 Fernet 密钥并缓存。"""
    if master_key in _fernet_cache:
        return _fernet_cache[master_key]

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_OTP_KDF_SALT,
        iterations=100_000,
    )
    derived_key = base64.urlsafe_b64encode(kdf.derive(master_key.encode()))
    fernet = Fernet(derived_key)
    _fernet_cache[master_key] = fernet
    return fernet


def encrypt_otp_secret(secret: str, master_key: str) -> str:
    """
    加密 OTP secret。

    :param secret: 明文 TOTP secret (base32)
    :param master_key: 主密钥（通常为 config.yml 的 jwt.secret_key）
    :return: 加密后的字符串
    """
    fernet = _get_fernet(master_key)
    return fernet.encrypt(secret.encode()).decode()


def decrypt_otp_secret(stored_value: str, master_key: str) -> str:
    """
    解密 OTP secret。

    :param stored_value: 数据库中存储的加密值
    :param master_key: 主密钥
    :return: 明文 TOTP secret (base32)
    """
    if not stored_value:
        return stored_value

    try:
        fernet = _get_fernet(master_key)
        return fernet.decrypt(stored_value.encode()).decode()
    except InvalidToken:
        logger.error("OTP Secret 解密失败（密钥可能已变更）")
        return ""


def generate_secret() -> str:
    """
    生成一个随机的 TOTP 密钥 (base32 编码)。

    :return: base32 编码的密钥字符串
    """
    return pyotp.random_base32()


def get_totp_uri(secret: str, username: str, issuer: str = APP_ISSUER) -> str:
    """
    生成 otpauth:// URI，用于生成二维码供验证器 App 扫描。

    :param secret: base32 编码的密钥
    :param username: 用户名（显示在验证器中）
    :param issuer: 应用名称
    :return: otpauth:// URI
    """
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=username, issuer_name=issuer)


def verify_otp(secret: str, code: str, valid_window: int = 1) -> bool:
    """
    验证用户提交的 TOTP 验证码。

    :param secret: base32 编码的密钥
    :param code: 用户提交的 6 位验证码
    :param valid_window: 允许的时间窗口偏差（默认 ±1 个周期，即前后各 30 秒）
    :return: 验证码是否正确
    """
    try:
        totp = pyotp.TOTP(secret)
        return totp.verify(code, valid_window=valid_window)
    except Exception as e:
        logger.warning(f"TOTP 验证异常: {e}")
        return False
