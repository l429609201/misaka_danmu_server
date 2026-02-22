"""
数据库迁移模块

此模块用于执行一次性的数据库迁移任务。
迁移任务通过标志位机制确保只执行一次。

注意：简单的添加字段和类型扩展现在由 db_maintainer.py 自动处理，
此模块仅用于需要数据转换、重命名、填充等复杂操作的迁移。
"""

import logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger(__name__)


async def _check_migration_flag(conn: AsyncConnection, migration_id: str) -> bool:
    """检查迁移是否已执行过。"""
    check_flag_sql = text("SELECT 1 FROM config WHERE config_key = :key")
    flag_exists = (await conn.execute(check_flag_sql, {"key": migration_id})).scalar_one_or_none() is not None
    if flag_exists:
        logger.debug(f"迁移 '{migration_id}' 已执行过，跳过。")
        return True
    return False


async def _set_migration_flag(conn: AsyncConnection, migration_id: str):
    """设置迁移完成的标志位。"""
    description = f"标志位，表示已完成数据库迁移: {migration_id}"
    if conn.dialect.name == 'mysql':
        stmt = text("INSERT INTO config (config_key, config_value, description) VALUES (:key, 'true', :desc) ON DUPLICATE KEY UPDATE config_value = 'true'")
    else:  # postgresql
        stmt = text("INSERT INTO config (config_key, config_value, description) VALUES (:key, 'true', :desc) ON CONFLICT (config_key) DO UPDATE SET config_value = 'true'")
    await conn.execute(stmt, {"key": migration_id, "desc": description})
    logger.info(f"成功设置迁移标志 '{migration_id}'。")


async def _run_migration(conn: AsyncConnection, migration_id: str, migration_func, *args):
    """一个包装器，用于运行单个迁移任务并处理标志位。"""
    if not await _check_migration_flag(conn, migration_id):
        logger.warning(f"将执行一次性数据库迁移 '{migration_id}'...")
        try:
            await migration_func(conn, *args)
            await _set_migration_flag(conn, migration_id)
            logger.info(f"迁移任务 '{migration_id}' 成功完成。")
        except Exception as e:
            logger.error(f"执行迁移 '{migration_id}' 时发生错误: {e}", exc_info=True)
            raise


async def _migrate_clear_rate_limit_state_v1(conn: AsyncConnection):
    """
    清空 rate_limit_state 表数据。

    原因：修复了 MySQL DATETIME 微秒四舍五入导致 checksum 验证失败的问题。
    旧数据的 checksum 是用错误的时间计算的，需要清空后重新生成。
    """
    logger.info("清空 rate_limit_state 表...")
    await conn.execute(text("DELETE FROM rate_limit_state"))
    logger.info("rate_limit_state 表已清空，将在下次访问时重新生成正确的 checksum。")


