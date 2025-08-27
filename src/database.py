import secrets
import string
import logging
from fastapi import FastAPI, Request
from sqlalchemy.engine.url import URL
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text
from .config import settings
from .orm_models import Base

# 使用模块级日志记录器
logger = logging.getLogger(__name__)

async def _migrate_add_anime_year(conn, db_type, db_name):
    """迁移任务: 确保 anime.year 字段存在，并移除旧的 source_url 字段。"""
    migration_id = "add_anime_year_and_drop_source_url"
    logger.info(f"正在检查是否需要执行迁移: {migration_id}...")

    if db_type == "mysql":
        check_source_url_sql = text(f"SELECT 1 FROM information_schema.columns WHERE table_schema = '{db_name}' AND table_name = 'anime' AND column_name = 'source_url'")
        check_year_sql = text(f"SELECT 1 FROM information_schema.columns WHERE table_schema = '{db_name}' AND table_name = 'anime' AND column_name = 'year'")
        add_year_sql = text("ALTER TABLE anime ADD COLUMN `year` INT NULL DEFAULT NULL AFTER `episode_count`")
        drop_source_url_sql = text("ALTER TABLE anime DROP COLUMN source_url")
    elif db_type == "postgresql":
        check_source_url_sql = text("SELECT 1 FROM information_schema.columns WHERE table_name = 'anime' AND column_name = 'source_url'")
        check_year_sql = text("SELECT 1 FROM information_schema.columns WHERE table_name = 'anime' AND column_name = 'year'")
        add_year_sql = text('ALTER TABLE anime ADD COLUMN "year" INT NULL')
        drop_source_url_sql = text("ALTER TABLE anime DROP COLUMN source_url")
    else:
        return

    has_source_url = (await conn.execute(check_source_url_sql)).scalar_one_or_none() is not None
    has_year = (await conn.execute(check_year_sql)).scalar_one_or_none() is not None

    if not has_year:
        logger.info(f"列 'anime.year' 不存在。正在添加...")
        await conn.execute(add_year_sql)
        logger.info(f"成功添加列 'anime.year'。")

    if has_source_url:
        logger.info(f"发现过时的列 'anime.source_url'。正在删除...")
        await conn.execute(drop_source_url_sql)
        logger.info(f"成功删除列 'anime.source_url'。")
    logger.info(f"迁移任务 '{migration_id}' 检查完成。")

async def _migrate_add_scheduled_task_id(conn, db_type, db_name):
    """
    迁移任务: 确保 task_history.scheduled_task_id 字段存在。
    """
    migration_id = "add_scheduled_task_id_to_task_history"
    logger.info(f"正在检查是否需要执行迁移: {migration_id}...")

    if db_type == "mysql":
        check_sql = text(f"SELECT 1 FROM information_schema.columns WHERE table_schema = '{db_name}' AND table_name = 'task_history' AND column_name = 'scheduled_task_id'")
        add_sql = text("ALTER TABLE task_history ADD COLUMN `scheduled_task_id` VARCHAR(100) NULL DEFAULT NULL AFTER `id`, ADD INDEX `ix_task_history_scheduled_task_id` (`scheduled_task_id`)")
    elif db_type == "postgresql":
        check_sql = text("SELECT 1 FROM information_schema.columns WHERE table_name = 'task_history' AND column_name = 'scheduled_task_id'")
        # 修正：将SQL语句拆分为两个，以兼容PostgreSQL
        add_column_sql = text('ALTER TABLE task_history ADD COLUMN "scheduled_task_id" VARCHAR(100) NULL')
        create_index_sql = text('CREATE INDEX ix_task_history_scheduled_task_id ON task_history ("scheduled_task_id")')
    else:
        return

    column_exists = (await conn.execute(check_sql)).scalar_one_or_none() is not None
    if not column_exists:
        logger.info(f"列 'task_history.scheduled_task_id' 不存在。正在添加...")
        if db_type == "mysql":
            await conn.execute(add_sql)
        elif db_type == "postgresql":
            await conn.execute(add_column_sql)
            await conn.execute(create_index_sql)
        logger.info(f"成功添加列 'task_history.scheduled_task_id'。")
    logger.info(f"迁移任务 '{migration_id}' 检查完成。")

