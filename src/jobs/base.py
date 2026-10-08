from abc import ABC, abstractmethod
from typing import Callable, Dict, Any, List
import logging
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.rate_limiter import RateLimiter
from src.services.config_service import ConfigService
from src.services.metadata_service import MetadataService
from src.services.scraper_manager import ScraperManager
from src.services.task_manager import TaskManager

class BaseJob(ABC):
    """
    所有定时任务的抽象基类。

    config_schema 定义该任务类型的可配置项列表，前端会根据此 schema 动态渲染表单。
    每个配置项是一个字典，支持以下字段:

    必填:
    - key (str): 配置项唯一标识，存入 taskConfig JSON 的 key
    - label (str): 显示名称
    - type (str): 组件类型，支持以下值:
        - "string":   普通文本输入 (Input)
        - "password":  密码输入，带遮罩 (Input.Password)
        - "number":   数字输入 (InputNumber)
        - "boolean":  开关 (Switch)
        - "textarea": 多行文本 (Input.TextArea)
        - "select":   下拉选择 (Select)

    可选:
    - description (str): 配置项说明文字，显示在标签下方
    - default (Any): 默认值。boolean 类型建议用 True/False，number 用数字
    - placeholder (str): 输入框占位符文字
    - min (number): number 类型的最小值限制
    - max (number): number 类型的最大值限制
    - suffix (str): number 类型的后缀文字，如 "天"、"秒"
    - rows (int): textarea 类型的行数，默认 3
    - options (list): select 类型的选项列表
        格式: [{"value": "xxx", "label": "显示文字"}] 或 ["选项1", "选项2"]
    """
    # 每个子类都必须覆盖这些类属性
    job_type: str = "" # 任务的唯一标识符, e.g., "incremental_refresh"
    job_name: str = "" # 任务的默认显示名称, e.g., "TMDB自动映射与更新"
    description: str = "" # 任务的详细描述，用于前端显示
    is_system_task: bool = False  # 标识是否为系统内置任务
    config_schema: List[Dict[str, Any]] = []  # 任务可配置项的 schema 定义

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        task_manager: "TaskManager",
        scraper_manager: "ScraperManager",
        rate_limiter: "RateLimiter",
        metadata_manager: "MetadataService",
        config_service: "ConfigService",
        title_recognition_manager=None,
        ai_service=None,
    ):
        self._session_factory = session_factory
        self.task_manager = task_manager
        self.scraper_manager = scraper_manager
        self.rate_limiter = rate_limiter
        self.metadata_manager = metadata_manager
        self.config_service = config_service
        self.title_recognition_manager = title_recognition_manager
        self.ai_service = ai_service
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    async def run(self, session: AsyncSession, progress_callback: Callable):
        """
        执行任务的核心逻辑。
        progress_callback: 一个回调函数，用于报告进度 (progress: int, description: str)。
        """
        raise NotImplementedError