async def _rollback_to_original_types_v1(conn: AsyncConnection, db_type: str):
    """
    将数据库字段类型恢复到原始 ORM 模型定义

    目标:
    0. 修复外键约束阻止的类型转换 (media_servers)
    1. VARCHAR/TEXT 时间字段 → DATETIME
    2. BIGINT 数值字段 → INTEGER (仅恢复原本为 Integer 的字段)
    3. TEXT/LONGTEXT 字符串字段 → VARCHAR (根据原始长度)

    Args:
        conn: 数据库连接
        db_type: 数据库类型 ('mysql' 或 'postgresql')
    """
    logger.info(f"开始回退字段类型到原始定义 (数据库类型: {db_type})...")

    total_converted = 0

    # ========== 步骤 0: 修复外键约束问题 ==========
    if db_type == 'mysql':
        logger.info("🔧 步骤 0/4: 修复 media_servers 外键约束问题")

        # 步骤 0.1: 删除外键约束
        try:
            await conn.execute(text(
                "ALTER TABLE `media_items` DROP FOREIGN KEY `media_items_ibfk_1`"
            ))
            logger.info("  ✅ 外键约束 media_items_ibfk_1 已删除")
        except Exception as e:
            if "check that it exists" in str(e).lower() or "doesn't exist" in str(e).lower():
                logger.info("  ⚠️  外键约束不存在，跳过删除")
            else:
                logger.warning(f"  ⚠️  删除外键失败: {e}")

        # 步骤 0.2: 修改字段类型
        try:
            await conn.execute(text("ALTER TABLE `media_servers` MODIFY COLUMN `id` BIGINT AUTO_INCREMENT"))
            logger.info("  ✅ media_servers.id → BIGINT")
            total_converted += 1
        except Exception as e:
            logger.warning(f"  ⚠️  media_servers.id 转换失败: {e}")

        try:
            await conn.execute(text("ALTER TABLE `media_items` MODIFY COLUMN `server_id` BIGINT"))
            logger.info("  ✅ media_items.server_id → BIGINT")
            total_converted += 1
        except Exception as e:
            logger.warning(f"  ⚠️  media_items.server_id 转换失败: {e}")

        # 步骤 0.3: 重新创建外键约束
        try:
            await conn.execute(text(
                "ALTER TABLE `media_items` ADD CONSTRAINT `media_items_ibfk_1` "
                "FOREIGN KEY (`server_id`) REFERENCES `media_servers` (`id`) ON DELETE CASCADE"
            ))
            logger.info("  ✅ 外键约束已重新创建")
        except Exception as e:
            if "duplicate" in str(e).lower():
                logger.info("  ⚠️  外键约束已存在，跳过创建")
            else:
                logger.warning(f"  ⚠️  重新创建外键失败: {e}")

    # ========== 第 1 步: 时间字段 → DATETIME ==========
    logger.info("🕐 步骤 1/4: 转换时间字段 → DATETIME")

    TIMESTAMP_FIELDS = {
        'anime': ['created_at'],
        'anime_sources': ['last_refresh_latest_episode_at', 'created_at'],
        'episode': ['fetched_at'],
        'users': ['token_update', 'created_at'],
        'user_sessions': ['created_at', 'last_used_at', 'expires_at'],
        'cache_data': ['expires_at'],
        'api_tokens': ['created_at', 'expires_at', 'last_call_at'],
        'token_access_logs': ['access_time'],
        'ua_rules': ['created_at'],
        'bangumi_auth': ['expires_at', 'authorized_at'],
        'oauth_states': ['expires_at'],
        'scheduled_tasks': ['last_run_at', 'next_run_at'],
        'webhook_tasks': ['reception_time', 'execute_time'],
        'task_history': ['created_at', 'updated_at', 'finished_at'],
        'task_state_cache': ['created_at', 'updated_at'],
        'external_api_logs': ['access_time'],
        'rate_limit_state': ['last_reset_time'],
        'title_recognition': ['created_at', 'updated_at'],
        'media_servers': ['created_at', 'updated_at'],
        'media_items': ['created_at', 'updated_at'],
        'local_danmaku_items': ['created_at', 'updated_at'],
    }

    for table, fields in TIMESTAMP_FIELDS.items():
        for field in fields:
            try:
                if db_type == 'mysql':
                    sql = text(f"ALTER TABLE `{table}` MODIFY COLUMN `{field}` DATETIME")
                else:  # postgresql
                    sql = text(f'ALTER TABLE "{table}" ALTER COLUMN "{field}" TYPE TIMESTAMP USING "{field}"::timestamp')

                await conn.execute(sql)
                logger.info(f"  ✅ {table}.{field} → DATETIME")
                total_converted += 1
            except Exception as e:
                logger.warning(f"  ⚠️  {table}.{field} 转换失败: {e}")


    # ========== 第 2 步: 数值字段 BIGINT → INTEGER ==========
    logger.info("🔢 步骤 2/4: 转换数值字段 BIGINT → INTEGER")

    # 注意: 只包含原本为 Integer 的字段,不包括主键等 BigInteger 字段
    # 注意: media_servers.id 已在步骤 0 中处理为 BIGINT
    INTEGER_FIELDS = {
        'anime': ['season', 'episode_count', 'year'],
        'anime_sources': ['source_order', 'incremental_refresh_failures'],
        'episode': ['episode_index', 'comment_count'],
        'scrapers': ['display_order'],
        'metadata_sources': ['display_order'],
        'api_tokens': ['id', 'daily_call_limit', 'daily_call_count'],
        'token_access_logs': ['token_id'],
        'ua_rules': ['id'],
        'bangumi_auth': ['bangumi_user_id'],
        'tmdb_episode_mapping': ['tmdb_tv_id', 'tmdb_episode_id', 'tmdb_season_number',
                                 'tmdb_episode_number', 'custom_season_number',
                                 'custom_episode_number', 'absolute_episode_number'],
        'task_history': ['progress'],
        'external_api_logs': ['status_code'],
        'rate_limit_state': ['request_count'],
        # 'title_recognition': ['id'],  # 主键字段，需要保留 AUTO_INCREMENT，单独处理
        # 'media_servers': ['id'],  # 被外键 media_items_ibfk_1 引用，跳过
        'media_items': ['season', 'episode', 'year'],
        'local_danmaku_items': ['season', 'episode', 'year'],
    }

    for table, fields in INTEGER_FIELDS.items():
        for field in fields:
            try:
                if db_type == 'mysql':
                    sql = text(f"ALTER TABLE `{table}` MODIFY COLUMN `{field}` INT")
                else:  # postgresql
                    sql = text(f'ALTER TABLE "{table}" ALTER COLUMN "{field}" TYPE INTEGER USING "{field}"::integer')

                await conn.execute(sql)
                logger.info(f"  ✅ {table}.{field} → INTEGER")
                total_converted += 1
            except Exception as e:
                logger.warning(f"  ⚠️  {table}.{field} 转换失败: {e}")


    # ========== 第 3 步: 字符串字段 TEXT/LONGTEXT → VARCHAR ==========
    logger.info("📝 步骤 3/4: 转换字符串字段 TEXT/LONGTEXT → VARCHAR")

    # VARCHAR 字段映射 {表名: {字段名: 长度}} - 最低 500
    # 注意: anime_sources.media_id 在复合唯一索引中，使用 255 避免超过 3072 字节限制
    VARCHAR_FIELDS = {
        'anime': {'title': 500, 'image_url': 512, 'local_image_path': 512},
        'anime_sources': {'provider_name': 500, 'media_id': 255},
        'episode': {'title': 500, 'provider_episode_id': 500, 'danmaku_file_path': 1024},
        'users': {'username': 500, 'hashed_password': 500},
        'user_sessions': {'jti': 500, 'ip_address': 500, 'user_agent': 500},
        'scrapers': {'provider_name': 500},
        'metadata_sources': {'provider_name': 500},
        'anime_metadata': {'tmdb_id': 500, 'tmdb_episode_group_id': 500, 'imdb_id': 500, 'tvdb_id': 500, 'douban_id': 500, 'bangumi_id': 500},
        'config': {'config_key': 500},
        'cache_data': {'cache_provider': 500, 'cache_key': 500},
        'api_tokens': {'name': 500, 'token': 500},
        'token_access_logs': {'ip_address': 500, 'status': 500, 'path': 512},
        'ua_rules': {'ua_string': 500},
        'bangumi_auth': {'nickname': 500, 'avatar_url': 512},
        'oauth_states': {'state_key': 500},
        'anime_aliases': {'name_en': 500, 'name_jp': 500, 'name_romaji': 500, 'alias_cn_1': 500, 'alias_cn_2': 500, 'alias_cn_3': 500},
        'tmdb_episode_mapping': {'tmdb_episode_group_id': 500},
        'scheduled_tasks': {'id': 500, 'name': 500, 'job_type': 500, 'cron_expression': 500},
        'webhook_tasks': {'webhook_source': 500, 'status': 500, 'unique_key': 500, 'task_title': 500},
        'task_history': {'id': 500, 'title': 500, 'status': 500, 'unique_key': 500, 'queue_type': 500, 'task_type': 500},
        'task_state_cache': {'task_id': 500, 'task_type': 500},
        'external_api_logs': {'ip_address': 500, 'endpoint': 500},
        'rate_limit_state': {'provider_name': 500, 'checksum': 500},
        'media_servers': {'name': 500, 'provider_name': 500, 'url': 512, 'api_token': 512},
        'media_items': {'media_id': 500, 'library_id': 500, 'title': 500, 'tmdb_id': 500, 'tvdb_id': 500, 'imdb_id': 500, 'poster_url': 1024},
        'local_danmaku_items': {'file_path': 1024, 'title': 512, 'tmdb_id': 500, 'tvdb_id': 500, 'imdb_id': 500, 'poster_url': 1024, 'nfo_path': 1024},
    }

    for table, fields_dict in VARCHAR_FIELDS.items():
        for field, length in fields_dict.items():
            try:
                if db_type == 'mysql':
                    sql = text(f"ALTER TABLE `{table}` MODIFY COLUMN `{field}` VARCHAR({length})")
                else:  # postgresql
                    sql = text(f'ALTER TABLE "{table}" ALTER COLUMN "{field}" TYPE VARCHAR({length})')

                await conn.execute(sql)
                logger.info(f"  ✅ {table}.{field} → VARCHAR({length})")
                total_converted += 1
            except Exception as e:
                logger.warning(f"  ⚠️  {table}.{field} 转换失败: {e}")


    # ========== 第 4 步: LONGTEXT → MEDIUMTEXT/TEXT ==========
    logger.info("📦 步骤 4/4: 转换 LONGTEXT → MEDIUMTEXT/TEXT")

    # 这些字段在 ORM 中是 TEXT().with_variant(MEDIUMTEXT, "mysql")
    # 数据库中当前是 LONGTEXT，需要转换为 MEDIUMTEXT (MySQL) 或 TEXT (PostgreSQL)
    MEDIUMTEXT_FIELDS = {
        'config': ['config_value'],
        'cache_data': ['cache_value'],
        'webhook_tasks': ['payload'],
        'task_history': ['description', 'task_parameters'],
        'task_state_cache': ['task_parameters'],
        'external_api_logs': ['message'],
        'title_recognition': ['content'],
    }

    for table, fields in MEDIUMTEXT_FIELDS.items():
        for field in fields:
            try:
                if db_type == 'mysql':
                    sql = text(f"ALTER TABLE `{table}` MODIFY COLUMN `{field}` MEDIUMTEXT")
                else:  # postgresql
                    sql = text(f'ALTER TABLE "{table}" ALTER COLUMN "{field}" TYPE TEXT')

                await conn.execute(sql)
                field_type = "MEDIUMTEXT" if db_type == 'mysql' else "TEXT"
                logger.info(f"  ✅ {table}.{field} → {field_type}")
                total_converted += 1
            except Exception as e:
                logger.warning(f"  ⚠️  {table}.{field} 转换失败: {e}")

    logger.info(f"\n✅ 回退迁移完成! 共转换 {total_converted} 个字段")
    logger.info("   请重启应用验证字段类型是否恢复正常")


