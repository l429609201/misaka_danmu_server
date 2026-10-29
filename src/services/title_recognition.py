"""标题规则基础服务：持久化读写及提交后切换规则缓存。"""
import asyncio
import logging
from copy import deepcopy
from typing import List, Optional, Tuple

from src.services.database_service import DatabaseService
from src.services.service_container import get_database_service
from src.utils.title_recognition_engine import TitleRecognitionEngine, TitleRecognitionRule

logger = logging.getLogger(__name__)


class TitleRecognitionService:
    """持有标题规则缓存，不承担规则应用业务。"""

    def __init__(self, database_service: Optional[DatabaseService] = None) -> None:
        """注入统一数据库服务，锁定并发加载与更新的缓存发布顺序。"""
        self._database_service = database_service
        self._engine = TitleRecognitionEngine()
        self._rules: Tuple[TitleRecognitionRule, ...] = ()
        self._rules_loaded = False
        self._lock = asyncio.Lock()

    def _get_database(self) -> DatabaseService:
        """取得统一数据库入口，不接收原始 Session 工厂。"""
        return self._database_service or get_database_service()

    async def get_content(self) -> str:
        """读取持久化配置原文，未配置时返回空文本。"""
        db = self._get_database()
        async with db.transaction():
            return await db.title_recognition.get_content() or ""

    async def get_configured_content(self) -> Optional[str]:
        """读取配置并保留未配置与主动保存空文本的区别。"""
        db = self._get_database()
        async with db.transaction():
            recognition = await db.title_recognition.get_current()
            return recognition.content if recognition is not None else None

    async def get_rules_snapshot(self) -> Tuple[TitleRecognitionRule, ...]:
        """返回独立规则快照，读取失败保留重试能力，不泄露可变缓存。"""
        async with self._lock:
            if not self._rules_loaded:
                try:
                    content = await self.get_content()
                    rules, warnings = self._engine._parse_recognition_content(content)
                    self._rules = tuple(rules)
                    self._rules_loaded = True
                    for warning in warnings:
                        logger.warning("识别词规则警告: %s", warning)
                except Exception as exc:
                    logger.error("从数据库加载识别词规则失败: %s", exc)
            return deepcopy(self._rules)

    async def update_recognition_rules(self, content: str) -> List[str]:
        """全量持久化配置，事务提交成功后才发布新缓存。"""
        rules, warnings = self._engine._parse_recognition_content(content)
        async with self._lock:
            db = self._get_database()
            async with db.transaction():
                await db.title_recognition.replace_content(content)
            # 提交失败时不修改已有缓存，避免规则与持久化内容不一致。
            self._rules = tuple(rules)
            self._rules_loaded = True
        logger.info("成功更新识别词规则，共 %s 条规则", len(rules))
        return warnings
