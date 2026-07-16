"""
诊断中心 API
- 日志智能诊断助手 (16)
- 运行环境诊断 (39)
合并为一个诊断页面
"""
import logging
import os
import platform
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db_session
from src.core import get_now
from src.core.cache import get_cache_backend
from src._version import APP_VERSION
from src.api.dependencies import get_config_manager
from src.db import ConfigManager
from src.db.database import get_db_type
from src.core.env import is_docker_environment

logger = logging.getLogger(__name__)
router = APIRouter()


# ==================== Models ====================

class EnvironmentInfo(BaseModel):
    appVersion: str = ""
    pythonVersion: str = ""
    platform: str = ""
    architecture: str = ""
    osName: str = ""
    dbType: str = ""
    cacheBackend: str = ""
    configDir: str = ""
    logsDir: str = ""
    configDirWritable: bool = False
    logsDirWritable: bool = False
    timezone: str = ""
    isDocker: bool = False
    uvloopEnabled: bool = False


class LogDiagnosticItem(BaseModel):
    errorType: str = ""
    count: int = 0
    latestMessage: str = ""
    latestTime: str = ""
    suggestion: str = ""


class DiagnosticSummary(BaseModel):
    environment: EnvironmentInfo
    logDiagnostics: List[LogDiagnosticItem] = []
    checks: List[Dict[str, Any]] = []


# ==================== 环境诊断 ====================

ERROR_PATTERNS = [
    ("proxy_error", re.compile(r"proxy|代理.*失败|ProxyError|CONNECT.*failed", re.I),
     "检查代理配置是否正确，代理服务器是否正常运行"),
    ("timeout", re.compile(r"timeout|超时|timed?\s*out|ReadTimeout|ConnectTimeout", re.I),
     "网络超时，检查网络连接或增加超时时间"),
    ("source_error", re.compile(r"搜索.*失败|search.*fail|scraper.*error|源.*异常", re.I),
     "弹幕源请求失败，检查源是否可用或网络是否正常"),
    ("db_error", re.compile(r"database|数据库.*错误|SQLAlchemy|OperationalError|IntegrityError", re.I),
     "数据库异常，检查数据库连接和磁盘空间"),
    ("ai_error", re.compile(r"AI.*fail|openai.*error|api.*key.*invalid|模型.*失败|quota|余额", re.I),
     "AI接口异常，检查API Key是否有效、额度是否充足"),
    ("auth_error", re.compile(r"401|403|unauthorized|forbidden|认证.*失败|token.*expired", re.I),
     "认证/授权异常，检查Token或密钥是否过期"),
    ("disk_error", re.compile(r"disk.*full|磁盘.*满|No space|ENOSPC|IOError.*write", re.I),
     "磁盘空间不足，清理日志或备份文件"),
    ("memory_error", re.compile(r"MemoryError|内存.*不足|OOM|killed", re.I),
     "内存不足，考虑增加内存或减少并发"),
]