async def _fix_api_tokens_id_autoincrement(conn: AsyncConnection):
    """
    修复 api_tokens 表的 id 字段，确保有自增属性

    问题：创建 API Token 时报错 "Field 'id' doesn't have a default value"
    原因：id 字段缺少自增属性（MySQL: AUTO_INCREMENT, PostgreSQL: SERIAL/IDENTITY）
    解决：根据数据库类型添加相应的自增属性
    """
    logger.info("检查 api_tokens 表的 id 字段...")
    db_type = conn.dialect.name

    try:
        if db_type == 'mysql':
            # === MySQL 处理逻辑 ===
            check_sql = text("""
                SELECT COLUMN_NAME, COLUMN_TYPE, EXTRA
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                AND TABLE_NAME = 'api_tokens'
                AND COLUMN_NAME = 'id'
            """)

            result = await conn.execute(check_sql)
            row = result.fetchone()

            if row:
                extra = str(row[2]).lower() if row[2] else ""
                logger.info(f"  当前 id 字段 (MySQL): {row[0]} {row[1]} {row[2]}")

                # 检查是否已经有 AUTO_INCREMENT
                if 'auto_increment' in extra:
                    logger.info("  ✅ id 字段已有 AUTO_INCREMENT 属性，无需修复")
                    return

            logger.warning("  ⚠️  id 字段缺少 AUTO_INCREMENT 属性，开始修复...")

            # 修改 id 字段，添加 AUTO_INCREMENT
            alter_sql = text("""
                ALTER TABLE api_tokens
                MODIFY COLUMN id INT NOT NULL AUTO_INCREMENT
            """)

            await conn.execute(alter_sql)
            logger.info("  ✅ 成功为 api_tokens.id 添加 AUTO_INCREMENT 属性")

        elif db_type == 'postgresql':
            # === PostgreSQL 处理逻辑 ===
            # 检查 id 字段是否已有默认值（序列或 IDENTITY）
            check_sql = text("""
                SELECT
                    column_name,
                    data_type,
                    column_default,
                    is_identity
                FROM information_schema.columns
                WHERE table_schema = CURRENT_SCHEMA()
                AND table_name = 'api_tokens'
                AND column_name = 'id'
            """)

            result = await conn.execute(check_sql)
            row = result.fetchone()

            if row:
                column_default = str(row[2]) if row[2] else ""
                is_identity = str(row[3]) if row[3] else "NO"
                logger.info(f"  当前 id 字段 (PostgreSQL): {row[0]} {row[1]}, default={row[2]}, is_identity={row[3]}")

                # 检查是否已有自增（序列或 IDENTITY）
                if 'nextval' in column_default or is_identity == 'YES':
                    logger.info("  ✅ id 字段已有自增属性，无需修复")
                    return

            logger.warning("  ⚠️  id 字段缺少自增属性，开始修复...")

            # 创建序列（如果不存在）
            sequence_name = 'api_tokens_id_seq'
            create_seq_sql = text(f"""
                CREATE SEQUENCE IF NOT EXISTS {sequence_name}
                START WITH 1
                INCREMENT BY 1
                NO MINVALUE
                NO MAXVALUE
                CACHE 1
            """)
            await conn.execute(create_seq_sql)

            # 获取当前最大 id 值
            max_id_sql = text("SELECT COALESCE(MAX(id), 0) FROM api_tokens")
            result = await conn.execute(max_id_sql)
            max_id = result.scalar_one()

            # 设置序列的当前值
            if max_id > 0:
                setval_sql = text(f"SELECT setval('{sequence_name}', :max_id)")
                await conn.execute(setval_sql, {"max_id": max_id})

            # 设置 id 字段的默认值为序列的下一个值
            alter_sql = text(f"""
                ALTER TABLE api_tokens
                ALTER COLUMN id SET DEFAULT nextval('{sequence_name}')
            """)
            await conn.execute(alter_sql)

            # 将序列的所有权交给表列（这样删除列时序列也会被删除）
            owner_sql = text(f"ALTER SEQUENCE {sequence_name} OWNED BY api_tokens.id")
            await conn.execute(owner_sql)

            logger.info("  ✅ 成功为 api_tokens.id 添加序列自增属性")

        else:
            logger.warning(f"  ⚠️  不支持的数据库类型: {db_type}，跳过此迁移")
            return

    except Exception as e:
        logger.error(f"  ❌ 修复 api_tokens.id 失败: {e}")
        raise


