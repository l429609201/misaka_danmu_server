"""
Docker 工具模块

提供 Docker 容器管理功能，包括：
- 检测 Docker socket 是否可用
- 重启容器（通过 Docker API 或进程退出）
- 拉取镜像
- 使用 watchtower 更新容器
"""

import os
import sys
import socket
import logging
import time
import threading
from pathlib import Path
from typing import Optional, Dict, Any, Generator

logger = logging.getLogger(__name__)

# Docker socket 路径
DOCKER_SOCKET_PATH = "/var/run/docker.sock"

# 尝试导入 docker 库
try:
    import docker
    from docker.errors import DockerException, NotFound, APIError
    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False
    docker = None
    DockerException = Exception
    NotFound = Exception
    APIError = Exception

# ==================== 缓存机制 ====================
# Docker 客户端缓存
_docker_client_cache: Optional[Any] = None
_docker_client_cache_time: float = 0
_DOCKER_CLIENT_CACHE_DURATION = 60  # 缓存 60 秒

# Docker socket 可用性缓存
_docker_socket_available_cache: Optional[bool] = None
_docker_socket_available_cache_time: float = 0
_DOCKER_SOCKET_CACHE_DURATION = 30  # 缓存 30 秒

# 容器 ID 缓存
_container_id_cache: Optional[str] = None
_container_id_cache_time: float = 0
_CONTAINER_ID_CACHE_DURATION = 300  # 缓存 5 分钟（容器 ID 不会频繁变化）

# 线程锁
_docker_lock = threading.Lock()


def is_docker_socket_available() -> bool:
    """
    检测 Docker socket 是否可用（带缓存）

    Returns:
        bool: Docker socket 是否可用
    """
    global _docker_socket_available_cache, _docker_socket_available_cache_time

    # 检查缓存
    current_time = time.time()
    if _docker_socket_available_cache is not None:
        if current_time - _docker_socket_available_cache_time < _DOCKER_SOCKET_CACHE_DURATION:
            return _docker_socket_available_cache

    if not DOCKER_AVAILABLE:
        logger.debug("Docker SDK 未安装")
        _docker_socket_available_cache = False
        _docker_socket_available_cache_time = current_time
        return False

    # 检查 socket 文件是否存在
    if not Path(DOCKER_SOCKET_PATH).exists():
        logger.debug(f"Docker socket 不存在: {DOCKER_SOCKET_PATH}")
        _docker_socket_available_cache = False
        _docker_socket_available_cache_time = current_time
        return False

    # 尝试连接 Docker daemon（带超时）
    try:
        with _docker_lock:
            client = docker.from_env(timeout=5)  # 添加 5 秒超时
            client.ping()
        logger.debug("Docker socket 可用")
        _docker_socket_available_cache = True
        _docker_socket_available_cache_time = current_time
        return True
    except Exception as e:
        logger.debug(f"无法连接 Docker daemon: {e}")
        _docker_socket_available_cache = False
        _docker_socket_available_cache_time = current_time
        return False


def get_docker_client() -> Optional[Any]:
    """
    获取 Docker 客户端（带缓存和超时）

    Returns:
        docker.DockerClient 或 None
    """
    global _docker_client_cache, _docker_client_cache_time

    if not DOCKER_AVAILABLE:
        return None

    current_time = time.time()

    # 检查缓存的客户端是否仍然有效
    if _docker_client_cache is not None:
        if current_time - _docker_client_cache_time < _DOCKER_CLIENT_CACHE_DURATION:
            # 快速验证客户端是否仍然可用
            try:
                _docker_client_cache.ping()
                return _docker_client_cache
            except Exception:
                # 客户端失效，需要重新创建
                _docker_client_cache = None

    try:
        with _docker_lock:
            # 双重检查
            if _docker_client_cache is not None and current_time - _docker_client_cache_time < _DOCKER_CLIENT_CACHE_DURATION:
                return _docker_client_cache

            _docker_client_cache = docker.from_env(timeout=10)  # 添加 10 秒超时
            _docker_client_cache_time = current_time
            return _docker_client_cache
    except Exception as e:
        logger.error(f"获取 Docker 客户端失败: {e}")
        return None


def invalidate_docker_cache():
    """清除所有 Docker 相关缓存"""
    global _docker_client_cache, _docker_client_cache_time
    global _docker_socket_available_cache, _docker_socket_available_cache_time
    global _container_id_cache, _container_id_cache_time

    with _docker_lock:
        _docker_client_cache = None
        _docker_client_cache_time = 0
        _docker_socket_available_cache = None
        _docker_socket_available_cache_time = 0
        _container_id_cache = None
        _container_id_cache_time = 0

    logger.debug("Docker 缓存已清除")


