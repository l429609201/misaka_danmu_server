"""
System workflows - 系统管理相关的业务编排层

本模块负责系统管理相关的业务逻辑编排，包括：
- 版本检查和更新
- Docker 容器管理
- 日志管理
- 数据库信息查询
- 缓存管理
"""

from .version_flow import (
    workflow_check_version,
    workflow_get_release_history,
)
from .docker_flow import (
    workflow_get_docker_status,
    workflow_get_docker_stats_stream,
    workflow_restart_service,
    workflow_update_service_stream,
)
from .logs_flow import (
    workflow_get_logs,
    workflow_list_log_files,
    workflow_read_log_file,
    workflow_stream_logs,
)
from .database_info_flow import (
    workflow_get_database_info,
)
from .cache_flow import (
    workflow_clear_all_caches,
)

__all__ = [
    # Version workflows
    "workflow_check_version",
    "workflow_get_release_history",
    # Docker workflows
    "workflow_get_docker_status",
    "workflow_get_docker_stats_stream",
    "workflow_restart_service",
    "workflow_update_service_stream",
    # Logs workflows
    "workflow_get_logs",
    "workflow_list_log_files",
    "workflow_read_log_file",
    "workflow_stream_logs",
    # Database info workflows
    "workflow_get_database_info",
    # Cache workflows
    "workflow_clear_all_caches",
]
