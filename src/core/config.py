import yaml
from pathlib import Path
from typing import Any, Dict, Tuple, Optional

from pydantic import BaseModel
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

# 1. 为配置的不同部分创建 Pydantic 模型，提供类型提示和默认值
class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 7768

# 新增：前端客户端配置模型
class ClientConfig(BaseModel):
    host: str = "localhost"
    port: int = 5173
    
class DatabaseConfig(BaseModel):
    type: str = "mysql"
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "root"
    password: str = "password"
    name: str = "danmaku_db"
    # 连接池配置
    pool_type: str = "QueuePool"        # QueuePool 或 NullPool
    pool_size: int = 10                 # 连接池常驻连接数
    max_overflow: int = 40              # 超出 pool_size 后允许的溢出连接数
    pool_recycle: int = 300             # 连接回收时间（秒），超过此时间的连接会被自动重建
    pool_timeout: int = 30              # 从池中获取连接的等待超时（秒）
    pool_pre_ping: bool = True          # 取连接前先 ping 检测是否存活
    echo: bool = False                  # 是否在控制台输出 SQL 语句

class JWTConfig(BaseModel):
    secret_key: str = "a_very_secret_key_that_should_be_changed"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 4320 # 3 days

# 4. (新增) 初始管理员配置
class AdminConfig(BaseModel):
    initial_user: Optional[str] = None
    initial_password: Optional[str] = None

class LogConfig(BaseModel):
    level: str = "INFO"

# 5. (新增) Bangumi OAuth 配置
class BangumiConfig(BaseModel):
    client_id: str = "" # 将从数据库加载
    client_secret: str = "" # 将从数据库加载

# 2. 创建一个自定义的配置源，用于从 YAML 文件加载设置
class YamlConfigSettingsSource(PydanticBaseSettingsSource):
    def __init__(self, settings_cls: type[BaseSettings]):
        super().__init__(settings_cls)
        # 在项目根目录的 config/ 文件夹下查找 config.yml
        # 修正：根据运行环境自动调整路径
        def _is_docker_environment():
            """检测是否在Docker容器中运行"""
            import os
            # 方法1: 检查 /.dockerenv 文件（Docker标准做法）
            if Path("/.dockerenv").exists():
                return True
            # 方法2: 检查环境变量
            if os.getenv("DOCKER_CONTAINER") == "true" or os.getenv("IN_DOCKER") == "true":
                return True
            # 方法3: 检查当前工作目录是否为 /app
            if Path.cwd() == Path("/app"):
                return True
            return False

        if _is_docker_environment():
            # 容器环境
            self.yaml_file = Path("/app/config/config.yml")
        else:
            # 源码运行环境
            self.yaml_file = Path("config/config.yml")

    def get_field_value(self, field, field_name):
        return None, None, False

    def __call__(self) -> Dict[str, Any]:
        if not self.yaml_file.is_file():
            return {}
        with open(self.yaml_file, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}


# 缓存配置
class CacheConfig(BaseModel):
    backend: str = "hybrid"             # memory / redis / database / hybrid（默认混合模式：内存L1 + 数据库L2）
    redis_url: str = ""                 # Redis 连接地址，如 redis://localhost:6379
    redis_max_memory: str = "256mb"     # Redis 最大内存限制
    redis_socket_timeout: int = 30      # Redis socket 超时（秒）
    redis_socket_connect_timeout: int = 5  # Redis 连接超时（秒）
    memory_maxsize: int = 1024          # 内存缓存最大条目数
    memory_default_ttl: int = 600       # 内存缓存默认 TTL（秒），10分钟

# (新增) 豆瓣配置
class DoubanConfig(BaseModel):
    cookie: Optional[str] = None


# 3. 定义主设置类，它将聚合所有配置
class Settings(BaseSettings):
    server: ServerConfig = ServerConfig()
    database: DatabaseConfig = DatabaseConfig()
    jwt: JWTConfig = JWTConfig()
    admin: AdminConfig = AdminConfig()
    bangumi: BangumiConfig = BangumiConfig()
    log: LogConfig = LogConfig()
    cache: CacheConfig = CacheConfig()
    douban: DoubanConfig = DoubanConfig()
    # 新增：时区配置，从 TZ 环境变量读取
    tz: str = "Asia/Shanghai"
    # 新增：环境标识和客户端配置
    environment: str = "production"
    # environment: str = "development"
    client: ClientConfig = ClientConfig()
    class Config:
        # 为环境变量设置前缀，避免与系统变量冲突
        # 例如，在容器中设置环境变量 DANMUAPI_SERVER__PORT=8080
        env_prefix = "DANMUAPI_"
        case_sensitive = False
        env_nested_delimiter = '__'

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        # 定义加载源的优先级:
        # 1. 环境变量 (最高)
        # 2. .env 文件
        # 3. YAML 文件
        # 4. 文件密钥
        # 5. Pydantic 模型中的默认值 (最低)
        return (
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
            init_settings,
        )


settings = Settings()
