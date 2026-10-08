"""
认证相关模型
"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


# --- 用户和认证模型 ---
class UserBase(BaseModel):
    """用户基础信息"""
    username: str


class UserCreate(UserBase):
    """创建用户"""
    password: str


class User(UserBase):
    """用户信息"""
    id: int

    class Config:
        from_attributes = True  # 允许从ORM对象创建模型


class Token(BaseModel):
    """Token 响应"""
    accessToken: str
    tokenType: str


class TokenData(BaseModel):
    """Token 数据"""
    username: Optional[str] = None


class PasswordChange(BaseModel):
    """密码修改请求"""
    oldPassword: str = Field(..., description="当前密码")
    newPassword: str = Field(..., min_length=8, description="新密码 (至少8位)")


# --- MFA 模型 ---
class MfaRequiredResponse(BaseModel):
    """MFA 验证要求响应"""
    detail: str = "MFA required"
    mfaRequired: bool = True
    mfaTypes: List[str] = Field(default_factory=list, description="可用的MFA类型: totp, passkey")


class TotpSetupResponse(BaseModel):
    """TOTP 设置响应（包含密钥和二维码URI）"""
    secret: str = Field(..., description="TOTP 密钥 (base32)")
    uri: str = Field(..., description="otpauth:// URI，用于生成二维码")


class TotpVerifyRequest(BaseModel):
    """TOTP 验证请求"""
    code: str = Field(..., min_length=6, max_length=6, description="6位验证码")


class TotpDisableRequest(BaseModel):
    """关闭 TOTP 请求"""
    password: str = Field(..., description="当前密码（安全确认）")


class PassKeyInfo(BaseModel):
    """PassKey 信息"""
    id: int
    deviceName: Optional[str] = None
    createdAt: Optional[datetime] = None
    lastUsedAt: Optional[datetime] = None

    class Config:
        from_attributes = True


class PassKeyRegisterRequest(BaseModel):
    """PassKey 注册验证请求"""
    credential: str = Field(..., description="浏览器返回的 WebAuthn 凭证 JSON")
    deviceName: Optional[str] = Field(None, description="设备名称")


class PassKeyAuthenticateRequest(BaseModel):
    """PassKey 认证验证请求"""
    credential: str = Field(..., description="浏览器返回的 WebAuthn 断言 JSON")


class PassKeyRenameRequest(BaseModel):
    """PassKey 重命名请求"""
    deviceName: str = Field(..., description="新的设备名称")


class MfaStatusResponse(BaseModel):
    """MFA 状态响应"""
    totpEnabled: bool = False
    passkeyCount: int = 0
    passkeys: List[PassKeyInfo] = Field(default_factory=list)


class BangumiAuthStatus(BaseModel):
    """Bangumi 认证状态"""
    isAuthenticated: bool
    nickname: Optional[str] = None
    username: Optional[str] = None
    sign: Optional[str] = None
    avatarUrl: Optional[str] = None
    bangumiUserId: Optional[int] = None
    authorizedAt: Optional[datetime] = None
    expiresAt: Optional[datetime] = None


__all__ = [
    "UserBase",
    "UserCreate",
    "User",
    "Token",
    "TokenData",
    "PasswordChange",
    "MfaRequiredResponse",
    "TotpSetupResponse",
    "TotpVerifyRequest",
    "TotpDisableRequest",
    "PassKeyInfo",
    "PassKeyRegisterRequest",
    "PassKeyAuthenticateRequest",
    "PassKeyRenameRequest",
    "MfaStatusResponse",
    "BangumiAuthStatus",
]
