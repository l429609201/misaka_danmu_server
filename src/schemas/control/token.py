"""
Control API - Token 管理相关模型
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class ApiTokenInfo(BaseModel):
    """API Token 信息"""
    id: int
    name: str
    token: str
    isEnabled: bool
    createdAt: datetime
    expiresAt: Optional[datetime] = None
    dailyCallLimit: int
    dailyCallCount: int


class ApiTokenCreate(BaseModel):
    """创建 API Token"""
    name: str = Field(..., min_length=1, max_length=50, description="Token 的描述性名称")
    validityPeriod: str = Field("permanent", description="有效期: permanent, 1d, 7d, 30d, 180d, 365d")
    dailyCallLimit: int = Field(500, description="每日调用次数限制, -1 表示无限")
    customToken: Optional[str] = Field(None, min_length=5, max_length=100, description="自定义Token字符串，留空则自动生成")


class TokenAccessLog(BaseModel):
    """Token 访问日志，使用日志主键区分同一时刻的多个请求。"""
    id: int
    accessTime: datetime
    ipAddress: str
    status: str
    path: Optional[str] = None
    userAgent: Optional[str] = None
    method: Optional[str] = None
    requestHeaders: Optional[str] = None
    requestBody: Optional[str] = None
    responseHeaders: Optional[str] = None
    responseBody: Optional[str] = None
    statusCode: Optional[int] = None


class UaRule(BaseModel):
    """UA 过滤规则"""
    id: int
    uaString: str
    createdAt: datetime


class UaRuleCreate(BaseModel):
    """创建 UA 规则"""
    uaString: str


__all__ = [
    "ApiTokenInfo",
    "ApiTokenCreate",
    "TokenAccessLog",
    "UaRule",
    "UaRuleCreate",
]
