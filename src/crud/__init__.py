"""
CRUD模块
按功能模块组织的数据库操作
"""

# Config模块
from .config import (
    get_config_value,
    update_config_value,
    initialize_configs,
)

# User模块
from .user import (
    get_user_by_id,
    get_user_by_username,
    create_user,
    update_user_password,
    update_user_login_info,
    create_oauth_state,
    consume_oauth_state,
    get_bangumi_auth,
    save_bangumi_auth,
    delete_bangumi_auth,
)

# Task模块
from .task import (
    is_system_task,
    get_scheduled_tasks,
    get_scheduled_task,
    check_scheduled_task_exists_by_type,
    get_scheduled_task_id_by_type,
    create_scheduled_task,
    update_scheduled_task,
    delete_scheduled_task,
    update_scheduled_task_run_times,
    get_last_run_result_for_scheduled_task,
    create_task_in_history,
    update_task_progress_in_history,
    finalize_task_in_history,
    update_task_status,
    get_tasks_from_history,
    get_task_details_from_history,
    get_task_from_history_by_id,
    delete_task_from_history,
    force_delete_task_from_history,
    force_fail_task,
    get_execution_task_id_from_scheduler_task,
    mark_interrupted_tasks_as_failed,
    find_recent_task_by_unique_key,
    create_webhook_task,
    get_webhook_tasks,
    delete_webhook_tasks,
    get_due_webhook_tasks,
    update_webhook_task_status,
    save_task_state_cache,
    get_task_state_cache,
    clear_task_state_cache,
    get_all_running_task_states,
)

# MediaServer模块
from .media_server import (
    get_all_media_servers,
    get_media_server_by_id,
    create_media_server,
    update_media_server,
    delete_media_server,
    get_media_items,
    create_media_item,
    update_media_item,
    delete_media_item,
    delete_media_items_batch,
    mark_media_items_imported,
    clear_media_items_by_server,
)

# Scraper模块
from .scraper import (
    sync_scrapers_to_db,
    get_scraper_setting_by_name,
    get_all_scraper_settings,
    update_scraper_proxy,
    update_scrapers_settings,
    remove_stale_scrapers,
)

# MetadataSource模块
from .metadata_source import (
    sync_metadata_sources_to_db,
    get_all_metadata_source_settings,
    get_metadata_source_setting_by_name,
    update_metadata_sources_settings,
    update_metadata_source_specific_settings,
    get_enabled_aux_metadata_sources,
    get_enabled_failover_sources,
)

# Anime模块
from .anime import (
    get_library_anime,
    get_library_anime_by_id,
    get_or_create_anime,
    create_anime,
    update_anime_aliases,
    update_anime_details,
    delete_anime,
    search_anime,
    search_episodes_in_library,
    find_anime_by_title_season_year,
    find_anime_by_metadata_id_and_season,
    find_favorited_source_for_anime,
    search_animes_for_dandan,
    find_animes_for_matching,
    get_anime_full_details,
    get_anime_id_by_bangumi_id,
    get_anime_id_by_tmdb_id,
    get_anime_id_by_tvdb_id,
    get_anime_id_by_imdb_id,
    get_anime_id_by_douban_id,
    update_anime_tmdb_group_id,
    update_anime_aliases_if_empty,
    get_animes_with_tmdb_id,
    get_anime_details_for_dandan,
)

# Episode模块
from .episode import (
    get_last_episode_for_source,
    get_episode_for_refresh,
    find_episode_by_index,
    get_episode_indices_by_anime_title,
    find_episode_via_tmdb_mapping,
    get_related_episode_ids,
    find_episode,
    check_episode_exists,
    create_episode_if_not_exists,
    get_episode_provider_info,
    delete_episode,
    update_episode_info,
    update_episode_fetch_time,
    update_episode_danmaku_info,
    clear_episode_comments,
    get_existing_episodes_for_source,
    fetch_comments,
    add_comments_from_xml,
    check_duplicate_import,
)

# Source模块
from .source import (
    check_source_exists_by_media_id,
    get_anime_id_by_source_media_id,
    link_source_to_anime,
    update_source_media_id,
    get_anime_source_info,
    get_anime_sources,
    get_episodes_for_source,
    clear_source_data,
    delete_anime_source,
    toggle_source_favorite_status,
    toggle_source_incremental_refresh,
    increment_incremental_refresh_failures,
    reset_incremental_refresh_failures,
    disable_incremental_refresh,
    get_sources_with_incremental_refresh_enabled,
    _assign_source_order_if_missing,
)

