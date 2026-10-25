"""
系统管理相关的 Schema 定义
"""
from typing import Optional, List
from pydantic import BaseModel, Field


class DatabaseInfoResponse(BaseModel):
    """数据库和缓存连接信息（含连接池和 Redis 详细指标）

    字段名与 workflow_get_database_info 返回值、前端 DatabaseInfoPanel 保持一致；
    Redis 未启用或 INFO 获取失败时相关字段为 None，故均为 Optional。
    """
    # 数据库基本信息
    dbType: str = Field(..., description="数据库类型 (mysql/postgresql/sqlite)")
    dbHost: Optional[str] = Field(None, description="数据库主机")
    dbPort: Optional[int] = Field(None, description="数据库端口")
    dbName: Optional[str] = Field(None, description="数据库名称")
    # 数据库连接池
    dbPoolType: Optional[str] = Field(None, description="连接池类型")
    dbPoolSize: int = Field(0, description="连接池常驻大小")
    dbActiveConnections: int = Field(0, description="活跃连接数 (checkedout)")
    dbIdleConnections: int = Field(0, description="空闲连接数 (checkedin)")
    dbOverflow: int = Field(0, description="当前溢出连接数")
    dbMaxOverflow: int = Field(0, description="最大溢出连接数")
    dbPoolRecycle: Optional[int] = Field(None, description="连接回收时间（秒）")
    # 缓存 / Redis 信息
    cacheBackend: Optional[str] = Field(None, description="缓存后端类型")
    redisUrl: Optional[str] = Field(None, description="Redis 地址（已脱敏）")
    redisConnected: bool = Field(False, description="Redis 是否已连接")
    redisVersion: Optional[str] = Field(None, description="Redis 版本")
    redisMemoryUsed: Optional[str] = Field(None, description="Redis 已用内存（可读）")
    redisMemoryMax: Optional[str] = Field(None, description="Redis 最大内存（可读）")
    redisMemoryUsedBytes: Optional[int] = Field(None, description="Redis 已用内存（字节）")
    redisMemoryMaxBytes: Optional[int] = Field(None, description="Redis 最大内存（字节）")
    redisTotalKeys: Optional[int] = Field(None, description="Redis 总键数")
    redisConnectedClients: Optional[int] = Field(None, description="Redis 连接客户端数")
    redisUptimeSeconds: Optional[int] = Field(None, description="Redis 运行时长（秒）")


class VersionCheckResponse(BaseModel):
    """版本检查响应"""
    currentVersion: str = Field(..., description="当前版本")
    latestVersion: Optional[str] = Field(None, description="最新版本")
    hasUpdate: bool = Field(False, description="是否有更新")
    releaseUrl: Optional[str] = Field(None, description="Release 页面链接")
    changelog: Optional[str] = Field(None, description="更新日志（Markdown）")
    publishedAt: Optional[str] = Field(None, description="发布时间")


class ReleaseInfo(BaseModel):
    """单个 Release 信息"""
    version: str = Field(..., description="版本号")
    releaseUrl: str = Field(..., description="Release 页面链接")
    changelog: str = Field("", description="更新日志（Markdown）")
    publishedAt: str = Field(..., description="发布时间")


class ReleasesResponse(BaseModel):
    """历史 Releases 响应"""
    releases: List[ReleaseInfo] = Field(default_factory=list, description="历史版本列表")


class ParseFilenameRequest(BaseModel):
    """文件名解析请求"""
    filename: str = Field(..., description="要解析的文件名", alias="fileName")


class DockerStatusResponse(BaseModel):
    """Docker 状态响应"""
    sdkInstalled: bool = Field(..., description="Docker SDK 是否已安装")
    socketAvailable: bool = Field(..., description="Docker socket 是否可用")
    socketPath: str = Field(..., description="Docker socket 路径")
    canRestart: bool = Field(..., description="是否可以通过 Docker API 重启")
    canUpdate: bool = Field(..., description="是否可以通过 Docker API 更新")
    message: str = Field(..., description="状态消息")


class RestartResponse(BaseModel):
    """重启响应"""
    success: bool
    message: str
    method: str = Field(..., description="重启方式: docker_api 或 process_exit")