async def _rename_duplicate_idx_created_at(conn: AsyncConnection, db_type: str):
    """
    修复重复的索引名 idx_created_at。
    task_history 和 media_items 表都使用了相同的索引名，需要重命名。
    """
    logger.info("🔧 修复重复索引名 idx_created_at...")

    try:
        if db_type == 'mysql':
            # MySQL: 检查 task_history 表是否有 idx_created_at 索引
            check_sql = text("""
                SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                AND TABLE_NAME = 'task_history'
                AND INDEX_NAME = 'idx_created_at'
            """)
            result = await conn.execute(check_sql)
            if result.scalar() > 0:
                # 重命名索引
                await conn.execute(text("ALTER TABLE `task_history` RENAME INDEX `idx_created_at` TO `idx_task_history_created_at`"))
                logger.info("  ✅ task_history.idx_created_at → idx_task_history_created_at")
            else:
                logger.info("  ⚠️  task_history 表没有 idx_created_at 索引，跳过")

            # 检查 media_items 表是否有 idx_created_at 索引
            check_sql = text("""
                SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                AND TABLE_NAME = 'media_items'
                AND INDEX_NAME = 'idx_created_at'
            """)
            result = await conn.execute(check_sql)
            if result.scalar() > 0:
                await conn.execute(text("ALTER TABLE `media_items` RENAME INDEX `idx_created_at` TO `idx_media_items_created_at`"))
                logger.info("  ✅ media_items.idx_created_at → idx_media_items_created_at")
            else:
                logger.info("  ⚠️  media_items 表没有 idx_created_at 索引，跳过")
        else:
            # PostgreSQL: 检查并重命名索引
            check_sql = text("""
                SELECT COUNT(*) FROM pg_indexes
                WHERE tablename = 'task_history'
                AND indexname = 'idx_created_at'
            """)
            result = await conn.execute(check_sql)
            if result.scalar() > 0:
                await conn.execute(text('ALTER INDEX "idx_created_at" RENAME TO "idx_task_history_created_at"'))
                logger.info("  ✅ task_history.idx_created_at → idx_task_history_created_at")
            else:
                logger.info("  ⚠️  task_history 表没有 idx_created_at 索引，跳过")

            # 检查 media_items 表
            check_sql = text("""
                SELECT COUNT(*) FROM pg_indexes
                WHERE tablename = 'media_items'
                AND indexname = 'idx_created_at'
            """)
            result = await conn.execute(check_sql)
            if result.scalar() > 0:
                await conn.execute(text('ALTER INDEX "idx_created_at" RENAME TO "idx_media_items_created_at"'))
                logger.info("  ✅ media_items.idx_created_at → idx_media_items_created_at")
            else:
                logger.info("  ⚠️  media_items 表没有 idx_created_at 索引，跳过")

        logger.info("✅ 重复索引名修复完成")
    except Exception as e:
        logger.warning(f"⚠️  修复重复索引名时出错: {e}")
        # 不抛出异常，允许继续执行


