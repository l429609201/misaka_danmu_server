import yaml
from pathlib import Path
from typing import Any, Dict, Tuple, Optional

from pydantic import BaseModel
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, EnvSettingsSource

# 1. 为配置的不同部分创建 Pydantic 模型，提供类型提示和默认值
class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 7768

# 新增：前端客户端配置模型
class ClientConfig(BaseModel):
    host: str = "localhost"
    port: int = 5173

class DatabaseConfig(BaseModel):
    host: str = "117.147.37.7"
    port: int = 3631
    user: str = "danmuapi"
    password: str = "jisi9Lnfr&CyJ4"
    name: str = "danmuapi"

class JWTConfig(BaseModel):
    secret_key: str = "a_very_secret_key_that_should_be_changed"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440 # 1 day

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
        self.yaml_file = Path(__file__).parent.parent / "config" / "config.yml"

    def get_field_value(self, field, field_name):
        return None, None, False

    def __call__(self) -> Dict[str, Any]:
        if not self.yaml_file.is_file():
            return {}
        with open(self.yaml_file, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}


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
    douban: DoubanConfig = DoubanConfig()
    # 新增：环境标识和客户端配置
    # environment: str = "production"
    environment: str = "development"
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