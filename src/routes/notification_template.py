"""
通知模板 API 路由
"""
import logging
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.database import get_db_session
from src.db.crud import notification_template as template_crud
from src.services.template_renderer import get_template_renderer
from src.notification.template_resolver import TemplateResolver

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ui/notification/templates", tags=["notification_templates"])


class TemplateUpdateRequest(BaseModel):
    title: str
    body: str


class TemplatePreviewRequest(BaseModel):
    templateId: str
    title: str
    body: str
    channel: str = "telegram"  # telegram/qq/wecom/serverchan
    # 兼容前端历史字段名 exampleStatus，二者任一均可
    sampleStatus: Optional[str] = None  # success/failed/no_change
    exampleStatus: Optional[str] = None

    @property
    def resolved_status(self) -> str:
        """归一化状态字段，优先 sampleStatus"""
        return self.sampleStatus or self.exampleStatus or "success"


@router.get("/scopes")
async def get_available_scopes(
    session: AsyncSession = Depends(get_db_session)
) -> Dict[str, Any]:
    """获取所有可用的发送范围（scopes）"""
    from src.notification.subscription_matcher import ScopeKey, SubscriptionMatcher

    # 精简后的核心事件：只保留用户真正需要的通知场景
    all_scopes = [
        # 手动操作结果
        {"key": ScopeKey.IMPORT_SUCCESS, "category": "manual", "label_key": "notification.scopeImportSuccess"},
        {"key": ScopeKey.IMPORT_FAILED, "category": "manual", "label_key": "notification.scopeImportFailed"},
        {"key": ScopeKey.REFRESH_SUCCESS, "category": "manual", "label_key": "notification.scopeRefreshSuccess"},
        {"key": ScopeKey.REFRESH_FAILED, "category": "manual", "label_key": "notification.scopeRefreshFailed"},

        # 自动追更（最重要的通知场景）
        {"key": ScopeKey.INCREMENTAL_REFRESH_SUCCESS, "category": "auto", "label_key": "notification.scopeIncrementalSuccess"},
        {"key": ScopeKey.INCREMENTAL_REFRESH_FAILED, "category": "auto", "label_key": "notification.scopeIncrementalFailed"},

        # 系统级事件
        {"key": ScopeKey.SYSTEM_STARTUP, "category": "system", "label_key": "notification.scopeSystemStartup"},
        {"key": ScopeKey.SYSTEM_EXCEPTION, "category": "system", "label_key": "notification.scopeSystemException"},
    ]

    # 获取默认配置
    default_scopes = SubscriptionMatcher.get_default_scopes()

    # 分类的 i18n 键
    category_labels = {
        "manual": "notification.groupManual",
        "auto": "notification.groupAuto",
        "system": "notification.groupSystem",
    }

    # 与本项目其他 UI 接口保持一致：直接返回数据本体，不额外包裹 data 层
    # （前端 axios 的 res.data 已是响应体，多包一层会导致解析为空）
    return {
        "scopes": all_scopes,
        "defaults": default_scopes,
        "category_labels": category_labels,
    }


@router.get("")
async def get_templates(
    session: AsyncSession = Depends(get_db_session)
) -> List[Dict[str, Any]]:
    """获取所有模板摘要"""
    templates = await template_crud.get_all_notification_templates(session)

    # 添加显示名称
    for tmpl in templates:
        tmpl["displayName"] = TemplateResolver.get_template_display_name(tmpl["templateId"], "zh")
        tmpl["displayName_en"] = TemplateResolver.get_template_display_name(tmpl["templateId"], "en")
        tmpl["displayName_tw"] = TemplateResolver.get_template_display_name(tmpl["templateId"], "tw")

    # 直接返回列表，前端以 Array.isArray(res.data) 判定
    return templates


