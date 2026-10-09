"""
通知模板 API 路由
"""
import logging
from typing import Dict, Any, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.notification.subscription_matcher import ScopeKey, SubscriptionMatcher
from src.notification.template_resolver import TemplateResolver
from src.services.service_container import get_database_service
from src.services.template_renderer import get_template_renderer
from src.schemas.notification_template import TemplateID, empty_template_variables
from src.utils.progress_bar import build_progress_bar

logger = logging.getLogger(__name__)

# 编辑器使用内置 SVG 示例图，避免预览依赖外网图片或暴露真实媒体地址。
_PREVIEW_IMAGE_URL = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='640' height='360'"
    "%3E%3Crect width='100%25' height='100%25' fill='%231f2937'/%3E"
    "%3Ctext x='50%25' y='50%25' fill='white' font-size='32' text-anchor='middle'"
    "%3EPoster Preview%3C/text%3E%3C/svg%3E"
)

router = APIRouter(prefix="/api/ui/notification/templates", tags=["notification_templates"])


class TemplateUpdateRequest(BaseModel):
    title: str
    body: str
    imageEnabled: bool = True


class TemplatePreviewRequest(BaseModel):
    templateId: str
    title: str
    body: str
    channel: str = "telegram"  # telegram/qq/wecom/serverchan
    # 兼容前端历史字段名 exampleStatus，二者任一均可
    sampleStatus: Optional[str] = None  # success/failed/no_change
    exampleStatus: Optional[str] = None
    exampleProgress: int = Field(55, ge=0, le=100)
    imageEnabled: bool = True

    @property
    def resolved_status(self) -> str:
        """归一化状态字段，优先 sampleStatus"""
        return self.sampleStatus or self.exampleStatus or (
            "running" if self.templateId == TemplateID.TASK_PROGRESS else "success"
        )


@router.get("/scopes")
async def get_available_scopes() -> Dict[str, Any]:
    """获取所有可用的发送范围（scopes）"""

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
async def get_templates() -> List[Dict[str, Any]]:
    """获取所有模板摘要"""
    # 模板读取同样通过统一事务访问，避免遗留 CRUD 变量悬空。
    db = get_database_service()
    async with db.transaction():
        templates = await db.notification_template.get_all()

    # 添加显示名称
    for tmpl in templates:
        tmpl["displayName"] = TemplateResolver.get_template_display_name(tmpl["templateId"], "zh")
        tmpl["displayName_en"] = TemplateResolver.get_template_display_name(tmpl["templateId"], "en")
        tmpl["displayName_tw"] = TemplateResolver.get_template_display_name(tmpl["templateId"], "tw")

    # 直接返回列表，前端以 Array.isArray(res.data) 判定
    return templates


@router.get("/{template_id}")
async def get_template(template_id: str) -> Dict[str, Any]:
    """获取单个模板详情"""
    db = get_database_service()
    async with db.transaction():
        template = await db.notification_template.get_by_id(template_id)
    
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
) -> Dict[str, Any]:
    """更新模板"""
    # 验证模板语法
    renderer = get_template_renderer()
    valid, error = renderer.validate(req.title, req.body)
    
    if not valid:
        raise HTTPException(status_code=400, detail=f"模板语法错误: {error}")
    
    # 更新模板
    db = get_database_service()
    async with db.transaction():
        success = await db.notification_template.upsert(
            template_id, req.title, req.body, req.imageEnabled
        )
    
    if not success:
        raise HTTPException(status_code=500, detail="更新失败")
    
    return {"status": "success", "message": "模板已更新"}


@router.post("/preview")
async def preview_template(
    req: TemplatePreviewRequest,
) -> Dict[str, Any]:
    """预览模板渲染结果"""
    renderer = get_template_renderer()
    
    # 获取示例变量（状态字段已做新旧字段名归一化）
    sample_vars = _get_sample_variables(req.templateId, req.resolved_status)
    if req.templateId == TemplateID.TASK_PROGRESS:
        # 预览使用用户选择的百分比，与真实发送复用同一进度条工具。
        sample_vars["progress"] = req.exampleProgress
        sample_vars["progress_bar"] = build_progress_bar(req.exampleProgress)
    if not req.imageEnabled:
        sample_vars["image_url"] = ""
    
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
        "imageUrl": sample_vars.get("image_url", ""),
        "exampleData": sample_vars,
    }


