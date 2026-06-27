"""
Bangumi Token 定时刷新任务

参考 ani-rss 实现：后台定时检查所有用户的 Bangumi OAuth token，
即使 token 已过期，只要 refresh_token 仍有效就尝试续期。

与现有被动刷新（用户请求时触发）的核心差异：
- 已过期的 token 也会尝试刷新，不会因为 isAuthenticated=False 而跳过
- 全局生效，不依赖用户主动访问
"""
import logging
from datetime import timedelta

from fastapi import FastAPI
from sqlalchemy import select

from .base import BasePollingTask

logger = logging.getLogger("BgmTokenRefresh")

# token 剩余天数 <= 此值时触发刷新（参考 ani-rss 的 3 天阈值）
REFRESH_THRESHOLD_DAYS = 3


class BgmTokenRefreshTask(BasePollingTask):
    """Bangumi OAuth Token 定时刷新"""
    name = "bgm_token_refresh"
    enabled_key = ""
    interval_key = ""
    default_interval = 720   # 12 小时（分钟）
    min_interval = 60        # 最小 1 小时
    startup_delay = 120      # 启动后 2 分钟开始

    @staticmethod
    async def handler(app: FastAPI) -> None:
        await _bgm_token_refresh_handler(app)


async def _bgm_token_refresh_handler(app: FastAPI) -> None:
    """
    遍历所有持有 refresh_token 的 BangumiAuth 记录，
    对即将过期或已过期的 token 执行刷新。
    """
    from src.db import orm_models
    from src.core import get_now, settings

    session_factory = app.state.db_session_factory
    config_manager = app.state.config_manager

    # 读取 OAuth 配置
    client_id = await config_manager.get("bangumiClientId", "")
    client_secret = await config_manager.get("bangumiClientSecret", "")
    if not client_id or not client_secret:
        logger.debug("Bangumi OAuth 未配置 (缺少 client_id/client_secret)，跳过刷新")
        return

    # 构造 redirect_uri（后台刷新时 bgm.tv 要求 redirect_uri 必填）
    # 优先用自定义域名；没配置时回退 localhost 并标记，供刷新函数判断是否打 warning。
    base_url = await config_manager.get("webhookCustomDomain", "")
    is_localhost_fallback = False
    if not base_url:
        base_url = f"http://localhost:{settings.server.port}"
        is_localhost_fallback = True
    redirect_uri = f"{base_url.rstrip('/')}/bgm-oauth-callback"

    oauth_config = {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "_is_localhost_fallback": is_localhost_fallback,
    }

    # 获取 Bangumi 代理配置（仅当 bangumi 元数据源开启 useProxy 时）
    from src.db import crud
    proxy = None
    proxy_mode = await config_manager.get("proxyMode", "none")
    if proxy_mode == "none" and (await config_manager.get("proxyEnabled", "false")).lower() == "true":
        proxy_mode = "http_socks"
    if proxy_mode == "http_socks":
        proxy_url = await config_manager.get("proxyUrl", "")
        if proxy_url:
            async with session_factory() as _s:
                ms_settings = await crud.get_all_metadata_source_settings(_s)
                bgm_s = next((s for s in ms_settings if s.get('providerName') == 'bangumi'), None)
                if bgm_s and bgm_s.get('useProxy', False):
                    proxy = proxy_url
    oauth_config["proxy"] = proxy

    now = get_now()

    async with session_factory() as session:
        # 查询所有持有 refresh_token 的记录
        stmt = select(orm_models.BangumiAuth).where(
            orm_models.BangumiAuth.refreshToken.isnot(None),
            orm_models.BangumiAuth.refreshToken != "",
        )
        result = await session.execute(stmt)
        auth_records = result.scalars().all()

        if not auth_records:
            logger.debug("没有需要刷新的 Bangumi token")
            return

        refreshed = 0
        skipped = 0
        failed = 0

        for auth in auth_records:
            needs_refresh = False

            # 优先用 bgm.tv token_status 实时查真实剩余时间（参考 ani-rss），
            # 不信任本地 expiresAt（可能因时区/未存 expires_in 而算错）。
            from src.metadata_sources.bangumi import _get_bgm_token_status
            remaining_seconds = None
            if auth.accessToken:
                remaining_seconds = await _get_bgm_token_status(auth.accessToken, proxy)

            if remaining_seconds is not None:
                # 远程查询成功：剩余 <= 3 天（含已过期的负数）则刷新
                days_left = remaining_seconds // 86400
                if remaining_seconds <= REFRESH_THRESHOLD_DAYS * 86400:
                    needs_refresh = True
                    if remaining_seconds <= 0:
                        logger.info(f"Bangumi token 已过期 (用户ID: {auth.userId})，尝试 refresh_token 续期")
                    else:
                        logger.info(f"Bangumi token 即将过期 (用户ID: {auth.userId}, 剩余 {days_left} 天)，执行刷新")
            else:
                # 远程查询失败（网络/接口异常）：回退到本地 expiresAt 判断
                if not auth.expiresAt:
                    needs_refresh = True
                elif auth.expiresAt < now:
                    needs_refresh = True
                    logger.info(f"Bangumi token 已过期 (用户ID: {auth.userId}，本地判断)，尝试 refresh_token 续期")
                else:
                    days_left = (auth.expiresAt - now).days
                    if days_left <= REFRESH_THRESHOLD_DAYS:
                        needs_refresh = True
                        logger.info(f"Bangumi token 即将过期 (用户ID: {auth.userId}, 剩余 {days_left} 天，本地判断)，执行刷新")

            if not needs_refresh:
                skipped += 1
                continue

            # 复用现有的刷新逻辑（内部已自行 commit）
            from src.metadata_sources.bangumi import _refresh_bangumi_token

            success = await _refresh_bangumi_token(session, auth.userId, oauth_config)
            if success:
                refreshed += 1
            else:
                failed += 1

        # _refresh_bangumi_token 内部已 commit，这里不再重复提交

        if refreshed > 0 or failed > 0:
            logger.info(
                f"Bangumi token 刷新完成: 成功 {refreshed}, 跳过 {skipped}, 失败 {failed}"
            )

