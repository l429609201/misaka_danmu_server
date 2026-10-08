"""御坂助手会话和消息的持久化仓储。"""

from datetime import datetime
import json
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, exists, select, update
from sqlalchemy.orm import selectinload

from src.core.timezone import get_now
from src.db.orm_models import AssistantMessage, AssistantPendingAction, AssistantSession
from src.db.repositories.base import BaseRepository


class AssistantSessionRepository(BaseRepository[AssistantSession]):
    """在调用方事务中访问会话和消息，只 flush 不提交。"""

    async def get_by_id(self, id: Any, owner_id: int) -> Optional[AssistantSession]:
        """仅按数据库主键及归属用户读取会话。"""
        return (await self._session.execute(
            select(AssistantSession).where(AssistantSession.id == id, AssistantSession.ownerId == owner_id)
        )).scalar_one_or_none()

    async def get_all(self, owner_id: int, **filters: Any) -> List[AssistantSession]:
        """读取当前用户的会话，可按会话标识过滤。"""
        stmt = select(AssistantSession).where(AssistantSession.ownerId == owner_id)
        if "session_id" in filters:
            stmt = stmt.where(AssistantSession.sessionId == filters["session_id"])
        return list((await self._session.execute(stmt)).scalars().all())

    async def create(self, *, owner_id: int, **data: Any) -> AssistantSession:
        """为指定用户创建会话并取得自增主键。"""
        row = AssistantSession(**data, ownerId=owner_id)
        self._session.add(row)
        await self._session.flush()
        return row

    async def update(self, id: Any, owner_id: int, **data: Any) -> Optional[AssistantSession]:
        """仅按用户归属更新会话，不允许改写归属。"""
        row = await self.get_by_id(id, owner_id)
        if row is not None:
            for key, value in data.items():
                if key in {"ownerId", "owner_id", "sessionId", "id"}:
                    raise ValueError("会话归属和标识不可修改")
                setattr(row, key, value)
            await self._session.flush()
        return row

    async def delete(self, id: Any, owner_id: int) -> bool:
        """仅删除用户自己的会话及消息。"""
        row = await self.get_by_id(id, owner_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def list_summaries(self, limit: int, owner_id: int) -> List[Dict[str, Any]]:
        """按更新时间倒序列出当前用户的会话摘要。"""
        rows = (await self._session.execute(
            select(AssistantSession).where(AssistantSession.ownerId == owner_id)
            .order_by(AssistantSession.updatedAt.desc()).limit(limit)
        )).scalars().all()
        return [{
            "sessionId": row.sessionId,
            "title": row.title or "新对话",
            "persona": row.persona,
            "isProcessing": row.isProcessing,
            "updatedAt": row.updatedAt.isoformat() if row.updatedAt else "",
        } for row in rows]

    async def get_detail(self, sid: str, owner_id: int) -> Optional[Dict[str, Any]]:
        """仅读取当前用户会话及消息，返回可脱离事务的快照。"""
        row = (await self._session.execute(
            select(AssistantSession)
            .where(AssistantSession.sessionId == sid, AssistantSession.ownerId == owner_id)
            .options(selectinload(AssistantSession.messages))
        )).scalar_one_or_none()
        if row is None:
            return None
        messages = []
        for msg in sorted(row.messages, key=lambda item: item.id):
            if msg.role == "event":
                try:
                    event = json.loads(msg.content)
                except (ValueError, TypeError):
                    continue
                if messages and messages[-1]["role"] == "bot" and isinstance(event, dict):
                    messages[-1].setdefault("events", []).append(event)
            elif msg.role in {"user", "bot", "assistant"}:
                messages.append({"role": msg.role, "content": msg.content})
        return {
            "sessionId": row.sessionId,
            "title": row.title,
            "persona": row.persona,
            "isProcessing": row.isProcessing,
            "messages": messages,
        }

    async def begin_stream(self, sid: str, owner_id: int, run_hash: str) -> None:
        """在同一事务中认领会话并建立最新流代次。"""
        await self.mark_processing(sid, True, owner_id)
        await self._session.execute(delete(AssistantPendingAction).where(
            AssistantPendingAction.sessionId == sid,
            AssistantPendingAction.ownerId == owner_id,
            AssistantPendingAction.toolName == "__stream__",
        ))
        await self.issue(run_hash, owner_id, sid, "__stream__", "{}", get_now())

    async def current_stream(
        self, sid: str, owner_id: int, run_hash: str, *, lock: bool = False,
    ) -> bool:
        """检查流代次；提交快照时加锁串行化写入。"""
        stmt = select(AssistantSession).where(
            AssistantSession.sessionId == sid, AssistantSession.ownerId == owner_id,
        )
        if lock:
            stmt = stmt.with_for_update()
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        latest = (await self._session.execute(
            select(AssistantPendingAction.tokenHash).where(
                AssistantPendingAction.sessionId == sid,
                AssistantPendingAction.ownerId == owner_id,
                AssistantPendingAction.toolName == "__stream__",
            ).order_by(AssistantPendingAction.id.desc()).limit(1)
        )).scalar_one_or_none()
        return latest == run_hash

    async def save_stream_snapshot(
        self, sid: str, title: str, messages: List[Dict[str, Any]],
        persona: str, owner_id: int, run_hash: str,
    ) -> bool:
        """原子检查最新流代次并提交其最终展示快照。"""
        if not await self.current_stream(sid, owner_id, run_hash, lock=True):
            return False
        await self.save_snapshot(sid, title, messages, persona, processing=False, owner_id=owner_id)
        return True

    async def mark_processing(self, sid: str, processing: bool, owner_id: int) -> None:
        """标记本用户流状态；不可认领旧会话或复用其他用户的标识。"""
        row = await self._get_by_sid(sid, owner_id)
        if row is None:
            row = await self.create(owner_id=owner_id, sessionId=sid, title="新对话")
        row.isProcessing = processing
        row.updatedAt = get_now()
        await self._session.flush()

    async def save_snapshot(
        self, sid: str, title: str, messages: List[Dict[str, str]],
        persona: Optional[str] = None, *, processing: Optional[bool] = None,
        owner_id: int,
    ) -> None:
        """整体覆盖当前用户消息，保留未指定的人设和原有处理状态。"""
        row = await self._get_by_sid(sid, owner_id)
        if row is None:
            row = await self.create(
                owner_id=owner_id, sessionId=sid, title=title, persona=persona or "misaka_20001",
            )
        else:
            old = await self.get_detail(sid, owner_id)
            old_messages = (old or {}).get("messages", [])
            remaining = iter(old_messages)
            for message in messages:
                if message.get("events") or message.get("role") not in {"bot", "assistant"}:
                    continue
                for previous in remaining:
                    if previous.get("role") == message.get("role") and previous.get("content") == message.get("content"):
                        if previous.get("events"):
                            message["events"] = previous["events"]
                        break
            for old_index, previous in enumerate(old_messages):
                if previous.get("role") != "bot" or previous.get("content"):
                    continue
                choices = [event for event in previous.get("events", []) if event.get("type") == "choice"]
                if not choices:
                    continue
                known = {event.get("id") for item in messages for event in item.get("events", [])
                         if event.get("type") == "choice"}
                if any(event.get("id") not in known for event in choices):
                    last_user = next((i for i in range(len(messages) - 1, -1, -1)
                                      if messages[i].get("role") == "user"), None)
                    earlier_user = next((old_messages[i] for i in range(old_index - 1, -1, -1)
                                         if old_messages[i].get("role") == "user"), None)
                    if last_user is None:
                        index = len(messages)
                    elif earlier_user and messages[last_user].get("content") == earlier_user.get("content"):
                        index = last_user + 1
                    else:
                        index = last_user
                    messages.insert(index, previous)
            if len(old_messages) >= 2 and old_messages[-1].get("role") == "user":
                selected = old_messages[-1]
                preceding = old_messages[-2]
                if (preceding.get("role") == "bot" and
                        any(event.get("type") == "choice" and event.get("selectedOptionId")
                            for event in preceding.get("events", [])) and
                        not any(item.get("role") == "user" and item.get("content") == selected["content"]
                                for item in messages)):
                    index = next((i + 1 for i, item in enumerate(messages)
                                  if item.get("role") == "bot" and
                                  any(event.get("type") == "choice" and event.get("selectedOptionId")
                                      for event in item.get("events", []))), len(messages))
                    messages.insert(index, selected)
            row.title = title
            if persona:
                row.persona = persona
            row.updatedAt = get_now()
            await self._session.execute(
                delete(AssistantMessage).where(AssistantMessage.sessionDbId == row.id)
            )
        for message in messages:
            if message.get("role") not in {"user", "bot", "assistant"}:
                continue
            if message.get("content") or message.get("events"):
                self._session.add(AssistantMessage(
                    sessionDbId=row.id, role=message["role"], content=message.get("content", ""),
                ))
                for event in message.get("events", []):
                    if event.get("type") in {"thinking", "tool", "choice", "text"}:
                        self._session.add(AssistantMessage(
                            sessionDbId=row.id, role="event",
                            content=json.dumps(event, ensure_ascii=False),
                        ))
        if processing is not None:
            row.isProcessing = processing
        await self._session.flush()

    async def delete_by_sid(self, sid: str, owner_id: int) -> bool:
        """仅删除当前用户的公开会话标识；不存在时视为成功。"""
        row = await self._get_by_sid(sid, owner_id, reject_foreign=False)
        if row is None:
            return False
        await self._session.execute(
            delete(AssistantPendingAction).where(
                AssistantPendingAction.sessionId == sid,
                AssistantPendingAction.ownerId == owner_id,
            )
        )
        return await self.delete(row.id, owner_id)

    async def issue(
        self, token_hash: str, owner_id: int, session_id: str,
        tool_name: str, arguments_json: str, expires_at: datetime,
    ) -> AssistantPendingAction:
        """为指定用户和会话记录一次性写工具确认。"""
        action = AssistantPendingAction(
            tokenHash=token_hash, ownerId=owner_id, sessionId=session_id,
            toolName=tool_name, argumentsJson=arguments_json, expiresAt=expires_at,
        )
        self._session.add(action)
        await self._session.flush()
        return action

    async def claim(
        self, token_hash: str, owner_id: int, session_id: str,
        *, expected_tool: Optional[str] = None,
    ) -> Optional[Dict[str, str]]:
        """原子消费未过期令牌，重复确认或归属不匹配时返回空。"""
        now = get_now()
        session_exists = exists(select(AssistantSession.id).where(
            AssistantSession.sessionId == session_id,
            AssistantSession.ownerId == owner_id,
        ))
        row = (await self._session.execute(
            select(AssistantPendingAction).where(
                AssistantPendingAction.tokenHash == token_hash,
                AssistantPendingAction.ownerId == owner_id,
                AssistantPendingAction.sessionId == session_id,
                AssistantPendingAction.consumedAt.is_(None),
                AssistantPendingAction.expiresAt > now,
                session_exists,
            )
        )).scalar_one_or_none()
        if row is None or (expected_tool is not None and row.toolName != expected_tool):
            return None
        if expected_tool is None and row.toolName.startswith("__"):
            return None
        stmt = update(AssistantPendingAction).where(
            AssistantPendingAction.id == row.id,
            AssistantPendingAction.ownerId == owner_id,
            AssistantPendingAction.sessionId == session_id,
            AssistantPendingAction.consumedAt.is_(None),
            AssistantPendingAction.expiresAt > now,
            session_exists,
        )
        if expected_tool is not None:
            stmt = stmt.where(AssistantPendingAction.toolName == expected_tool)
        result = await self._session.execute(stmt.values(consumedAt=now))
        if result.rowcount != 1:
            return None
        await self._session.flush()
        return {"tool_name": row.toolName, "arguments_json": row.argumentsJson}

    async def select_choice(self, sid: str, owner_id: int, choice_id: str, option_id: str) -> bool:
        """在一次性票据消费事务中写回已选状态。"""
        rows = (await self._session.execute(
            select(AssistantMessage).join(AssistantSession, AssistantMessage.sessionDbId == AssistantSession.id)
            .where(AssistantSession.sessionId == sid, AssistantSession.ownerId == owner_id,
                   AssistantMessage.role == "event")
            .order_by(AssistantMessage.id.desc()).with_for_update()
        )).scalars().all()
        for row in rows:
            try:
                event = json.loads(row.content)
            except (ValueError, TypeError):
                continue
            if event.get("type") == "choice" and event.get("id") == choice_id:
                if option_id not in {option.get("id") for option in event.get("options", [])}:
                    return False
                event["selectedOptionId"] = option_id
                row.content = json.dumps(event, ensure_ascii=False)
                await self._session.flush()
                return True
        return False

    async def append_user_message(self, sid: str, owner_id: int, content: str) -> None:
        """锁定归属会话后追加用户选择消息，并跳过相邻重复消息。"""
        row = (await self._session.execute(
            select(AssistantSession).where(
                AssistantSession.sessionId == sid, AssistantSession.ownerId == owner_id,
            ).with_for_update()
        )).scalar_one_or_none()
        if row is None:
            raise PermissionError("会话不存在")
        latest = (await self._session.execute(
            select(AssistantMessage).where(
                AssistantMessage.sessionDbId == row.id,
                AssistantMessage.role.in_(("user", "bot", "assistant")),
            ).order_by(AssistantMessage.id.desc()).limit(1)
        )).scalar_one_or_none()
        if latest is not None and latest.role == "user" and latest.content == content:
            return
        self._session.add(AssistantMessage(sessionDbId=row.id, role="user", content=content))
        row.updatedAt = get_now()
        await self._session.flush()

    async def _get_by_sid(
        self, sid: str, owner_id: int, *, reject_foreign: bool = True,
    ) -> Optional[AssistantSession]:
        """先核对全局唯一 SID，禁止将外部或旧会话静默当作新会话。"""
        result = await self._session.execute(
            select(AssistantSession).where(AssistantSession.sessionId == sid)
        )
        row = result.scalar_one_or_none()
        if row is not None and row.ownerId != owner_id:
            if reject_foreign:
                raise PermissionError("会话不存在")
            return None
        return row
