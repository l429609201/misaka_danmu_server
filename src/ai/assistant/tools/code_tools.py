"""管理员源码诊断工具；工作副本不授予真实项目的直接写权限。"""

from typing import Any, Awaitable, Callable

from src.workflows.assistant_code import get_assistant_code_workflow
from src.ai.assistant.security_gateway import ToolPermission
from .base import Tool, registry


async def _capabilities(arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """检查源码能力与隔离验证后端真实状态。"""
    return await get_assistant_code_workflow().capabilities(context)


async def _search(arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """窄范围搜索源码与路径。"""
    return await get_assistant_code_workflow().search(arguments['query'], context, arguments.get('prefix', ''))


async def _read(arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """读取源码片段及冲突检测哈希。"""
    return await get_assistant_code_workflow().read(arguments['path'], arguments.get('start_line', 1), context)


async def _prepare(arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """只准备隔离草稿，不写真实项目。"""
    return await get_assistant_code_workflow().prepare(arguments['changes'], context)


async def _validate(arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """验证只能在通过宿主检查的容器中执行。"""
    return await get_assistant_code_workflow().validate(arguments['draft_id'], arguments['profile'], context)


async def _apply(arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """当前管理员修复授权或确认端点提供单次授权后应用补丁。"""
    return await get_assistant_code_workflow().apply(arguments['draft_id'], context)


async def _rollback(arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """当前管理员修复授权或独立确认后恢复未产生后续变更的补丁。"""
    return await get_assistant_code_workflow().rollback(arguments['draft_id'], context)


async def _preview(arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """重新读取服务端草稿状态，不接受模型提供的验证状态。"""
    return get_assistant_code_workflow().preview(arguments['draft_id'], context)


def _safe_executor(executor: Callable[..., Awaitable[dict[str, Any]]]) -> Callable[..., Awaitable[dict[str, Any]]]:
    async def execute(arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        try:
            return await executor(arguments, context)
        except PermissionError as exc:
            return {'error': str(exc) if exc.errno is None else '源码权限检查未通过'}
        except SyntaxError:
            return {'error': '补丁Python语法不正确，尚未创建或应用'}
        except ValueError as exc:
            return {'error': str(exc)}
    return execute


def register_code_tools() -> None:
    """注册单独的代码工具组；仅认证管理员站内会话导出。"""
    draft_schema = {'type': 'object', 'properties': {'draft_id': {'type': 'string'}},
                    'required': ['draft_id'], 'additionalProperties': False}
    definitions = [
        ('code_capabilities', '检查受控代码模式与隔离容器是否可用。先调用，不猜测权限或验证结果。',
         {'type': 'object', 'properties': {}}, _capabilities, '正在核对代码修复能力', False),
        ('code_search', '按字面关键词搜索项目源码。传prefix缩小目录；不检索运行配置、二进制或受保护内容。',
         {'type': 'object', 'properties': {'query': {'type': 'string'}, 'prefix': {'type': 'string'}},
          'required': ['query']}, _search, '正在检索项目源码', False),
        ('code_read', '分段读取允许的源码和项目规则，每次最多120行；返回sha256用于补丁冲突检测。',
         {'type': 'object', 'properties': {'path': {'type': 'string'}, 'start_line': {'type': 'integer', 'minimum': 1}},
          'required': ['path']}, _read, '正在读取源码片段', False),
        ('code_prepare_patch', '为已读取的源码准备补丁草稿，不修改真实项目。提供完整新内容及读取时的sha256；新增文件sha256为null。',
         {'type': 'object', 'properties': {'changes': {'type': 'array', 'minItems': 1, 'maxItems': 8,
          'items': {'type': 'object', 'properties': {'path': {'type': 'string'}, 'content': {'type': 'string'},
          'sha256': {'type': ['string', 'null']}}, 'required': ['path', 'content', 'sha256'], 'additionalProperties': False}}},
          'required': ['changes']}, _prepare, '正在准备修复补丁', False),
        ('code_validate_patch', '在隔离容器验证服务端补丁。profile为python_syntax/python_tests/frontend_build/frontend_lint；未配置容器时拒绝执行。',
         {'type': 'object', 'properties': {'draft_id': {'type': 'string'}, 'profile': {'type': 'string',
          'enum': ['python_syntax', 'python_tests', 'frontend_build', 'frontend_lint']}},
          'required': ['draft_id', 'profile']}, _validate, '正在隔离验证补丁', False),
        ('code_patch_status', '获取当前会话补丁真实状态、diff和验证结果。', draft_schema, _preview, '正在读取补丁状态', False),
        ('code_apply_patch', '将通过隔离验证的补丁应用到真实源码。管理员已启用本轮代码修复时自主应用；未启用时等待确认卡。不会自动重启、部署或提交。',
         draft_schema, _apply, '正在应用修复补丁', True),
        ('code_rollback_patch', '恢复本会话已应用且未产生后续修改的补丁。当前管理员修复授权允许自主恢复，否则独立确认；不自动部署或重启。',
         draft_schema, _rollback, '正在恢复已确认补丁', True),
    ]
    for name, description, parameters, executor, label, write in definitions:
        registry.register(Tool(name=name, description=description, parameters=parameters,
                               permission=ToolPermission.WRITE if write else ToolPermission.READ_ONLY,
                               executor=_safe_executor(executor), running_label=label))
