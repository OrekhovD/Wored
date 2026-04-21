"""Context service — manage conversation state, snapshots, compression."""
from __future__ import annotations

from storage.repositories import (
    ConversationMessageRepository,
    ConversationSessionRepository,
    ContextSnapshotRepository,
)
from storage.models import ConversationMessage, ContextSnapshot


class ContextService:
    def __init__(
        self,
        session_repo: ConversationSessionRepository,
        message_repo: ConversationMessageRepository,
        snapshot_repo: ContextSnapshotRepository,
    ) -> None:
        self._session_repo = session_repo
        self._message_repo = message_repo
        self._snapshot_repo = snapshot_repo

    async def get_or_create_session(self, chat_id: int, user_id: int, mode: str = "free_only") -> str:
        """Return session_id."""
        existing = await self._session_repo.get_active_session_for_user(user_id)
        if existing:
            return existing.session_id

        import uuid
        session_id = f"sess_{uuid.uuid4().hex[:16]}"
        from storage.models import ConversationSession
        obj = ConversationSession(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
            mode=mode,
            active_model="",
        )
        await self._session_repo.create(obj)
        return session_id

    async def add_message(self, session_id: str, role: str, content: str, token_count: int | None = None) -> None:
        from storage.models import ConversationMessage
        msg = ConversationMessage(
            session_id=session_id,
            role=role,
            content=content,
            token_count=token_count,
        )
        await self._message_repo.create(msg)

    async def get_messages(self, session_id: str, limit: int = 50) -> list[ConversationMessage]:
        return await self._message_repo.get_by_session(session_id, limit)

    async def get_conversation_history(self, session_id: str, max_tokens: int | None = None) -> list[dict]:
        msgs = await self._message_repo.get_by_session(session_id, limit=100)
        msgs = list(reversed(msgs))
        if max_tokens is None:
            return [{"role": m.role, "content": m.content} for m in msgs]

        result: list[dict] = []
        tokens = 0
        for m in msgs:
            tc = m.token_count or (len(m.content) // 4)
            if tokens + tc > max_tokens:
                break
            result.append({"role": m.role, "content": m.content})
            tokens += tc
        return list(reversed(result))

    async def save_snapshot(
        self,
        session_id: str,
        summary: str,
        market_facts: str,
        active_mode: str,
        active_model: str,
        compression: str = "none",
    ) -> ContextSnapshot:
        from storage.models import ContextSnapshot
        snap = ContextSnapshot(
            session_id=session_id,
            version="v1",
            summary_text=summary,
            last_market_facts=market_facts,
            active_mode=active_mode,
            active_model=active_model,
            token_budget_state="ok",
            compression_method=compression,
        )
        return await self._snapshot_repo.create(snap)

    async def get_latest_snapshot(self, session_id: str) -> ContextSnapshot | None:
        return await self._snapshot_repo.get_latest(session_id)

    async def compress_context(self, messages: list[dict], max_tokens: int) -> str:
        if not messages:
            return ""
        result: list[str] = []
        tokens = 0
        for m in reversed(messages):
            content = m.get("content", "")
            tc = len(content) // 4
            if tokens + tc > max_tokens:
                break
            result.append(f"{m.get('role', 'unknown')}: {content}")
            tokens += tc
        return "\n".join(reversed(result))

    async def get_context_for_model(self, session_id: str, max_tokens: int = 4000) -> list[dict]:
        return await self.get_conversation_history(session_id, max_tokens)