async def _migrate_add_failover_enabled_to_metadata_sources(conn, db_type, db_name):
    """
    迁移任务: 确保 metadata_sources.is_failover_enabled 字段存在。
    """
    migration_id = "add_failover_enabled_to_metadata_sources"
    logger.info(f"正在检查是否需要执行迁移: {migration_id}...")

    if db_type == "mysql":
        check_sql = text(f"SELECT 1 FROM information_schema.columns WHERE table_schema = '{db_name}' AND table_name = 'metadata_sources' AND column_name = 'is_failover_enabled'")
        add_sql = text("ALTER TABLE metadata_sources ADD COLUMN `is_failover_enabled` BOOLEAN NOT NULL DEFAULT FALSE")
    elif db_type == "postgresql":
        check_sql = text("SELECT 1 FROM information_schema.columns WHERE table_name = 'metadata_sources' AND column_name = 'is_failover_enabled'")
        add_sql = text('ALTER TABLE metadata_sources ADD COLUMN "is_failover_enabled" BOOLEAN NOT NULL DEFAULT FALSE')
    else:
        return

    column_exists = (await conn.execute(check_sql)).scalar_one_or_none() is not None
    if not column_exists:
        logger.info(f"列 'metadata_sources.is_failover_enabled' 不存在。正在添加...")
        await conn.execute(add_sql)
        logger.info(f"成功添加列 'metadata_sources.is_failover_enabled'。")
    logger.info(f"迁移任务 '{migration_id}' 检查完成。")

async def _run_migrations(conn):
    """
    执行所有一次性的数据库架构迁移。
    """
    db_type = settings.database.type.lower()
    db_name = settings.database.name

    if db_type not in ["mysql", "postgresql"]:
        logger.warning(f"不支持为数据库类型 '{db_type}' 自动执行迁移。")
        return

    await _migrate_add_anime_year(conn, db_type, db_name)
    await _migrate_add_scheduled_task_id(conn, db_type, db_name)
    await _migrate_add_failover_enabled_to_metadata_sources(conn, db_type, db_name)

async def create_db_engine_and_session(app: FastAPI):
    """创建数据库引擎和会话工厂，并存储在 app.state 中"""
    db_type = settings.database.type.lower()
    if db_type == "mysql":
        db_url = URL.create(
            drivername="mysql+aiomysql",
            username=settings.database.user,
            password=settings.database.password,
            host=settings.database.host,
            port=settings.database.port,
            database=settings.database.name,
            query={"charset": "utf8mb4"},
        )
    elif db_type == "postgresql":
        db_url = URL.create(
            drivername="postgresql+asyncpg",
            username=settings.database.user,
            password=settings.database.password,
            host=settings.database.host,
            port=settings.database.port,
            database=settings.database.name,
        )
    else:
        raise ValueError(f"不支持的数据库类型: '{db_type}'。请使用 'mysql' 或 'postgresql'。")
    try:
        engine = create_async_engine(
            db_url,
            echo=False,
            pool_recycle=3600,
            pool_size=10,
            max_overflow=20,
            pool_timeout=30
        )
        app.state.db_engine = engine
        app.state.db_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        logger.info("数据库引擎和会话工厂创建成功。")
    except Exception as e:
        logger.error("="*60)
        logger.error("=== 无法连接到数据库服务器，应用无法启动。 ===")
        logger.error(f"=== 错误类型: {type(e).__name__}")
        logger.error(f"=== 错误详情: {e}")
        logger.error("---")
        logger.error("--- 可能的原因与排查建议: ---")
        logger.error("--- 1. 数据库服务未运行: 请确认您的 数据库 服务正在运行。")
        logger.error(f"--- 2. 配置错误: 请检查您的配置文件或环境变量中的数据库连接信息是否正确。")
        logger.error(f"---    - 主机 (Host): {settings.database.host}")
        logger.error(f"---    - 端口 (Port): {settings.database.port}")
        logger.error(f"---    - 用户 (User): {settings.database.user}")
        logger.error("--- 3. 网络问题: 如果应用和数据库在不同的容器或机器上，请检查它们之间的网络连接和防火墙设置。")
        logger.error("--- 4. 权限问题: 确认提供的用户有权限从应用所在的IP地址连接，并有创建数据库的权限。")
        logger.error("="*60)
        raise