async def _fix_title_recognition_id_autoincrement(conn: AsyncConnection):
    """
    修复 title_recognition 表的 id 字段，确保有自增属性

    问题：更新识别词配置时报错 "Field 'id' doesn't have a default value"
    原因：id 字段缺少自增属性（MySQL: AUTO_INCREMENT, PostgreSQL: SERIAL/IDENTITY）
    解决：根据数据库类型添加相应的自增属性
    """
    logger.info("检查 title_recognition 表的 id 字段...")
    db_type = conn.dialect.name

    try:
        if db_type == 'mysql':
            # === MySQL 处理逻辑 ===
            check_sql = text("""
                SELECT COLUMN_NAME, COLUMN_TYPE, EXTRA
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                AND TABLE_NAME = 'title_recognition'
                AND COLUMN_NAME = 'id'
            """)

            result = await conn.execute(check_sql)
            row = result.fetchone()

            if row:
                extra = str(row[2]).lower() if row[2] else ""
                logger.info(f"  当前 id 字段 (MySQL): {row[0]} {row[1]} {row[2]}")

                # 检查是否已经有 AUTO_INCREMENT
                if 'auto_increment' in extra:
                    logger.info("  ✅ id 字段已有 AUTO_INCREMENT 属性，无需修复")
                    return

            logger.warning("  ⚠️  id 字段缺少 AUTO_INCREMENT 属性，开始修复...")

            # 修改 id 字段，添加 AUTO_INCREMENT
            alter_sql = text("""
                ALTER TABLE title_recognition
                MODIFY COLUMN id INT NOT NULL AUTO_INCREMENT
            """)

            await conn.execute(alter_sql)
            logger.info("  ✅ 成功为 title_recognition.id 添加 AUTO_INCREMENT 属性")

        elif db_type == 'postgresql':
            # === PostgreSQL 处理逻辑 ===
            check_sql = text("""
                SELECT
                    column_name,
                    data_type,
                    column_default,
                    is_identity
                FROM information_schema.columns
                WHERE table_schema = CURRENT_SCHEMA()
                AND table_name = 'title_recognition'
                AND column_name = 'id'
            """)

            result = await conn.execute(check_sql)
            row = result.fetchone()

            if row:
                column_default = str(row[2]) if row[2] else ""
                is_identity = str(row[3]) if row[3] else "NO"
                logger.info(f"  当前 id 字段 (PostgreSQL): {row[0]} {row[1]}, default={row[2]}, is_identity={row[3]}")

                # 检查是否已有自增（序列或 IDENTITY）
                if 'nextval' in column_default or is_identity == 'YES':
                    logger.info("  ✅ id 字段已有自增属性，无需修复")
                    return

            logger.warning("  ⚠️  id 字段缺少自增属性，开始修复...")

            # 创建序列（如果不存在）
            sequence_name = 'title_recognition_id_seq'
            create_seq_sql = text(f"""
                CREATE SEQUENCE IF NOT EXISTS {sequence_name}
                START WITH 1
                INCREMENT BY 1
                NO MINVALUE
                NO MAXVALUE
                CACHE 1
            """)
            await conn.execute(create_seq_sql)

            # 获取当前最大 id 值
            max_id_sql = text("SELECT COALESCE(MAX(id), 0) FROM title_recognition")
            result = await conn.execute(max_id_sql)
            max_id = result.scalar() or 0

            # 设置序列的起始值
            if max_id > 0:
                await conn.execute(text(f"SELECT setval('{sequence_name}', {max_id})"))

            # 设置默认值为序列
            alter_sql = text(f"""
                ALTER TABLE title_recognition
                ALTER COLUMN id SET DEFAULT nextval('{sequence_name}')
            """)
            await conn.execute(alter_sql)

            # 将序列与列关联
            await conn.execute(text(f"ALTER SEQUENCE {sequence_name} OWNED BY title_recognition.id"))

            logger.info("  ✅ 成功为 title_recognition.id 添加序列自增属性")

        else:
            logger.warning(f"  ⚠️  不支持的数据库类型: {db_type}，跳过此迁移")
            return

    except Exception as e:
        logger.error(f"  ❌ 修复 title_recognition.id 失败: {e}")
        raise


