"""受控源码诊断与修复服务；工作副本与真实项目应用权限分离。"""

import ast
import asyncio
import difflib
import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from src.core.config import settings
from src.utils.model_content_policy import contains_forbidden_control_content
from src.schemas.auth import User
from src.services.assistant_code_runner import AssistantCodeRunner
from src.services.file_storage_service import FileStorageService, get_file_storage_service

ROOT = Path(__file__).resolve().parents[2]
DIRECTORIES = ['src', 'web/src', 'tests', 'docs']
EXTENSIONS = {'.py', '.js', '.jsx', '.ts', '.tsx', '.css', '.html', '.md'}
# 禁止通过修正助手自身来扩大能力，也不开放真实配置、认证和部署入口。
PROTECTED = ('src/ai/assistant/', 'src/rate_limit/', 'src/utils/auth/',
             'src/services/assistant_code', 'src/workflows/assistant_code', 'src/services/file_storage_service.py',
             'src/services/service_container.py', 'src/utils/model_content_policy.py',
             'src/utils/runtime/cancellation.py', 'src/core/', 'src/main.py',
             'src/api/ui/assistant', 'src/api/ui/auth', 'src/api/ui/config',
             'src/api/control/settings', 'web/src/pages/task/',
             'web/src/components/assistant/AssistantPanel.jsx', 'web/src/components/assistant/useAssistantChat.js',
             'src/schemas/auth.py', 'src/schemas/ui_models.py',
             'tests/test_assistant_code', 'tests/test_assistant_tool_security')
READ_MANIFESTS = {'web/package.json', 'web/package-lock.json', 'web/index.html', 'web/vite.config.js', 'web/.eslintrc.cjs'}
READ_RULES = {'docs/最终架构规范.md', 'src/ai/assistant/rules/02-design-patterns.md',
              'src/ai/assistant/rules/03-code-styles.md'}
SENSITIVE_LITERAL = re.compile(
    r'-----BEGIN [^-]*PRIVATE KEY|(?:password|passwd|api[_-]?key|secret|cookie|(?:access[_-]?)?token)'
    r'[\"\x27]?\s*[=:]\s*[\"\x27](?P<literal>[^\"\x27\r\n]{8,})[\"\x27]|https?://[^\s/:]+:[^\s/@]+@', re.I)
MAX_FILE = 262144
MAX_DRAFTS = 20
TTL = 1800


def code_authorized(context: dict[str, Any]) -> bool:
    """只信任服务端鉴权用户和站内会话，通知渠道不能获得编码权限。"""
    user = context.get('current_user')
    return bool(isinstance(user, User) and context.get('session_id')
                and context.get('owner_id') == user.id
                and user.username == os.environ.get('MISAKA_CODE_ADMIN_USERNAME', settings.admin.initial_user or 'admin'))


def code_write_authorized(context: dict[str, Any]) -> bool:
    """只认可服务端从管理员本轮修复模式取得的授权，不认可模型自报。"""
    return code_authorized(context) and context.get('code_repair_authorized') is True


@dataclass
class CodeDraft:
    """保存绑定用户会话的补丁原文、哈希、验证状态与应用结果。"""
    owner: int
    session: str
    changes: list[dict[str, Any]]
    expires: float
    validation: dict[str, Any] = field(default_factory=lambda: {'validated': False, 'status': 'not_run'})
    state: str = 'draft'
    backups: dict[Path, Optional[str]] = field(default_factory=dict)


