"""
JSON序列化工具

从 api/dandan/helpers.py 提取
"""

import json
from typing import Any


def convert_to_serializable(obj: Any) -> Any:
    """
    递归转换对象为可JSON序列化的格式
    
    支持的对象类型：
    - Pydantic模型（model_dump 或 dict 方法）
    - 字典（递归转换所有键值）
    - 列表（递归转换所有元素）
    - 其他类型（原样返回）
    
    Args:
        obj: 需要转换的对象
        
    Returns:
        可JSON序列化的对象
        
    Examples:
        >>> from pydantic import BaseModel
        >>> class User(BaseModel):
        ...     name: str
        ...     age: int
        >>> user = User(name="Alice", age=30)
        >>> convert_to_serializable(user)
        {'name': 'Alice', 'age': 30}
        
        >>> data = {'user': user, 'items': [user]}
        >>> convert_to_serializable(data)
        {'user': {'name': 'Alice', 'age': 30}, 'items': [{'name': 'Alice', 'age': 30}]}
    """
    if hasattr(obj, 'model_dump'):
        return obj.model_dump()
    elif hasattr(obj, 'dict'):
        return obj.dict()
    elif isinstance(obj, dict):
        return {k: convert_to_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_to_serializable(item) for item in obj]
    else:
        return obj


def fix_bangumi_mapping(data: Any) -> Any:
    """修复 bangumi_mapping 缓存的双重 JSON 序列化历史数据。

    why: 历史版本将 bangumi_mapping 的 value（本身已是 dict）二次 json.dumps 成字符串
         存入缓存，导致读取时拿到的是字符串而非 dict。此补丁检测并自动修复。

    从 db/crud/fallback.py 的 _fix_bangumi_mapping 迁入，逻辑保持一致。
    本函数为纯数据变换，无外部依赖，可被 service / db 各层安全复用。

    Args:
        data: 待检查的缓存数据，非 dict 或无 bangumi_mapping 字段时原样返回

    Returns:
        修复后的数据（原地修改并返回同一对象）
    """
    if not isinstance(data, dict):
        return data
    mapping = data.get("bangumi_mapping")
    if not isinstance(mapping, dict):
        return data
    fixed = False
    for bid, mi in mapping.items():
        if isinstance(mi, str):
            try:
                mapping[bid] = json.loads(mi)
                fixed = True
            except (json.JSONDecodeError, TypeError):
                pass  # 无法修复的保持原样，交由调用方再容错
    if fixed:
        data["bangumi_mapping"] = mapping
    return data