def _get_template_variables(template_id: str) -> List[Dict[str, Any]]:
    """返回所有通知流程共享的变量合同，模板编辑器与预览共用这份定义。"""
    return [
        # 通用状态字段
        {"name": "status_icon", "label": "状态图标", "example": "✅", "category": "通用"},
        {"name": "status_name", "label": "状态名称", "example": "成功", "category": "通用"},
        {"name": "action_name", "label": "操作名称", "example": "刷新", "category": "通用"},
        {"name": "task_id", "label": "任务 ID", "example": "task-123", "category": "通用"},
        # 进度字段保持数值合同，正文可忽略默认进度条或使用沙箱循环自定义。
        {"name": "task_title", "label": "任务标题", "example": "刷新弹幕", "category": "进度"},
        {"name": "progress", "label": "进度百分比", "example": 55, "category": "进度"},
        {"name": "progress_bar", "label": "二十格进度条", "example": build_progress_bar(55), "category": "进度"},
        {"name": "description", "label": "进度说明", "example": "正在处理第 11/20 集", "category": "进度"},
        # 媒体主体字段
        {"name": "anime_title", "label": "作品标题", "example": "某动画", "category": "媒体"},
        {"name": "season", "label": "季度", "example": "1", "category": "媒体"},
        {"name": "episode", "label": "集数", "example": "1", "category": "媒体"},
        {"name": "episode_range", "label": "集数范围", "example": "1-3, 5", "category": "媒体"},
        {"name": "episode_count", "label": "分集数量", "example": "12", "category": "媒体"},
        {"name": "media_type", "label": "媒体类型", "example": "电视剧", "category": "媒体"},
        {"name": "year", "label": "年份", "example": "2026", "category": "媒体"},
        {"name": "media_id", "label": "媒体 ID", "example": "media-123", "category": "媒体"},
        {"name": "tmdb_id", "label": "TMDB ID", "example": "12345", "category": "媒体"},
        # 来源与刷新字段
        {"name": "provider", "label": "来源", "example": "bilibili", "category": "来源"},
        {"name": "source", "label": "来源标识", "example": "bilibili", "category": "来源"},
        {"name": "trigger_name", "label": "触发方式", "example": "自动追更", "category": "刷新"},
        {"name": "search_term", "label": "搜索词", "example": "某动画", "category": "刷新"},
        {"name": "search_type", "label": "搜索类型", "example": "标题", "category": "刷新"},
        # 结果与诊断字段
        {"name": "comment_count", "label": "弹幕数", "example": "1000", "category": "结果"},
        {"name": "added_count", "label": "新增数", "example": "50", "category": "结果"},
        {"name": "success_count", "label": "成功数", "example": "12", "category": "结果"},
        {"name": "failed_count", "label": "失败数", "example": "1", "category": "结果"},
        {"name": "message", "label": "说明", "example": "任务已完成", "category": "结果"},
        {"name": "duration", "label": "耗时", "example": "5", "category": "结果"},
        {"name": "error", "label": "错误信息", "example": "网络超时", "category": "结果"},
        # 系统与扩展上下文
        {"name": "finished_at", "label": "完成时间", "example": "2026-10-02 21:00:00", "category": "系统"},
        {"name": "webhook_source", "label": "Webhook 来源", "example": "Emby", "category": "系统"},
        {"name": "image_url", "label": "图片地址", "example": "海报图片 URL", "category": "媒体"},
    ]


def _get_sample_variables(template_id: str, status: str) -> Dict[str, Any]:
    """返回覆盖所有模板流程的预览上下文，未使用字段使用空值。"""
    base_vars = empty_template_variables()
    base_vars.update({
        "status_icon": "✅" if status == "success" else ("ℹ️" if status == "no_change" else "❌"),
        "status_name": {"success": "成功", "failed": "失败", "no_change": "无变化"}.get(status, "成功"),
        "action_name": "刷新",
        "task_id": "task-preview-123",
        "anime_title": "某部动画作品",
        "season": 1,
        "episode": 1,
        "episode_range": "1-3, 5",
        "episode_count": 12,
        "media_type": "电视剧",
        "year": 2026,
        "media_id": "media-preview-123",
        "tmdb_id": "12345",
        "provider": "bilibili",
        "source": "bilibili",
        "trigger_name": "自动追更",
        "search_term": "某部动画作品",
        "search_type": "标题",
        "comment_count": 1234,
        "added_count": 56,
        "success_count": 12,
        "failed_count": 1,
        "message": "任务已完成",
        "duration": 5,
        "error": "网络连接超时" if status == "failed" else "",
        "finished_at": "2026-10-02 21:00:00",
        "webhook_source": "",
        "image_url": _PREVIEW_IMAGE_URL,
    })
    if template_id == TemplateID.TASK_PROGRESS:
        # 进度没有媒体海报；即使历史请求保留图片默认参数也只预览文本。
        base_vars.update({
            "status_icon": "⏳", "status_name": "进行中", "action_name": "任务",
            "task_title": "刷新弹幕", "progress": 55,
            "progress_bar": build_progress_bar(55),
            "description": "正在处理第 11/20 集", "image_url": "",
        })
    return base_vars


def _adapt_for_channel(title: str, body: str, channel: str) -> tuple:
    """模拟渠道适配（简化版本）"""
    # 实际应调用各渠道的适配逻辑
    if channel == "qq":
        # QQ 可能需要降级 Markdown
        return (title, body)
    return (title, body)