async def _migrate_docker_image_name_v1(conn: AsyncConnection):
    """
    修正 dockerImageName 配置的默认值。

    将错误的 'yanyutin753/misaka_danmu_server:latest'
    修正为 'l429609201/misaka_danmu_server:latest'
    """
    logger.info("检查并修正 dockerImageName 配置...")

    # 检查当前值
    check_sql = text("SELECT config_value FROM config WHERE config_key = 'dockerImageName'")
    result = await conn.execute(check_sql)
    current_value = result.scalar_one_or_none()

    if current_value == 'yanyutin753/misaka_danmu_server:latest':
        # 修正为正确的镜像名
        update_sql = text("""
            UPDATE config
            SET config_value = 'l429609201/misaka_danmu_server:latest'
            WHERE config_key = 'dockerImageName'
        """)
        await conn.execute(update_sql)
        logger.info("已将 dockerImageName 从 'yanyutin753/misaka_danmu_server:latest' 修正为 'l429609201/misaka_danmu_server:latest'")
    else:
        logger.info(f"dockerImageName 当前值为 '{current_value}'，无需修正")


async def _drop_force_scrape_column_v1(conn: AsyncConnection, db_type: str):
    """删除 scheduled_tasks 表中已废弃的 force_scrape 列（已被 task_config JSON 列取代）。"""
    # 使用 information_schema 安全检测列是否存在，避免 PostgreSQL 事务因失败的 SELECT 进入 aborted 状态
    if db_type == "mysql":
        check_sql = text("""
            SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
            AND TABLE_NAME = 'scheduled_tasks'
            AND COLUMN_NAME = 'force_scrape'
        """)
    else:  # postgresql
        check_sql = text("""
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_name = 'scheduled_tasks'
            AND column_name = 'force_scrape'
        """)

    result = await conn.execute(check_sql)
    if result.scalar() == 0:
        logger.info("force_scrape 列不存在，无需删除")
        return

    if db_type == "mysql":
        await conn.execute(text("ALTER TABLE `scheduled_tasks` DROP COLUMN `force_scrape`"))
    else:
        await conn.execute(text('ALTER TABLE "scheduled_tasks" DROP COLUMN "force_scrape"'))
    logger.info("已删除 scheduled_tasks 表的 force_scrape 列")


