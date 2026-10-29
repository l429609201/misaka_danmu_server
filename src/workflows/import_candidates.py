"""下载候选顺延编排：仅消费建库前的明确候选拒绝信号。"""
from functools import wraps
from inspect import signature
from typing import Any, Awaitable, Callable

from src.utils.diagnostics.task_exceptions import TaskFailed


class ImportCandidateRejected(TaskFailed):
    """候选在建库前无有效分集或弹幕，可以安全尝试下一源。"""


def with_import_candidates(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """为单源导入添加候选循环，保留任务签名且不反向依赖任务模块。"""
    task_signature = signature(func)

    @wraps(func)
    async def run(*args: Any, **kwargs: Any) -> Any:
        bound = task_signature.bind(*args, **kwargs)
        bound.apply_defaults()
        params = dict(bound.arguments)
        candidates = params.pop("fallbackCandidates", None) or []
        # 主选源始终优先；不使用候选标题覆盖已经识别词处理的入库标题。
        ordered = [{"provider": params["provider"], "mediaId": params["mediaId"],
                    "mediaType": params["mediaType"]}, *candidates]
        seen = set()
        failures = []
        for candidate in ordered:
            provider = candidate.get("provider")
            media_id = candidate.get("mediaId")
            key = (provider, media_id)
            if not provider or not media_id or key in seen:
                continue
            seen.add(key)
            attempt = dict(params)
            attempt.update(provider=provider, mediaId=media_id,
                           mediaType=candidate.get("mediaType") or params["mediaType"])
            try:
                # 成功、暂停、取消、数据库异常均直接结束循环；不捕获普通失败。
                return await func(**attempt)
            except ImportCandidateRejected as exc:
                failures.append(f"{provider}: {exc}")
                await params["progress_callback"](10, f"候选源 {provider} 验证失败，检查剩余候选")
        raise TaskFailed("所有候选源均未通过建库前验证：" + "; ".join(failures))

    return run
