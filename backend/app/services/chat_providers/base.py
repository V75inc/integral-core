"""Backend chat provider Protocol — keeps the chat router harness-agnostic.

Mirrors the frontend ``ChatProvider`` interface
(``frontend/src/features/ai-chat/providers/types.ts``): both sides agree on a
single normalized event envelope (``NormalizedEvent``) so the chat surface,
persistence, and runtime stay decoupled from any specific agent harness
(jvagent embedded, jvagent HTTP, OpenAI Direct, LangGraph, mock fixtures, …).

Adding a new harness means dropping a new ``ChatBackendProvider`` adapter
(typically wrapping its native streaming surface), registering it in
``ChatBackendRegistry`` at startup, and surfacing it on the frontend by
adding a matching ``ChatProvider``. The router never imports a harness
module directly.

The normalized envelope itself is documented in
``.planning/initiatives/ai-chat/SPEC.md`` § 6.3 and produced by adapters by
calling :mod:`app.providers.jvagent_streaming.translate_envelope` (or its
harness-specific equivalent).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    TypedDict,
    runtime_checkable,
)


class AgentDescriptor(TypedDict, total=False):
    """Provider-agnostic shape of a single selectable agent.

    Returned by :meth:`ChatBackendProvider.list_agents` so the chat UI can
    render an agent switcher without knowing harness specifics. ``id`` is
    the provider-native identifier the caller persists onto
    ``ChatThread.agent_id`` (for jvagent this is the agent's ``alias`` —
    stable across deploys, unlike the regenerated internal ``id``).
    """

    id: str
    name: str
    description: str
    avatar_url: str
    role_label: str


class ChatProviderCapabilities(TypedDict):
    """Capability flags surfaced to the frontend ``GET /api/chat/providers``.

    The frontend uses these to enable / disable composer affordances
    (attachment picker, voice input, etc.) — they describe what the harness
    *can* do, not what's enabled in any given thread.
    """

    reasoning: bool
    tools: bool
    attachments: bool
    vision: bool
    # Conversational audio in/out. NOT composer dictation: that is pre-send
    # speech-to-text (app.agentive.services.speech) and works with any harness.
    voice: bool


@dataclass(frozen=True)
class ChatTurnContext:
    """Inputs for a single chat turn — shape is harness-independent.

    Adapters destructure this into whatever their native API expects. The
    router does not know what an adapter does with each field, only that it
    passes the same shape to every adapter.
    """

    user_id: str
    """Stable Integral user identifier (jvspatial node id of the User)."""

    user_email: str
    """Authenticated user's email — provided for legacy harnesses that
    historically keyed users by email rather than node id (e.g. the
    out-of-process jvagent integration)."""

    text: str
    """User utterance for this turn. Already validated non-empty by the
    request layer."""

    thread_id: str
    """ChatThread node id. Adapters may forward this to their native
    ``data`` payload for downstream context (Integral expects it surfaced
    as ``thread_id`` in jvagent's data dict, e.g.)."""

    session_id: Optional[str]
    """Provider-side session id from a prior turn, if any. ``None`` for
    first-turn-of-thread; adapters are responsible for capturing the
    session id their harness assigns and surfacing it back via the
    ``_meta`` event so the router can persist it on the ChatThread."""

    focused_track_id: Optional[str] = None
    focused_space_id: Optional[str] = None
    focused_view_id: Optional[str] = None

    workspace_id: Optional[str] = None
    """Active workspace scope from ``X-Integral-Scope`` header.

    The agent's read/list tools (list_tracks, query_entries, etc.) must
    filter to this workspace so the agent sees what the user currently
    has selected in the UI. ``None`` defers to the user's Personal
    Workspace (matches the REST surface's fail-closed fallback)."""

    extra_data: Optional[Dict[str, Any]] = None
    """Free-form extra payload merged into the harness-specific ``data``
    field. Reserved for future composer affordances (focused entry id,
    selected tool override, etc.)."""

    start_time: Optional[float] = None
    """``time.monotonic()`` snapshot taken when the request entered the
    router — adapters use this to compute end-to-end timing for
    ``message-finish`` events."""

    is_disconnected: Optional[Callable[[], Awaitable[bool]]] = None
    """Async predicate (typically ``request.is_disconnected``) the adapter
    polls so a client that walked away cancels the harness turn instead of
    leaving an orphan walker calling the model and dispatching tools.
    ``None`` = never disconnected."""


@runtime_checkable
class ChatBackendProvider(Protocol):
    """Backend implementation of a chat provider.

    The router calls :meth:`stream_turn` and consumes whatever it yields —
    nothing else. ``id`` is the public identifier persisted on
    ``ChatThread.provider_id`` (must match the frontend's ``ChatProvider.id``);
    ``label`` is the human-readable display name; ``capabilities`` is surfaced
    via ``GET /api/chat/providers``; :meth:`is_available` is checked at
    request time so missing config produces a clean 503 rather than a stack
    trace.

    Adapters are expected to be cheap to instantiate — they usually hold no
    state of their own and delegate to module-level streaming helpers (e.g.
    :mod:`app.providers.jvagent_embed`).
    """

    id: str
    label: str
    capabilities: ChatProviderCapabilities

    def is_available(self) -> bool:
        """Whether this provider is wired in the current process / config.

        Should be cheap (env / settings check) — called on every
        ``GET /api/chat/providers`` request and again on every ``POST
        /threads/{id}/messages`` to short-circuit before opening a stream.
        """
        ...

    def stream_turn(self, ctx: ChatTurnContext) -> AsyncIterator[Dict[str, Any]]:
        """Yield normalized envelope events for one turn.

        Event types mirror the frontend's ``NormalizedEvent`` union:
        ``text-delta`` / ``reasoning-delta`` / ``tool-call`` / ``source`` /
        ``status`` / ``step`` / ``message-finish`` / ``error``, plus the
        out-of-band ``_meta`` event the router consumes to capture
        provider-side session ids for thread resume.

        Adapters MUST NOT raise after the iterator yields its first event —
        terminal failures must be surfaced as a final ``error`` envelope so
        the router's draft-accumulation + persistence path stays consistent.
        Pre-yield raises (e.g. validation) are fine and propagate naturally.
        """
        ...

    # ------------------------------------------------------------------
    # Identity & conversation lifecycle
    # ------------------------------------------------------------------
    #
    # Multi-user isolation is achieved by passing the host's stable
    # ``user_id`` (e.g. an Integral User node id) as the provider's user
    # identifier on every turn. Adapters that back onto a multi-tenant
    # harness (jvagent, etc.) materialize a per-user memory subgraph keyed
    # on this id so different host users sharing the same agent never see
    # each other's conversations or long-term memory.
    #
    # The methods below let the host explicitly bind / inspect / tear down
    # that per-user state. They are optional — adapters whose harness has
    # no notion of users-as-entities (raw stateless LLM endpoints, mock
    # fixtures) can return the safe default.

    async def ensure_user(
        self, *, user_id: str, email: str = "", name: str = ""
    ) -> Optional[Dict[str, Any]]:
        """Idempotently bind a host user identity into the provider's user model.

        Called by the host at login (whoami) or before the first turn to
        guarantee the provider's user record exists. Subsequent
        :meth:`stream_turn` calls land on the bound user automatically.

        Adapters that don't track users may return ``None``.
        """
        return None

    async def list_conversations(self, *, user_id: str) -> List[Dict[str, Any]]:
        """Return provider-known conversations for ``user_id``.

        Optional cross-check the host can use for admin / debug surfaces.
        Default = empty list (host's own thread store is authoritative).
        """
        return []

    async def delete_conversation(
        self, *, agent_id: str, user_id: str, session_id: str
    ) -> bool:
        """Best-effort delete of provider-side conversation state.

        Called when the host hard-deletes the corresponding thread so
        per-(agent × user) memory in the provider stays in sync.
        ``agent_id`` is the provider-native agent identifier persisted on
        the thread. Adapters that don't track conversations as discrete
        entities may no-op (return ``False``).

        MUST be idempotent. MUST NOT raise on missing conversation —
        return ``False`` instead.
        """
        return False

    async def purge_user(self, *, user_id: str) -> bool:
        """Cascade-delete the entire user subgraph in the provider.

        Called by the host on account deletion / "forget me" flows.
        Returns ``True`` if anything was deleted, ``False`` if there was
        nothing to delete (or the adapter doesn't support it). MUST be
        idempotent and MUST NOT raise on missing user.
        """
        return False

    # ------------------------------------------------------------------
    # Agent catalog (multi-agent providers)
    # ------------------------------------------------------------------

    async def list_agents(self) -> List[AgentDescriptor]:
        """Return the agents this provider exposes for the calling user.

        Empty list = single-agent provider. The UI hides the agent
        switcher when the list is empty.

        Adapters MUST NOT cache internally — the caller scopes calls
        per (provider, workspace).
        """
        return []


@dataclass
class ProviderInfo:
    """Serialized form of a provider for the FE registry endpoint.

    Built by the router from a :class:`ChatBackendProvider` to keep the
    FE/BE wire shape stable across provider implementations.
    """

    id: str
    label: str
    available: bool
    capabilities: ChatProviderCapabilities = field(
        default_factory=lambda: ChatProviderCapabilities(
            reasoning=False,
            tools=False,
            attachments=False,
            vision=False,
            voice=False,
        )
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to the wire shape consumed by the FE registry endpoint."""
        return {
            "id": self.id,
            "label": self.label,
            "available": self.available,
            "capabilities": dict(self.capabilities),
        }
