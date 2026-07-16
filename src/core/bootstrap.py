"""
启动预处理模块 - 配置加载与验证

在应用启动前调用 preload_config()，完成以下工作：
1. 检测配置文件是否存在，不存在则自动生成模板
2. 检测环境变量覆盖情况
3. 打印配置来源日志（ENV / FILE / DEFAULT）

优先级：环境变量 (DANMUAPI_*) > .env 文件 > config.yml > Pydantic 默认值

辅助脚本（如 reset_password.py）直接使用 settings 不会触发自动生成配置文件。
"""
import os
import sys
import signal
import secrets
import logging
from pathlib import Path

from src.core.env import is_docker_environment as _is_docker_environment

logger = logging.getLogger("bootstrap")

# 需要显示来源的核心参数（精简版）
_CORE_ENV_KEYS = [
    ("DANMUAPI_DATABASE__TYPE", "database.type", "数据库类型"),
    ("DANMUAPI_DATABASE__HOST", "database.host", "数据库地址"),
    ("DANMUAPI_DATABASE__PORT", "database.port", "数据库端口"),
    ("DANMUAPI_DATABASE__USER", "database.user", "数据库用户"),
    ("DANMUAPI_DATABASE__PASSWORD", "database.password", "数据库密码"),
    ("DANMUAPI_DATABASE__NAME", "database.name", "数据库名称"),
    ("DANMUAPI_JWT__SECRET_KEY", "jwt.secret_key", "JWT密钥"),
    ("DANMUAPI_CACHE__BACKEND", "cache.backend", "缓存后端"),
    ("DANMUAPI_CACHE__REDIS_URL", "cache.redis_url", "Redis地址"),
    ("DANMUAPI_SERVER__PORT", "server.port", "监听端口"),
]


def _get_config_path() -> Path:
    """获取配置文件路径（基于源码位置推算，不依赖 CWD）"""
    if _is_docker_environment():
        return Path("/app/config/config.yml")
    else:
        # bootstrap.py 位于 src/core/bootstrap.py → 项目根 = ../../
        project_root = Path(__file__).resolve().parent.parent.parent
        return project_root / "config" / "config.yml"


def _generate_config_template(jwt_secret: str) -> str:
    """生成带注释的 config.yml 模板（内联版，避免循环导入）"""
    return f"""\
# ============================================================
# Misaka Danmaku API 配置文件
# 首次启动自动生成，请根据实际环境修改后重启服务
# ============================================================

# 服务器监听配置
server:
  host: "0.0.0.0"
  port: 7768
  ipv6: true

# 数据库配置（支持 mysql / postgresql）
database:
  type: "mysql"
  host: "127.0.0.1"
  port: 3306
  user: "root"
  password: "password"
  name: "danmaku_db"
  pool_type: "QueuePool"
  pool_size: 10
  max_overflow: 50
  pool_recycle: 300
  pool_timeout: 30
  pool_pre_ping: true
  echo: false

# JWT 鉴权配置
jwt:
  secret_key: "{jwt_secret}"
  algorithm: "HS256"
  access_token_expire_minutes: 4320

# 初始管理员账号
admin:
  initial_user: null
  initial_password: null

# 日志级别
log:
  level: "INFO"

# 缓存配置
cache:
  backend: "hybrid"
  redis_url: ""
  redis_max_memory: "256mb"
  redis_socket_timeout: 30
  redis_socket_connect_timeout: 5
  memory_maxsize: 1024
  memory_default_ttl: 600

# 豆瓣配置（可选）
douban:
  cookie: null

# 时区
tz: "Asia/Shanghai"

# 运行环境
environment: "production"
"""


_bootstrap_executed = False


def _safe_print(msg: str) -> None:
    """安全打印，避免 Windows GBK 终端因 Unicode 符号崩溃"""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"))