def get_current_container_id() -> Optional[str]:
    """
    自动检测当前运行的容器 ID（带缓存）

    通过以下方式尝试获取：
    1. /proc/self/mountinfo 文件中 resolv.conf 的挂载路径（最可靠，参考 MoviePilot）
    2. /proc/self/cgroup 文件（兼容旧版本 Docker）
    3. /proc/1/cpuset 文件
    4. 环境变量 HOSTNAME / socket.gethostname()（兜底）

    Returns:
        容器 ID（完整64位）或 None
    """
    global _container_id_cache, _container_id_cache_time

    # 检查缓存
    current_time = time.time()
    if _container_id_cache is not None:
        if current_time - _container_id_cache_time < _CONTAINER_ID_CACHE_DURATION:
            return _container_id_cache

    container_id = _detect_container_id_impl()

    # 更新缓存（即使是 None 也缓存，避免重复检测）
    _container_id_cache = container_id
    _container_id_cache_time = current_time

    return container_id


def _detect_container_id_impl() -> Optional[str]:
    """容器 ID 检测的实际实现（不带缓存）"""

    # 方法1: 通过 /proc/self/mountinfo 中 resolv.conf 的挂载路径提取（最可靠）
    # Docker 挂载 resolv.conf 的路径中始终包含完整 64 位容器 ID，不受 hostname 设置影响
    # 路径格式: /var/lib/docker/containers/<64位容器ID>/resolv.conf
    try:
        mountinfo_path = Path('/proc/self/mountinfo')
        if mountinfo_path.exists():
            data = mountinfo_path.read_text()
            # 先尝试从 resolv.conf 路径提取（参考 MoviePilot 的方式）
            index_resolv = data.find("resolv.conf")
            if index_resolv != -1:
                index_second_slash = data.rfind("/", 0, index_resolv)
                index_first_slash = data.rfind("/", 0, index_second_slash) + 1
                container_id = data[index_first_slash:index_second_slash].strip()
                # 验证提取的是否为有效的 64 位容器 ID
                if len(container_id) == 64 and all(c in '0123456789abcdef' for c in container_id.lower()):
                    logger.info(f"通过 /proc/self/mountinfo (resolv.conf) 获取到容器 ID: {container_id[:12]}")
                    return container_id
            # resolv.conf 方式失败时，回退到正则匹配
            import re
            for line in data.splitlines():
                if 'docker/containers/' in line or '/docker-' in line:
                    match = re.search(r'([0-9a-f]{64})', line)
                    if match:
                        container_id = match.group(1)
                        logger.info(f"通过 /proc/self/mountinfo (正则) 获取到容器 ID: {container_id[:12]}")
                        return container_id
    except Exception as e:
        logger.debug(f"通过 /proc/self/mountinfo 获取容器 ID 失败: {e}")

    # 方法2: 通过 /proc/self/cgroup 文件（兼容旧版本 Docker / cgroup v1）
    try:
        cgroup_path = Path('/proc/self/cgroup')
        if cgroup_path.exists():
            content = cgroup_path.read_text()
            for line in content.splitlines():
                if 'docker' in line or 'containerd' in line:
                    parts = line.strip().split('/')
                    if parts:
                        potential_id = parts[-1]
                        if len(potential_id) == 64 and all(c in '0123456789abcdef' for c in potential_id.lower()):
                            logger.info(f"通过 /proc/self/cgroup 获取到容器 ID: {potential_id[:12]}")
                            return potential_id
    except Exception as e:
        logger.debug(f"通过 /proc/self/cgroup 获取容器 ID 失败: {e}")

    # 方法3: 通过 /proc/1/cpuset 文件
    try:
        cpuset_path = Path('/proc/1/cpuset')
        if cpuset_path.exists():
            content = cpuset_path.read_text().strip()
            if 'docker' in content or 'containerd' in content:
                parts = content.split('/')
                if parts:
                    potential_id = parts[-1]
                    if len(potential_id) == 64 and all(c in '0123456789abcdef' for c in potential_id.lower()):
                        logger.info(f"通过 /proc/1/cpuset 获取到容器 ID: {potential_id[:12]}")
                        return potential_id
    except Exception as e:
        logger.debug(f"通过 /proc/1/cpuset 获取容器 ID 失败: {e}")

    # 方法4: 通过 HOSTNAME / socket.gethostname()（兜底，仅在未自定义 hostname 时有效）
    try:
        hostname = os.environ.get('HOSTNAME', '') or socket.gethostname()
        if len(hostname) == 12 and all(c in '0123456789abcdef' for c in hostname.lower()):
            logger.info(f"通过 HOSTNAME 获取到容器 ID (短格式): {hostname}")
            return hostname
    except Exception as e:
        logger.debug(f"通过 HOSTNAME 获取容器 ID 失败: {e}")

    logger.warning("无法自动检测容器 ID，可能不在 Docker 容器中运行或使用了自定义 hostname")
    return None


