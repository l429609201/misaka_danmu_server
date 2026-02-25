import secrets
import string
import logging
from fastapi import FastAPI, Request
from sqlalchemy.engine.url import URL
from sqlalchemy import NullPool
from sqlalchemy.pool import AsyncAdaptedQueuePool
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text
from sqlalchemy.exc import OperationalError # Import specific SQLAlchemy exceptions
from src.core.config import settings
from .orm_models import Base # type: ignore
from src.core.timezone import get_app_timezone, get_timezone_offset_str, get_now
from .migrations import run_migrations
# 使用模块级日志记录器
logger = logging.getLogger(__name__)


class DatabaseStartupError(Exception):
    """数据库启动失败异常，用于在 lifespan 中捕获并干净退出"""
    pass

# 全局 session_factory，在应用启动时设置
_global_session_factory = None

def get_db_type() -> str:
    """获取数据库类型"""
    return settings.database.type.lower()


def get_session_factory():
    """获取全局 session_factory"""
    if _global_session_factory is None:
        raise RuntimeError("Session factory 尚未初始化，请确保应用已启动")
    return _global_session_factory


async def sync_postgres_sequence(session: AsyncSession, table_name: str = "anime", sequence_name: str = "anime_id_seq"):
    """
    同步PostgreSQL序列到表的最大ID值,避免主键冲突

    Args:
        session: 数据库会话
        table_name: 表名,默认为"anime"
        sequence_name: 序列名,默认为"anime_id_seq"
    """
    if get_db_type() == "postgresql":
        await session.execute(text(
            f"SELECT setval('{sequence_name}', (SELECT MAX(id) FROM {table_name}))"
        ))
        logger.info(f"已同步PostgreSQL序列 {sequence_name} 到表 {table_name} 的最大ID")


def _get_db_url(include_db_name: bool = True, for_server: bool = False) -> URL:
    """
    根据配置生成数据库连接URL。
    :param include_db_name: URL中是否包含数据库名称。
    :param for_server: 是否为连接到服务器（而不是特定数据库）生成URL，主要用于PostgreSQL。
    """
    db_type = get_db_type()
    
    if db_type == "mysql":
        drivername = "mysql+aiomysql"
        query = {"charset": "utf8mb4"}
        database = settings.database.name if include_db_name else None
    elif db_type == "postgresql":
        drivername = "postgresql+asyncpg"
        # 修正：移除在连接URL中设置时区的逻辑。
        # 这确保了应用与数据库的交互在时间处理上是“时区无关”的，
        # 避免了因驱动程序自动转换时区而导致的数据不一致问题。
        query = None # 确保不通过查询参数传递时区
        if for_server:
            database = "postgres"
        else:
            database = settings.database.name if include_db_name else None
    else:
        raise ValueError(f"不支持的数据库类型: '{db_type}'。请使用 'mysql' 或 'postgresql'。")

    return URL.create(
        drivername=drivername, username=settings.database.user, password=settings.database.password,
        host=settings.database.host, port=settings.database.port, database=database, query=query,
    )

def _log_db_connection_error(context_message: str, e: Exception):
    """
    按照三个方面记录数据库连接错误：连接、用户名密码、权限
    """
    logger.error("="*60)
    logger.error(f"数据库连接失败，应用无法启动")
    logger.error("="*60)

    # 根据错误类型进行分类诊断
    if isinstance(e, OperationalError):
        if hasattr(e.orig, 'errno'):
            # MySQL错误码
            if e.orig.errno == 2003:
                logger.error("1. 连接数据库方面：无法连接到数据库服务器")
                logger.error(f"   - 数据库服务器地址：{settings.database.host}:{settings.database.port}")
                logger.error("   - 请检查数据库服务是否启动")
                logger.error("   - 请检查网络连接和防火墙设置")
            elif e.orig.errno == 1045:
                logger.error("2. 用户名、密码方面：身份验证失败")
                logger.error(f"   - 数据库用户：{settings.database.user}")
                logger.error("   - 请检查用户名和密码是否正确")
            elif e.orig.errno == 1044:
                logger.error("3. 权限方面：数据库访问权限不足")
                logger.error(f"   - 数据库用户：{settings.database.user}")
                logger.error(f"   - 目标数据库：{settings.database.name}")
                logger.error("   - 请检查用户是否有访问该数据库的权限")
            else:
                logger.error(f"数据库错误：{e}")
        elif isinstance(e.orig, Exception):
            # PostgreSQL错误
            err_str = str(e.orig).lower()
            if "connection refused" in err_str or "could not connect" in err_str:
                logger.error("1. 连接数据库方面：无法连接到数据库服务器")
                logger.error(f"   - 数据库服务器地址：{settings.database.host}:{settings.database.port}")
                logger.error("   - 请检查数据库服务是否启动")
                logger.error("   - 请检查网络连接和防火墙设置")
            elif "password authentication failed" in err_str:
                logger.error("2. 用户名、密码方面：身份验证失败")
                logger.error(f"   - 数据库用户：{settings.database.user}")
                logger.error("   - 请检查用户名和密码是否正确")
            elif "permission denied" in err_str or "privilege" in err_str:
                logger.error("3. 权限方面：数据库访问权限不足")
                logger.error(f"   - 数据库用户：{settings.database.user}")
                logger.error(f"   - 目标数据库：{settings.database.name}")
                logger.error("   - 请检查用户是否有访问该数据库的权限")
            else:
                logger.error(f"数据库错误：{e}")
    else:
        logger.error(f"数据库错误：{e}")

    logger.error("="*60)

