"""
Repository 基类

定义所有 Repository 的统一接口规范。
所有 Repository 返回 ORM 对象，数据转换由 Service 层负责。
"""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar, Optional, List, Any
from sqlalchemy.ext.asyncio import AsyncSession

# 泛型类型变量，代表 ORM 模型类型
T = TypeVar('T')


class BaseRepository(ABC, Generic[T]):
    """
    Repository 基类
    
    职责：
    - 封装数据库访问逻辑
    - 返回 ORM 对象
    - 不处理业务逻辑
    - 不处理数据转换
    
    Args:
        session: SQLAlchemy 异步会话
    """
    
    def __init__(self, session: AsyncSession):
        """
        初始化 Repository
        
        Args:
            session: 数据库会话（由调用方管理事务）
        """
        self._session = session
    
    @abstractmethod
    async def get_by_id(self, id: Any) -> Optional[T]:
        """
        根据 ID 获取实体
        
        Args:
            id: 实体 ID
            
        Returns:
            ORM 对象，不存在时返回 None
        """
        pass
    
    @abstractmethod
    async def get_all(self, **filters) -> List[T]:
        """
        获取所有实体（可选过滤条件）
        
        Args:
            **filters: 过滤条件
            
        Returns:
            ORM 对象列表
        """
        pass
    
    @abstractmethod
    async def create(self, **data) -> T:
        """
        创建新实体
        
        Args:
            **data: 实体数据
            
        Returns:
            创建的 ORM 对象
        """
        pass
    
    @abstractmethod
    async def update(self, id: Any, **data) -> Optional[T]:
        """
        更新实体
        
        Args:
            id: 实体 ID
            **data: 更新数据
            
        Returns:
            更新后的 ORM 对象，不存在时返回 None
        """
        pass
    
    @abstractmethod
    async def delete(self, id: Any) -> bool:
        """
        删除实体
        
        Args:
            id: 实体 ID
            
        Returns:
            是否删除成功
        """
        pass
    
    async def exists(self, id: Any) -> bool:
        """
        检查实体是否存在
        
        Args:
            id: 实体 ID
            
        Returns:
            是否存在
        """
        entity = await self.get_by_id(id)
        return entity is not None
    
    async def count(self, **filters) -> int:
        """
        统计实体数量
        
        Args:
            **filters: 过滤条件
            
        Returns:
            实体数量
        """
        entities = await self.get_all(**filters)
        return len(entities)
