"""受控代码验证的本地容器适配器，不提供宿主执行或自动安装回退。"""

import asyncio
import json
import os
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, TypeVar
from uuid import uuid4


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_IMAGE = re.compile(r"(?:[a-zA-Z0-9][a-zA-Z0-9._:/-]*@)?sha256:[0-9a-f]{64}\Z")
_REMOTE_ENV = (
    "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH",
    "CONTAINER_HOST", "CONTAINER_CONNECTION", "PODMAN_CONNECTIONS_CONF",
)
_PROCESS_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent",
                "LANG": "C", "LC_ALL": "C"}
_SYNTAX_SCRIPT = (
    "import ast,pathlib,sys; "
    "[ast.parse(pathlib.Path(p).read_bytes(),filename=p) for p in sys.argv[1:]]"
)
_PROFILES = {
    "python_syntax": ("python3", "-I", "-B", "-c", _SYNTAX_SCRIPT),
    "python_tests": ("python3", "-B", "-m", "pytest", "-p", "no:cacheprovider", "-o", "addopts=", "--"),
    "frontend_build": ("npm", "--offline", "--prefix", "/workspace/web", "run", "build",
                       "--", "--outDir", "/tmp/misaka-dist"),
    "frontend_lint": ("npm", "--offline", "--prefix", "/workspace/web", "run", "check"),
}
_MESSAGES = {
    "unavailable": "本地受控容器环境未配置或不可用，未执行验证。",
    "rejected": "验证参数不符合受控策略，未执行验证。",
    "passed": "受控容器验证通过。",
    "failed": "受控容器验证未通过。",
    "timeout": "受控容器验证超时。",
    "output_limit": "受控容器输出超过限制，验证已终止。",
    "cleanup_failed": "无法确认验证容器已清理，验证结果不可用。",
}
_OUTPUT_LIMIT = 65536
_RUN_TIMEOUT = 120.0
_CONTROL_TIMEOUT = 10.0
_T = TypeVar("_T")


class _OutputLimitError(RuntimeError):
    """内部输出限额信号，不包含运行时或用户输出。"""


async def _settle(operation: Awaitable[_T]) -> _T:
    """等待资源收尾完成，重复取消不得中断清理。"""
    task = asyncio.ensure_future(operation)
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


