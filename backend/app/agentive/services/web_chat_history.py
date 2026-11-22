"""Unmounted bounded history reader over completed Web Chat turn receipts."""

from __future__ import annotations

from typing import Awaitable, Callable

from jvspatial.db.postgres import PostgresDB

from app.agentive.connectors.external_chat import (
    ExternalChatAssignment,
    ExternalChatEventKey,
    VerifiedExternalChatMessage,
)
from app.agentive.connectors.web_chat_turn_store import (
    history_event_record_id,
    history_scope_digest,
    open_history_receipt,
)
from app.agentive.services.web_chat_admission import (
    FullPageWebChatAdmission,
    VerifiedFullPageSession,
    WorkspaceReadiness,
)
from app.agentive.services.web_chat_browser_transport import BrowserWebChatHistory
from app.agentive.services.web_chat_profile_inference import (
    MAX_HISTORY_CHARACTERS,
    MAX_HISTORY_EXCHANGES,
    StoredWebChatExchange,
    WebChatHistorySnapshot,
    _text,
)


class PostgresWebChatHistory:
    """Current-session history; reads never mutate, enqueue, or run inference."""

    def __init__(
        self,
        *,
        database: PostgresDB,
        read_verified_session: Callable[[], Awaitable[VerifiedFullPageSession | None]],
        readiness: WorkspaceReadiness | None = None,
    ) -> None:
        """Require a request-bound session reader and the shared receipt database."""
        if not isinstance(database, PostgresDB):
            raise TypeError("Web Chat history requires PostgreSQL")
        self._database = database
        self._admission = FullPageWebChatAdmission(
            read_verified_session, readiness=readiness
        )
        self.ports = BrowserWebChatHistory(prior=self.prior, recent=self.recent)

    async def _read(
        self,
        session: VerifiedFullPageSession,
        assignment: ExternalChatAssignment,
        *,
        exclude: str | None = None,
        include: str | None = None,
    ) -> WebChatHistorySnapshot:
        conversation, visitor = session.conversation, session.scope.visitor_id
        if (
            session.scope.workspace_id != conversation.provision.workspace_id
            or session.scope.profile_key != assignment.profile_key
            or str(session.scope.published_version) != assignment.publication_revision
            or str(session.scope.route_generation) != assignment.route_epoch
        ):
            raise ValueError("Web Chat history unavailable")
        # This is an internal authority check only, not a submitted event.
        check = VerifiedExternalChatMessage(
            ExternalChatEventKey(conversation, "history-read"),
            visitor,
            "Check current history scope.",
        )
        if await self._admission.text_only_assignment(check) != assignment:
            raise ValueError("Web Chat history unavailable")
        digest = history_scope_digest(conversation, assignment, visitor)
        query: dict = {
            "entity": "WebChatTurnReceipt",
            "context.history_scope_digest": digest,
            "context.history_completed_at": {"$gt": ""},
        }
        if exclude is not None:
            query["id"] = {
                "$ne": history_event_record_id(
                    ExternalChatEventKey(conversation, exclude)
                )
            }
        rows = await self._database.find(
            "object",
            query,
            limit=MAX_HISTORY_EXCHANGES,
            sort=[("context.history_completed_at", -1), ("id", -1)],
        )
        required_id = None
        if include is not None:
            required_id = history_event_record_id(
                ExternalChatEventKey(conversation, include)
            )
            if not any(row["id"] == required_id for row in rows):
                target = await self._database.get("object", required_id)
                if target is not None:
                    state = target["context"]
                    if state.get("history_scope_digest") == digest and state.get(
                        "history_completed_at"
                    ):
                        rows.append(target)
        decoded = []
        for row in rows:
            stamp, event, text, reply = open_history_receipt(
                row, conversation, assignment, visitor
            )
            exchange = StoredWebChatExchange(
                event, _text(text, 1000), _text(reply, 4000)
            )
            decoded.append(((stamp, row["id"]), exchange))

        # Keep the requested completed reply when available, even after a long
        # gap, followed by the newest remaining exchanges within both caps.
        selected = []
        total = 0
        for order, exchange in sorted(
            decoded, key=lambda item: (item[0][1] == required_id, item[0]), reverse=True
        ):
            cost = len(exchange.visitor_text) + len(exchange.assistant_text)
            if total + cost <= MAX_HISTORY_CHARACTERS:
                selected.append((order, exchange))
                total += cost
            if len(selected) >= MAX_HISTORY_EXCHANGES:
                break
        if await self._admission.text_only_assignment(check) != assignment:
            raise ValueError("Web Chat history unavailable")
        return WebChatHistorySnapshot(
            conversation,
            visitor,
            assignment,
            tuple(
                exchange for _, exchange in sorted(selected, key=lambda item: item[0])
            ),
        )

    async def prior(
        self,
        session: VerifiedFullPageSession,
        assignment: ExternalChatAssignment,
        event: ExternalChatEventKey,
    ) -> WebChatHistorySnapshot:
        """Return the completed tail before inference, excluding this exact event."""
        if event.conversation != session.conversation:
            raise ValueError("Web Chat history unavailable")
        return await self._read(session, assignment, exclude=event.external_event_id)

    async def recent(
        self,
        session: VerifiedFullPageSession,
        assignment: ExternalChatAssignment,
        include_event_id: str | None,
    ) -> WebChatHistorySnapshot:
        """Return recent completed exchanges, including the exact retry if settled."""
        return await self._read(session, assignment, include=include_event_id)
