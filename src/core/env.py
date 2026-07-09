"""
运行环境检测工具（零依赖，供全项目复用，避免 _is_docker_environment 到处重复定义）

why：项目内 16+ 处各自定义了 _is_docker_environment，逻辑完全一致。此处收口为单一权威实现，
新代码统一从这里 import。本模块不 import 任何项目内模块，避免循环依赖。
"""

import os
from pathlib import Path


def is_docker_environment() -> bool:
    """检测是否在 Docker 容器中运行。

    判定依据（任一成立即视为容器）：
    1. 存在 /.dockerenv 文件（Docker 标准做法）
    2. 环境变量 DOCKER_CONTAINER=true 或 IN_DOCKER=true
    3. 当前工作目录为 /app
    """
    if Path("/.dockerenv").exists():
        return True
    if os.getenv("DOCKER_CONTAINER") == "true" or os.getenv("IN_DOCKER") == "true":
        return True
    if Path.cwd() == Path("/app"):
        return True
    return False
