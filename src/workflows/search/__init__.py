"""搜索领域能力层。

本包提供搜索相关的纯业务能力，供 tasks/ 编排层、API 层、AI 工具层、通知层调用。

分层约定：
    本包内所有模块**不得**导入 services/task_manager.py，也不得提交任务。
    任务提交统一由 tasks/search_tasks.py（交互类）与 tasks/auto_import.py（自动类）承担，
    与下载侧「能力在 import_core.py、提交在 import_dispatch.py」保持对称。
"""

from .ai_matcher_helper import select_best_with_ai
from .coordinator import SearchCoordinator
from .engine import unified_search
from .filtering import correct_movie_type_by_title, filter_by_season
from .library_sources import collect_existing_source_keys, collect_favorited_source_keys
from .scoring import compute_score, load_provider_order
from .validator import validate_candidates_with_fallback
# TODO: 修复 fallback_search 的导入错误
# from .fallback_search import (
#     handle_fallback_search,
#     execute_fallback_search_task,
#     search_implementation,
# )
from .episodes_flow import search_episodes_flow
from .anime_flow import search_anime_flow
from .dandan_helpers import format_db_results
from .provider_search_flow import (
    ProviderSearchOutcome,
    RecognitionMapping,
    execute_provider_search,
    read_search_cache,
    resolve_search_context,
    write_search_cache,
)

__all__ = [
    "SearchCoordinator",
    "collect_existing_source_keys",
    "collect_favorited_source_keys",
    "compute_score",
    "load_provider_order",
    "correct_movie_type_by_title",
    "filter_by_season",
    "select_best_with_ai",
    "unified_search",
    "validate_candidates_with_fallback",
    "handle_fallback_search",
    "execute_fallback_search_task",
    "search_implementation",
    "search_episodes_flow",
    "search_anime_flow",
    "format_db_results",
    # 外部源搜索编排（WebUI 首页搜索 / 控制 API 搜索共用）
    "ProviderSearchOutcome",
    "RecognitionMapping",
    "execute_provider_search",
    "resolve_search_context",
    "read_search_cache",
    "write_search_cache",
]

# 入口编排只组合已有能力，统一提供主页和控制搜索入口。
from .entry_flow import search_home, search_control, SearchBusyError, SearchCacheUnavailableError

__all__ += ["search_home", "search_control", "SearchBusyError", "SearchCacheUnavailableError"]
