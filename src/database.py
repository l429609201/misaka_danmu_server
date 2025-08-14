import aiomysql
import secrets
import string
import logging
from fastapi import FastAPI, Request
from .config import settings
from pymysql.err import OperationalError

# 使用模块级日志记录器
logger = logging.getLogger(__name__)


async def create_db_pool(app: FastAPI) -> aiomysql.Pool:
    """创建数据库连接池并存储在 app.state 中"""
    try:
        app.state.db_pool = await aiomysql.create_pool(
            host=settings.database.host,
            port=settings.database.port,
            user=settings.database.user,
            password=settings.database.password,
            db=settings.database.name,
            autocommit=True  # 建议在Web应用中开启自动提交
        )
        logger.info("数据库连接池创建成功。")
        return app.state.db_pool
    except OperationalError as e:
        # 捕获特定的 OperationalError 以提供更具指导性的错误消息
        logger.error("="*60)
        logger.error("=== 无法创建数据库连接池，应用无法启动。 ===")
        logger.error(f"=== 错误类型: {type(e).__name__}")
        logger.error(f"=== 错误详情: {e}")
        logger.error("---")
        logger.error("--- 可能的原因与排查建议: ---")
        logger.error("--- 1. 数据库服务未运行: 请确认您的 MySQL/MariaDB 服务正在运行。")
        logger.error(f"--- 2. 配置错误: 请检查您的配置文件或环境变量中的数据库连接信息是否正确。")
        logger.error(f"---    - 主机 (Host): {settings.database.host}")
        logger.error(f"---    - 端口 (Port): {settings.database.port}")
        logger.error(f"---    - 用户 (User): {settings.database.user}")
        logger.error(f"---    - 数据库 (DB Name): {settings.database.name}")
        logger.error("--- 3. 网络问题: 如果应用和数据库在不同的容器或机器上，请检查它们之间的网络连接和防火墙设置。")
        logger.error("--- 4. 权限问题: 确认提供的用户有权限从应用所在的IP地址连接。")
        logger.error("="*60)
        # 重新抛出异常以终止应用启动，因为没有数据库连接应用无法运行
        raise

async def get_db_pool(request: Request) -> aiomysql.Pool:
    """依赖项：从应用状态获取数据库连接池"""
    return request.app.state.db_pool

async def close_db_pool(app: FastAPI):
    """关闭数据库连接池"""
    if hasattr(app.state, "db_pool") and app.state.db_pool:
        app.state.db_pool.close()
        await app.state.db_pool.wait_closed()
        logger.info("数据库连接池已关闭。")

async def create_initial_admin_user(app: FastAPI):
    """在应用启动时创建初始管理员用户（如果已配置且不存在）"""
    # 将导入移到函数内部以避免循环导入
    from . import crud
    from . import models

    admin_user = settings.admin.initial_user
    if not admin_user:
        return

    pool = app.state.db_pool
    existing_user = await crud.get_user_by_username(pool, admin_user)

    if existing_user:
        logger.info(f"管理员用户 '{admin_user}' 已存在，跳过创建。")
        return

    # 用户不存在，开始创建
    admin_pass = settings.admin.initial_password
    if not admin_pass:
        # 生成一个安全的16位随机密码
        alphabet = string.ascii_letters + string.digits
        admin_pass = ''.join(secrets.choice(alphabet) for _ in range(16))
        logger.info("未提供初始管理员密码，已生成随机密码。")

    user_to_create = models.UserCreate(username=admin_user, password=admin_pass)
    await crud.create_user(pool, user_to_create)

    # 打印凭据信息。
    # 注意：，
    # 以确保敏感的初始密码只输出到控制台，而不会被写入到持久化的日志文件中，从而提高安全性。     
    logger.info("\n" + "="*60)
    logger.info(f"=== 初始管理员账户已创建 (用户: {admin_user}) ".ljust(56) + "===")
    logger.info(f"=== 请使用以下随机生成的密码登录: {admin_pass} ".ljust(56) + "===")
    logger.info("="*60 + "\n")
    print("\n" + "="*60)
    print(f"=== 初始管理员账户已创建 (用户: {admin_user}) ".ljust(56) + "===")
    print(f"=== 请使用以下随机生成的密码登录: {admin_pass} ".ljust(56) + "===")
    print("="*60 + "\n")