# Danmaku模块
from .danmaku import (
    save_danmaku_for_episode,
    _generate_danmaku_path,
    _generate_xml_from_comments,
    _get_fs_path_from_web_path,
    update_metadata_if_empty,
)

# Reassociation模块
from .reassociation import (
    check_reassociation_conflicts,
    reassociate_anime_sources,
    reassociate_anime_sources_with_resolution,
)

# Cache模块
from .cache import (
    get_cache,
    set_cache,
    clear_expired_cache,
    clear_all_cache,
    delete_cache,
    get_cache_keys_by_pattern,
    clear_task_state_cache,
)

# ApiToken模块
from .api_token import (
    get_all_api_tokens,
    get_api_token_by_id,
    get_api_token_by_token_str,
    create_api_token,
    update_api_token,
    delete_api_token,
    toggle_api_token,
    reset_token_counter,
    validate_api_token,
    increment_token_call_count,
    reset_all_token_daily_counts,
)

# TokenLog模块
from .token_log import (
    create_token_access_log,
    get_token_access_logs,
    get_ua_rules,
    add_ua_rule,
    delete_ua_rule,
)

# TMDB模块
from .tmdb import (
    save_tmdb_episode_group_mappings,
)

# RateLimit模块
from .rate_limit import (
    get_or_create_rate_limit_state,
    get_all_rate_limit_states,
    reset_all_rate_limit_states,
    increment_rate_limit_count,
)

# ExternalLog模块
from .external_log import (
    create_external_api_log,
    get_external_api_logs,
)

# Utility模块
from .utility import (
    _is_docker_environment,
    _get_base_dir,
    DANMAKU_BASE_DIR,
    prune_logs,
    clear_expired_oauth_states,
    find_recent_task_by_unique_key,
    get_all_running_task_states,
    mark_interrupted_tasks_as_failed,
    get_due_webhook_tasks,
    delete_webhook_tasks,
    get_last_run_result_for_scheduled_task,
    get_execution_task_id_from_scheduler_task,
    force_delete_task_from_history,
    force_fail_task,
    get_task_from_history_by_id,
    delete_task_from_history,
    get_task_details_from_history,
    get_tasks_from_history,
    finalize_task_in_history,
    update_task_progress_in_history,
    update_scheduled_task_run_times,
    get_scheduled_task,
    get_scheduled_task_id_by_type,
    check_scheduled_task_exists_by_type,
)

