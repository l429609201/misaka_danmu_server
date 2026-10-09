"""
TitleRecognitionRepository - 识别词配置数据访问层

why: 识别词配置（title_recognition 表）此前没有 Repository 承接，
     导致 TitleRecognitionManager 与 AI 助手工具层各自直接执行 SQL / 调用 crud。
     本模块统一收口该表的读写，供服务层调用。

注意：title_recognition 为「单记录全量存储」表，全表仅保留一条记录，
     故读取一律取 limit(1)，写入走「有则更新、无则插入」。
"""

import logging
from typing import Any, List, Optional

from sqlalchemy import delete, select

from ..orm_models import TitleRecognition
from .base import BaseRepository

logger = logging.getLogger(__name__)


class TitleRecognitionRepository(BaseRepository[TitleRecognition]):
    """识别词配置 Repository（单记录表）"""

    async def get_by_id(self, id: Any) -> Optional[TitleRecognition]:
        """根据主键获取识别词记录。"""
        return await self._session.get(TitleRecognition, id)

    async def get_all(self, **filters) -> List[TitleRecognition]:
        """获取全部识别词记录（正常情况下最多一条）。"""
        result = await self._session.execute(select(TitleRecognition))
        return list(result.scalars().all())

    async def get_current(self) -> Optional[TitleRecognition]:
        """
        获取当前生效的识别词记录。

        Returns:
            识别词 ORM 对象；表为空时返回 None
        """
        result = await self._session.execute(select(TitleRecognition).limit(1))
        return result.scalar_one_or_none()

    async def get_content(self) -> str:
        """
        获取识别词配置全文。

        Returns:
            识别词内容；未配置时返回空字符串
        """
        recognition = await self.get_current()
        return recognition.content if recognition else ""

    async def create(self, content: str) -> TitleRecognition:
        """创建识别词记录。"""
        recognition = TitleRecognition(content=content)
        self._session.add(recognition)
        await self._session.flush()
        return recognition

    async def update(self, id: Any, **data) -> Optional[TitleRecognition]:
        """按主键更新识别词记录。"""
        recognition = await self.get_by_id(id)
        if recognition is None:
            return None
        if "content" in data:
            recognition.content = data["content"]
        await self._session.flush()
        return recognition

    async def upsert_content(self, content: str) -> TitleRecognition:
        """
        写入识别词全文：已有记录则更新，否则插入。

        Args:
            content: 识别词配置全文

        Returns:
            写入后的识别词 ORM 对象
        """
        recognition = await self.get_current()
        if recognition is None:
            return await self.create(content)
        recognition.content = content
        await self._session.flush()
        return recognition

    async def replace_content(self, content: str) -> TitleRecognition:
        """全量替换单记录配置，仅 flush，提交交给 DatabaseService。"""
        await self._session.execute(delete(TitleRecognition))
        return await self.create(content)

    async def delete(self, id: Any) -> bool:
        """按主键删除识别词记录。"""
        recognition = await self.get_by_id(id)
        if recognition is None:
            return False
        await self._session.delete(recognition)
        await self._session.flush()
        return True
