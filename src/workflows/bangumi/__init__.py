"""
番剧相关业务流程

包含：
- 番剧详情获取流程
- Episode ID 生成
- Source Order 预测
"""

from .helpers import generate_episode_id, get_or_predict_source_order
from .details_flow import get_bangumi_details_flow

__all__ = [
    'generate_episode_id',
    'get_or_predict_source_order',
    'get_bangumi_details_flow',
]
