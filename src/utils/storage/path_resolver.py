"""
路径解析工具模块

提供统一的路径解析函数，处理 Docker 环境和本地环境的路径差异。
"""
from pathlib import Path
from src.core.env import is_docker_environment


def get_scrapers_dir() -> Path:
    """
    获取 scrapers 目录路径
    
    Returns:
        Path: scrapers 目录的绝对路径
        
    Examples:
        Docker 环境: /app/src/scrapers
        本地环境: src/scrapers
    """
    if is_docker_environment():
        return Path("/app/src/scrapers")
    else:
        return Path("src/scrapers")


def get_backup_dir() -> Path:
    """
    获取备份目录路径
    
    Returns:
        Path: 备份目录的绝对路径
        
    Examples:
        Docker 环境: /app/config/scrapers_backup
        本地环境: config/scrapers_backup
    """
    if is_docker_environment():
        return Path("/app/config/scrapers_backup")
    else:
        return Path("config/scrapers_backup")
