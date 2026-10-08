"""
FileStorageService - 文件存储服务

============================================================
定位：统一收口文件系统的读取、写入、编辑、删除、创建与目录清理
============================================================

设计约束：
1. 纯 I/O 能力层 —— 不依赖数据库、不依赖 AsyncSession、不含任何弹幕/XML 业务语义
2. 所有阻塞 I/O 一律经 asyncio.to_thread 执行，避免阻塞事件循环
3. 目录删除保留原有四重安全校验，防止误删系统目录
4. 全局单例，与 get_cache_service() / get_database_service() 用法一致

替代来源（本服务建立前，这些能力散落在三个层）：
- src/db/crud/danmaku.py::_get_fs_path_from_web_path   → resolve_fs_path()
- src/tasks/delete.py::_determine_cleanup_stop_dir     → determine_cleanup_stop_dir()
- src/tasks/delete.py::_is_safe_to_delete_directory    → _is_safe_to_delete_directory()
- src/tasks/delete.py::_cleanup_empty_parent_directories → cleanup_empty_parent_directories()
- src/tasks/delete.py::delete_danmaku_file             → delete_by_web_path()
- src/db/crud/danmaku_storage.py 中的 shutil.move / Path.rename 裸调用 → move_file()
- src/db/crud/danmaku_storage.py 中重复三次的 while 冲突改名循环 → resolve_available_path()
- src/db/crud/danmaku_storage.py 中重复三次的 relative_to(CONFIG_DIR) 反查 → to_web_path()

典型用法::

    from src.services.file_storage_service import get_file_storage_service

    fs = get_file_storage_service()
    fs_path = fs.resolve_fs_path(episode.danmakuFilePath)
    await fs.delete_file(fs_path)
"""

import asyncio
import logging
import os
import shutil
import stat
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
from typing import AsyncIterator, Awaitable, List, Optional, Set, TypeVar

from src.core.env import is_docker_environment as _is_docker_environment

logger = logging.getLogger(__name__)
T = TypeVar("T")


async def wait_for_settlement(operation: Awaitable[T]) -> T:
    """等待底层 I/O 收尾后传播取消，避免写线程与文件补偿竞争。"""
    task = asyncio.ensure_future(operation)
    cancellation: Optional[asyncio.CancelledError] = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if task.cancelled():
                raise
            cancellation = exc
        except BaseException:
            # 统一消费结果，避免后台任务异常无人读取。
            break
    if cancellation is not None:
        try:
            task.result()
        except BaseException:
            logger.exception("取消等待期间底层文件操作失败")
        raise cancellation
    return task.result()


def _get_base_dir() -> Path:
    """获取基础目录，根据运行环境自动调整。

    Docker 环境固定为 /app；源码运行环境使用当前工作目录。
    与原 crud 层各文件的重复实现保持完全一致的行为。
    """
    if _is_docker_environment():
        return Path("/app")
    return Path(".")


#: 基础目录（随运行环境变化）
BASE_DIR = _get_base_dir()
#: 配置目录，Docker 标准 Web 路径 /app/config/... 的映射根
CONFIG_DIR = BASE_DIR / "config"
#: 弹幕文件根目录，同时作为目录清理的默认安全边界
DANMAKU_BASE_DIR = CONFIG_DIR / "danmaku"