def get_docker_status() -> Dict[str, Any]:
    """
    获取 Docker 状态信息

    Returns:
        包含 Docker 状态的字典
    """
    status = {
        "sdkInstalled": DOCKER_AVAILABLE,
        "socketAvailable": False,
        "socketPath": DOCKER_SOCKET_PATH,
        "socketExists": Path(DOCKER_SOCKET_PATH).exists(),
        "canRestart": False,
        "canUpdate": False,
    }

    if not DOCKER_AVAILABLE:
        status["message"] = "Docker SDK 未安装，请确保已安装 docker 库 (pip install docker)"
        return status

    if not Path(DOCKER_SOCKET_PATH).exists():
        status["message"] = f"Docker 套接字 未映射 ({DOCKER_SOCKET_PATH})，请在 docker-compose.yml 中添加: - /var/run/docker.sock:/var/run/docker.sock"
        return status

    try:
        client = docker.from_env()
        client.ping()
        status["socketAvailable"] = True
        status["canRestart"] = True
        status["canUpdate"] = True
        status["message"] = "Docker 连接正常"
    except PermissionError as e:
        status["message"] = f"权限不足，无法访问 Docker socket。请确保容器有权限访问 /var/run/docker.sock (错误: {str(e)})"
    except Exception as e:
        status["message"] = f"无法连接 Docker daemon: {str(e)}"

    return status


async def restart_container(fallback_container_name: str = "misaka_danmu_server") -> Dict[str, Any]:
    """
    重启当前容器

    优先自动检测当前容器 ID，如果检测失败则使用兜底容器名称

    Args:
        fallback_container_name: 兜底容器名称（自动检测失败时使用）

    Returns:
        操作结果字典
    """
    if not is_docker_socket_available():
        return {
            "success": False,
            "message": "Docker socket 不可用，无法通过 Docker API 重启",
            "fallback": True
        }

    # 优先自动检测当前容器 ID
    container_name = get_current_container_id()

    # 如果自动检测失败，使用兜底容器名称
    if not container_name:
        logger.info(f"自动检测容器 ID 失败，使用兜底容器名称: {fallback_container_name}")
        container_name = fallback_container_name
    else:
        logger.info(f"自动检测到当前容器 ID: {container_name}")

    try:
        client = get_docker_client()
        if not client:
            return {"success": False, "message": "无法获取 Docker 客户端", "fallback": True}

        container = client.containers.get(container_name)
        container_id = container.short_id
        logger.info(f"正在重启容器: {container_name} (ID: {container_id})")

        # 刷新日志，确保上面的日志被写入
        import sys
        for handler in logging.getLogger().handlers:
            handler.flush()
        sys.stdout.flush()
        sys.stderr.flush()

        # 使用线程异步执行重启，避免阻塞当前进程
        # 这样可以让日志有时间刷新，然后容器才会被重启
        import threading
        def do_restart():
            try:
                container.restart(timeout=10)
            except Exception as e:
                logger.error(f"重启容器时发生错误: {e}")

        restart_thread = threading.Thread(target=do_restart, daemon=True)
        restart_thread.start()

        # 不等待线程完成，直接返回
        # 容器重启会杀死当前进程，所以我们不需要等待

        return {
            "success": True,
            "message": f"已向容器 '{container_name}' 发送重启指令",
            "container_id": container_id
        }
    except NotFound:
        return {
            "success": False,
            "message": f"找不到名为 '{container_name}' 的容器",
            "fallback": True
        }
    except Exception as e:
        logger.error(f"重启容器失败: {e}")
        return {
            "success": False,
            "message": f"重启容器失败: {str(e)}",
            "fallback": True
        }


def restart_via_exit() -> None:
    """
    通过退出进程来触发容器重启（依赖 Docker 的 restart policy）

    这是在没有 Docker socket 时的备用方案
    """
    logger.info("正在通过退出进程触发容器重启...")
    sys.exit(0)


