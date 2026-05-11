"""
Token Log相关的CRUD操作
"""

import asyncio
import logging
from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..orm_models import TokenAccessLog, UaRule
from ..database import get_session_factory
from src.core.timezone import get_now

logger = logging.getLogger(__name__)


async def _write_token_log_bg(token_id: int, ip_address: str, user_agent: Optional[str], log_status: str, path: Optional[str],
                               method: Optional[str] = None, request_headers: Optional[str] = None,
                               request_body: Optional[str] = None,
                               response_headers: Optional[str] = None,
                               response_body: Optional[str] = None, status_code: Optional[int] = None):
    """后台写入访问日志，使用独立 session，失败静默忽略，不影响主请求。"""
    try:
        session_factory = get_session_factory()
        async with session_factory() as session:
            new_log = TokenAccessLog(
                tokenId=token_id,
                ipAddress=ip_address,
                userAgent=user_agent,
                status=log_status,
                path=path,
                method=method,
                requestHeaders=request_headers,
                requestBody=request_body,
                responseHeaders=response_headers,
                responseBody=response_body,
                statusCode=status_code,
                accessTime=get_now())
            session.add(new_log)
            await session.commit()
    except Exception as e:
        logger.warning(f"后台写入 token_access_log 失败（不影响请求）: {e}")


def create_token_access_log(_session: AsyncSession, token_id: int, ip_address: str, user_agent: Optional[str], log_status: str, path: Optional[str] = None,
                            method: Optional[str] = None, request_headers: Optional[str] = None,
                            request_body: Optional[str] = None,
                            response_headers: Optional[str] = None,
                            response_body: Optional[str] = None, status_code: Optional[int] = None):
    """
    异步写入访问日志。使用 asyncio.create_task 在后台执行，主请求无需等待。
    """
    asyncio.create_task(_write_token_log_bg(token_id, ip_address, user_agent, log_status, path,
                                            method, request_headers, request_body,
                                            response_headers, response_body, status_code))


async def create_token_access_log_awaited(
    token_id: int, ip_address: str, user_agent: Optional[str], log_status: str,
    path: Optional[str] = None, method: Optional[str] = None,
    request_headers: Optional[str] = None, request_body: Optional[str] = None,
) -> Optional[int]:
    """
    写入访问日志并返回 log ID（用于后续中间件回填响应信息）。
    使用独立 session，不阻塞也不污染主请求的 session。
    """
    try:
        session_factory = get_session_factory()
        async with session_factory() as session:
            new_log = TokenAccessLog(
                tokenId=token_id,
                ipAddress=ip_address,
                userAgent=user_agent,
                status=log_status,
                path=path,
                method=method,
                requestHeaders=request_headers,
                requestBody=request_body,
                accessTime=get_now())
            session.add(new_log)
            await session.commit()
            await session.refresh(new_log)
            return new_log.id
    except Exception as e:
        logger.warning(f"写入 token_access_log (awaited) 失败: {e}")
        return None


async def update_token_access_log_response(
    log_id: int,
    status_code: Optional[int] = None,
    response_headers: Optional[str] = None,
    response_body: Optional[str] = None,
):
    """更新 Token 访问日志的响应信息（由中间件调用）。"""
    try:
        session_factory = get_session_factory()
        async with session_factory() as session:
            stmt = select(TokenAccessLog).where(TokenAccessLog.id == log_id)
            result = await session.execute(stmt)
            log_entry = result.scalar_one_or_none()
            if log_entry:
                if status_code is not None:
                    log_entry.statusCode = status_code
                if response_headers is not None:
                    log_entry.responseHeaders = response_headers
                if response_body is not None:
                    log_entry.responseBody = response_body
                await session.commit()
    except Exception as e:
        logger.warning(f"更新 token_access_log 响应信息失败: {e}")


async def get_token_access_logs(session: AsyncSession, token_id: int) -> List[Dict[str, Any]]:
    stmt = select(TokenAccessLog).where(TokenAccessLog.tokenId == token_id).order_by(TokenAccessLog.accessTime.desc()).limit(200)
    result = await session.execute(stmt)
    return [
        {
            "ipAddress": log.ipAddress, "userAgent": log.userAgent,
            "accessTime": log.accessTime, "status": log.status, "path": log.path,
            "method": log.method, "requestHeaders": log.requestHeaders,
            "requestBody": log.requestBody, "responseHeaders": log.responseHeaders,
            "responseBody": log.responseBody, "statusCode": log.statusCode,
        }
        for log in result.scalars()
    ]


async def get_ua_rules(session: AsyncSession) -> List[Dict[str, Any]]:
    stmt = select(UaRule).order_by(UaRule.createdAt.desc())
    result = await session.execute(stmt)
    return [{"id": r.id, "uaString": r.uaString, "createdAt": r.createdAt} for r in result.scalars()]


async def add_ua_rule(session: AsyncSession, ua_string: str) -> int:
    new_rule = UaRule(uaString=ua_string, createdAt=get_now())
    session.add(new_rule)
    await session.commit()
    return new_rule.id


async def delete_ua_rule(session: AsyncSession, rule_id: int) -> bool:
    rule = await session.get(UaRule, rule_id)
    if rule:
        await session.delete(rule)
        await session.commit()
        return True
    return False