async def init_db_tables(app: FastAPI):
    """初始化数据库和表"""
    db_name = settings.database.name
    # 1. 先尝试连接MySQL实例，但不指定数据库
    try:
        conn = await aiomysql.connect(
            host=settings.database.host, port=settings.database.port,
            user=settings.database.user, password=settings.database.password
        )
    except OperationalError as e:
        logger.error(f"数据库连接失败，请检查配置: {e}")
        raise RuntimeError(f"无法连接到数据库: {e}") from e

    async with conn.cursor() as cursor:
        # 2. 创建数据库 (如果不存在)
        await cursor.execute("SHOW DATABASES LIKE %s", (db_name,))
        if not await cursor.fetchone():
            logger.info(f"数据库 '{db_name}' 不存在，正在创建...")
            await cursor.execute(f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            logger.info(f"数据库 '{db_name}' 创建成功。")
    conn.close()

    # 3. 检查并创建/更新表
    async with app.state.db_pool.acquire() as conn:
        async with conn.cursor() as cursor:
            # --- 步骤 3.1: 检查并创建所有表 ---
            logger.info("正在检查并创建数据表...")
            
            # 将所有建表语句放入一个字典中
            tables_to_create = {
                "anime": """CREATE TABLE `anime` (`id` BIGINT NOT NULL AUTO_INCREMENT, `title` VARCHAR(255) NOT NULL, `type` ENUM('tv_series', 'movie', 'ova', 'other') NOT NULL DEFAULT 'tv_series', `image_url` VARCHAR(512) NULL, `season` INT NOT NULL DEFAULT 1, `episode_count` INT NULL DEFAULT NULL, `source_url` VARCHAR(512) NULL, `created_at` TIMESTAMP NULL, PRIMARY KEY (`id`), FULLTEXT INDEX `idx_title_fulltext` (`title`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",
                "episode": """CREATE TABLE `episode` (`id` BIGINT NOT NULL AUTO_INCREMENT, `source_id` BIGINT NOT NULL, `title` VARCHAR(255) NOT NULL, `episode_index` INT NOT NULL, `provider_episode_id` VARCHAR(255) NULL, `source_url` VARCHAR(512) NULL, `fetched_at` TIMESTAMP NULL, `comment_count` INT NOT NULL DEFAULT 0, PRIMARY KEY (`id`), UNIQUE INDEX `idx_source_episode_unique` (`source_id` ASC, `episode_index` ASC)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",
                "comment": """CREATE TABLE `comment` (`id` BIGINT NOT NULL AUTO_INCREMENT, `cid` VARCHAR(255) NOT NULL, `episode_id` BIGINT NOT NULL, `p` VARCHAR(255) NOT NULL, `m` TEXT NOT NULL, `t` DECIMAL(10, 2) NOT NULL, PRIMARY KEY (`id`), UNIQUE INDEX `idx_episode_cid_unique` (`episode_id` ASC, `cid` ASC), INDEX `idx_episode_time` (`episode_id` ASC, `t` ASC)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",
                "users": """CREATE TABLE `users` (`id` BIGINT NOT NULL AUTO_INCREMENT, `username` VARCHAR(50) NOT NULL, `hashed_password` VARCHAR(255) NOT NULL, `token` TEXT NULL, `token_update` TIMESTAMP NULL, `created_at` TIMESTAMP NULL, PRIMARY KEY (`id`), UNIQUE INDEX `idx_username_unique` (`username` ASC)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",
                "scrapers": """CREATE TABLE `scrapers` (`provider_name` VARCHAR(50) NOT NULL, `is_enabled` BOOLEAN NOT NULL DEFAULT TRUE, `display_order` INT NOT NULL DEFAULT 0, PRIMARY KEY (`provider_name`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",
                "anime_sources": """CREATE TABLE `anime_sources` (`id` BIGINT NOT NULL AUTO_INCREMENT, `anime_id` BIGINT NOT NULL, `provider_name` VARCHAR(50) NOT NULL, `media_id` VARCHAR(255) NOT NULL, `is_favorited` BOOLEAN NOT NULL DEFAULT FALSE, `created_at` TIMESTAMP NULL, PRIMARY KEY (`id`), UNIQUE INDEX `idx_anime_provider_media_unique` (`anime_id` ASC, `provider_name` ASC, `media_id` ASC)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",
                "anime_metadata": """CREATE TABLE `anime_metadata` (`id` BIGINT NOT NULL AUTO_INCREMENT, `anime_id` BIGINT NOT NULL, `tmdb_id` VARCHAR(50) NULL, `tmdb_episode_group_id` VARCHAR(50) NULL, `imdb_id` VARCHAR(50) NULL, `tvdb_id` VARCHAR(50) NULL, `douban_id` VARCHAR(50) NULL, `bangumi_id` VARCHAR(50) NULL, PRIMARY KEY (`id`), UNIQUE INDEX `idx_anime_id_unique` (`anime_id` ASC), CONSTRAINT `fk_metadata_anime` FOREIGN KEY (`anime_id`) REFERENCES `anime`(`id`) ON DELETE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",
                "config": """CREATE TABLE `config` (`config_key` VARCHAR(100) NOT NULL, `config_value` TEXT NOT NULL, `description` TEXT NULL, PRIMARY KEY (`config_key`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",
                "cache_data": """CREATE TABLE `cache_data` (`cache_provider` VARCHAR(50) NULL, `cache_key` VARCHAR(255) NOT NULL, `cache_value` LONGTEXT NOT NULL, `expires_at` TIMESTAMP NOT NULL, PRIMARY KEY (`cache_key`), INDEX `idx_expires_at` (`expires_at`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",
                "api_tokens": """CREATE TABLE `api_tokens` (`id` INT NOT NULL AUTO_INCREMENT, `name` VARCHAR(100) NOT NULL, `token` VARCHAR(50) NOT NULL, `is_enabled` BOOLEAN NOT NULL DEFAULT TRUE, `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, `expires_at` TIMESTAMP NULL DEFAULT NULL, PRIMARY KEY (`id`), UNIQUE INDEX `idx_token_unique` (`token` ASC)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",
                "token_access_logs": """CREATE TABLE `token_access_logs` (`id` BIGINT NOT NULL AUTO_INCREMENT, `token_id` INT NOT NULL, `ip_address` VARCHAR(45) NOT NULL, `user_agent` TEXT NULL, `access_time` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, `status` VARCHAR(50) NOT NULL, `path` VARCHAR(512) NULL, PRIMARY KEY (`id`), INDEX `idx_token_id_time` (`token_id` ASC, `access_time` DESC)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",
                "ua_rules": """CREATE TABLE `ua_rules` (`id` INT NOT NULL AUTO_INCREMENT, `ua_string` VARCHAR(255) NOT NULL, `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (`id`), UNIQUE INDEX `idx_ua_string_unique` (`ua_string` ASC)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",
                "bangumi_auth": """CREATE TABLE `bangumi_auth` (`user_id` BIGINT NOT NULL, `bangumi_user_id` INT NULL, `nickname` VARCHAR(255) NULL, `avatar_url` VARCHAR(512) NULL, `access_token` TEXT NOT NULL, `refresh_token` TEXT NULL, `expires_at` TIMESTAMP NULL, `authorized_at` TIMESTAMP NULL, PRIMARY KEY (`user_id`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",
                "oauth_states": """CREATE TABLE `oauth_states` (`state_key` VARCHAR(100) NOT NULL, `user_id` BIGINT NOT NULL, `expires_at` TIMESTAMP NOT NULL, PRIMARY KEY (`state_key`), INDEX `idx_oauth_expires_at` (`expires_at`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",
                "anime_aliases": """CREATE TABLE `anime_aliases` (`id` BIGINT NOT NULL AUTO_INCREMENT, `anime_id` BIGINT NOT NULL, `name_en` VARCHAR(255) NULL, `name_jp` VARCHAR(255) NULL, `name_romaji` VARCHAR(255) NULL, `alias_cn_1` VARCHAR(255) NULL, `alias_cn_2` VARCHAR(255) NULL, `alias_cn_3` VARCHAR(255) NULL, PRIMARY KEY (`id`), UNIQUE INDEX `idx_anime_id_unique` (`anime_id` ASC), CONSTRAINT `fk_aliases_anime` FOREIGN KEY (`anime_id`) REFERENCES `anime`(`id`) ON DELETE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",
                "tmdb_episode_mapping": """CREATE TABLE `tmdb_episode_mapping` (`id` BIGINT NOT NULL AUTO_INCREMENT, `tmdb_tv_id` INT NOT NULL, `tmdb_episode_group_id` VARCHAR(50) NOT NULL, `tmdb_episode_id` INT NOT NULL, `tmdb_season_number` INT NOT NULL, `tmdb_episode_number` INT NOT NULL, `custom_season_number` INT NOT NULL, `custom_episode_number` INT NOT NULL, `absolute_episode_number` INT NOT NULL, PRIMARY KEY (`id`), UNIQUE KEY `idx_group_episode_unique` (`tmdb_episode_group_id`, `tmdb_episode_id`), INDEX `idx_custom_season_episode` (`tmdb_tv_id`, `tmdb_episode_group_id`, `custom_season_number`, `custom_episode_number`), INDEX `idx_absolute_episode` (`tmdb_tv_id`, `tmdb_episode_group_id`, `absolute_episode_number`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",
                "scheduled_tasks": """CREATE TABLE `scheduled_tasks` (`id` VARCHAR(100) NOT NULL, `name` VARCHAR(255) NOT NULL, `job_type` VARCHAR(50) NOT NULL, `cron_expression` VARCHAR(100) NOT NULL, `is_enabled` BOOLEAN NOT NULL DEFAULT TRUE, `last_run_at` TIMESTAMP NULL, `next_run_at` TIMESTAMP NULL, PRIMARY KEY (`id`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",
                "task_history": """CREATE TABLE `task_history` (`id` VARCHAR(100) NOT NULL, `title` VARCHAR(255) NOT NULL, `status` VARCHAR(50) NOT NULL, `progress` INT NOT NULL DEFAULT 0, `description` TEXT NULL, `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, `finished_at` TIMESTAMP NULL, PRIMARY KEY (`id`), INDEX `idx_created_at` (`created_at` DESC)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",
                "metadata_sources": """CREATE TABLE `metadata_sources` (`provider_name` VARCHAR(50) NOT NULL, `is_enabled` BOOLEAN NOT NULL DEFAULT TRUE, `is_aux_search_enabled` BOOLEAN NOT NULL DEFAULT TRUE, `display_order` INT NOT NULL DEFAULT 0, PRIMARY KEY (`provider_name`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",
            }

            # 先获取数据库中所有已存在的表
            await cursor.execute("SELECT table_name FROM information_schema.TABLES WHERE table_schema = %s", (db_name,))
            existing_tables = {row[0] for row in await cursor.fetchall()}

            # 遍历需要创建的表
            for table_name, create_sql in tables_to_create.items():
                if table_name in existing_tables:
                    logger.debug(f"数据表 '{table_name}' 已存在，跳过创建。")
                else:
                    logger.info(f"正在创建数据表 '{table_name}'...")
                    # 在建表语句中保留 IF NOT EXISTS 作为最后的保险
                    await cursor.execute(create_sql.replace(f"CREATE TABLE `{table_name}`", f"CREATE TABLE IF NOT EXISTS `{table_name}`"))
                    logger.info(f"数据表 '{table_name}' 创建成功。")
            
            logger.info("数据表检查完成。")

            # --- 步骤 3.2: 检查并修正旧的表结构 (静默迁移) ---
            logger.info("正在检查并修正表结构...")
            try:
                # 检查 token_access_logs.status
                await cursor.execute("""
                    SELECT CHARACTER_MAXIMUM_LENGTH FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'token_access_logs' AND COLUMN_NAME = 'status'
                """, (db_name,))
                status_col_len_row = await cursor.fetchone()
                if status_col_len_row and status_col_len_row[0] < 50:
                    logger.info("检测到旧的 'token_access_logs.status' 列定义，正在将其更新为 VARCHAR(50)...")
                    await cursor.execute("ALTER TABLE token_access_logs MODIFY COLUMN status VARCHAR(50) NOT NULL;")
                    logger.info("列 'token_access_logs.status' 更新成功。")

                # 检查 task_history.status
                await cursor.execute("""
                    SELECT CHARACTER_MAXIMUM_LENGTH FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'task_history' AND COLUMN_NAME = 'status'
                """, (db_name,))
                task_status_col_len_row = await cursor.fetchone()
                if task_status_col_len_row and task_status_col_len_row[0] < 50:
                    logger.info("检测到旧的 'task_history.status' 列定义，正在将其更新为 VARCHAR(50)...")
                    await cursor.execute("ALTER TABLE task_history MODIFY COLUMN status VARCHAR(50) NOT NULL;")
                    logger.info("列 'task_history.status' 更新成功。")

                # 检查 config.config_value 的类型
                await cursor.execute("""
                    SELECT DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'config' AND COLUMN_NAME = 'config_value'
                """, (db_name,))
                config_value_col_type_row = await cursor.fetchone()
                if config_value_col_type_row and config_value_col_type_row[0].lower() not in ['text', 'longtext']:
                    logger.info("检测到旧的 'config.config_value' 列定义，正在将其更新为 TEXT...")
                    await cursor.execute("ALTER TABLE config MODIFY COLUMN config_value TEXT NOT NULL;")
                    logger.info("列 'config.config_value' 更新成功。")
            except Exception as e:
                # 仅记录错误，不中断启动流程
                logger.warning(f"检查或更新表结构时发生非致命错误: {e}")

            # --- 步骤 3.3: 初始化默认配置 ---
            # 初始化默认配置的逻辑已移至 ConfigManager
            pass