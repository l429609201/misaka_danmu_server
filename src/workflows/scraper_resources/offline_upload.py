"""弹幕源离线包校验、清单生成与部署流程。"""
import asyncio
import io
import json
import logging
import platform
import tarfile
import zipfile
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict

from src._version import APP_VERSION
from src.services.file_storage_service import get_file_storage_service
from src.services.scraper_manager import _version_satisfies
from src.services.service_container import get_task_manager
from src.utils.diagnostics.task_exceptions import TaskSuccess
from src.workflows.scraper_resources.deployment import count_scraper_files, should_restart_for_deployment
from src.workflows.scraper_resources.resources import BACKUP_DIR, _get_scrapers_dir
from src.workflows.scraper_resources.version_manager import ScraperVersionManager

logger = logging.getLogger(__name__)
_MAX_ARCHIVE_MEMBERS = 256
_MAX_MEMBER_BYTES = 128 * 1024 * 1024
_MAX_EXTRACTED_BYTES = 512 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 200


class OfflineUploadError(Exception):
    """离线包业务错误，由 HTTP 入口转换为原有错误响应。"""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _safe_archive_target(extract_dir: Path, member_name: str) -> Path:
    """拒绝绝对路径与目录穿越，解压目录由文件服务独立创建。"""
    # 只做路径计算，不通过 pathlib 执行文件 I/O。
    member = Path(member_name)
    if member.is_absolute() or '..' in member.parts:
        raise OfflineUploadError(400, f"检测到恶意压缩包路径: {member_name}")
    return extract_dir / member


def _validate_archive_size(member_count: int, member_size: int, total_size: int, packed_size: int) -> None:
    """限制解压规模，防止恶意压缩包耗尽资源。"""
    if member_count > _MAX_ARCHIVE_MEMBERS:
        raise OfflineUploadError(400, f"压缩包文件数超过限制（{_MAX_ARCHIVE_MEMBERS}）")
    if member_size > _MAX_MEMBER_BYTES:
        raise OfflineUploadError(400, "压缩包内单个文件过大")
    if total_size > _MAX_EXTRACTED_BYTES:
        raise OfflineUploadError(400, "压缩包解压后总大小超过限制")
    if packed_size > 0 and total_size / packed_size > _MAX_COMPRESSION_RATIO:
        raise OfflineUploadError(400, "压缩包压缩比异常，已拒绝解压")


def _write_member(source: Any, target: Path) -> None:
    """经文件服务分块写入成员，避免把解压后文件整体载入内存。"""
    fs = get_file_storage_service()
    fs.resource_mkdir(target.parent, parents=True, exist_ok=True)
    fs.resource_write_bytes(target, b'')
    while chunk := source.read(1024 * 1024):
        fs.resource_append_bytes(target, chunk)


def _extract_archive_safely(file_path: Path, extract_dir: Path) -> None:
    """在流程层解析压缩包，真实文件访问统一委托文件服务。"""
    fs = get_file_storage_service()
    content = fs.resource_read_bytes(file_path)
    total_size = 0
    total_packed_size = 0
    if file_path.name.endswith('.zip'):
        with zipfile.ZipFile(io.BytesIO(content), 'r') as archive:
            members = archive.infolist()
            if len(members) > _MAX_ARCHIVE_MEMBERS:
                raise OfflineUploadError(400, f"压缩包文件数超过限制（{_MAX_ARCHIVE_MEMBERS}）")
            for index, member in enumerate(members, 1):
                total_size += member.file_size
                total_packed_size += member.compress_size
                _validate_archive_size(index, member.file_size, total_size, total_packed_size)
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise OfflineUploadError(400, f"压缩包包含符号链接: {member.filename}")
                target = _safe_archive_target(extract_dir, member.filename)
                if member.is_dir():
                    fs.resource_mkdir(target, parents=True, exist_ok=True)
                else:
                    with archive.open(member) as source:
                        _write_member(source, target)
        return
    if file_path.name.endswith(('.tar.gz', '.tgz')):
        with tarfile.open(fileobj=io.BytesIO(content), mode='r:gz') as archive:
            for index, member in enumerate(archive, 1):
                total_size += member.size
                _validate_archive_size(index, member.size, total_size, max(len(content), 1))
                if not (member.isdir() or member.isfile()):
                    raise OfflineUploadError(400, f"压缩包包含不安全成员: {member.name}")
                target = _safe_archive_target(extract_dir, member.name)
                if member.isdir():
                    fs.resource_mkdir(target, parents=True, exist_ok=True)
                    continue
                source = archive.extractfile(member)
                if source is None:
                    raise OfflineUploadError(400, f"无法读取压缩包成员: {member.name}")
                with source:
                    _write_member(source, target)
        return
    raise OfflineUploadError(400, "不支持的文件格式，仅支持 .zip 或 .tar.gz")