class AssistantCodeRunner:
    """仅执行管理员预装的本地 digest 镜像和固定验证任务。

    workspace 必须是主服务筛选后的独立、不可变副本，而非项目根目录。
    主服务必须排除所有配置、日志、凭据、流控运行数据、共享库、链接和
    特殊文件，并在验证完成前独占副本。本适配器不会读取或筛选文件内容，
    也不能把任意目录自动变成安全副本。镜像依赖必须由管理员预先准备。
    """

    def capabilities(self) -> dict[str, Any]:
        """返回配置及本地客户端能力；守护进程和镜像仍须逐次验证。"""
        config = self._configuration()
        return {"available": config is not None, "configured": config is not None,
                "runtime_verified": False, "status": "configured" if config else "unavailable",
                "profiles": list(_PROFILES), "host_execution": False,
                "auto_install": False, "raw_output": False}

    async def validate(
        self, workspace: Path, changed_files: list[str], profile: str,
    ) -> dict[str, Any]:
        """在只读隔离副本上验证，只返回固定状态和退出码，不返回原始输出。"""
        # 不回显不受信的 profile，避免参数成为另一条文本泄露通道。
        if not isinstance(profile, str) or profile not in _PROFILES:
            return self._result("rejected", None)
        paths = self._safe_paths(workspace, changed_files)
        if (paths is None or
                (profile == "python_syntax" and not any(p.endswith(".py") for p in paths)) or
                (profile == "python_tests" and not any(self._is_test(p) for p in paths))):
            return self._result("rejected", profile)
        config = self._configuration()
        if config is None:
            return self._result("unavailable", profile)
        prefix, image = config
        image_id = await self._local_image(prefix, image)
        if image_id is None:
            return self._result("unavailable", profile)
        name = "misaka-code-" + uuid4().hex
        result = self._result("unavailable", profile)
        try:
            code, _ = await self._execute(
                self._run_argv(prefix, image_id, name, workspace, paths, profile),
                timeout=_RUN_TIMEOUT,
            )
            result = self._result("passed" if code == 0 else "failed", profile, code)
        except asyncio.TimeoutError:
            result = self._result("timeout", profile)
        except _OutputLimitError:
            result = self._result("output_limit", profile)
        except (OSError, ValueError):
            result = self._result("unavailable", profile)
        finally:
            # 取消先回收客户端进程，再强制删除命名容器；不依赖客户端断线自动停止。
            owner = asyncio.current_task()
            cancellations = owner.cancelling() if owner else 0
            cleaned = await _settle(self._cleanup(prefix, name))
            if owner and owner.cancelling() > cancellations:
                raise asyncio.CancelledError()
        if not cleaned:
            return self._result("cleanup_failed", profile)
        return result

    @staticmethod
    def _configuration() -> tuple[list[str], str] | None:
        runtime = os.environ.get("MISAKA_CODE_RUNTIME", "")
        image = os.environ.get("MISAKA_CODE_IMAGE", "")
        if runtime not in ("docker", "podman") or not _IMAGE.fullmatch(image):
            return None
        if any(os.environ.get(key) for key in _REMOTE_ENV):
            return None
        executable = shutil.which(runtime, path=_PROCESS_ENV["PATH"])
        if not executable:
            return None
        # 显式本地端点和独立配置路径避免默认 context 或远程连接配置介入。
        prefix = ([executable, "--host", "unix:///var/run/docker.sock", "--config",
                   "/nonexistent/misaka-code-docker"] if runtime == "docker"
                  else [executable, "--remote=false"])
        return prefix, image

    @staticmethod
    def _safe_paths(workspace: Path, changed_files: list[str]) -> list[str] | None:
        if not isinstance(workspace, Path) or not workspace.is_absolute():
            return None
        if any(char in str(workspace) for char in (",", ":", "\n", "\r", "\x00")):
            return None
        if ".." in workspace.parts or workspace == Path("/"):
            return None
        project_root = Path(__file__).parent.parent.parent
        if workspace.is_relative_to(project_root):
            return None
        if not isinstance(changed_files, list) or not changed_files or len(changed_files) > 256:
            return None
        paths = []
        for value in changed_files:
            if not isinstance(value, str) or not value or len(value) > 512:
                return None
            path = PurePosixPath(value)
            parts = {part.lower() for part in path.parts}
            if (path.is_absolute() or str(path) != value or ".." in parts or
                    any(char in value for char in ("\\", "\x00", "\n", "\r")) or
                    any(part.startswith(".") for part in parts) or
                    parts & {"config", "logs", "log", "credentials", "secrets", "rate_limit"} or
                    ".so" in path.name.lower() or path.suffix.lower() in {".pem", ".key", ".log"}):
                return None
            paths.append("/workspace/" + value)
        return paths

    @staticmethod
    def _is_test(path: str) -> bool:
        relative = PurePosixPath(path)
        return ("tests" in relative.parts and relative.suffix == ".py" and
                (relative.name.startswith("test_") or relative.name.endswith("_test.py")))

    async def _local_image(self, prefix: list[str], image: str) -> str | None:
        try:
            os_format = "{{json .OSType}}" if "--host" in prefix else "{{json .Host.OS}}"
            code, output = await self._execute(
                [*prefix, "info", "--format", os_format], capture=True,
            )
            if code != 0 or json.loads(output) != "linux":
                return None
            code, output = await self._execute(
                [*prefix, "image", "inspect", "--format",
                 '{"id":{{json .Id}},"digests":{{json .RepoDigests}}}', image], capture=True,
            )
            if code != 0:
                return None
            metadata = json.loads(output)
            if not isinstance(metadata, dict):
                return None
            image_id = metadata.get("id")
            # Podman 的本地镜像 ID 可不带算法前缀，仍只接受完整 64 位摘要。
            if isinstance(image_id, str) and re.fullmatch(r"[0-9a-f]{64}", image_id):
                image_id = "sha256:" + image_id
            if not isinstance(image_id, str) or not _DIGEST.fullmatch(image_id):
                return None
            if "@" not in image:
                return image_id if image_id == image else None
            digests = metadata.get("digests")
            return image_id if isinstance(digests, list) and image in digests else None
        except (OSError, ValueError, TypeError, asyncio.TimeoutError, _OutputLimitError):
            return None

    @staticmethod
    def _run_argv(
        prefix: list[str], image: str, name: str, workspace: Path,
        paths: list[str], profile: str,
    ) -> list[str]:
        command = _PROFILES[profile]
        arguments = list(command[1:])
        if profile == "python_syntax":
            arguments.extend(path for path in paths if path.endswith(".py"))
        elif profile == "python_tests":
            arguments.extend(path for path in paths if AssistantCodeRunner._is_test(path))
        return [
            *prefix, "run", "--name", name, "--pull=never", "--network=none",
            "--user", "65534:65534", "--cap-drop=ALL",
            "--security-opt", "no-new-privileges", "--read-only",
            "--pids-limit", "64", "--memory", "512m", "--memory-swap", "512m",
            "--cpus", "1", "--ulimit", "nofile=256:256", "--log-driver", "none",
            "--ipc=none", "--mount", f"type=bind,src={workspace},dst=/workspace,readonly",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m,uid=65534,gid=65534,mode=1777",
            "--workdir", "/workspace", "--env", "HOME=/tmp",
            "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "PYTHONPATH=/workspace",
            "--env", "NPM_CONFIG_OFFLINE=true", "--env", "NPM_CONFIG_IGNORE_SCRIPTS=true",
            "--env", "PATH=/usr/local/bin:/usr/bin:/bin:/opt/validation/bin",
            "--entrypoint", command[0], image, *arguments,
        ]

    async def _execute(
        self, argv: list[str], *, timeout: float = _CONTROL_TIMEOUT, capture: bool = False,
    ) -> tuple[int, bytes]:
        process = None
        owner = asyncio.current_task()
        cancellations = owner.cancelling() if owner else 0
        tasks: list[asyncio.Task[Any]] = []
        spawn = asyncio.ensure_future(asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=dict(_PROCESS_ENV),
            limit=8192, close_fds=True,
        ))
        try:
            process = await asyncio.shield(spawn)
            budget = [0]
            tasks = [asyncio.ensure_future(self._drain(process.stdout, budget, capture)),
                     asyncio.ensure_future(self._drain(process.stderr, budget, False)),
                     asyncio.ensure_future(process.wait())]
            stdout, _, code = await asyncio.wait_for(asyncio.gather(*tasks), timeout)
            return code, stdout
        finally:
            # 创建子进程期间取消也要取得进程句柄，否则无法回收客户端。
            if process is None:
                process = await _settle(spawn)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await _settle(self._reap(process, tasks))
            if owner and owner.cancelling() > cancellations:
                raise asyncio.CancelledError()

    @staticmethod
    async def _drain(
        stream: asyncio.StreamReader | None, budget: list[int], capture: bool,
    ) -> bytes:
        output = bytearray()
        if stream is None:
            return bytes(output)
        while chunk := await stream.read(4096):
            budget[0] += len(chunk)
            if budget[0] > _OUTPUT_LIMIT:
                raise _OutputLimitError()
            if capture:
                output.extend(chunk)
        return bytes(output)

    @staticmethod
    async def _reap(process: asyncio.subprocess.Process, tasks: list[asyncio.Task[Any]]) -> None:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.wait()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _cleanup(self, prefix: list[str], name: str) -> bool:
        try:
            code, _ = await self._execute([*prefix, "rm", "--force", name])
            return code == 0
        except (OSError, ValueError, asyncio.TimeoutError, _OutputLimitError):
            return False

    @staticmethod
    def _result(status: str, profile: str | None, code: int | None = None) -> dict[str, Any]:
        result = {"validated": status == "passed", "status": status,
                  "message": _MESSAGES[status], "profile": profile}
        if code is not None:
            result["exit_code"] = code if isinstance(code, int) and 0 <= code <= 255 else None
        return result