class AssistantCodeService:
    """只操作白名单文本源码；每次读取和应用均重新校验权限及边界。"""

    def __init__(self, root: Path = ROOT, fs: Optional[FileStorageService] = None,
                 runner: Optional[AssistantCodeRunner] = None) -> None:
        self.root = root.resolve()
        self.fs = fs or get_file_storage_service()
        self.runner = runner or AssistantCodeRunner()
        self.drafts: dict[str, CodeDraft] = {}
        self.lock = asyncio.Lock()

    def _path(self, name: str, write: bool = False, snapshot: bool = False) -> Path:
        if not isinstance(name, str) or '\\' in name:
            raise PermissionError('源码路径无效')
        path = Path(name)
        normalized = path.as_posix()
        if any(part.lower() in {'config', 'logs', 'log', 'credentials', 'secrets', 'data', 'runtime_data'} for part in path.parts):
            raise PermissionError('运行数据目录不属于源码范围')
        if (path.is_absolute() or '..' in path.parts or normalized != name
                or (name not in READ_MANIFESTS and any(part.startswith('.') for part in path.parts))
                or '.so' in path.name.lower() or (name not in READ_MANIFESTS and path.suffix.lower() not in EXTENSIONS)
                or (name not in READ_MANIFESTS and not any(name.startswith(directory + '/') for directory in DIRECTORIES))
                or (contains_forbidden_control_content(name) and (write or not snapshot))):
            raise PermissionError('源码路径不在允许范围')
        if any(name.startswith(prefix) for prefix in PROTECTED) and (write or (name not in READ_RULES and not snapshot)):
            raise PermissionError('受保护源码不允许访问或修改')
        if write and (path.name == '__init__.py' or (path.suffix == '.py' and (self.root / path.with_suffix('')).is_dir())):
            raise PermissionError('包初始化和同名包遮蔽不允许修改')
        if write and (name.startswith('docs/') or name in READ_RULES or name in READ_MANIFESTS):
            raise PermissionError('项目规则只读')
        target = self.root / path
        for ancestor in (target, *target.parents):
            if ancestor == self.root:
                break
            if ancestor.is_symlink():
                raise PermissionError('禁止符号链接源码')
        if not target.resolve().is_relative_to(self.root):
            raise PermissionError('源码路径越界')
        return target

    def _safe_text(self, text: str) -> None:
        if '\0' in text or contains_forbidden_control_content(text) or SENSITIVE_LITERAL.search(text):
            raise PermissionError('源码包含受保护内容，不向模型提供或允许修改')

    def _check_patch(self, name: str, text: str) -> None:
        self._safe_text(text)
        if name.endswith('.py'):
            tree = ast.parse(text, filename=name)
            dangerous_modules = {'os', 'sys', 'subprocess', 'importlib', 'builtins', 'ctypes', 'socket', 'inspect', 'pickle', 'marshal', 'pathlib', 'io', 'shutil', 'tempfile'}
            dangerous_names = {'eval', 'exec', 'compile', '__import__', 'getattr', 'setattr', 'delattr', 'globals', 'locals', 'vars', 'open', 'breakpoint'}
            dangerous_attrs = {'open', 'write', '__dict__', '__class__', '__globals__', '__builtins__', '__subclasses__',
                               'read_text', 'read_bytes', 'write_text', 'write_bytes', 'unlink', 'rename',
                               'read_source_bytes', 'replace_source_bytes', 'write_with_backup', 'restore_backups',
                               'source_path', 'read_source', 'current_source', 'replace_source', 'get_draft',
                               'delete_file', 'delete_by_web_path', 'move_file', 'make_dirs', 'source_workspace',
                               'get_file_storage_service', 'FileStorageService',
                               'config', 'execute', 'execute_query', 'execute_sql', 'system', 'popen'}
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) and any(alias.name.split('.')[0] in dangerous_modules for alias in node.names):
                    raise PermissionError('补丁包含不允许的运行时依赖')
                if isinstance(node, ast.ImportFrom) and (node.module or '').split('.')[0] in dangerous_modules:
                    raise PermissionError('补丁包含不允许的运行时依赖')
                if isinstance(node, ast.ImportFrom):
                    parts = list(Path(name).parent.parts)
                    if node.level:
                        parts = parts[:len(parts) - node.level + 1]
                        module_path = '/'.join(parts + (node.module or '').split('.')).rstrip('/')
                    else:
                        module_path = (node.module or '').replace('.', '/')
                    if any(module_path.startswith(prefix.rstrip('/').removesuffix('.py')) for prefix in PROTECTED):
                        raise PermissionError('补丁不得引入受保护模块的运行时访问')
                    if any(alias.name in {'FileStorageService', 'get_file_storage_service', 'AssistantCodeService',
                                          'get_assistant_code_service', 'AssistantCodeRunner', 'AssistantCodeWorkflow',
                                          'get_assistant_code_workflow', '*'} for alias in node.names):
                        raise PermissionError('补丁不得引入受保护能力的导出符号')
                if isinstance(node, ast.Import):
                    if any(any(alias.name.replace('.', '/').startswith(prefix.rstrip('/').removesuffix('.py'))
                               for prefix in PROTECTED) for alias in node.names):
                        raise PermissionError('补丁不得引入受保护模块的运行时访问')
                if isinstance(node, ast.Name) and node.id in dangerous_names:
                    raise PermissionError('补丁包含动态执行或反射访问')
                if isinstance(node, ast.Attribute) and (node.attr in dangerous_attrs or node.attr in dangerous_modules or node.attr.startswith('__')):
                    raise PermissionError('补丁包含受保护运行时访问')
        elif re.search(r'\b(?:eval|Function|atob|btoa|fromCharCode)\b|__proto__', text):
            raise PermissionError('补丁包含动态执行构造')

    async def _read(self, name: str, write: bool = False, snapshot: bool = False) -> tuple[str, str]:
        self._path(name, write, snapshot)
        raw = await self.fs.read_source_bytes(self.root, name, 4 * 1024 * 1024 if snapshot or name in READ_MANIFESTS else MAX_FILE)
        text = raw.decode('utf-8')
        if name in READ_RULES:
            text = '\n'.join(line for line in text.splitlines()
                             if not contains_forbidden_control_content(line) and not SENSITIVE_LITERAL.search(line))
        if snapshot:
            if '\0' in text:
                raise PermissionError('源码依赖包含不允许复制的内容')
            for match in reversed(list(SENSITIVE_LITERAL.finditer(text))):
                if match.group('literal') is None:
                    raise PermissionError('源码依赖包含不允许复制的内容')
                start, end = match.span('literal')
                text = text[:start] + '<redacted>' + text[end:]
        else:
            self._safe_text(text)
        return text, hashlib.sha256(raw).hexdigest()

    def _require(self, context: dict[str, Any]) -> None:
        if not code_authorized(context):
            raise PermissionError('仅站内管理员当前会话可使用代码模式')

    def _draft(self, draft_id: str, context: dict[str, Any]) -> CodeDraft:
        self._require(context)
        if not isinstance(draft_id, str) or not draft_id.strip() or len(draft_id) > 128:
            raise ValueError('补丁编号必须为非空字符串')
        draft = self.drafts.get(draft_id)
        if (draft is None or draft.owner != context['owner_id'] or draft.session != context['session_id']
                or draft.expires <= time.time()):
            raise PermissionError('补丁已过期或不属于当前会话')
        return draft

    async def capabilities(self, context: dict[str, Any]) -> dict[str, Any]:
        """返回真实能力与部署方式，不把配置声明视为隔离验证通过。"""
        self._require(context)
        return {'sourceRead': True, 'patchDraft': True, 'confirmationRequired': not code_write_authorized(context),
                'autonomousRepairAuthorized': code_write_authorized(context),
                'sharedLibrariesReadable': False, 'runtime': self.runner.capabilities(),
                'runtimeSetup': '管理员在服务环境配置 MISAKA_CODE_RUNTIME 与 MISAKA_CODE_IMAGE；镜像必须预装验证依赖并固定摘要。',
                'message': '只读项目规则；受保护模块不可修改；未通过隔离验证的补丁不可应用。'}

    async def search(self, query: str, context: dict[str, Any], prefix: str = '') -> dict[str, Any]:
        """按字面关键字检索路径与行，返回行号并限制读取总量。"""
        self._require(context)
        if not query or len(query) > 200 or len(prefix) > 200:
            raise ValueError('搜索关键词或范围无效')
        self._safe_text(query)
        hits = []
        scanned = 0
        for name in await self.fs.list_source_files(self.root, DIRECTORIES):
            if prefix and not name.startswith(prefix):
                continue
            try:
                self._path(name)
                scanned += 1
                if query.casefold() in name.casefold():
                    hits.append({'path': name, 'line': 0, 'text': '路径匹配'})
                else:
                    text, _ = await self._read(name)
                    for number, line in enumerate(text.splitlines(), 1):
                        if query.casefold() in line.casefold():
                            hits.append({'path': name, 'line': number, 'text': line[:400]})
                            if len(hits) >= 40:
                                break
            except (OSError, PermissionError, UnicodeError, ValueError):
                continue
            if len(hits) >= 40 or scanned >= 1000:
                break
        return {'matches': hits[:40], 'scanned': scanned,
                'limited': len(hits) >= 40 or scanned >= 1000}

    async def read(self, name: str, start: int, context: dict[str, Any]) -> dict[str, Any]:
        """返回最多一百二十行及真实哈希，修正必须携带该哈希。"""
        self._require(context)
        if start < 1:
            raise ValueError('行号必须大于零')
        text, digest = await self._read(name)
        lines = text.splitlines()
        return {'path': name, 'sha256': digest, 'startLine': start,
                'totalLines': len(lines), 'content': '\n'.join(lines[start - 1:start + 119]),
                'nextLine': start + 120 if len(lines) >= start + 120 else None}

    async def prepare(self, changes: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
        """创建有限量替换或新增补丁，不删除文件且不直接写真实项目。"""
        self._require(context)
        if not isinstance(changes, list) or not 1 <= len(changes) <= 8:
            raise ValueError('每个补丁需要一到八个文件')
        prepared = []
        names = set()
        for change in changes:
            name = change['path']
            target = self._path(name, True)
            if name in names:
                raise ValueError('补丁路径重复')
            names.add(name)
            content = change['content']
            if not isinstance(content, str) or len(content.encode('utf-8')) > MAX_FILE:
                raise ValueError('补丁内容超限')
            self._check_patch(name, content)
            expected = change.get('sha256')
            if self.fs.exists(target):
                old, digest = await self._read(name, True)
                if expected != digest:
                    raise ValueError('原文件已变更，请重新读取后生成补丁')
            else:
                if expected is not None:
                    raise ValueError('新增文件哈希必须为 null')
                old, digest = '', None
            if old == content:
                raise ValueError('补丁没有实际变更')
            prepared.append({'path': name, 'content': content, 'original': old,
                             'sha256': digest, 'action': 'add' if digest is None else 'update'})
        async with self.lock:
            self.drafts = {key: draft for key, draft in self.drafts.items()
                           if draft.expires > time.time()}
            if len(self.drafts) >= MAX_DRAFTS:
                raise ValueError('待处理补丁过多，请等待过期或完成现有补丁')
            draft_id = uuid4().hex
            self.drafts[draft_id] = CodeDraft(context['owner_id'], context['session_id'], prepared, time.time() + TTL)
            return self.preview(draft_id, context)

    def preview(self, draft_id: str, context: dict[str, Any]) -> dict[str, Any]:
        """确认卡只能获取服务端保存的补丁，不接受模型自报验证结果。"""
        draft = self._draft(draft_id, context)
        diff = ''.join(''.join(difflib.unified_diff(
            change['original'].splitlines(keepends=True), change['content'].splitlines(keepends=True),
            fromfile=change['path'], tofile=change['path'])) for change in draft.changes)
        return {'draftId': draft_id, 'files': [{'path': item['path'], 'action': item['action']} for item in draft.changes],
                'diff': diff[:50000], 'diffTruncated': len(diff) > 50000,
                'validation': draft.validation, 'state': draft.state,
                'expiresAt': draft.expires, 'recoveryScope': '当前用户会话、当前服务进程内有效，补丁和恢复记录30分钟过期。'}

    def require_action(self, context: dict[str, Any], *, rollback: bool = False) -> None:
        """复核服务端身份和单次确认或本轮修复授权。"""
        self._require(context)
        if context.get('confirmed_action') is not True and not code_write_authorized(context):
            raise PermissionError('恢复尚未获得确认' if rollback else '补丁尚未获得确认卡授权')

    def get_draft(self, draft_id: str, context: dict[str, Any]) -> CodeDraft:
        """向持有服务锁的流程提供当前用户会话有效草稿。"""
        return self._draft(draft_id, context)

    def source_path(self, name: str, *, write: bool = False) -> Path:
        """提供经白名单、保护范围和链接边界复核的源码路径。"""
        return self._path(name, write)

    def check_patch(self, name: str, text: str) -> None:
        """重新执行补丁安全策略，不暴露任意执行能力。"""
        self._check_patch(name, text)

    async def read_source(self, name: str, *, write: bool = False, snapshot: bool = False) -> tuple[str, str]:
        """提供限量读取、保护文本筛选和原始哈希的窄能力。"""
        return await self._read(name, write, snapshot)

    async def current_source(self, name: str) -> Optional[bytes]:
        """在写边界复核后读取当前原文；缺失时返回空值。"""
        target = self._path(name, True)
        return await self.fs.read_source_bytes(self.root, name, MAX_FILE) if self.fs.exists(target) else None

    async def replace_source(self, name: str, content: Optional[bytes], expected: Optional[bytes]) -> None:
        """复核路径后执行文件存储 CAS，禁止无条件覆盖或删除。"""
        self._path(name, True)
        await self.fs.replace_source_bytes(self.root, name, content, expected)


_code_service: Optional[AssistantCodeService] = None


def get_assistant_code_service() -> AssistantCodeService:
    """取得进程内代码服务；重启后旧补丁失效，避免不明状态重复应用。"""
    global _code_service
    if _code_service is None:
        _code_service = AssistantCodeService()
    return _code_service