def _prepare_manifest(extract_dir: Path) -> Dict[str, Any]:
    """先校验版本和平台，再复用既有入口生成权威清单。"""
    fs = get_file_storage_service()
    versions_file = extract_dir / 'versions.json'
    if not fs.resource_exists(versions_file):
        raise OfflineUploadError(400, "压缩包中缺少 versions.json 文件")
    versions_data = json.loads(fs.resource_read_text(versions_file, encoding='utf-8'))
    package_file = extract_dir / 'package.json'
    min_server_version = None
    if fs.resource_exists(package_file):
        try:
            min_server_version = json.loads(fs.resource_read_text(package_file, encoding='utf-8')).get('min_server_version')
        except Exception as exc:
            logger.warning(f"离线包 package.json 解析失败，回退 versions.json: {exc}")
    min_server_version = min_server_version or versions_data.get('min_server_version')
    if min_server_version and not _version_satisfies(APP_VERSION, min_server_version):
        raise OfflineUploadError(400, f"离线包要求服务器版本 >= {min_server_version}，当前版本 {APP_VERSION}，请先升级服务器")

    # 沿用离线上传的历史平台与架构映射，不改变包的兼容范围。
    expected_platform = {'linux': 'linux', 'darwin': 'macos', 'windows': 'windows'}.get(
        platform.system().lower(), platform.system().lower(),
    )
    current_arch = platform.machine().lower()
    expected_arch = {'x86_64': 'x86', 'amd64': 'x86', 'aarch64': 'arm', 'arm64': 'arm'}.get(
        current_arch, current_arch,
    )
    package_platform = versions_data.get('platform', '').lower()
    package_arch = versions_data.get('type', '').lower()
    if package_platform != expected_platform:
        raise OfflineUploadError(400, f"平台不匹配: 当前系统是 {expected_platform}, 压缩包是 {package_platform}")
    if package_arch != expected_arch:
        raise OfflineUploadError(400, f"架构不匹配: 当前系统是 {expected_arch}, 压缩包是 {package_arch}")
    if not fs.resource_exists(package_file):
        fs.resource_write_text(package_file, json.dumps({
            'version': versions_data.get('version', 'unknown'),
            'platform': versions_data.get('platform', ''),
            'type': versions_data.get('type', ''),
            'min_server_version': versions_data.get('min_server_version'),
        }, indent=2, ensure_ascii=False), encoding='utf-8')
    try:
        return ScraperVersionManager.extract_manifest_from_legacy(package_file, versions_file, extract_dir)
    except Exception as exc:
        raise OfflineUploadError(500, f"生成 manifest 失败: {exc}") from exc


async def _reload_scrapers(manager: Any, progress_callback: Any) -> None:
    """通过统一任务管理器执行延迟热加载，不创建游离后台任务。"""
    await asyncio.sleep(0.5)
    await progress_callback(0, "正在热加载弹幕源")
    await manager.load_and_sync_scrapers()
    logger.info("弹幕源热加载完成")
    raise TaskSuccess("弹幕源热加载完成")


async def _deploy_package(extract_dir: Path, manager: Any, username: str) -> Dict[str, Any]:
    """沿用备份优先及热加载双目录部署策略，不自动重启进程。"""
    fs = get_file_storage_service()
    scrapers_dir = _get_scrapers_dir()
    file_count = count_scraper_files(extract_dir)
    strategy = should_restart_for_deployment(scrapers_dir, BACKUP_DIR, manager, logger_instance=logger)
    manifest = _prepare_manifest(extract_dir)
    need_restart = strategy['need_restart']
    fs.resource_mkdir(scrapers_dir, parents=True, exist_ok=True)
    fs.resource_mkdir(BACKUP_DIR, parents=True, exist_ok=True)
    if need_restart:
        ScraperVersionManager.copy_scraper_files(extract_dir, BACKUP_DIR)
        ScraperVersionManager.save_manifest(manifest, BACKUP_DIR)
    else:
        ScraperVersionManager.copy_scraper_files(extract_dir, scrapers_dir)
        ScraperVersionManager.copy_scraper_files(extract_dir, BACKUP_DIR)
        ScraperVersionManager.save_manifest(manifest, scrapers_dir)
        ScraperVersionManager.save_manifest(manifest, BACKUP_DIR)
        logger.info(f"用户 '{username}' 上传了离线包,共 {file_count} 个文件")
        await get_task_manager().submit_task(
            lambda session, callback: _reload_scrapers(manager, callback),
            '离线包弹幕源热加载', queue_type='management', run_immediately=True,
        )
    return {
        'message': f"上传成功,共安装 {file_count} 个文件（{'需重启容器生效' if need_restart else '已热加载'}）",
        'version': manifest.get('version'),
        'scrapers': list(manifest.get('sources', {}).keys()),
        'need_restart': need_restart,
    }


async def install_offline_package(
    filename: str, read_chunk: Callable[[int], Awaitable[bytes]], manager: Any, username: str,
) -> Dict[str, Any]:
    """保存请求提供的分块数据，校验并安装离线包，始终清理临时资源。"""
    fs = get_file_storage_service()
    temp_path = None
    try:
        temp_path = fs.resource_temp_dir()
        file_path = temp_path / filename
        fs.resource_write_bytes(file_path, b'')
        while chunk := await read_chunk(1024 * 1024):
            fs.resource_append_bytes(file_path, chunk)
        extract_dir = temp_path / 'extracted'
        fs.resource_mkdir(extract_dir)
        _extract_archive_safely(file_path, extract_dir)
        return await _deploy_package(extract_dir, manager, username)
    except OfflineUploadError:
        raise
    except PermissionError as exc:
        raise OfflineUploadError(403, f"权限错误: 无法写入弹幕源文件到 {_get_scrapers_dir()}。错误: {exc}") from exc
    except OSError as exc:
        raise OfflineUploadError(500, f"文件系统错误: 无法写入弹幕源文件到 {_get_scrapers_dir()}。错误: {exc}") from exc
    except Exception as exc:
        logger.error(f"上传弹幕源离线包失败: {exc}", exc_info=True)
        raise OfflineUploadError(500, f"上传失败: {exc}") from exc
    finally:
        if temp_path is not None:
            fs.resource_rmtree(temp_path)