class FileStorageService:
    """统一文件 I/O 与变更互斥；业务事务和补偿决策仍由编排层负责。"""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        """
        Args:
            base_dir: 目录清理的默认安全边界，默认为 DANMAKU_BASE_DIR。
                      仅用于测试或多存储根场景下的覆盖。
        """
        self._base_dir = base_dir or DANMAKU_BASE_DIR
        self._danmaku_lock = asyncio.Lock()
        self._danmaku_owner: Optional[asyncio.Task] = None

    @asynccontextmanager
    async def danmaku_mutation(self) -> AsyncIterator[None]:
        """取得单进程弹幕变更权，由调用方持有至提交或补偿结束。"""
        owner = asyncio.current_task()
        if owner is self._danmaku_owner:
            raise RuntimeError("弹幕变更锁不能嵌套获取，请由最外层业务事务持锁")
        # 锁归属统一文件服务，不另建并列的文件管理入口。
        async with self._danmaku_lock:
            self._danmaku_owner = owner
            try:
                yield
            finally:
                self._danmaku_owner = None

    async def list_source_files(self, root: Path, directories: List[str], limit: int = 20000) -> List[str]:
        """枚举指定源码目录的普通文件，不跟随符号链接或进入隐藏目录。"""
        def collect() -> List[str]:
            result: List[str] = []
            for directory in directories:
                base = root / directory
                if base.is_symlink() or not base.is_dir():
                    continue
                for current, folders, files in os.walk(base, followlinks=False):
                    folders[:] = sorted(name for name in folders
                                        if not name.startswith('.') and name not in ('node_modules', '__pycache__', 'dist')
                                        and not (Path(current) / name).is_symlink())
                    for name in sorted(files):
                        path = Path(current) / name
                        if not name.startswith('.') and not path.is_symlink():
                            result.append(path.relative_to(root).as_posix())
                            if len(result) >= limit:
                                return result
            return result
        return await asyncio.to_thread(collect)

    async def read_source_bytes(self, root: Path, relative_path: str, max_bytes: int = 262144) -> bytes:
        """逐段以 no-follow 打开源码；拒绝符号链接、硬链接和超限文件。"""
        def load() -> bytes:
            parts = Path(relative_path).parts
            if not parts or Path(relative_path).is_absolute() or any(part in ('.', '..') for part in parts):
                raise PermissionError('源码路径无效')
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                for part in parts[:-1]:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                    os.close(descriptor)
                    descriptor = child
                file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
                with os.fdopen(file_fd, 'rb') as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > max_bytes:
                        raise PermissionError('源码文件类型或大小不允许')
                    content = stream.read(max_bytes + 1)
                    if len(content) > max_bytes:
                        raise PermissionError('源码文件过大')
                    return content
            finally:
                os.close(descriptor)
        return await asyncio.to_thread(load)

    async def replace_source_bytes(self, root: Path, name: str, content: Optional[bytes], expected: Optional[bytes]) -> None:
        """复核普通源码原文后原子替换或恢复；不跟随父目录和文件链接。"""
        def replace() -> None:
            parts = Path(name).parts
            if not parts or Path(name).is_absolute() or any(part in ('.', '..') for part in parts):
                raise PermissionError('源码路径无效')
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            temporary: Optional[str] = None
            try:
                for part in parts[:-1]:
                    if content is not None:
                        try:
                            os.mkdir(part, mode=0o755, dir_fd=descriptor)
                        except FileExistsError:
                            pass
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                    os.close(descriptor)
                    descriptor = child
                previous = None
                mode = 0o644
                try:
                    file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=descriptor)
                except FileNotFoundError:
                    file_fd = None
                if file_fd is not None:
                    with os.fdopen(file_fd, 'rb') as stream:
                        info = os.fstat(stream.fileno())
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 262144:
                            raise PermissionError('源码目标类型不允许')
                        previous = stream.read(262145)
                        mode = stat.S_IMODE(info.st_mode) & 0o777
                if previous != expected:
                    raise ValueError('源码在应用期间已变化')
                if content is None:
                    if previous is not None:
                        latest = os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
                        if (latest.st_dev, latest.st_ino, latest.st_mtime_ns, latest.st_ctime_ns) != (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns):
                            raise ValueError('源码在恢复期间已变化')
                        os.unlink(parts[-1], dir_fd=descriptor)
                else:
                    temporary = '.misaka-code-' + uuid4().hex
                    target_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                        mode, dir_fd=descriptor)
                    with os.fdopen(target_fd, 'wb') as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if expected is None:
                        os.link(temporary, parts[-1], src_dir_fd=descriptor, dst_dir_fd=descriptor, follow_symlinks=False)
                        os.unlink(temporary, dir_fd=descriptor)
                    else:
                        latest = os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
                        if (latest.st_dev, latest.st_ino, latest.st_mtime_ns, latest.st_ctime_ns) != (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns):
                            raise ValueError('源码在应用期间已变化')
                        os.replace(temporary, parts[-1], src_dir_fd=descriptor, dst_dir_fd=descriptor)
                    temporary = None
                os.fsync(descriptor)
            finally:
                if temporary is not None:
                    os.unlink(temporary, dir_fd=descriptor)
                os.close(descriptor)
        await wait_for_settlement(asyncio.to_thread(replace))

    async def source_workspace(self) -> Path:
        """创建不含运行数据的独立源码工作目录，容器用户可只读遍历。"""
        def create() -> Path:
            path = Path(tempfile.mkdtemp(prefix='misaka-code-'))
            path.chmod(0o755)
            return path
        task = asyncio.create_task(asyncio.to_thread(create))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            async def discard() -> None:
                created = await task
                await self.remove_source_workspace(created)
            await wait_for_settlement(discard())
            raise

    async def make_source_workspace_readable(self, path: Path) -> None:
        """仅调整本服务临时副本的只读访问权限，不修改真实源码权限。"""
        resolved = path.resolve()
        if path.is_symlink() or resolved.parent != Path(tempfile.gettempdir()).resolve() or not resolved.name.startswith('misaka-code-'):
            raise PermissionError('源码工作目录不允许调整')
        def prepare() -> None:
            for current, folders, files in os.walk(resolved, followlinks=False):
                Path(current).chmod(0o755)
                for name in folders + files:
                    child = Path(current) / name
                    if child.is_symlink():
                        raise PermissionError('副本不允许符号链接')
                for name in files:
                    (Path(current) / name).chmod(0o644)
        await wait_for_settlement(asyncio.to_thread(prepare))

    async def remove_source_workspace(self, path: Path) -> None:
        """只清理本服务创建的临时目录，拒绝符号链接和其他根路径。"""
        resolved = path.resolve()
        parent = Path(tempfile.gettempdir()).resolve()
        if path.is_symlink() or resolved.parent != parent or not resolved.name.startswith('misaka-code-'):
            raise PermissionError('源码工作目录不允许清理')
        await wait_for_settlement(asyncio.to_thread(shutil.rmtree, resolved))

    async def write_with_backup(
        self, path: Path, content: str, backups: dict[Path, Optional[str]],
    ) -> None:
        """在调用方独占的记录中保存原内容，再执行原子覆盖。"""
        path = path.absolute()
        if path not in backups:
            exists = self.exists(path)
            original = await self.read_text(path) if exists else None
            if exists and original is None:
                raise OSError(f"无法备份文件：{path}")
            backups[path] = original
        if not await self.write_text(path, content):
            raise OSError(f"文件写入失败：{path}")

    async def restore_backups(self, backups: dict[Path, Optional[str]]) -> bool:
        """逆序恢复文件；补偿失败保留记录并返回失败，不伪装为已恢复。"""
        async def restore() -> bool:
            success = True
            for path, content in reversed(list(backups.items())):
                try:
                    ok = (await self.write_text(path, content) if content is not None
                          else not self.exists(path) or await self.delete_file(path))
                    if not ok:
                        success = False
                        logger.error("文件补偿失败，需人工恢复：%s", path)
                except Exception:
                    success = False
                    logger.exception("文件补偿异常：%s", path)
            return success

        # 重复取消不能打断恢复循环，等待全部补偿操作收尾。
        return await wait_for_settlement(restore())


    # ==================== 路径解析 ====================

    def canonical_path(self, path: Path) -> Path:
        """统一绝对路径、点段和符号链接，错误交由调用方保守处理。"""
        return path.resolve()

    def same_file_path(self, left: Path, right: Path) -> bool:
        """判断规范路径或现有文件身份相同，兼顾别名和硬链接。"""
        if self.canonical_path(left) == self.canonical_path(right):
            return True
        try:
            return left.samefile(right)
        except FileNotFoundError:
            # 缺失文件仍可通过上面的规范路径判断引用。
            return False

    def path_occupied(self, path: Path) -> bool:
        """检查目录项占用，悬空符号链接也不能被当作空闲目标。"""
        return os.path.lexists(path)


    def resolve_fs_path(self, web_path: Optional[str]) -> Optional[Path]:
        """将数据库中存储的 Web 路径转换为文件系统路径。

        兼容五种历史格式：
        - Docker 标准路径：/app/config/... → 映射到 CONFIG_DIR 下
        - 其他 /app/ 前缀路径：剥离 /app/ 前缀
        - Windows 绝对路径：D:\\... （第二字符为冒号）
        - Linux/Mac 绝对路径：/...
        - 旧版相对路径：含 /danmaku/ 或 /custom_danmaku/ 片段

        安全性：本方法的返回值会直接用于删除与覆盖写，因此统一执行
        路径穿越拦截与合法性校验（合并自 crud/danmaku_storage.py 的
        public 实现，原 crud/danmaku.py 的私有实现缺失这些防护）。

        Args:
            web_path: 数据库中的 Web 路径，可为 None

        Returns:
            对应的文件系统路径；无法解析或校验不通过时返回 None
        """
        if not web_path:
            return None

        # 统一的路径穿越拦截：任何形态的输入都不允许包含 ..
        if '..' in web_path:
            logger.warning(f"检测到路径穿越尝试，已拒绝: {web_path}")
            return None

        # Docker 标准路径: /app/config/... → 映射进 CONFIG_DIR 并校验边界
        if web_path.startswith('/app/config/'):
            relative_path = web_path[len('/app/config/'):]
            if relative_path.startswith('/'):
                logger.warning(f"非法的相对片段，已拒绝: {web_path}")
                return None
            result_path = CONFIG_DIR / relative_path
            # 校验解析后仍在 CONFIG_DIR 之内，防止符号链接等绕过
            try:
                result_path.resolve().relative_to(CONFIG_DIR.resolve())
            except ValueError:
                logger.warning(f"路径穿越检测：{web_path} 解析到 CONFIG_DIR 之外")
                return None
            return result_path

        # 其他 /app/ 前缀路径 → 剥离前缀（保留原 crud/danmaku.py 的兼容行为）
        if web_path.startswith('/app/'):
            return self._validated_path(web_path[5:], web_path)

        # Windows 项目迁移到非 Windows 环境后，仅映射 config 内的文件，保留相对目录。
        if os.name != 'nt' and len(web_path) >= 3 and web_path[1] == ':' and web_path[2] in ('/', '\\'):
            normalized = web_path.replace('\\', '/')
            _, separator, relative_path = normalized.partition('/config/')
            if separator:
                result_path = self._validated_path(CONFIG_DIR / relative_path, web_path)
                if result_path is None:
                    return None
                try:
                    result_path.resolve().relative_to(CONFIG_DIR.resolve())
                except ValueError:
                    logger.warning(f"路径穿越检测：{web_path} 解析到 CONFIG_DIR 之外")
                    return None
                return result_path

        # 绝对路径：Windows (D:\...) 或 Linux/Mac (/...)
        if web_path.startswith('/') or (len(web_path) >= 2 and web_path[1] == ':'):
            return self._validated_path(web_path, web_path)

        # 兼容旧的相对路径格式
        if '/danmaku/' in web_path or '\\danmaku\\' in web_path:
            sep = '/danmaku/' if '/danmaku/' in web_path else '\\danmaku\\'
            relative_part = web_path.split(sep, 1)[1]
            return self._validated_path(DANMAKU_BASE_DIR / relative_part, web_path)
        if '/custom_danmaku/' in web_path:
            relative_part = web_path.split('/custom_danmaku/', 1)[1]
            return self._validated_path(relative_part, web_path)

        logger.warning(f"无法从 Web 路径解析文件系统路径: {web_path}")
        return None

    @staticmethod
    def _validated_path(candidate, original: str) -> Optional[Path]:
        """构造 Path 并验证其可被解析，无效时返回 None。

        Args:
            candidate: 候选路径（str 或 Path）
            original: 原始 Web 路径，仅用于日志

        Returns:
            合法的 Path；路径非法时返回 None
        """
        try:
            result_path = Path(candidate)
            result_path.resolve()
            return result_path
        except (OSError, RuntimeError, ValueError):
            logger.warning(f"无效路径，已拒绝: {original}")
            return None

    # ==================== 读取 ====================

    def exists(self, path: Optional[Path]) -> bool:
        """判断路径是否为一个已存在的文件。"""
        return bool(path) and path.is_file()

    async def read_bytes(
        self, path: Optional[Path], *, max_bytes: int = 10 * 1024 * 1024,
    ) -> Optional[bytes]:
        """限量读取二进制文件，避免图片加载阻塞事件循环或无限占用内存。"""
        if path is None:
            return None
        if max_bytes <= 0:
            raise ValueError("二进制读取上限必须为正数")

        def _read() -> Optional[bytes]:
            if not path.is_file():
                return None
            with path.open("rb") as stream:
                data = stream.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError("文件超过二进制读取上限")
            return data

        try:
            return await asyncio.to_thread(_read)
        except (OSError, ValueError):
            logger.warning("读取二进制文件失败：%s", path, exc_info=True)
            return None

    async def write_bytes(self, path: Path, content: bytes) -> bool:
        """同目录原子写入二进制文件，等待线程收尾后才传播取消。"""
        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary: Optional[Path] = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb", dir=path.parent, prefix=f".{path.name}.",
                    suffix=".tmp", delete=False,
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                # 读者只能看到完整图片，写入失败不破坏原文件。
                os.replace(temporary, path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

        try:
            await wait_for_settlement(asyncio.to_thread(_write))
            return True
        except OSError:
            logger.exception("写入二进制文件失败：%s", path)
            return False

    async def read_text(
        self,
        path: Optional[Path],
        *,
        encoding: str = 'utf-8'
    ) -> Optional[str]:
        """异步读取文本文件内容。

        读取在线程池中执行，不阻塞事件循环。

        Args:
            path: 文件系统路径
            encoding: 文件编码，默认 utf-8

        Returns:
            文件内容；文件不存在或读取失败时返回 None
        """
        if not path:
            return None

        def _read() -> Optional[str]:
            if not path.is_file():
                return None
            return path.read_text(encoding=encoding)

        try:
            return await asyncio.to_thread(_read)
        except Exception as e:
            logger.error(f"读取文件失败: {path}。错误: {e}", exc_info=True)
            return None

    # ==================== 写入 / 创建 ====================

    async def write_text(
        self,
        path: Path,
        content: str,
        *,
        encoding: str = 'utf-8',
        make_parents: bool = True
    ) -> bool:
        """异步写入文本文件（覆盖写）。

        Args:
            path: 目标文件路径
            content: 待写入内容
            encoding: 文件编码，默认 utf-8
            make_parents: 是否自动创建缺失的父目录，默认 True

        Returns:
            写入是否成功
        """
        if not path:
            return False

        def _write() -> None:
            if make_parents:
                path.parent.mkdir(parents=True, exist_ok=True)
            # 同目录替换避免读者看到半个 XML；失败时保留原文件。
            temporary: Optional[Path] = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode='w', encoding=encoding, dir=path.parent,
                    prefix=f'.{path.name}.', suffix='.tmp', delete=False,
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

        try:
            # to_thread 不会随请求取消而停止，必须等线程结束再允许上层补偿。
            await wait_for_settlement(asyncio.to_thread(_write))
            return True
        except Exception as e:
            logger.error(f"写入文件失败: {path}。错误: {e}", exc_info=True)
            return False

    async def make_dirs(self, path: Path) -> bool:
        """创建目录（含所有缺失的父级），目录已存在时视为成功。"""
        if not path:
            return False
        try:
            await asyncio.to_thread(lambda: path.mkdir(parents=True, exist_ok=True))
            return True
        except Exception as e:
            logger.error(f"创建目录失败: {path}。错误: {e}", exc_info=True)
            return False

    # ==================== 移动 / 重命名 ====================

    def resolve_available_path(self, path: Path) -> Path:
        """为目标路径寻找一个未被占用的文件名。

        目标不存在时原样返回；已存在时按 ``{stem}_{n}{suffix}`` 递增探测，
        n 从 1 开始，直到找到空位。

        why: 原实现在 crud/danmaku_storage.py 中重复三次（迁移/重命名/套用模板），
             三处的递增规则完全一致，收口到此处避免规则漂移。

        Args:
            path: 期望的目标路径

        Returns:
            一个当前未被占用的路径
        """
        if not path or not path.exists():
            return path

        stem, suffix = path.stem, path.suffix
        counter = 1
        candidate = path
        while candidate.exists():
            candidate = candidate.parent / f"{stem}_{counter}{suffix}"
            counter += 1
        return candidate

    async def move_file(
        self,
        src: Optional[Path],
        dst: Optional[Path],
        *,
        make_parents: bool = True,
        on_conflict: str = 'error'
    ) -> Optional[Path]:
        """移动或重命名文件。

        why: 原先散落在 crud 层的 11 处裸调用（shutil.move 3 处、Path.rename 8 处）
             未经本服务，既绕过统一日志也无法复用冲突处理。

        跨设备移动由 shutil.move 兜底，因此可安全用于自定义路径
        （目标可能不在 CONFIG_DIR 所在的挂载点内）。

        Args:
            src: 源文件路径
            dst: 目标文件路径
            make_parents: 是否自动创建目标的父目录，默认 True
            on_conflict: 目标已存在时的处理方式
                - ``'error'``（默认）：放弃移动，返回 None
                - ``'rename'``：经 resolve_available_path 另取可用名
                - ``'overwrite'``：直接覆盖

        Returns:
            移动后的实际路径；源文件不存在、冲突未解决或失败时返回 None
        """
        if not src or not dst:
            return None

        def _move() -> Optional[Path]:
            if not src.is_file():
                logger.debug(f"源文件不存在，跳过移动: {src}")
                return None

            target = dst
            if target.exists():
                if on_conflict == 'rename':
                    target = self.resolve_available_path(target)
                elif on_conflict == 'error':
                    logger.warning(f"目标文件已存在，放弃移动: {target}")
                    return None
                # overwrite 交由 shutil.move 覆盖

            if make_parents:
                target.parent.mkdir(parents=True, exist_ok=True)

            # why: 统一用 shutil.move 而非 Path.rename —— 后者跨文件系统会抛
            #      OSError(EXDEV)，自定义存储路径可能位于不同挂载点。
            shutil.move(str(src), str(target))
            return target

        try:
            moved = await asyncio.to_thread(_move)
        except Exception as e:
            logger.error(f"移动文件失败: {src} → {dst}。错误: {e}", exc_info=True)
            return None

        if moved:
            logger.info(f"已移动文件: {src} → {moved}")
        return moved

    def to_web_path(self, fs_path: Optional[Path]) -> Optional[str]:
        """将文件系统路径转换回数据库存储用的 Web 路径。

        resolve_fs_path 的逆操作。位于 CONFIG_DIR 下时转为 ``/app/config/...``
        标准形式；否则原样返回绝对路径字符串（兼容自定义存储路径）。

        why: 原实现在 crud/danmaku_storage.py 中以 try/except ValueError
             的形式重复三次，收口后与 resolve_fs_path 成对维护。

        Args:
            fs_path: 文件系统路径

        Returns:
            Web 路径字符串；入参为空时返回 None
        """
        if not fs_path:
            return None
        try:
            return "/app/config/" + str(fs_path.relative_to(CONFIG_DIR))
        except ValueError:
            # 自定义路径不在 CONFIG_DIR 下，直接使用绝对路径
            return str(fs_path)

    # ==================== 删除 ====================

    async def delete_file(
        self,
        path: Optional[Path],
        *,
        cleanup_empty_dirs: bool = True
    ) -> bool:
        """删除单个文件，并可选级联清理因此产生的空父目录。

        Args:
            path: 待删除的文件路径
            cleanup_empty_dirs: 删除后是否向上清理空目录，默认 True

        Returns:
            文件是否被实际删除（文件原本不存在时返回 False）
        """
        if not path:
            return False

        try:
            deleted = await asyncio.to_thread(self._unlink_if_exists, path)
        except Exception as e:
            logger.error(f"删除文件失败: {path}。错误: {e}", exc_info=True)
            return False

        if not deleted:
            logger.debug(f"文件不存在，跳过删除: {path}")
            return False

        logger.info(f"已删除文件: {path}")

        if cleanup_empty_dirs:
            stop_at = self.determine_cleanup_stop_dir(path)
            await asyncio.to_thread(
                self.cleanup_empty_parent_directories, path, stop_at
            )

        return True

    async def delete_by_web_path(
        self,
        web_path: Optional[str],
        *,
        cleanup_empty_dirs: bool = True
    ) -> Optional[Path]:
        """按数据库中存储的 Web 路径删除文件。

        这是 delete_file 与 resolve_fs_path 的组合封装，
        替代原 tasks/delete.py::delete_danmaku_file。

        Args:
            web_path: 数据库中的 Web 路径
            cleanup_empty_dirs: 是否清理空的父目录，默认 True

        Returns:
            实际被删除的文件系统路径；未删除任何文件时返回 None
        """
        fs_path = self.resolve_fs_path(web_path)
        if not fs_path:
            return None

        deleted = await self.delete_file(
            fs_path, cleanup_empty_dirs=cleanup_empty_dirs
        )
        return fs_path if deleted else None

    @staticmethod
    def _unlink_if_exists(path: Path) -> bool:
        """同步删除文件，返回是否实际执行了删除。供线程池调用。"""
        if not path.is_file():
            return False
        path.unlink()
        return True

    # ==================== 同步删除入口 ====================
    # 说明：以下同步方法供已在线程池中执行的调用方使用
    # （典型场景：await asyncio.to_thread(fs.delete_by_web_path_sync, path)）。
    # 严禁在事件循环线程内直接调用，否则会阻塞事件循环。

    def delete_by_web_path_sync(
        self,
        web_path: Optional[str],
        *,
        cleanup_empty_dirs: bool = True
    ) -> Optional[Path]:
        """同步版按 Web 路径删除文件，替代原 tasks/delete.py::delete_danmaku_file。

        Args:
            web_path: 数据库中的 Web 路径
            cleanup_empty_dirs: 是否清理空的父目录，默认 True

        Returns:
            实际被删除的文件系统路径；未删除任何文件时返回 None
        """
        if not web_path:
            return None

        try:
            fs_path = self.resolve_fs_path(web_path)
            if not fs_path or not fs_path.is_file():
                return None

            fs_path.unlink(missing_ok=True)
            logger.debug(f"已删除文件: {fs_path}")

            if cleanup_empty_dirs:
                stop_at = self.determine_cleanup_stop_dir(fs_path)
                self.cleanup_empty_parent_directories(fs_path, stop_at)

            return fs_path
        except (ValueError, FileNotFoundError):
            # 路径无效或文件已不存在，视为无需处理
            return None
        except Exception as e:
            logger.error(f"删除文件 '{web_path}' 时出错: {e}", exc_info=True)
            return None

    def delete_by_web_paths_batch_sync(
        self,
        web_paths: List[Optional[str]]
    ) -> None:
        """同步批量删除文件，最后统一清理空目录。

        相比逐个调用 delete_by_web_path_sync 更高效：先集中删文件，
        再按目录深度倒序一次性清理，避免对同一目录重复检查。
        替代原 tasks/delete.py::delete_danmaku_files_batch。

        Args:
            web_paths: 数据库中的 Web 路径列表
        """
        if not web_paths:
            return

        # 收集所有被删除文件的父目录
        affected_dirs: Set[Path] = set()

        for web_path in web_paths:
            if not web_path:
                continue
            try:
                fs_path = self.resolve_fs_path(web_path)
                if fs_path and fs_path.is_file():
                    affected_dirs.add(fs_path.parent)
                    fs_path.unlink(missing_ok=True)
                    logger.debug(f"已删除文件: {fs_path}")
            except (ValueError, FileNotFoundError):
                pass
            except Exception as e:
                logger.error(f"删除文件 '{web_path}' 时出错: {e}", exc_info=True)

        # 统一清理空目录：按路径深度倒序，先处理深层目录
        sorted_dirs = sorted(affected_dirs, key=lambda p: len(p.parts), reverse=True)
        cleaned_dirs: Set[Path] = set()

        for dir_path in sorted_dirs:
            # 用虚拟子路径推断该目录的安全边界
            stop_at = self.determine_cleanup_stop_dir(dir_path / "dummy.xml")

            current = dir_path
            while current and current.resolve() != stop_at.resolve():
                if current in cleaned_dirs:
                    # 该目录已在前一轮处理过，无需重复
                    break
                if self.is_safe_to_delete_directory(current, base_dir=stop_at):
                    try:
                        current.rmdir()
                        logger.info(f"已清理空目录: {current}")
                        cleaned_dirs.add(current)
                        current = current.parent
                    except OSError:
                        break
                else:
                    break

    async def delete_by_web_paths_batch(
        self,
        web_paths: List[Optional[str]]
    ) -> None:
        """异步批量删除文件，内部经线程池执行同步批量逻辑。"""
        if not web_paths:
            return
        await asyncio.to_thread(self.delete_by_web_paths_batch_sync, web_paths)

    # ==================== 目录清理 ====================

    def determine_cleanup_stop_dir(self, fs_path: Path) -> Path:
        """根据文件路径确定空目录清理的停止点（安全边界）。

        - 路径位于 base_dir 之下：停止点为 base_dir
        - 自定义路径（不在 base_dir 下）：停止点为文件上方 3 级目录，
          覆盖大多数自定义模板结构（如 title/season/episode.xml）

        Args:
            fs_path: 被删除文件的路径

        Returns:
            清理的停止目录，该目录本身及其父级不会被删除
        """
        base_dir = self._base_dir
        try:
            fs_path.resolve().relative_to(base_dir.resolve())
            return base_dir
        except ValueError:
            # 自定义路径 — 向上推 3 级作为安全边界
            stop = fs_path.parent
            for _ in range(3):
                if stop.parent and stop.parent != stop:
                    stop = stop.parent
                else:
                    break
            return stop

    def is_safe_to_delete_directory(
        self,
        dir_path: Path,
        base_dir: Optional[Path] = None
    ) -> bool:
        """检查目录是否可以安全删除（公开 API，供跨层调用方复用同一套判定）。

        四重安全条件，全部满足才允许删除：
        1. 目录必须存在且确实是目录
        2. 目录必须为空（无任何文件或子目录）
        3. 目录必须位于 base_dir 之下（防止误删系统目录）
        4. 目录不能是 base_dir 本身
        """
        base_dir = base_dir or self._base_dir

        if not dir_path or not dir_path.exists() or not dir_path.is_dir():
            return False

        # 安全检查：确保目录在 base_dir 下
        try:
            dir_path.resolve().relative_to(base_dir.resolve())
        except ValueError:
            logger.warning(f"目录 {dir_path} 不在安全边界 {base_dir} 下，跳过删除")
            return False

        # 不能删除 base_dir 本身
        if dir_path.resolve() == base_dir.resolve():
            return False

        # 检查目录是否为空
        try:
            return not any(dir_path.iterdir())
        except PermissionError:
            logger.warning(f"无权限访问目录 {dir_path}")
            return False

    def cleanup_empty_parent_directories(
        self,
        file_path: Optional[Path],
        stop_at: Optional[Path] = None
    ) -> None:
        """递归清理空的父目录，直到遇到非空目录或抵达停止点。

        同步方法（纯文件系统操作），由 delete_file 经线程池调用。

        Args:
            file_path: 被删除文件的路径
            stop_at: 停止清理的目录，不会删除该目录及其父级；
                     默认取 base_dir
        """
        if not file_path:
            return

        stop_at = stop_at or self._base_dir
        parent = file_path.parent

        # 向上遍历，清理空目录
        while parent and parent != stop_at and parent.resolve() != stop_at.resolve():
            if self.is_safe_to_delete_directory(parent, base_dir=stop_at):
                try:
                    parent.rmdir()
                    logger.info(f"已清理空目录: {parent}")
                    parent = parent.parent
                except OSError as e:
                    # 目录可能不为空或无权限，停止清理
                    logger.debug(f"无法删除目录 {parent}: {e}")
                    break
            else:
                # 目录不为空或不安全，停止清理
                break


# ==================== 全局单例 ====================

_file_storage_service: Optional[FileStorageService] = None


def get_file_storage_service() -> FileStorageService:
    """获取文件存储服务的全局单例。

    与 get_cache_service() / get_database_service() 的用法保持一致。
    """
    global _file_storage_service
    if _file_storage_service is None:
        _file_storage_service = FileStorageService()
    return _file_storage_service
