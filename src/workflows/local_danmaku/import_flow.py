"""
本地弹幕 - 导入 Workflow
处理本地弹幕导入到正式数据库的核心业务逻辑
"""
import logging
from typing import List, Dict, Any

from src.services.database_service import DatabaseService
from src.services.task_manager import TaskManager

logger = logging.getLogger(__name__)


async def import_local_items_flow(
    db: DatabaseService,
    task_manager: TaskManager,
    item_ids: List[int],
    import_options: Dict[str, Any]
) -> Dict[str, Any]:
    """
    将本地弹幕项导入到正式数据库
    
    这是核心导入逻辑，会创建后台任务来处理：
    1. 读取本地弹幕项的元数据
    2. 匹配或创建正式的Anime/Episode记录
    3. 解析XML弹幕文件
    4. 导入弹幕到正式数据库
    5. 标记本地项为"已导入"
    
    Args:
        db: 数据库服务
        task_manager: 任务管理器
        item_ids: 要导入的本地弹幕项ID列表
        import_options: 导入选项（如是否覆盖、匹配策略等）
        
    Returns:
        任务提交结果
        
    Raises:
        ValueError: 参数无效
    """
    if not item_ids:
        raise ValueError("未提供要导入的项")
    
    # 先校验真实仓储接口，短事务结束后才提交后台任务。
    item_ids = list(dict.fromkeys(item_ids))
    async with db.transaction():
        for item_id in item_ids:
            if await db.local_danmaku.get_by_id(item_id) is None:
                raise ValueError(f"本地弹幕项不存在: {item_id}")
    parameters = {"item_ids": item_ids, "import_options": import_options}
    task_id, _ = await task_manager.submit_task(
        task_manager.build_task_coro_factory("local_danmaku_import", **parameters),
        f"导入本地弹幕 ({len(item_ids)} 项)",
        queue_type="management", task_type="local_danmaku_import",
        task_parameters=parameters,
    )
    
    logger.info(f"提交了本地弹幕导入任务: {task_id}, 项数: {len(item_ids)}")
    
    return {
        "message": f"已提交导入任务，共 {len(item_ids)} 项",
        "taskId": task_id,
        "itemCount": len(item_ids)
    }