def pull_image_stream(image_name: str, proxy_url: Optional[str] = None) -> Generator[Dict[str, Any], None, None]:
    """
    流式拉取 Docker 镜像

    Args:
        image_name: 镜像名称（含标签）
        proxy_url: 代理 URL（可选）

    Yields:
        拉取进度信息
    """
    if not is_docker_socket_available():
        yield {"status": "Docker 套接字 不可用", "event": "ERROR"}
        return

    # 设置代理环境变量
    old_env = os.environ.copy()
    try:
        if proxy_url:
            os.environ['HTTPS_PROXY'] = proxy_url
            os.environ['HTTP_PROXY'] = proxy_url
            yield {"status": f"使用代理: {proxy_url}"}

        client = get_docker_client()
        if not client:
            yield {"status": "无法获取 Docker 客户端", "event": "ERROR"}
            return

        yield {"status": f"正在拉取镜像: {image_name}..."}

        # 使用低级 API 获取流式输出
        stream = client.api.pull(image_name, stream=True, decode=True)

        last_line = {}
        for line in stream:
            last_line = line
            # 可以在这里发送更详细的进度，但为简化只保留最后状态

        # 检查最终状态
        final_status = last_line.get('status', '')
        if 'Status: Image is up to date' in final_status:
            yield {"status": "当前已是最新版本", "event": "UP_TO_DATE"}
        elif 'errorDetail' in last_line:
            error_msg = last_line['errorDetail'].get('message', '未知错误')
            yield {"status": f"拉取失败: {error_msg}", "event": "ERROR"}
        else:
            yield {"status": "镜像拉取完成", "event": "PULLED"}

    except Exception as e:
        logger.error(f"拉取镜像失败: {e}")
        yield {"status": f"拉取镜像失败: {str(e)}", "event": "ERROR"}
    finally:
        # 恢复环境变量
        os.environ.clear()
        os.environ.update(old_env)


def get_container_stats(fallback_container_name: str = "misaka_danmu_server") -> Dict[str, Any]:
    """
    获取容器的资源使用统计信息

    Args:
        fallback_container_name: 兜底容器名称（自动检测失败时使用）

    Returns:
        包含 CPU、内存、网络等统计信息的字典
    """
    if not is_docker_socket_available():
        return {
            "available": False,
            "message": "Docker socket 不可用"
        }

    # 优先自动检测当前容器 ID
    container_name = get_current_container_id()

    # 如果自动检测失败，使用兜底容器名称
    if not container_name:
        logger.info(f"自动检测容器 ID 失败，使用兜底容器名称: {fallback_container_name}")
        container_name = fallback_container_name

    try:
        client = get_docker_client()
        if not client:
            return {"available": False, "message": "无法获取 Docker 客户端"}

        container = client.containers.get(container_name)

        # 获取一次性统计数据（stream=False）
        stats = container.stats(stream=False)

        # 调试：记录原始统计数据的关键字段
        logger.debug(f"Docker stats 原始数据 keys: {list(stats.keys())}")

        # 计算 CPU 使用率
        cpu_percent = 0.0
        cpu_stats = stats.get("cpu_stats", {})
        precpu_stats = stats.get("precpu_stats", {})

        cpu_delta = cpu_stats.get("cpu_usage", {}).get("total_usage", 0) - \
                    precpu_stats.get("cpu_usage", {}).get("total_usage", 0)
        system_delta = cpu_stats.get("system_cpu_usage", 0) - \
                       precpu_stats.get("system_cpu_usage", 0)

        logger.debug(f"CPU 计算: cpu_delta={cpu_delta}, system_delta={system_delta}")

        if system_delta > 0 and cpu_delta > 0:
            # 获取 CPU 核心数
            online_cpus = cpu_stats.get("online_cpus")
            if online_cpus is None:
                # 兼容旧版本 Docker
                online_cpus = len(cpu_stats.get("cpu_usage", {}).get("percpu_usage", [1]))
            cpu_percent = (cpu_delta / system_delta) * online_cpus * 100.0

        # 计算内存使用
        memory_stats = stats.get("memory_stats", {})
        memory_usage = memory_stats.get("usage", 0)
        memory_limit = memory_stats.get("limit", 0)
        # 减去缓存（如果有的话）
        cache = memory_stats.get("stats", {}).get("cache", 0)
        memory_usage_actual = memory_usage - cache

        memory_percent = 0.0
        if memory_limit > 0:
            memory_percent = (memory_usage_actual / memory_limit) * 100.0

        # 网络 I/O
        # Docker 网络统计可能在 "networks" 或容器的网络设置中
        networks = stats.get("networks", {})
        network_rx = 0
        network_tx = 0

        logger.debug(f"网络统计 networks keys: {list(networks.keys()) if networks else 'empty'}")

        if networks:
            for iface_name, iface_stats in networks.items():
                rx = iface_stats.get("rx_bytes", 0)
                tx = iface_stats.get("tx_bytes", 0)
                logger.debug(f"  网卡 {iface_name}: rx={rx}, tx={tx}")
                network_rx += rx
                network_tx += tx
        else:
            # 某些网络模式（如 host）可能没有 networks 字段
            # 尝试从容器信息中获取
            logger.debug("networks 字段为空，可能使用 host 网络模式")

        # 磁盘 I/O
        blkio_stats = stats.get("blkio_stats", {})
        io_read = 0
        io_write = 0
        io_entries = blkio_stats.get("io_service_bytes_recursive", []) or []

        logger.debug(f"磁盘 I/O entries 数量: {len(io_entries)}")

        for entry in io_entries:
            op = entry.get("op", "").lower()
            value = entry.get("value", 0)
            if op == "read":
                io_read += value
            elif op == "write":
                io_write += value

        # 获取容器信息
        container_info = container.attrs
        state = container_info.get("State", {})

        # 获取真正的容器名称（Docker 返回的名称带有前导斜杠，需要去掉）
        real_container_name = container_info.get("Name", "").lstrip("/") or container_name

        return {
            "available": True,
            "containerName": real_container_name,
            "containerId": container.short_id,
            "status": state.get("Status", "unknown"),
            "startedAt": state.get("StartedAt"),
            "cpu": {
                "percent": round(cpu_percent, 2),
                "onlineCpus": cpu_stats.get("online_cpus", 1)
            },
            "memory": {
                "usage": memory_usage_actual,
                "limit": memory_limit,
                "percent": round(memory_percent, 2),
                "usageFormatted": _format_bytes(memory_usage_actual),
                "limitFormatted": _format_bytes(memory_limit)
            },
            "network": {
                "rxBytes": network_rx,
                "txBytes": network_tx,
                "rxFormatted": _format_bytes(network_rx),
                "txFormatted": _format_bytes(network_tx)
            },
            "io": {
                "readBytes": io_read,
                "writeBytes": io_write,
                "readFormatted": _format_bytes(io_read),
                "writeFormatted": _format_bytes(io_write)
            }
        }

    except NotFound:
        return {
            "available": False,
            "message": f"找不到容器: {container_name}"
        }
    except Exception as e:
        logger.error(f"获取容器统计信息失败: {e}")
        return {
            "available": False,
            "message": f"获取统计信息失败: {str(e)}"
        }