@router.get("/diagnostics/environment", response_model=EnvironmentInfo, summary="运行环境诊断")
async def get_environment_info(
    request: Request,
    config_manager: ConfigManager = Depends(get_config_manager),
):
    config_dir = os.path.join(os.getcwd(), "config")
    logs_dir = os.path.join(config_dir, "logs")
    is_docker = is_docker_environment()
    uvloop_enabled = False
    try:
        import uvloop
        uvloop_enabled = True
    except ImportError:
        pass

    # 运行时读取：直接从 engine URL 获取真实数据库类型
    db_type = get_db_type()
    try:
        engine = getattr(request.app.state, "db_engine", None)
        if engine is not None:
            db_type = engine.url.get_dialect().name
    except Exception:
        pass

    # 运行时读取：从全局缓存实例类名获取真实后端类型
    _BACKEND_LABELS = {
        "MemoryBackend": "memory",
        "RedisBackend": "redis",
        "DatabaseBackend": "database",
        "HybridBackend": "hybrid",
    }
    try:
        backend = get_cache_backend()
        cache_backend = _BACKEND_LABELS.get(type(backend).__name__, type(backend).__name__)
    except Exception:
        cache_backend = "unknown"
    import time
    tz_name = time.tzname[0] if time.tzname else "Unknown"
    # 计算当前 UTC 偏移，格式如 UTC+8 或 UTC-5
    utc_offset_seconds = -time.timezone if not time.daylight else -time.altzone
    utc_offset_hours = utc_offset_seconds / 3600
    if utc_offset_hours == int(utc_offset_hours):
        utc_offset_str = f"UTC{int(utc_offset_hours):+d}"
    else:
        h = int(utc_offset_hours)
        m = int(abs(utc_offset_hours - h) * 60)
        utc_offset_str = f"UTC{h:+d}:{m:02d}"
    tz_display = f"{tz_name} ({utc_offset_str})"

    return EnvironmentInfo(
        appVersion=APP_VERSION,
        pythonVersion=sys.version.split()[0],
        platform=platform.system(),
        architecture=platform.machine(),
        osName=f"{platform.system()} {platform.release()}",
        dbType=str(db_type),
        cacheBackend=str(cache_backend),
        configDir=config_dir,
        logsDir=logs_dir,
        configDirWritable=os.access(config_dir, os.W_OK) if os.path.exists(config_dir) else False,
        logsDirWritable=os.access(logs_dir, os.W_OK) if os.path.exists(logs_dir) else False,
        timezone=tz_display,
        isDocker=is_docker,
        uvloopEnabled=uvloop_enabled,
    )


# ==================== 日志智能诊断 ====================

@router.get("/diagnostics/log-analysis", summary="日志智能诊断分析")
async def analyze_logs(
    hours: int = 24,
    max_lines: int = 5000,
):
    """分析最近的日志文件，自动识别常见错误并给出建议"""
    results: Dict[str, Dict] = {}
    log_dir = os.path.join(os.getcwd(), "config", "logs")
    if not os.path.exists(log_dir):
        return []

    now = get_now()
    for fname in os.listdir(log_dir):
        if not fname.endswith(".log"):
            continue
        fpath = os.path.join(log_dir, fname)
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
            if (now - mtime).total_seconds() > hours * 3600 * 2:
                continue
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()[-max_lines:]
            for line in lines:
                for err_type, pattern, suggestion in ERROR_PATTERNS:
                    if pattern.search(line):
                        if err_type not in results:
                            results[err_type] = {
                                "errorType": err_type,
                                "count": 0,
                                "latestMessage": "",
                                "latestTime": "",
                                "suggestion": suggestion,
                            }
                        results[err_type]["count"] += 1
                        results[err_type]["latestMessage"] = line.strip()[:200]
                        ts_match = re.match(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", line)
                        if ts_match:
                            results[err_type]["latestTime"] = ts_match.group()
                        break
        except Exception:
            continue

    items = sorted(results.values(), key=lambda x: x["count"], reverse=True)
    return items


# ==================== 综合诊断 ====================

@router.get("/diagnostics/full", response_model=DiagnosticSummary, summary="完整诊断报告")
async def get_full_diagnostics(
    request: Request,
    config_manager: ConfigManager = Depends(get_config_manager),
):
    env = await get_environment_info(request, config_manager)
    log_items = await analyze_logs(hours=24)

    checks = []
    # 目录检查
    checks.append({
        "name": "config_dir", "label": "配置目录",
        "status": "ok" if env.configDirWritable else "warning",
        "detail": env.configDir,
    })
    checks.append({
        "name": "logs_dir", "label": "日志目录",
        "status": "ok" if env.logsDirWritable else "warning",
        "detail": env.logsDir,
    })
    # Python版本
    py_ok = sys.version_info >= (3, 10)
    checks.append({
        "name": "python_version", "label": "Python版本",
        "status": "ok" if py_ok else "warning",
        "detail": env.pythonVersion,
    })

    return DiagnosticSummary(environment=env, logDiagnostics=log_items, checks=checks)