def _kill_old_instances(port: int) -> None:
    """检查并杀死占用指定端口的旧后端进程（排除自身）"""
    import subprocess
    my_pid = os.getpid()
    _TAG = "[启动预检]"

    try:
        # Windows: netstat 查端口占用
        if sys.platform == "win32":
            result = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace"
            )
            pids_to_kill = set()
            for line in result.stdout.splitlines():
                # 匹配 LISTENING 状态的端口行
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    if parts:
                        try:
                            pid = int(parts[-1])
                            if pid != my_pid and pid != os.getppid():
                                pids_to_kill.add(pid)
                        except (ValueError, IndexError):
                            pass

            if pids_to_kill:
                for pid in pids_to_kill:
                    try:
                        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
                        _safe_print(f"{_TAG} 已终止占用端口 {port} 的旧进程 (PID: {pid})")
                    except Exception:
                        pass
                # 等待端口释放
                import time
                time.sleep(1)
        else:
            # Linux/macOS: lsof 或 fuser
            try:
                result = subprocess.run(
                    ["lsof", "-ti", f":{port}"],
                    capture_output=True, text=True, timeout=5
                )
                if result.stdout.strip():
                    for pid_str in result.stdout.strip().split("\n"):
                        try:
                            pid = int(pid_str.strip())
                            if pid != my_pid and pid != os.getppid():
                                os.kill(pid, signal.SIGTERM)
                                _safe_print(f"{_TAG} 已终止占用端口 {port} 的旧进程 (PID: {pid})")
                        except (ValueError, ProcessLookupError, PermissionError):
                            pass
                    import time
                    time.sleep(1)
            except FileNotFoundError:
                pass  # lsof 不可用，跳过
    except Exception as e:
        _safe_print(f"{_TAG} 检查旧实例时出错: {e}")


def preload_config() -> None:
    """
    启动前预处理：检查配置文件、环境变量，打印来源日志。

    - 确保 config.example.yml 示例文件存在
    - 检查 config.yml 是否存在
    - 检测环境变量覆盖情况
    """
    global _bootstrap_executed
    if _bootstrap_executed:
        return
    _bootstrap_executed = True

    config_path = _get_config_path()
    docker = _is_docker_environment()

    _TAG = "[启动预检]"
    _safe_print(f"{_TAG} 运行环境: {'Docker 容器' if docker else '本地开发'}")
    _safe_print(f"{_TAG} 配置文件: {config_path.resolve()}")

    # --- Step 0: 读取端口并检查旧实例 ---
    port = 7768  # 默认端口
    if config_path.is_file():
        try:
            import yaml
            with open(config_path, "r", encoding="utf-8") as f:
                raw_cfg = yaml.safe_load(f) or {}
            port = int(raw_cfg.get("server", {}).get("port", 7768))
        except Exception:
            pass
    env_port = os.environ.get("DANMUAPI_SERVER__PORT")
    if env_port:
        try:
            port = int(env_port)
        except ValueError:
            pass

    if not docker:
        _kill_old_instances(port)

    # --- Step 1: 确保示例配置文件存在 ---
    example_path = config_path.parent / "config.example.yml"
    if not example_path.is_file():
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            jwt_secret = secrets.token_urlsafe(32)
            template = _generate_config_template(jwt_secret)
            example_path.write_text(template, encoding="utf-8")
            _safe_print(f"{_TAG} [OK] 已创建示例配置文件: {example_path.name}")
        except OSError as e:
            _safe_print(f"{_TAG} [WARN] 无法创建示例配置文件: {e}")

    # --- Step 2: 检查配置文件 ---
    if config_path.is_file():
        _safe_print(f"{_TAG} [OK] 配置文件已加载")
    else:
        _safe_print(f"{_TAG} [WARN] 配置文件 {config_path.name} 不存在，将使用默认值和环境变量")

    # --- Step 3: 参数来源分析 ---
    # 读取配置文件中的值
    file_values = {}
    if config_path.is_file():
        try:
            import yaml
            with open(config_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            def _flatten(d, prefix=""):
                for k, v in d.items():
                    full_key = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
                    if isinstance(v, dict):
                        _flatten(v, full_key)
                    else:
                        file_values[full_key] = v
            _flatten(raw)
        except Exception:
            pass

    # 脱敏函数
    def _mask(val, key):
        s = str(val)
        if "password" in key.lower() or "secret" in key.lower():
            return s[:3] + "***" if len(s) > 3 else "***"
        return s

    # 打印每个核心参数的值和来源
    _safe_print(f"{_TAG} 配置参数:")
    for env_key, config_key, display_name in _CORE_ENV_KEYS:
        env_val = os.environ.get(env_key)
        file_val = file_values.get(config_key)

        if env_val is not None:
            _safe_print(f"  {display_name}: {_mask(env_val, config_key)} [环境变量]")
        elif file_val is not None:
            _safe_print(f"  {display_name}: {_mask(file_val, config_key)} [配置文件]")
        else:
            _safe_print(f"  {display_name}: (默认值) [内置默认]")

    _safe_print(f"{_TAG} 预处理完成")