def _format_bytes(bytes_value: int) -> str:
    """将字节数格式化为人类可读的字符串"""
    if bytes_value == 0:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    value = float(bytes_value)

    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1

    return f"{value:.2f} {units[unit_index]}"


def update_container_with_watchtower(
    container_name: str,
    watchtower_image: str = "containrrr/watchtower"
) -> Generator[Dict[str, Any], None, None]:
    """
    使用 watchtower 更新容器

    Args:
        container_name: 要更新的容器名称
        watchtower_image: watchtower 镜像名称

    Yields:
        更新进度信息
    """
    if not is_docker_socket_available():
        yield {"status": "Docker socket 不可用", "event": "ERROR"}
        return

    try:
        client = get_docker_client()
        if not client:
            yield {"status": "无法获取 Docker 客户端", "event": "ERROR"}
            return

        # 确保 watchtower 镜像存在
        try:
            client.images.get(watchtower_image)
        except NotFound:
            yield {"status": f"正在拉取更新工具: {watchtower_image}..."}
            client.images.pull(watchtower_image)

        yield {"status": "正在启动更新任务..."}

        # 运行 watchtower 一次性更新
        command = ["--cleanup", "--run-once", container_name]

        client.containers.run(
            image=watchtower_image,
            command=command,
            remove=True,
            detach=True,
            volumes={DOCKER_SOCKET_PATH: {'bind': DOCKER_SOCKET_PATH, 'mode': 'rw'}}
        )

        yield {"status": "更新任务已启动，容器将在后台被重启"}
        yield {"status": "请稍后刷新页面访问新版本", "event": "DONE"}

    except NotFound:
        yield {"status": f"找不到容器: {container_name}", "event": "ERROR"}
    except Exception as e:
        logger.error(f"更新容器失败: {e}")
        yield {"status": f"更新失败: {str(e)}", "event": "ERROR"}