@router.get("/{template_id}")
async def get_template(
    template_id: str,
    session: AsyncSession = Depends(get_db_session)
) -> Dict[str, Any]:
    """获取单个模板详情"""
    template = await template_crud.get_notification_template(session, template_id)
    
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在")
    
    # 添加可用变量列表（简化版本，实际应根据模板 ID 返回对应变量）
    template["variables"] = _get_template_variables(template_id)
    template["displayName"] = TemplateResolver.get_template_display_name(template_id, "zh")
    
    return template


@router.put("/{template_id}")
async def update_template(
    template_id: str,
    req: TemplateUpdateRequest,
    session: AsyncSession = Depends(get_db_session)
) -> Dict[str, Any]:
    """更新模板"""
    # 验证模板语法
    renderer = get_template_renderer()
    valid, error = renderer.validate(req.title, req.body)
    
    if not valid:
        raise HTTPException(status_code=400, detail=f"模板语法错误: {error}")
    
    # 更新模板
    success = await template_crud.update_notification_template(
        session,
        template_id,
        req.title,
        req.body
    )
    
    if not success:
        raise HTTPException(status_code=500, detail="更新失败")
    
    return {"status": "success", "message": "模板已更新"}


@router.post("/preview")
async def preview_template(
    req: TemplatePreviewRequest,
    session: AsyncSession = Depends(get_db_session)
) -> Dict[str, Any]:
    """预览模板渲染结果"""
    renderer = get_template_renderer()
    
    # 获取示例变量（状态字段已做新旧字段名归一化）
    sample_vars = _get_sample_variables(req.templateId, req.resolved_status)
    
    # 渲染
    success, title, body, error = renderer.render(req.title, req.body, sample_vars)
    
    if not success:
        return {
            "success": False,
            "error": error
        }
    
    # 模拟渠道适配
    adapted_title, adapted_body = _adapt_for_channel(title, body, req.channel)
    
    return {
        "success": True,
        "title": adapted_title,
        "body": adapted_body,
        "channel": req.channel,
    }


def _get_template_variables(template_id: str) -> List[Dict[str, Any]]:
    """获取模板可用变量（简化版本）"""
    common_vars = [
        {"name": "status_icon", "label": "状态图标", "example": "✅", "category": "通用"},
        {"name": "status_name", "label": "状态名称", "example": "成功", "category": "通用"},
        {"name": "action_name", "label": "操作名称", "example": "导入", "category": "通用"},
        {"name": "anime_title", "label": "作品标题", "example": "某动画", "category": "媒体"},
        {"name": "season", "label": "季度", "example": "1", "category": "媒体"},
        {"name": "episode", "label": "集数", "example": "1", "category": "媒体"},
        {"name": "provider", "label": "来源", "example": "bilibili", "category": "来源"},
        {"name": "comment_count", "label": "弹幕数", "example": "1000", "category": "结果"},
        {"name": "added_count", "label": "新增数", "example": "50", "category": "结果"},
        {"name": "duration", "label": "耗时", "example": "5", "category": "结果"},
        {"name": "error", "label": "错误信息", "example": "网络超时", "category": "结果"},
    ]
    return common_vars


def _get_sample_variables(template_id: str, status: str) -> Dict[str, Any]:
    """获取示例变量值"""
    base_vars = {
        "status_icon": "✅" if status == "success" else "❌",
        "status_name": "成功" if status == "success" else "失败",
        "action_name": "导入",
        "anime_title": "某部动画作品",
        "season": 1,
        "episode": 1,
        "provider": "bilibili",
        "comment_count": 1234,
        "added_count": 56,
        "duration": 5,
    }
    
    if status == "failed":
        base_vars["error"] = "网络连接超时"
    
    return base_vars


def _adapt_for_channel(title: str, body: str, channel: str) -> tuple:
    """模拟渠道适配（简化版本）"""
    # 实际应调用各渠道的适配逻辑
    if channel == "qq":
        # QQ 可能需要降级 Markdown
        return (title, body)
    return (title, body)
