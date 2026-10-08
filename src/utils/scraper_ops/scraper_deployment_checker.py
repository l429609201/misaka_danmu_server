"""
弹幕源部署状态检查工具

提供统一的检测逻辑，判断是否可以安全地进行热加载。
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def has_scraper_files(directory: Path) -> bool:
    """
    检查目录中是否存在弹幕源文件
    
    Args:
        directory: 要检查的目录路径
        
    Returns:
        True 如果目录中存在 .so 或 .pyd 文件
    """
    if not directory.exists():
        return False
    
    for file_path in directory.glob("*"):
        if file_path.is_file() and file_path.suffix in ['.so', '.pyd']:
            return True
    
    return False


def has_loaded_scrapers(scraper_manager) -> bool:
    """
    检查是否有已加载到内存的弹幕源
    
    Args:
        scraper_manager: ScraperManager 实例
        
    Returns:
        True 如果有已加载的弹幕源
    """
    if scraper_manager is None:
        return False
    
    return len(scraper_manager.scrapers) > 0


def should_restart_for_deployment(
    scrapers_dir: Path,
    backup_dir: Path,
    scraper_manager,
    logger_instance: Optional[logging.Logger] = None
) -> dict:
    """
    综合判断部署策略：是否需要重启容器
    
    判断逻辑：
    1. 如果有已加载的 .so 文件，必须重启（避免内存问题）
    2. 如果没有已加载的 .so，可以热加载
    
    Args:
        scrapers_dir: scrapers 运行目录
        backup_dir: backup 备份目录
        scraper_manager: ScraperManager 实例
        logger_instance: 可选的 logger 实例
        
    Returns:
        {
            "need_restart": bool,  # 是否需要重启
            "has_current_files": bool,  # 当前目录是否有文件
            "has_backup_files": bool,  # 备份目录是否有文件
            "has_loaded": bool,  # 是否有已加载的弹幕源
            "reason": str  # 决策原因
        }
    """
    log = logger_instance or logger
    
    # 检查目录中是否有文件
    has_current = has_scraper_files(scrapers_dir)
    has_backup = has_scraper_files(backup_dir)
    
    # 检查是否有已加载的弹幕源
    has_loaded = has_loaded_scrapers(scraper_manager)
    
    # 决策逻辑
    if has_loaded:
        reason = "检测到已加载的弹幕源，必须重启容器以避免 .so 文件覆盖导致的内存问题"
        need_restart = True
    else:
        reason = "没有已加载的弹幕源，可以安全地执行热加载"
        need_restart = False
    
    result = {
        "need_restart": need_restart,
        "has_current_files": has_current,
        "has_backup_files": has_backup,
        "has_loaded": has_loaded,
        "reason": reason
    }
    
    log.info(
        f"部署策略检测: "
        f"当前目录有文件={has_current}, "
        f"备份目录有文件={has_backup}, "
        f"已加载={has_loaded}, "
        f"需要重启={need_restart}"
    )
    log.info(f"决策原因: {reason}")
    
    return result


def count_scraper_files(directory: Path) -> int:
    """
    统计目录中的弹幕源文件数量
    
    Args:
        directory: 要统计的目录路径
        
    Returns:
        弹幕源文件数量
    """
    if not directory.exists():
        return 0
    
    count = 0
    for file_path in directory.iterdir():
        if file_path.is_file() and file_path.suffix in ['.so', '.pyd']:
            count += 1
    
    return count
