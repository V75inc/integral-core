"""Non-serving browser contract for the full-page Web Chat component."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Awaitable, Callable, Literal

from app.agentive.connectors.external_chat import (
    ExternalChatAssignment,
    ExternalChatProvision,
)
from app.agentive.connectors.web_chat_turn import WebChatTurnStore
from app.agentive.services import public_web_chat
from app.agentive.services.web_chat_admission import (
    VerifiedFullPageSession,
    WorkspaceReadiness,
)
from app.agentive.services.web_chat_profile_inference import (
    MAX_HISTORY_CHARACTERS,
    MAX_HISTORY_EXCHANGES,
    FullPageWebChatProfileInference,
    HistoryReader,
    StoredWebChatExchange,
    TextModel,
    WebChatHistorySnapshot,
    _text,
)

StartMode = Literal["new", "resume"]
SessionKind = Literal["current", "expired", "unavailable"]
Operation = Literal["start", "send"]
MessageRole = Literal["visitor", "assistant"]
ReplyKind = Literal["ready", "pending", "expired", "unavailable"]
_EVENT_ID = re.compile(r"[a-zA-Z0-9_-]{1,128}")
_TEXT_LIMIT = 1_000
_REPLY_LIMIT = 4_000


@dataclass(frozen=True)
class BrowserSessionAccess:
    """Private result of host-owned opaque credential and route checks."""

    kind: SessionKind
    session: VerifiedFullPageSession | None = None


@dataclass(frozen=True)
class BrowserVisitorSessions:
    """Request-bound verified sessions; no cookie or token goes to this service.

    On first access, resume may open a new conversation. A stale credential
    expires rather than silently changing visitors. New rotates to an empty
    conversation and retires any prior one according to the host's policy.
    """

    open: Callable[[StartMode], Awaitable[BrowserSessionAccess]]
    current: Callable[[], Awaitable[BrowserSessionAccess]]


ReadRecent = Callable[
    [VerifiedFullPageSession, ExternalChatAssignment, str | None],
    Awaitable[WebChatHistorySnapshot],
]


@dataclass(frozen=True)
class BrowserWebChatHistory:
    """Durable same-visitor history, in chronological order within the cap."""

    prior: HistoryReader
    # Third arg of recent is the just submitted event to include if complete.
    recent: ReadRecent


RequestBudget = Callable[
    [Operation, ExternalChatProvision, VerifiedFullPageSession | None], Awaitable[bool]
]


@dataclass(frozen=True)
class BrowserWebChatMessage:
    """Stable presentation ID and literal text only."""

    id: str
    role: MessageRole
    text: str


@dataclass(frozen=True)
class BrowserWebChatReply:
    """Minimal response accepted by PublicWebChatTransport."""

    kind: ReplyKind
    messages: tuple[BrowserWebChatMessage, ...] = ()

    def to_wire(self) -> dict:
        """Do not include scope IDs, credentials, fences, or errors in JSON."""
        if self.kind == "ready":
            return {
                "kind": self.kind,
                "messages": [asdict(msg) for msg in self.messages],
            }
        return {"kind": self.kind}


class _SessionExpired(Exception):
    pass


def _display(
    snapshot: WebChatHistorySnapshot,
    session: VerifiedFullPageSession,
    assignment: ExternalChatAssignment,
) -> tuple[tuple[BrowserWebChatMessage, ...], dict[str, tuple[str, str]]]:
    if (
        not isinstance(snapshot, WebChatHistorySnapshot)
        or snapshot.conversation != session.conversation
        or snapshot.visitor_id != session.scope.visitor_id
        or snapshot.assignment != assignment
        or not isinstance(snapshot.exchanges, tuple)
        or len(snapshot.exchanges) > MAX_HISTORY_EXCHANGES
    ):
        raise ValueError("Invalid visitor history")
    messages: list[BrowserWebChatMessage] = []
    exchanges: dict[str, tuple[str, str]] = {}
    characters = 0
    for exchange in snapshot.exchanges:
        if (
            not isinstance(exchange, StoredWebChatExchange)
            or not isinstance(exchange.event_id, str)
            or not _EVENT_ID.fullmatch(exchange.event_id)
            or exchange.event_id in exchanges
        ):
            raise ValueError("Invalid visitor exchange")
        visitor = _text(exchange.visitor_text, _TEXT_LIMIT)
        assistant = _text(exchange.assistant_text, _REPLY_LIMIT)
        characters += len(visitor) + len(assistant)
        if characters > MAX_HISTORY_CHARACTERS:
            raise ValueError("Visitor history exceeds its budget")
        exchanges[exchange.event_id] = (visitor, assistant)
        digest = hashlib.sha256(exchange.event_id.encode()).hexdigest()
        messages.extend(
            (
                BrowserWebChatMessage(f"visitor-{digest}", "visitor", visitor),
                BrowserWebChatMessage(f"assistant-{digest}", "assistant", assistant),
            )
        )
    return tuple(messages), exchanges


class BrowserWebChatController:
    """Compose one routed browser request without serving a route.

    The host must resolve the provision from a trusted route and enforce body
    limits, origin/credential handling, and a shared request budget. Ports are
    request scoped; they must not accept a browser-selected workspace,
    profile, account, conversation or visitor. No default authorizer, model,
    persistence, readiness, or token issuance is installed here.
    """

    def __init__(
        self,
        provision: ExternalChatProvision,
        sessions: BrowserVisitorSessions,
        budget: RequestBudget,
        history: BrowserWebChatHistory,
        store: WebChatTurnStore,
        complete: TextModel,
        *,
        readiness: WorkspaceReadiness | None = None,
    ) -> None:
        if provision.connector_kind != "public_web_chat":
            raise ValueError("Expected a trusted Web Chat provision")
        self._provision = provision
        self._sessions = sessions
        self._budget = budget
        self._history = history
        self._store = store
        self._complete = complete
        self._readiness = readiness or public_web_chat.transport_ready

    def _expect_session(self, access: BrowserSessionAccess) -> VerifiedFullPageSession:
        if not isinstance(access, BrowserSessionAccess):
            raise ValueError("Invalid visitor session response")
        if access.kind == "expired" and access.session is None:
            raise _SessionExpired
        session = access.session
        if (
            access.kind != "current"
            or not isinstance(session, VerifiedFullPageSession)
            or session.conversation.provision != self._provision
            or session.scope.workspace_id != self._provision.workspace_id
        ):
            raise ValueError("Unexpected visitor session")
        return session

    async def _assignment(
        self, session: VerifiedFullPageSession
    ) -> ExternalChatAssignment:
        if self._expect_session(await self._sessions.current()) != session:
            raise _SessionExpired
        scope = session.scope
        current = await public_web_chat.admit(
            scope.workspace_id,
            scope.visitor_id,
            existing=scope,
            expected_provision_id=self._provision.provision_id,
            transport_ready=await self._readiness(scope.workspace_id) is True,
        )
        if self._expect_session(await self._sessions.current()) != session:
            raise _SessionExpired
        return ExternalChatAssignment(
            self._provision,
            current.profile_key,
            str(current.published_version),
            str(current.route_generation),
        )

    async def start(self, mode: StartMode) -> BrowserWebChatReply:
        """Open or resume a same-route conversation without exposing credentials."""
        try:
            if (
                mode not in ("resume", "new")
                or await self._budget("start", self._provision, None) is not True
            ):
                return BrowserWebChatReply("unavailable")
            previous = await self._sessions.current() if mode == "new" else None
            session = self._expect_session(await self._sessions.open(mode))
            if (
                previous is not None
                and previous.kind == "current"
                and self._expect_session(previous).conversation == session.conversation
            ):
                return BrowserWebChatReply("unavailable")
            assignment = await self._assignment(session)
            messages, _ = _display(
                await self._history.recent(session, assignment, None),
                session,
                assignment,
            )
            if (mode == "new" and messages) or await self._assignment(
                session
            ) != assignment:
                return BrowserWebChatReply("unavailable")
            return BrowserWebChatReply("ready", messages)
        except _SessionExpired:
            return BrowserWebChatReply("expired")
        except Exception:
            return BrowserWebChatReply("unavailable")

    async def send(self, event_id: str, text: str) -> BrowserWebChatReply:
        """Send or retry an exact attempt, then read its durable display history."""
        expired_during_inference = False
        try:
            if not isinstance(event_id, str) or not _EVENT_ID.fullmatch(event_id):
                return BrowserWebChatReply("unavailable")
            visitor_text = _text(text, _TEXT_LIMIT)
            session = self._expect_session(await self._sessions.current())
            if await self._budget("send", self._provision, session) is not True:
                return BrowserWebChatReply("unavailable")
            assignment = await self._assignment(session)

            async def read_exact() -> VerifiedFullPageSession | None:
                nonlocal expired_during_inference
                try:
                    current = self._expect_session(await self._sessions.current())
                except _SessionExpired:
                    expired_during_inference = True
                    return None
                if current != session:
                    expired_during_inference = True
                    return None
                return session

            inference = FullPageWebChatProfileInference(
                read_exact,
                self._store,
                self._history.prior,
                self._complete,
                readiness=self._readiness,
            )
            outcome = await inference.submit(event_id, text)
            if outcome.state == "conflict":
                return BrowserWebChatReply("unavailable")
            if await self._assignment(session) != assignment:
                return BrowserWebChatReply("unavailable")
            if outcome.state == "in_progress":
                return BrowserWebChatReply("pending")

            snapshot = await self._history.recent(session, assignment, event_id)
            messages, exchanges = _display(snapshot, session, assignment)
            if await self._assignment(session) != assignment:
                return BrowserWebChatReply("unavailable")
            if event_id not in exchanges:
                # Do not pretend an in-memory reply will survive reload.
                return BrowserWebChatReply("pending")
            if exchanges[event_id] != (visitor_text, outcome.reply_text):
                return BrowserWebChatReply("unavailable")
            return BrowserWebChatReply("ready", messages)
        except _SessionExpired:
            return BrowserWebChatReply("expired")
        except Exception:
            return BrowserWebChatReply(
                "expired" if expired_during_inference else "unavailable"
            )