__all__ = [
    # Config
    'get_config_value',
    'update_config_value',
    'initialize_configs',
    # User
    'get_user_by_id',
    'get_user_by_username',
    'create_user',
    'update_user_password',
    'update_user_login_info',
    'create_oauth_state',
    'consume_oauth_state',
    'get_bangumi_auth',
    'save_bangumi_auth',
    'delete_bangumi_auth',
    # Task
    'is_system_task',
    'get_scheduled_tasks',
    'get_scheduled_task',
    'check_scheduled_task_exists_by_type',
    'get_scheduled_task_id_by_type',
    'create_scheduled_task',
    'update_scheduled_task',
    'delete_scheduled_task',
    'update_scheduled_task_run_times',
    'get_last_run_result_for_scheduled_task',
    'create_task_in_history',
    'update_task_progress_in_history',
    'finalize_task_in_history',
    'update_task_status',
    'get_tasks_from_history',
    'get_task_details_from_history',
    'get_task_from_history_by_id',
    'delete_task_from_history',
    'force_delete_task_from_history',
    'force_fail_task',
    'get_execution_task_id_from_scheduler_task',
    'mark_interrupted_tasks_as_failed',
    'find_recent_task_by_unique_key',
    'create_webhook_task',
    'get_webhook_tasks',
    'delete_webhook_tasks',
    'get_due_webhook_tasks',
    'update_webhook_task_status',
    'save_task_state_cache',
    'get_task_state_cache',
    'clear_task_state_cache',
    'get_all_running_task_states',
    # MediaServer
    'get_all_media_servers',
    'get_media_server_by_id',
    'create_media_server',
    'update_media_server',
    'delete_media_server',
    'get_media_items',
    'create_media_item',
    'update_media_item',
    'delete_media_item',
    'delete_media_items_batch',
    'mark_media_items_imported',
    'clear_media_items_by_server',
    # Scraper
    'sync_scrapers_to_db',
    'get_scraper_setting_by_name',
    'get_all_scraper_settings',
    'update_scraper_proxy',
    'update_scrapers_settings',
    'remove_stale_scrapers',
    # MetadataSource
    'sync_metadata_sources_to_db',
    'get_all_metadata_source_settings',
    'get_metadata_source_setting_by_name',
    'update_metadata_sources_settings',
    'update_metadata_source_specific_settings',
    'get_enabled_aux_metadata_sources',
    'get_enabled_failover_sources',
    # Anime
    'get_library_anime',
    'get_library_anime_by_id',
    'get_or_create_anime',
    'create_anime',
    'update_anime_aliases',
    'update_anime_details',
    'delete_anime',
    'search_anime',
    'search_episodes_in_library',
    'find_anime_by_title_season_year',
    'find_anime_by_metadata_id_and_season',
    'find_favorited_source_for_anime',
    'search_animes_for_dandan',
    'find_animes_for_matching',
    'get_anime_full_details',
    'get_anime_id_by_bangumi_id',
    'get_anime_id_by_tmdb_id',
    'get_anime_id_by_tvdb_id',
    'get_anime_id_by_imdb_id',
    'get_anime_id_by_douban_id',
    'update_anime_tmdb_group_id',
    'update_anime_aliases_if_empty',
    'get_animes_with_tmdb_id',
    'get_anime_details_for_dandan',
    # Episode
    'get_last_episode_for_source',
    'get_episode_for_refresh',
    'find_episode_by_index',
    'get_episode_indices_by_anime_title',
    'find_episode_via_tmdb_mapping',
    'get_related_episode_ids',
    'find_episode',
    'check_episode_exists',
    'create_episode_if_not_exists',
    'get_episode_provider_info',
    'delete_episode',
    'update_episode_info',
    'update_episode_fetch_time',
    'update_episode_danmaku_info',
    'clear_episode_comments',
    'get_existing_episodes_for_source',
    'fetch_comments',
    'add_comments_from_xml',
    'check_duplicate_import',
    # Source
    'check_source_exists_by_media_id',
    'get_anime_id_by_source_media_id',
    'link_source_to_anime',
    'update_source_media_id',
    'get_anime_source_info',
    'get_anime_sources',
    'get_episodes_for_source',
    'clear_source_data',
    'delete_anime_source',
    'toggle_source_favorite_status',
    'toggle_source_incremental_refresh',
    'increment_incremental_refresh_failures',
    'reset_incremental_refresh_failures',
    'disable_incremental_refresh',
    'get_sources_with_incremental_refresh_enabled',
    '_assign_source_order_if_missing',
    # Danmaku
    'save_danmaku_for_episode',
    '_generate_danmaku_path',
    '_generate_xml_from_comments',
    '_get_fs_path_from_web_path',
    'update_metadata_if_empty',
    # Reassociation
    'check_reassociation_conflicts',
    'reassociate_anime_sources',
    'reassociate_anime_sources_with_resolution',
    # Cache
    'get_cache',
    'set_cache',
    'clear_expired_cache',
    'clear_all_cache',
    'delete_cache',
    'get_cache_keys_by_pattern',
    'clear_task_state_cache',
    # ApiToken
    'get_all_api_tokens',
    'get_api_token_by_id',
    'get_api_token_by_token_str',
    'create_api_token',
    'update_api_token',
    'delete_api_token',
    'toggle_api_token',
    'reset_token_counter',
    'validate_api_token',
    'increment_token_call_count',
    'reset_all_token_daily_counts',
    # TokenLog
    'create_token_access_log',
    'get_token_access_logs',
    'get_ua_rules',
    'add_ua_rule',
    'delete_ua_rule',
    # TMDB
    'save_tmdb_episode_group_mappings',
    # RateLimit
    'get_or_create_rate_limit_state',
    'get_all_rate_limit_states',
    'reset_all_rate_limit_states',
    'increment_rate_limit_count',
    # ExternalLog
    'create_external_api_log',
    'get_external_api_logs',
    # Utility
    '_is_docker_environment',
    '_get_base_dir',
    'prune_logs',
    'clear_expired_oauth_states',
    'find_recent_task_by_unique_key',
    'get_all_running_task_states',
    'mark_interrupted_tasks_as_failed',
    'get_due_webhook_tasks',
    'delete_webhook_tasks',
    'get_last_run_result_for_scheduled_task',
    'get_execution_task_id_from_scheduler_task',
    'force_delete_task_from_history',
    'force_fail_task',
    'get_task_from_history_by_id',
    'delete_task_from_history',
    'get_task_details_from_history',
    'get_tasks_from_history',
    'finalize_task_in_history',
    'update_task_progress_in_history',
    'update_scheduled_task_run_times',
    'get_scheduled_task',
    'get_scheduled_task_id_by_type',
    'check_scheduled_task_exists_by_type',
]

