"""
弹幕编辑API - 弹幕详情、时间偏移、分集拆分、分集合并
"""

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException

# DTO 归位到 Schema 层，API 只保留协议适配与鉴权。
from src.schemas.danmaku_edit import (
    CommentsPageResponse, DanmakuDetailResponse, MergeRequest, MergeResponse,
    SplitRequest, SplitResponse, TimeOffsetRequest, TimeOffsetResponse,
)
# 配置服务单例由配置模块提供，不能从服务容器导入。
from src.services.config_service import get_config_service
from src.services.service_container import get_database_service
from src.utils.auth import security
from src.workflows.danmaku_edit_workflow import DanmakuEditWorkflow

router = APIRouter()


# ==================== API端点 ====================

@router.get('/danmaku/detail/{episodeId}', response_model=DanmakuDetailResponse, summary='获取弹幕详情')
async def get_danmaku_detail(
    episodeId: int, current_user: Any = Depends(security.get_current_user),
) -> DanmakuDetailResponse:
    """获取分集统计与预览。"""
    db = get_database_service()
    async with db.transaction():
        result = await DanmakuEditWorkflow().get_danmaku_detail(episodeId)
    if result is None:
        raise HTTPException(status_code=404, detail='分集不存在或没有弹幕')
    return DanmakuDetailResponse(**result)


@router.get('/danmaku/comments/{episodeId}', response_model=CommentsPageResponse, summary='分页获取弹幕列表')
async def get_danmaku_comments(
    episodeId: int, page: int = 1, pageSize: int = 100,
    startTime: Optional[float] = None, endTime: Optional[float] = None,
    current_user: Any = Depends(security.get_current_user),
) -> CommentsPageResponse:
    """分页获取弹幕，不将缺失分集解包为响应模型。"""
    if page < 1 or pageSize < 1:
        raise HTTPException(status_code=422, detail='页码和每页数量必须大于0')
    db = get_database_service()
    async with db.transaction():
        result = await DanmakuEditWorkflow().get_danmaku_comments_page(
            episodeId, page, pageSize, startTime, endTime,
        )
    if result is None:
        raise HTTPException(status_code=404, detail='分集不存在或没有弹幕')
    return CommentsPageResponse(**result)


@router.post('/danmaku/offset', response_model=TimeOffsetResponse, summary='时间偏移调整')
async def apply_time_offset(
    request: TimeOffsetRequest, current_user: Any = Depends(security.get_current_user),
) -> TimeOffsetResponse:
    """委托工作流执行偏移，写入失败不得向前端报告成功。"""
    modified = total = 0
    workflow = DanmakuEditWorkflow()
    for episode_id in dict.fromkeys(request.episodeIds):
        result = await workflow.apply_time_offset(episode_id, request.offsetSeconds)
        if not result.get('success'):
            raise HTTPException(status_code=500, detail=f'分集 {episode_id} 偏移失败，已完成 {modified} 集')
        modified += 1
        total += result.get('modifiedCount', 0)
    return TimeOffsetResponse(success=True, modifiedCount=modified, totalComments=total)


@router.post('/danmaku/split', response_model=SplitResponse, summary='分集拆分')
async def split_episode_danmaku(
    request: SplitRequest, current_user: Any = Depends(security.get_current_user),
) -> SplitResponse:
    """拆分事务与文件补偿由工作流负责，API 不嵌套写事务。"""
    result = await DanmakuEditWorkflow().split_episode_danmaku(
        request.sourceEpisodeId, [s.model_dump() for s in request.splits],
        request.deleteSource, request.resetTime, get_config_service(),
    )
    return SplitResponse(**result)


@router.post('/danmaku/merge', response_model=MergeResponse, summary='分集合并')
async def merge_episodes_danmaku(
    request: MergeRequest, current_user: Any = Depends(security.get_current_user),
) -> MergeResponse:
    """合并源弹幕，保持请求与响应字段不变。"""
    result = await DanmakuEditWorkflow().merge_episodes_danmaku(
        [s.model_dump() for s in request.sourceEpisodes], request.targetEpisodeIndex,
        request.targetTitle, request.deleteSources, request.deduplicate, get_config_service(),
    )
    return MergeResponse(**result)

