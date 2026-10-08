"""
弹弹Play 兼容 API 的常量定义

从 api/dandan/constants.py 迁移到 utils/dandan/constants.py
原因：这些常量被多个模块共享，不应该独占在 API 层
"""

# --- Module-level Constants for Type Mappings and Parsing ---
# To avoid repetition and improve maintainability.
DANDAN_TYPE_MAPPING = {
    "tv_series": "tvseries", "movie": "movie", "ova": "ova", "other": "other"
}
DANDAN_TYPE_DESC_MAPPING = {
    "tv_series": "TV动画", "movie": "电影/剧场版", "ova": "OVA", "other": "其他"
}

# 后备搜索状态管理
FALLBACK_SEARCH_BANGUMI_ID = 999999999  # 搜索中的固定bangumiId
SAMPLED_CACHE_TTL = 86400  # 缓存1天 (24小时) - 保留用于兼容性

# episodeId到源映射的缓存键前缀
EPISODE_MAPPING_CACHE_PREFIX = "episode_mapping_"

# 缓存键前缀定义
FALLBACK_SEARCH_CACHE_PREFIX = "fallback_search_"
TOKEN_SEARCH_TASKS_PREFIX = "token_search_task_"
USER_LAST_BANGUMI_CHOICE_PREFIX = "user_last_bangumi_"
COMMENTS_FETCH_CACHE_PREFIX = "comments_fetch_"
SAMPLED_COMMENTS_CACHE_PREFIX = "sampled_comments_"

# 缓存TTL定义
FALLBACK_SEARCH_CACHE_TTL = 3600  # 后备搜索缓存1小时
TOKEN_SEARCH_TASKS_TTL = 3600  # Token搜索任务1小时
USER_LAST_BANGUMI_CHOICE_TTL = 86400  # 用户选择记录1天
COMMENTS_FETCH_CACHE_TTL = 300  # 弹幕获取缓存5分钟(临时缓存)
SAMPLED_COMMENTS_CACHE_TTL_DB = 86400  # 弹幕采样缓存1天

# 搜索类型常量
SEARCH_TYPE_FALLBACK_MATCH = "fallback_match"  # 后备匹配搜索类型


# 搜索会话使用冒号分隔，与旧后备弹幕缓存的下划线前缀不同；读写双方必须共用。
FALLBACK_SEARCH_SESSION_PREFIX = "fallback_search"
FALLBACK_SEARCH_SESSION_TTL = 1800
