"""Async repository classes for all ORM entities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Generic, TypeVar

from sqlalchemy import Float, Integer, Result, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from storage.database import async_session_factory
from storage.models import (
    AdminEvent,
    Base,
    ContextHandoff,
    ContextSnapshot,
    ConversationMessage,
    ConversationSession,
    HTXMarketSnapshot,
    ModelRegistry,
    ProviderHealthEvent,
    ProviderRegistry,
    QuotaState,
    RouteDecision,
    TelegramChat,
    UsageRecord,
    User,
)

T = TypeVar("T", bound=Base)


class BaseRepo(Generic[T]):
    """Generic async CRUD repo."""

    def __init__(self, model: type[T]) -> None:
        self._model = model

    async def find_by_id(self, id_: int, session: AsyncSession | None = None) -> T | None:
        sess = session or async_session_factory()
        if session is None:
            async with sess as s:
                return await s.get(self._model, id_)
        return await sess.get(self._model, id_)

    async def list(
        self,
        limit: int = 50,
        offset: int = 0,
        order_by: str = "id",
        desc: bool = True,
        session: AsyncSession | None = None,
    ) -> list[T]:
        sess = session or async_session_factory()
        col = getattr(self._model, order_by)
        stmt = select(self._model).order_by(col.desc() if desc else col.asc()).limit(limit).offset(offset)
        if session is None:
            async with sess as s:
                result = await s.execute(stmt)
                return list(result.scalars().all())
        result = await sess.execute(stmt)
        return list(result.scalars().all())

    async def create(self, obj_: T, session: AsyncSession | None = None) -> T:
        sess = session or async_session_factory()
        if session is None:
            async with sess as s:
                s.add(obj_)
                await s.commit()
                await s.refresh(obj_)
                return obj_
        session.add(obj_)
        await session.flush()
        return obj_

    async def update(self, obj_id: int, **fields: Any) -> T | None:
        async with async_session_factory() as sess:
            obj = await sess.get(self._model, obj_id)
            if obj is None:
                return None
            for k, v in fields.items():
                if hasattr(obj, k):
                    setattr(obj, k, v)
            await sess.commit()
            await sess.refresh(obj)
            return obj

    async def delete(self, obj_id: int, session: AsyncSession | None = None) -> bool:
        sess = session or async_session_factory()
        if session is None:
            async with sess as s:
                obj = await s.get(self._model, obj_id)
                if obj:
                    await s.delete(obj)
                    await s.commit()
                    return True
                return False
        obj = await session.get(self._model, obj_id)
        if obj:
            await session.delete(obj)
            return True
        return False


class UserRepository(BaseRepo[User]):
    def __init__(self, session_factory=None) -> None:
        """Use session_factory if provided, otherwise implicit."""
        super().__init__(User)
        self._sf = session_factory or async_session_factory

    async def get_by_telegram_id(self, tid: int) -> User | None:
        async with self._sf() as s:
            stmt = select(User).where(User.telegram_id == tid)
            r = await s.execute(stmt)
            return r.scalar_one_or_none()

    async def create_or_update(
        self,
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> User:
        async with self._sf() as s:
            stmt = select(User).where(User.telegram_id == telegram_id)
            user = (await s.execute(stmt)).scalar_one_or_none()
            if user:
                if username is not None:
                    user.username = username
                if first_name is not None:
                    user.first_name = first_name
                if last_name is not None:
                    user.last_name = last_name
                await s.commit()
                await s.refresh(user)
                return user
            
            new_user = User(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
            )
            s.add(new_user)
            await s.commit()
            await s.refresh(new_user)
            return new_user


class TelegramChatRepository(BaseRepo[TelegramChat]):
    def __init__(self, session_factory=None) -> None:
        super().__init__(TelegramChat)
        self._sf = session_factory or async_session_factory

    async def get_by_chat_id(self, cid: int) -> TelegramChat | None:
        async with self._sf() as s:
            return (await s.execute(select(TelegramChat).where(TelegramChat.chat_id == cid))).scalar_one_or_none()

    async def create(self, chat_id: int, chat_type: str, user_id: int, title: str | None = None, session: AsyncSession | None = None) -> TelegramChat:
        chat = TelegramChat(chat_id=chat_id, chat_type=chat_type, user_id=user_id, title=title)
        return await super().create(chat, session=session)


class ConversationSessionRepository(BaseRepo[ConversationSession]):
    def __init__(self, session_factory=None) -> None:
        super().__init__(ConversationSession)
        self._sf = session_factory or async_session_factory

    async def get_by_session_id(self, sid: str) -> ConversationSession | None:
        async with self._sf() as s:
            return (await s.execute(select(ConversationSession).where(ConversationSession.session_id == sid))).scalar_one_or_none()

    async def get_active_session_for_user(self, user_id: int) -> ConversationSession | None:
        async with self._sf() as s:
            stmt = select(ConversationSession).where(
                ConversationSession.user_id == user_id,
                ConversationSession.closed_at.is_(None),
            ).order_by(ConversationSession.updated_at.desc()).limit(1)
            r = await s.execute(stmt)
            return r.scalar_one_or_none()

    async def get_active_session(self, user_id: int, chat_id: int) -> ConversationSession | None:
        async with self._sf() as s:
            stmt = select(ConversationSession).where(
                ConversationSession.user_id == user_id,
                ConversationSession.chat_id == chat_id,
                ConversationSession.closed_at.is_(None)
            ).order_by(ConversationSession.updated_at.desc()).limit(1)
            r = await s.execute(stmt)
            return r.scalar_one_or_none()

    async def create_session(self, session_id: str, user_id: int, chat_id: int, mode: str = "free_only", active_model: str = "") -> ConversationSession:
        session_obj = ConversationSession(
            session_id=session_id,
            user_id=user_id,
            chat_id=chat_id,
            routing_mode=mode,
            active_model=active_model
        )
        return await super().create(session_obj)

    async def update_mode(self, session_id: str, mode: str) -> None:
        async with self._sf() as s:
            stmt = select(ConversationSession).where(ConversationSession.session_id == session_id)
            obj = (await s.execute(stmt)).scalar_one_or_none()
            if obj:
                obj.routing_mode = mode
                await s.commit()

    async def update_model(self, session_id: str, model: str) -> None:
        async with self._sf() as s:
            stmt = select(ConversationSession).where(ConversationSession.session_id == session_id)
            obj = (await s.execute(stmt)).scalar_one_or_none()
            if obj:
                obj.active_model = model
                await s.commit()

    async def close_session(self, sid: str) -> bool:
        async with self._sf() as s:
            stmt = select(ConversationSession).where(ConversationSession.session_id == sid)
            obj = (await s.execute(stmt)).scalar_one_or_none()
            if obj:
                obj.closed_at = datetime.utcnow()
                await s.commit()
                return True
            return False

    async def update_by_field(self, field: str, value: Any, **fields: Any) -> ConversationSession | None:
        async with self._sf() as s:
            stmt = select(ConversationSession).where(getattr(ConversationSession, field) == value)
            r = await s.execute(stmt)
            obj = r.scalar_one_or_none()
            if obj is None:
                return None
            for k, v in fields.items():
                setattr(obj, k, v)
            await s.commit()
            await s.refresh(obj)
            return obj


class ConversationMessageRepository(BaseRepo[ConversationMessage]):
    def __init__(self, session_factory=None) -> None:
        super().__init__(ConversationMessage)
        self._sf = session_factory or async_session_factory

    async def get_by_session(self, session_id: str, limit: int = 50) -> list[ConversationMessage]:
        return await self.get_history(session_id, limit)

    async def get_history(self, session_id: str, limit: int = 50) -> list[ConversationMessage]:
        async with self._sf() as s:
            stmt = select(ConversationMessage).where(
                ConversationMessage.session_id == session_id
            ).order_by(ConversationMessage.created_at.desc()).limit(limit)
            r = await s.execute(stmt)
            return list(r.scalars().all())

    async def count_tokens(self, session_id: str) -> int:
        async with self._sf() as s:
            stmt = select(func.coalesce(func.sum(ConversationMessage.token_count), 0)).where(
                ConversationMessage.session_id == session_id
            )
            r = await s.execute(stmt)
            return int(r.scalar_one())

    async def count_messages(self, session_id: str) -> int:
        async with self._sf() as s:
            stmt = select(func.count(ConversationMessage.id)).where(
                ConversationMessage.session_id == session_id
            )
            r = await s.execute(stmt)
            return int(r.scalar_one())

    async def add_message(self, session_id: str, role: str, content: str, token_count: int | None = None) -> ConversationMessage:
        msg = ConversationMessage(
            session_id=session_id,
            role=role,
            content=content,
            token_count=token_count
        )
        return await super().create(msg)


class ContextSnapshotRepository(BaseRepo[ContextSnapshot]):
    def __init__(self, session_factory=None) -> None:
        super().__init__(ContextSnapshot)
        self._sf = session_factory or async_session_factory

    async def get_latest(self, session_id: str) -> ContextSnapshot | None:
        async with self._sf() as s:
            stmt = select(ContextSnapshot).where(
                ContextSnapshot.session_id == session_id
            ).order_by(ContextSnapshot.created_at.desc()).limit(1)
            r = await s.execute(stmt)
            return r.scalar_one_or_none()


class ContextHandoffRepository(BaseRepo[ContextHandoff]):
    def __init__(self, session_factory=None) -> None:
        super().__init__(ContextHandoff)
        self._sf = session_factory or async_session_factory

    async def recent(self, limit: int = 20) -> list[ContextHandoff]:
        async with self._sf() as s:
            stmt = select(ContextHandoff).order_by(ContextHandoff.created_at.desc()).limit(limit)
            return list((await s.execute(stmt)).scalars().all())


class ProviderRegistryRepository(BaseRepo[ProviderRegistry]):
    def __init__(self, session_factory=None) -> None:
        super().__init__(ProviderRegistry)
        self._sf = session_factory or async_session_factory

    async def get_by_provider_id(self, pid: str) -> ProviderRegistry | None:
        async with self._sf() as s:
            return (await s.execute(select(ProviderRegistry).where(ProviderRegistry.provider_id == pid))).scalar_one_or_none()


class ModelRegistryRepository(BaseRepo[ModelRegistry]):
    def __init__(self, session_factory=None) -> None:
        super().__init__(ModelRegistry)
        self._sf = session_factory or async_session_factory

    async def get_by_model_id(self, mid: str) -> ModelRegistry | None:
        async with self._sf() as s:
            return (await s.execute(select(ModelRegistry).where(ModelRegistry.model_id == mid))).scalar_one_or_none()

    async def get_all(self) -> list[ModelRegistry]:
        async with self._sf() as s:
            r = await s.execute(select(ModelRegistry))
            return list(r.scalars().all())

    async def get_by_provider(self, pid: str) -> list[ModelRegistry]:
        async with self._sf() as s:
            r = await s.execute(select(ModelRegistry).where(ModelRegistry.provider_id == pid))
            return list(r.scalars().all())


class ProviderHealthEventRepository(BaseRepo[ProviderHealthEvent]):
    def __init__(self, session_factory=None) -> None:
        super().__init__(ProviderHealthEvent)
        self._sf = session_factory or async_session_factory

    async def latest_health(self, pid: str) -> ProviderHealthEvent | None:
        async with self._sf() as s:
            stmt = select(ProviderHealthEvent).where(
                ProviderHealthEvent.provider_id == pid
            ).order_by(ProviderHealthEvent.checked_at.desc()).limit(1)
            r = await s.execute(stmt)
            return r.scalar_one_or_none()

    async def get_latest(self, pid: str) -> ProviderHealthEvent | None:
        return await self.latest_health(pid)

    async def record_event(self, provider_id: str, event_type: str, is_healthy: bool, details: str | None = None) -> None:
        event = ProviderHealthEvent(
            provider_id=provider_id,
            event_type=event_type,
            is_healthy=is_healthy,
            details=details,
        )
        await super().create(event)


class UsageRecordRepository(BaseRepo[UsageRecord]):
    def __init__(self, session_factory=None) -> None:
        super().__init__(UsageRecord)
        self._sf = session_factory or async_session_factory

    async def by_user(self, user_id: int, start: datetime, end: datetime) -> list[UsageRecord]:
        async with self._sf() as s:
            stmt = select(UsageRecord).where(
                UsageRecord.telegram_user_id == user_id,
                UsageRecord.created_at >= start,
                UsageRecord.created_at <= end,
            ).order_by(UsageRecord.created_at.desc())
            r = await s.execute(stmt)
            return list(r.scalars().all())

    async def by_provider(self, pid: str, start: datetime, end: datetime) -> list[UsageRecord]:
        async with self._sf() as s:
            stmt = select(UsageRecord).where(
                UsageRecord.provider_id == pid,
                UsageRecord.created_at >= start,
                UsageRecord.created_at <= end,
            ).order_by(UsageRecord.created_at.desc())
            r = await s.execute(stmt)
            return list(r.scalars().all())

    async def by_model(self, mid: str, start: datetime, end: datetime) -> list[UsageRecord]:
        async with self._sf() as s:
            stmt = select(UsageRecord).where(
                UsageRecord.model_id == mid,
                UsageRecord.created_at >= start,
                UsageRecord.created_at <= end,
            ).order_by(UsageRecord.created_at.desc())
            r = await s.execute(stmt)
            return list(r.scalars().all())

    async def by_status(self, status: str, limit: int = 20) -> list[UsageRecord]:
        async with self._sf() as s:
            stmt = select(UsageRecord).where(
                UsageRecord.status == status
            ).order_by(UsageRecord.created_at.desc()).limit(limit)
            r = await s.execute(stmt)
            return list(r.scalars().all())

    async def aggregate_by_user(self, user_id: int, start: datetime, end: datetime) -> dict:
        async with self._sf() as s:
            if user_id > 0:
                stmt = select(
                    func.count(UsageRecord.id),
                    func.sum(UsageRecord.input_tokens),
                    func.sum(UsageRecord.output_tokens),
                    func.sum(UsageRecord.cost_estimate),
                ).where(
                    UsageRecord.telegram_user_id == user_id,
                    UsageRecord.created_at >= start,
                    UsageRecord.created_at <= end,
                )
            else: # All users aggregation
                stmt = select(
                    func.count(UsageRecord.id),
                    func.sum(UsageRecord.input_tokens),
                    func.sum(UsageRecord.output_tokens),
                    func.sum(UsageRecord.cost_estimate),
                ).where(
                    UsageRecord.created_at >= start,
                    UsageRecord.created_at <= end,
                )
                
            r = await s.execute(stmt)
            row = r.one()
            return {
                "requests": row[0] or 0,
                "input_tokens": row[1] or 0,
                "output_tokens": row[2] or 0,
                "cost": row[3] or 0.0,
            }

    async def error_rate(self, user_id: int, start: datetime, end: datetime) -> float:
        async with self._sf() as s:
            total = select(func.count(UsageRecord.id)).where(
                UsageRecord.telegram_user_id == user_id,
                UsageRecord.created_at >= start,
                UsageRecord.created_at <= end,
            )
            errors = select(func.count(UsageRecord.id)).where(
                UsageRecord.telegram_user_id == user_id,
                UsageRecord.created_at >= start,
                UsageRecord.created_at <= end,
                UsageRecord.status != "success",
            )
            t = (await s.execute(total)).scalar_one() or 0
            e = (await s.execute(errors)).scalar_one() or 0
            return e / t if t > 0 else 0.0

    async def recent_fallbacks(self, limit: int = 20) -> list[UsageRecord]:
        async with self._sf() as s:
            stmt = select(UsageRecord).where(UsageRecord.fallback_triggered == True).order_by(UsageRecord.created_at.desc()).limit(limit)
            return list((await s.execute(stmt)).scalars().all())

    async def recent_handoffs(self, limit: int = 20) -> list[UsageRecord]:
        async with self._sf() as s:
            stmt = select(UsageRecord).where(UsageRecord.context_handoff_triggered == True).order_by(UsageRecord.created_at.desc()).limit(limit)
            return list((await s.execute(stmt)).scalars().all())


class QuotaStateRepository(BaseRepo[QuotaState]):
    def __init__(self, session_factory=None) -> None:
        super().__init__(QuotaState)
        self._sf = session_factory or async_session_factory

    async def get_for_provider_model(self, pid: str, mid: str) -> QuotaState | None:
        async with self._sf() as s:
            stmt = select(QuotaState).where(
                QuotaState.provider_id == pid,
                QuotaState.model_id == mid,
            )
            r = await s.execute(stmt)
            return r.scalar_one_or_none()

    async def update(self, obj_id: int, **fields: Any) -> QuotaState | None:
        return await super().update(obj_id, **fields)

    async def get_current_status(self, provider_id: str, model_id: str) -> dict:
        obj = await self.get_for_provider_model(provider_id, model_id)
        if not obj:
            return {"remaining_pct": 100.0}
        return {"remaining_pct": obj.remaining_pct, "used_tokens": obj.used_tokens, "limit_tokens": obj.limit_tokens}


class RouteDecisionRepository(BaseRepo[RouteDecision]):
    def __init__(self, session_factory=None) -> None:
        super().__init__(RouteDecision)
        self._sf = session_factory or async_session_factory

    async def recent(self, limit: int = 20) -> list[RouteDecision]:
        async with self._sf() as s:
            stmt = select(RouteDecision).order_by(RouteDecision.created_at.desc()).limit(limit)
            return list((await s.execute(stmt)).scalars().all())


class HTXMarketSnapshotRepository(BaseRepo[HTXMarketSnapshot]):
    def __init__(self, session_factory=None) -> None:
        super().__init__(HTXMarketSnapshot)
        self._sf = session_factory or async_session_factory

    async def save_snapshot(self, symbol: str, data_type: str, raw_data: str) -> None:
        snap = HTXMarketSnapshot(symbol=symbol, data_type=data_type, raw_data=raw_data)
        await super().create(snap)

    async def get_latest(self, symbol: str, data_type: str) -> HTXMarketSnapshot | None:
        async with self._sf() as s:
            stmt = select(HTXMarketSnapshot).where(
                HTXMarketSnapshot.symbol == symbol,
                HTXMarketSnapshot.data_type == data_type
            ).order_by(HTXMarketSnapshot.created_at.desc()).limit(1)
            return (await s.execute(stmt)).scalar_one_or_none()


class AdminEventRepository(BaseRepo[AdminEvent]):
    def __init__(self, session_factory=None) -> None:
        super().__init__(AdminEvent)
        self._sf = session_factory or async_session_factory

    async def log_event(self, event_type: str, admin_id: int | None = None, details: str | None = None) -> None:
        event = AdminEvent(event_type=event_type, admin_id=admin_id, details=details)
        await super().create(event)

    async def recent(self, limit: int = 50) -> list[AdminEvent]:
        async with self._sf() as s:
            stmt = select(AdminEvent).order_by(AdminEvent.created_at.desc()).limit(limit)
            return list((await s.execute(stmt)).scalars().all())
