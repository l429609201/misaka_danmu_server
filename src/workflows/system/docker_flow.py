"""
Docker 管理相关的 workflow 编排层
"""
import asyncio
import json
import logging
import time
from typing import Dict, Any, AsyncIterator

from src.services.config_service import ConfigService
from src.utils.runtime.docker_utils import (
    DOCKER_AVAILABLE,
    DOCKER_SOCKET_PATH,
    is_docker_socket_available,
    get_current_container_id,
    get_container_stats,
    _format_bytes,
)
from src._version import GITHUB_OWNER, GITHUB_REPO

logger = logging.getLogger(__name__)


async def workflow_get_docker_status() -> Dict[str, Any]:
    """异步获取 Docker 状态，所有分支均提供完整响应字段。"""
    status_data: Dict[str, Any] = {
        "sdkInstalled": DOCKER_AVAILABLE,
        "socketAvailable": False,
        "socketPath": DOCKER_SOCKET_PATH,
        "canRestart": False,
        "canUpdate": False,
        "message": "Docker socket 不可用",
    }
    try:
        if not DOCKER_AVAILABLE:
            status_data["message"] = "Docker SDK 未安装"
            return status_data
        # Docker SDK 与文件探测为同步操作，移入线程以免阻塞事件循环。
        status_data["socketAvailable"] = await asyncio.to_thread(is_docker_socket_available)
        if not status_data["socketAvailable"]:
            return status_data
        container_id = await asyncio.to_thread(get_current_container_id)
        if not container_id:
            status_data["message"] = "未检测到当前容器 ID"
            return status_data
        status_data.update(canRestart=True, canUpdate=True, message="Docker 可用")
    except Exception as exc:
        logger.error(f"获取 Docker 状态失败: {exc}")
        status_data["message"] = f"检查 Docker 状态时出错: {exc}"
    return status_data


async def workflow_get_docker_stats_stream() -> AsyncIterator[str]:
    """持续采样容器统计；连接关闭后不再调度采样。"""
    previous = None
    try:
        while True:
            # SDK 为阻塞调用；逐次等待采样，不创建无限队列或常驻采集线程。
            stats = await asyncio.to_thread(get_container_stats)
            sampled_at = time.monotonic()
            if stats.get("available"):
                network = stats.get("network", {})
                rx, tx = network.get("rxBytes", 0), network.get("txBytes", 0)
                rx_rate = tx_rate = 0.0
                if previous and previous[0] == stats.get("containerId"):
                    elapsed = sampled_at - previous[1]
                    if elapsed > 0:
                        rx_rate = max(0, rx - previous[2]) / elapsed
                        tx_rate = max(0, tx - previous[3]) / elapsed
                network.update(rxRateFormatted=f"{_format_bytes(rx_rate)}/s",
                               txRateFormatted=f"{_format_bytes(tx_rate)}/s")
                previous = (stats.get("containerId"), sampled_at, rx, tx)
            else:
                previous = None
            yield f"data: {json.dumps(stats, ensure_ascii=False)}\n\n"
            await asyncio.sleep(2)
    except Exception as e:
        logger.error(f"获取 Docker 统计信息失败: {e}")
        yield f"data: {json.dumps({'available': False, 'message': str(e)})}\n\n"


async def workflow_restart_service(
    current_user_username: str,
    config_service: ConfigService
) -> Dict[str, Any]:
    """重启服务的 workflow"""
    container_name = await config_service.get("containerName", "misaka_danmu_server")
    
    if is_docker_socket_available():
        logger.info(f"用户 '{current_user_username}' 通过 Docker API 重启容器 '{container_name}'")
        return {
            "success": True,
            "message": "正在通过 Docker API 重启容器...",
            "method": "docker_api"
        }
    else:
        logger.info(f"用户 '{current_user_username}' 通过进程退出方式重启服务")
        
        async def delayed_exit():
            await asyncio.sleep(1)
            import os
            os._exit(0)
        
        asyncio.create_task(delayed_exit())
        
        return {
            "success": True,
            "message": "服务将在 1 秒后重启，请稍后刷新页面",
            "method": "process_exit"
        }


async def workflow_update_service_stream(
    source: str,
    current_user_username: str,
    config_service: ConfigService
):
    """流式更新服务的 workflow（生成器）"""
    if not is_docker_socket_available():
        yield f"data: {json.dumps({'status': 'Docker socket 不可用，无法执行更新', 'event': 'ERROR'})}\n\n"
        return
    
    docker_hub_image = await config_service.get("dockerImageName", "l429609201/misaka_danmu_server:latest")
    
    if source == "github":
        image_name = f"ghcr.io/{GITHUB_OWNER}/{GITHUB_REPO}:latest"
    else:
        image_name = docker_hub_image
    
    logger.info(f"用户 '{current_user_username}' 开始更新服务，镜像: {image_name}")
    
    yield f"data: {json.dumps({'status': '更新功能开发中', 'event': 'INFO', 'image': image_name})}\n\n"
