"""受控补丁验证、应用和恢复流程；隔离执行与权限状态由服务提供。"""

from pathlib import Path
from typing import Any, Optional

from src.services.assistant_code_service import (
    AssistantCodeService, CodeDraft, DIRECTORIES, READ_MANIFESTS, get_assistant_code_service,
)
from src.utils.runtime.cancellation import finish_before_cancel


class AssistantCodeWorkflow:
    """编排代码修复流程，共享服务锁和会话草稿，不提供任意执行入口。"""

    def __init__(self, service: AssistantCodeService) -> None:
        self.service = service

    async def capabilities(self, context: dict[str, Any]) -> dict[str, Any]:
        """提供代码模式能力，权限与隔离后端状态由服务核定。"""
        return await self.service.capabilities(context)

    async def search(self, query: str, context: dict[str, Any], prefix: str = '') -> dict[str, Any]:
        """通过安全服务搜索白名单源码，不另建读取入口。"""
        return await self.service.search(query, context, prefix)

    async def read(self, name: str, start: int, context: dict[str, Any]) -> dict[str, Any]:
        """通过安全服务读取源码片段及基线哈希。"""
        return await self.service.read(name, start, context)

    async def prepare(self, changes: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
        """委托服务建立绑定用户会话的安全草稿。"""
        return await self.service.prepare(changes, context)

    async def validate(self, draft_id: str, profile: str, context: dict[str, Any]) -> dict[str, Any]:
        """筛选独立源码副本，仅交由隔离容器验证并确保副本回收。"""
        service = self.service
        async with service.lock:
            draft = service.get_draft(draft_id, context)
            if draft.state != 'draft':
                raise ValueError('只有未应用补丁可验证')
            draft.validation = {'validated': False, 'status': 'not_run', 'profile': profile}
            paths = [item['path'] for item in draft.changes]
            if profile in ('python_syntax', 'python_tests'):
                if not all(path.endswith('.py') for path in paths):
                    raise ValueError('Python验证只能用于纯Python补丁')
            elif profile in ('frontend_build', 'frontend_lint'):
                if not all(path.startswith('web/src/') for path in paths):
                    raise ValueError('前端验证只能用于前端补丁')
            else:
                raise ValueError('不支持此验证profile')
            if service.runner.capabilities().get('available') is False:
                draft.validation = {'validated': False, 'status': 'unavailable', 'profile': profile,
                                    'message': '隔离容器未配置，未执行验证，补丁不可应用。'}
                return service.preview(draft_id, context)
            workspace = await service.fs.source_workspace()
            try:
                total = 0
                snapshot = {}
                # 保护源码只作为私有依赖复制，仍排除真实配置、日志、链接和共享库。
                directories = ['web/src'] if profile.startswith('frontend_') else DIRECTORIES
                for name in await service.fs.list_source_files(service.root, directories):
                    try:
                        text, digest = await service.read_source(name, snapshot=True)
                    except (OSError, PermissionError, UnicodeError, ValueError):
                        continue
                    total += len(text.encode('utf-8'))
                    if total > 32 * 1024 * 1024:
                        raise ValueError('验证副本过大，请缩小修正范围')
                    snapshot[name] = digest
                    if not await service.fs.write_text(workspace / name, text):
                        raise OSError('验证副本写入失败')
                for change in draft.changes:
                    if snapshot.get(change['path']) != change['sha256']:
                        raise ValueError('补丁基线已改变或已不允许访问')
                    if not await service.fs.write_text(workspace / change['path'], change['content']):
                        raise OSError('验证补丁写入失败')
                for name in sorted(READ_MANIFESTS):
                    if not service.fs.exists(service.root / name):
                        continue
                    text, _ = await service.read_source(name)
                    if not await service.fs.write_text(workspace / name, text):
                        raise OSError('构建清单写入失败')
                if profile.startswith('frontend_'):
                    extensions = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.ico', '.woff', '.woff2', '.ttf'}
                    for name in await service.fs.list_source_files(service.root, ['web/src/assets', 'web/public']):
                        if Path(name).suffix.lower() not in extensions or '.so' in name.lower():
                            continue
                        data = await service.fs.read_source_bytes(service.root, name, 4 * 1024 * 1024)
                        total += len(data)
                        if total > 32 * 1024 * 1024:
                            raise ValueError('验证副本过大')
                        if not await service.fs.write_bytes(workspace / name, data):
                            raise OSError('静态资源副本写入失败')
                await service.fs.make_source_workspace_readable(workspace)
                result = await service.runner.validate(workspace, paths, profile)
            finally:
                # 重复取消也必须完成副本清理，清理失败不授予应用资格。
                await finish_before_cancel(service.fs.remove_source_workspace(workspace))
            draft.validation = result
            return service.preview(draft_id, context)

    async def _restore(self, draft: CodeDraft) -> bool:
        success = True
        for change in reversed(draft.changes):
            target = self.service.root / change['path']
            if target not in draft.backups:
                continue
            original = draft.backups[target]
            try:
                current = await self.service.current_source(change['path'])
                original_bytes = original.encode('utf-8') if original is not None else None
                if current == original_bytes:
                    continue
                await self.service.replace_source(change['path'], original_bytes, change['content'].encode('utf-8'))
            except (OSError, ValueError, PermissionError):
                success = False
        return success

    async def apply(self, draft_id: str, context: dict[str, Any]) -> dict[str, Any]:
        """复核授权、隔离验证和基线，以 CAS 应用，失败或取消时完成补偿。"""
        service = self.service
        service.require_action(context)
        async with service.lock:
            draft = service.get_draft(draft_id, context)
            if draft.state != 'draft' or draft.validation.get('validated') is not True or draft.validation.get('status') != 'passed':
                raise PermissionError('补丁未通过隔离验证或已应用')
            for change in draft.changes:
                path = service.source_path(change['path'], write=True)
                digest = (await service.read_source(change['path'], write=True))[1] if service.fs.exists(path) else None
                if digest != change['sha256']:
                    raise ValueError('项目文件已发生变化，禁止覆盖；请重新生成并验证补丁')
                service.check_patch(change['path'], change['content'])
            draft.state = 'applying'
            try:
                for change in draft.changes:
                    path = service.source_path(change['path'], write=True)
                    original = change['original'] if change['sha256'] is not None else None
                    draft.backups[path] = original
                    await service.replace_source(change['path'], change['content'].encode('utf-8'),
                                                 original.encode('utf-8') if original is not None else None)
            except BaseException:
                async def compensate() -> None:
                    restored = await self._restore(draft)
                    draft.state = 'failed' if restored else 'recovery_required'
                await finish_before_cancel(compensate())
                raise
            draft.state = 'applied'
            return {'draftId': draft_id, 'state': draft.state, 'files': [item['path'] for item in draft.changes],
                    'message': '补丁已应用到源码，未重启、部署或提交。需要手动执行相应部署步骤。'}

    async def rollback(self, draft_id: str, context: dict[str, Any]) -> dict[str, Any]:
        """独立确认后恢复补丁，后续文件变化不得覆盖，取消必须等待恢复完成。"""
        service = self.service
        service.require_action(context, rollback=True)
        async with service.lock:
            draft = service.get_draft(draft_id, context)
            if draft.state != 'applied':
                raise ValueError('补丁不处于已应用状态')
            for change in draft.changes:
                text, _ = await service.read_source(change['path'], write=True)
                if text != change['content']:
                    raise ValueError('应用后的文件已变化，不允许自动恢复')
            async def settle_restore() -> dict[str, Any]:
                if not await self._restore(draft):
                    draft.state = 'recovery_required'
                    raise OSError('恢复失败，需要管理员检查')
                draft.state = 'rolled_back'
                return {'draftId': draft_id, 'state': draft.state, 'message': '源码已恢复，未重启或部署。'}
            return await finish_before_cancel(settle_restore())

    def preview(self, draft_id: str, context: dict[str, Any]) -> dict[str, Any]:
        """获取服务保存的当前会话补丁状态，用于上层确认卡。"""
        return self.service.preview(draft_id, context)


def get_assistant_code_workflow(service: Optional[AssistantCodeService] = None) -> AssistantCodeWorkflow:
    """取得绑定进程内草稿服务的轻量流程，不另建状态或生命周期。"""
    return AssistantCodeWorkflow(service if service is not None else get_assistant_code_service())