async def create_db_engine_and_session(app: FastAPI):
    """创建数据库引擎和会话工厂，并存储在 app.state 中"""
    global _global_session_factory

    try:
        db_url = _get_db_url()
        db_cfg = settings.database
        pool_class = NullPool if db_cfg.pool_type == "NullPool" else AsyncAdaptedQueuePool

        engine_args = {
            "echo": db_cfg.echo,
            "pool_pre_ping": db_cfg.pool_pre_ping,
            "pool_recycle": db_cfg.pool_recycle,
            "poolclass": pool_class,
        }

        # QueuePool 特有参数，NullPool 不需要这些
        if pool_class is AsyncAdaptedQueuePool:
            engine_args.update({
                "pool_size": db_cfg.pool_size,
                "max_overflow": db_cfg.max_overflow,
                "pool_timeout": db_cfg.pool_timeout,
            })

        engine = create_async_engine(db_url, **engine_args)
        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        app.state.db_engine = engine
        app.state.db_session_factory = session_factory
        _global_session_factory = session_factory  # 设置全局变量

        logger.info("数据库引擎和会话工厂创建成功。")
    except Exception as e:
        _log_db_connection_error(f"连接目标数据库 '{settings.database.name}'", e)
        raise DatabaseStartupError("数据库连接失败") from None

async def _create_db_if_not_exists():
    """如果数据库不存在，则使用 SQLAlchemy 引擎创建它。"""
    db_type = settings.database.type.lower()
    db_name = settings.database.name

    if db_type == "mysql":
        server_url = _get_db_url(include_db_name=False)
        check_sql = text(f"SHOW DATABASES LIKE '{db_name}'")
        create_sql = text(f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    elif db_type == "postgresql":
        # 对于PostgreSQL，连接到默认的 'postgres' 数据库来执行创建操作
        server_url = _get_db_url(for_server=True)
        check_sql = text(f"SELECT 1 FROM pg_database WHERE datname = '{db_name}'")
        create_sql = text(f'CREATE DATABASE "{db_name}"')
    else:
        logger.warning(f"不支持为数据库类型 '{db_type}' 自动创建数据库。请确保数据库已手动创建。")
        return

    # 设置隔离级别以允许 DDL 语句
    engine_args = {
        "echo": False,
        "isolation_level": "AUTOCOMMIT"
    }

    engine = create_async_engine(server_url, **engine_args)
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
        _log_db_connection_error("检查或创建数据库时连接服务器", e)
        raise DatabaseStartupError("数据库连接失败") from None
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

async def _is_fresh_database(engine) -> bool:
    """
    检测是否为全新的空数据库（没有任何应用表）。
    通过检查核心表 'config' 是否存在来判断。
    """
    db_type = get_db_type()
    async with engine.connect() as conn:
        if db_type == "mysql":
            sql = text("SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'config'")
        else:  # postgresql
            sql = text("SELECT 1 FROM information_schema.tables WHERE table_name = 'config'")
        result = await conn.execute(sql)
        return result.scalar_one_or_none() is None


async def init_db_tables(app: FastAPI):
    """初始化数据库和表"""
    from .db_maintainer import sync_database_schema
    from .migrations import run_migrations, mark_all_migrations_done

    # 1. 确保数据库存在
    await _create_db_if_not_exists()

    # 2. 创建数据库引擎和会话工厂
    await create_db_engine_and_session(app)

    engine = app.state.db_engine

    # 3. 检测是否为全新数据库（在 create_all 之前检查）
    is_fresh = await _is_fresh_database(engine)

    # 4. 创建所有表（基于 ORM 模型）
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    if is_fresh:
        # 全新数据库：create_all 已按 ORM 模型正确创建所有表，
        # 无需同步字段或执行迁移，只需标记所有迁移为已完成
        logger.info("检测到全新数据库，跳过结构同步和历史迁移。")
        async with engine.begin() as conn:
            await mark_all_migrations_done(conn)
    else:
        # 已有数据库：执行正常的同步和迁移流程
        # 5. 同步数据库结构（自动检测并补充缺失的字段）
        async with engine.begin() as conn:
            await sync_database_schema(conn, settings.database.type.lower())

        # 6. 执行数据库迁移任务
        async with engine.begin() as conn:
            await run_migrations(conn, settings.database.type.lower(), settings.database.name)