async def _create_db_if_not_exists():
    """如果数据库不存在，则使用 SQLAlchemy 引擎创建它。"""
    db_type = settings.database.type.lower()
    db_name = settings.database.name

    if db_type == "mysql":
        # 创建一个不带数据库名称的连接URL
        server_url = URL.create(
            drivername="mysql+aiomysql",
            username=settings.database.user,
            password=settings.database.password,
            host=settings.database.host,
            port=settings.database.port,
            query={"charset": "utf8mb4"},
        )
        check_sql = text(f"SHOW DATABASES LIKE '{db_name}'")
        create_sql = text(f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    elif db_type == "postgresql":
        # 对于PostgreSQL，连接到默认的 'postgres' 数据库来执行创建操作
        server_url = URL.create(
            drivername="postgresql+asyncpg",
            username=settings.database.user,
            password=settings.database.password,
            host=settings.database.host,
            port=settings.database.port,
            database="postgres",
        )
        check_sql = text(f"SELECT 1 FROM pg_database WHERE datname = '{db_name}'")
        create_sql = text(f'CREATE DATABASE "{db_name}"')
    else:
        logger.warning(f"不支持为数据库类型 '{db_type}' 自动创建数据库。请确保数据库已手动创建。")
        return

    # 设置隔离级别以允许 DDL 语句
    engine = create_async_engine(server_url, echo=False, isolation_level="AUTOCOMMIT")
    
    try:
        async with engine.connect() as conn:
            # 检查数据库是否存在
            result = await conn.execute(check_sql)
            if result.scalar_one_or_none() is None:
                logger.info(f"数据库 '{db_name}' 不存在，正在创建...")
                await conn.execute(create_sql)
                logger.info(f"数据库 '{db_name}' 创建成功。")
            else:
                logger.info(f"数据库 '{db_name}' 已存在，跳过创建。")
    except Exception as e:
        logger.error(f"检查或创建数据库时发生错误: {e}", exc_info=True)
        # Provide detailed error message like the old code
        logger.error("="*60)
        logger.error("=== 无法连接到数据库服务器，应用无法启动。 ===")
        logger.error(f"=== 错误类型: {type(e).__name__}")
        logger.error(f"=== 错误详情: {e}")
        logger.error("---")
        logger.error("--- 可能的原因与排查建议: ---")
        logger.error("--- 1. 数据库服务未运行: 请确认您的 数据库 服务正在运行。")
        logger.error(f"--- 2. 配置错误: 请检查您的配置文件或环境变量中的数据库连接信息是否正确。")
        logger.error(f"---    - 主机 (Host): {settings.database.host}")
        logger.error(f"---    - 端口 (Port): {settings.database.port}")
        logger.error(f"---    - 用户 (User): {settings.database.user}")
        logger.error("--- 3. 网络问题: 如果应用和数据库在不同的容器或机器上，请检查它们之间的网络连接和防火墙设置。")
        logger.error("--- 4. 权限问题: 确认提供的用户有权限从应用所在的IP地址连接，并有创建数据库的权限。")
        logger.error("="*60)
        raise
    finally:
        await engine.dispose()

async def get_db_session(request: Request) -> AsyncSession:
    """依赖项：从应用状态获取数据库会话"""
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        yield session

async def close_db_engine(app: FastAPI):
    """关闭数据库引擎"""
    if hasattr(app.state, "db_engine"):
        await app.state.db_engine.dispose()
        logger.info("数据库引擎已关闭。")

async def create_initial_admin_user(app: FastAPI):
    """在应用启动时创建初始管理员用户（如果已配置且不存在）"""
    # 将导入移到函数内部以避免循环导入
    from . import crud
    from . import models

    admin_user = settings.admin.initial_user
    if not admin_user:
        return

    session_factory = app.state.db_session_factory
    async with session_factory() as session:
        existing_user = await crud.get_user_by_username(session, admin_user)

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
    async with session_factory() as session:
        await crud.create_user(session, user_to_create)

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
    await _create_db_if_not_exists()
    await create_db_engine_and_session(app)

    engine = app.state.db_engine
    async with engine.begin() as conn:
        # 1. 首先，确保所有基于模型的表都已创建。
        # `create_all` 会安全地跳过已存在的表。
        logger.info("正在同步数据库模型，创建新表...")
        await conn.run_sync(Base.metadata.create_all)
        logger.info("数据库模型同步完成。")

        # 2. 然后，在已存在的表结构上运行手动迁移。
        await _run_migrations(conn)
    logger.info("数据库初始化完成。")