# 所有迁移任务的 ID 列表（新增迁移时需同步更新此列表）
ALL_MIGRATION_IDS = [
    "migrate_clear_rate_limit_state_v1",
    "rollback_to_original_types_v1",
    "fix_api_tokens_id_autoincrement_v2",
    "rename_duplicate_idx_created_at_v1",
    "fix_title_recognition_id_autoincrement_v1",
    "migrate_docker_image_name_v1",
    "drop_force_scrape_column_v1",
]


async def mark_all_migrations_done(conn: AsyncConnection):
    """
    将所有迁移标记为已完成（用于全新数据库初始化场景）。

    全新数据库由 create_all 直接按 ORM 模型创建，表结构已经是最新的，
    不需要执行任何迁移。但需要设置标志位，避免下次启动时误执行迁移。
    """
    for migration_id in ALL_MIGRATION_IDS:
        if not await _check_migration_flag(conn, migration_id):
            await _set_migration_flag(conn, migration_id)
    logger.info(f"全新数据库：已标记 {len(ALL_MIGRATION_IDS)} 个迁移为已完成，跳过实际执行。")


async def run_migrations(conn: AsyncConnection, db_type: str, db_name: str):
    """
    按顺序执行所有数据库架构迁移。

    注意：简单的添加字段和类型扩展现在由 db_maintainer.py 自动处理。
    此处仅保留需要数据转换的复杂迁移任务。
    """
    logger.info("开始执行数据库迁移检查...")

    # 迁移任务列表
    migrations = [
        # 格式: ("migration_id", migration_func, (args,))
        ("migrate_clear_rate_limit_state_v1", _migrate_clear_rate_limit_state_v1, ()),
        ("rollback_to_original_types_v1", _rollback_to_original_types_v1, (db_type,)),
        ("fix_api_tokens_id_autoincrement_v2", _fix_api_tokens_id_autoincrement, ()),  # v2: 增加 PostgreSQL 支持
        ("rename_duplicate_idx_created_at_v1", _rename_duplicate_idx_created_at, (db_type,)),  # 修复重复索引名
        ("fix_title_recognition_id_autoincrement_v1", _fix_title_recognition_id_autoincrement, ()),  # 修复 title_recognition.id 自增
        ("migrate_docker_image_name_v1", _migrate_docker_image_name_v1, ()),  # 修正 dockerImageName 默认值
        ("drop_force_scrape_column_v1", _drop_force_scrape_column_v1, (db_type,)),  # 删除已废弃的 force_scrape 列
    ]

    for migration_id, migration_func, args in migrations:
        await _run_migration(conn, migration_id, migration_func, *args)

    logger.info("所有数据库迁移检查完成。")
