"""AI chat surface — provider-agnostic SSE proxy + thread persistence.

Endpoints (mounted under /api/chat/*):

  GET    /providers                          → registry + capabilities
  GET    /threads                            → list user's threads
  POST   /threads                            → create thread
  GET    /threads/{id}                       → thread + full transcript
  PATCH  /threads/{id}                       → rename
  DELETE /threads/{id}                       → archive (default) or hard-delete
  POST   /threads/{id}/messages              → user turn, normalized SSE stream
  POST   /threads/{id}/agent-turn            → agent-initiated turn (workstream)
  POST   /threads/{id}/cancel                → cancel in-flight turn on thread
  POST   /threads/{id}/system-message        → append non-model message

See docs/backend/ai-chat.md for parallel-stream invariants and WS events.

Persistence model (initiative SPEC § 6.4):
- ChatThread node owned by User via `user_id` field; cataloged under the
  user's thread registry via `catalog_chat_thread` (`app_graph.py`).
- ChatMessage nodes addressed by `thread_id` field, sorted by created_at.
- Assistant turns are accumulated in-flight (mirroring SPEC § 6.3 envelope)
  and persisted on `message-finish`.

Provider abstraction:
- All harness-specific knowledge lives behind the
  :class:`app.services.chat_providers.ChatBackendProvider` Protocol. This
  router is harness-agnostic — adding (or replacing) the agent backend means
  registering a new ``ChatBackendProvider`` adapter at startup; nothing in
  this module changes.
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

from fastapi import Request
from fastapi.responses import StreamingResponse
from jvspatial.api import endpoint

from app.agentive.services.approval_intent import looks_like_approval
from app.agentive.staging import (
    claim_open_batch_auto_continuation,
    format_open_batch_marker,
    format_staging_pending_marker,
    list_unresolved_for_session,
    peek_open_batch,
    release_open_batch_auto_continuation,
)
from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    InternalServerError,
    MissingAuthenticationError,
    ResourceConflictError,
    ResourceNotFoundError,
    ServiceUnavailableError,
    UnprocessableEntityError,
)
from app.api.utils import resolve_principal_id
from app.schemas.api.ai_chat import PageContext
from app.schemas.chat_entity_refs import EntityRef
from app.services import chat_threads as chat_store
from app.services import chat_turn_registry
from app.services.chat_providers import (
    ChatTurnContext,
    ProviderInfo,
    get_registry,
)
from app.services.chat_streaming import SSE_HEADERS, generate_chat_turn_sse
from app.services.chat_thread_events import notify_thread_stream_update

logger = logging.getLogger(__name__)


# A recovered scaffold gets several turns to finish a multi-track build, but
# never an unbounded background loop if the provider repeatedly ignores its
# tool contract. The claim is stored on the open batch, not on a process-wide
# task, so it is scoped to the user's exact build session.
_MAX_SCAFFOLD_AUTO_CONTINUATIONS = 6
_SCAFFOLD_RECOVERY_ORIGIN = "scaffold_recovery"


def _scaffold_recovery_commit_outcome(events: Iterable[Dict[str, Any]]) -> str:
    """Return the authoritative batch outcome emitted during a recovery turn.

    Recovery turns are headless.  Their conversational prose must therefore
    never be the source of truth for a user-visible build status: the model can
    reply after an incomplete commit, while a subsequent turn completes it.
    The normalized tool-result envelope is the receipt boundary.
    """
    outcome = ""
    for event in events:
        if (
            event.get("type") != "tool-call"
            or event.get("name") != "integral_commit_batch"
            or event.get("status") != "complete"
        ):
            continue
        result = event.get("result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                continue
        if isinstance(result, dict):
            kind = result.get("_kind")
            if isinstance(kind, str):
                outcome = kind
    return outcome


async def _append_scaffold_recovery_status(
    *, thread: Any, outcome: str, exhausted: bool
) -> None:
    """Persist a receipt-backed terminal message for a headless scaffold run."""
    if outcome == "batch_applied":
        text = (
            "Your approved build is complete. The requested app, tracks, views, "
            "and records are now available in Apps."
        )
    elif exhausted:
        text = (
            "The approved build needs attention before it can finish. Some steps "
            "may have been applied; Integral preserved the remaining build rather "
            "than claiming completion."
        )
    else:
        return
    await chat_store.append_message(
        thread=thread,
        role="assistant",
        parts=[{"type": "text", "text": text}],
        provider_metadata={
            "source": "system_message",
            "kind": "scaffold_recovery_status",
            "outcome": outcome or "incomplete",
        },
    )


# ---------------------------------------------------------------------------
# Provider registry view
# ---------------------------------------------------------------------------


def _list_providers() -> List[Dict[str, Any]]:
    """Serialize every registered provider for the public registry endpoint.

    Order matches registration order (insertion-stable). Each provider's
    availability is checked at request time so missing config produces a
    clean ``available: false`` rather than a stack trace.
    """
    return [
        ProviderInfo(
            id=p.id,
            label=p.label,
            available=p.is_available(),
            capabilities=p.capabilities,
        ).to_dict()
        for p in get_registry().list()
    ]


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _resolve_principal(request: Request) -> tuple[str, str]:
    """Return (user_id, email). Raises if either is missing."""
    user_id = resolve_principal_id(request)
    user = getattr(request.state, "user", None)
    email = (getattr(user, "email", None) or "").strip() if user else ""
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    if not email:
        raise BadRequestError(
            message="Authenticated user has no email; cannot start chat session"
        )
    return user_id, email


async def _resolve_owned_thread(thread_id: str, user_id: str) -> Any:
    thread = await chat_store.get_thread(thread_id)
    if thread is None or thread.user_id != user_id:
        raise ResourceNotFoundError(message="Thread not found")
    return thread


async def _resolve_turn_workspace(
    request: Request, user_id: str, thread: Any
) -> Optional[str]:
    """Return the workspace a turn on ``thread`` runs in.

    The thread's own ``workspace_id`` (stamped at create) is authoritative —
    NOT the ``X-Integral-Scope`` header. Scoping the turn by the header let a
    stale or hostile client run an org-workspace conversation against a
    different workspace's data: the agent's tools filter by whatever scope
    the turn binds, so the header decided what the resident could read.

    * Caller must still hold access to that workspace (same membership check
      ``resolve_workspace_id_from_request`` applied at create) — 403 if lost.
    * A present header that names a different workspace is a client bug or
      an attempt to re-scope; refused with 409 ``thread.workspace_mismatch``
      so the client re-syncs rather than silently running elsewhere.
    * Threads from before workspace stamping (empty ``workspace_id``) keep
      the header-driven resolution.
    """
    from app.services.request_scope import resolve_workspace_id_from_request
    from app.services.scope_header import parse_scope_header
    from app.services.workspace_permissions import user_in_workspace_member_pool

    thread_ws = str(getattr(thread, "workspace_id", "") or "").strip()
    if not thread_ws:
        return await resolve_workspace_id_from_request(request, user_id)

    header_scope = parse_scope_header(request.headers.get("X-Integral-Scope"))
    header_ws = (
        str(header_scope.get("workspace_id") or "").strip() if header_scope else ""
    )
    if header_ws and header_ws != thread_ws:
        exc = ResourceConflictError(
            message=(
                "This conversation belongs to a different workspace than the "
                "one selected. Switch workspaces to continue it."
            ),
            details={
                "thread_id": thread.id,
                "thread_workspace_id": thread_ws,
                "requested_workspace_id": header_ws,
                "reason": "thread.workspace_mismatch",
            },
        )
        # ``error_code`` is a class attribute on JVSpatialAPIException and
        # ``to_dict`` reads it from the instance, so the specific code rides
        # on the instance without a new subclass in app/api/errors.py.
        exc.error_code = "thread.workspace_mismatch"
        raise exc

    if not await user_in_workspace_member_pool(user_id, thread_ws):
        raise InsufficientPermissionsError(
            message="You no longer have access to this conversation's workspace",
            details={"thread_id": thread.id, "workspace_id": thread_ws},
        )
    return thread_ws


# ---------------------------------------------------------------------------
# Provider listing
# ---------------------------------------------------------------------------


@endpoint("/chat/providers", methods=["GET"], auth=True, tags=["AI Chat"])
async def list_providers(request: Request) -> Dict[str, Any]:
    """List registered chat providers + their availability for this caller."""
    _resolve_principal(request)
    return {"providers": _list_providers()}


@endpoint(
    "/chat/providers/{provider_id}/agents",
    methods=["GET"],
    auth=True,
    tags=["AI Chat"],
)
async def list_provider_agents(request: Request, provider_id: str) -> Dict[str, Any]:
    """Return the agents the named provider exposes for the calling user.

    Empty list = single-agent provider. The UI hides its agent switcher
    in that case.
    """
    provider = get_registry().get(provider_id)
    if provider is None:
        raise BadRequestError(message=f"Unknown provider_id: {provider_id}")
    try:
        agents = await provider.list_agents()
    except Exception:
        # Never bubble adapter failures as 500 — surface as empty catalog
        # instead so the surface degrades gracefully.
        logger.exception("list_agents failed for %s", provider_id)
        agents = []
    return {"agents": agents}


# ---------------------------------------------------------------------------
# Thread CRUD
# ---------------------------------------------------------------------------


@endpoint("/chat/threads", methods=["GET"], auth=True, tags=["AI Chat"])
async def list_threads(
    request: Request,
    include_archived: bool = False,
    provider_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """List the caller's chat threads in the active workspace (most-recent first).

    When both ``provider_id`` and ``agent_id`` are supplied, narrow to threads
    stamped with that (provider, agent) pair. Either omitted = no agent
    filter (single-agent providers like MockEcho expose an empty catalog,
    the UI hides the switcher and the threadlist returns all threads).
    """
    user_id, _ = _resolve_principal(request)
    from app.services.request_scope import resolve_workspace_id_from_request

    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    threads = await chat_store.list_threads(
        user_id=user_id,
        include_archived=include_archived,
        workspace_id=workspace_id,
        provider_id=provider_id,
        agent_id=agent_id,
    )
    return {
        "threads": [chat_store.thread_to_dict(t) for t in threads],
    }


async def _resolve_workspace_agent_preference(
    user_id: str, workspace_id: str, provider_id: str
) -> Optional[str]:
    """Read the caller's ``HAS_AGENT_PREFERENCE`` for ``workspace_id``.

    Returns the stored ``agent_id`` when the edge exists AND its
    ``provider_id`` matches the active provider; otherwise ``None``.
    The preference is provider-scoped because each provider exposes
    a distinct agent catalog — a jvagent preference is meaningless to
    a hypothetical future ``openai`` provider.
    """
    if not workspace_id:
        return None
    from app.models.edges import HasAgentPreference
    from app.models.nodes import Workspace
    from app.services.permissions import get_user_node

    user = await get_user_node(user_id)
    workspace = await Workspace.get(workspace_id)
    if user is None or workspace is None:
        return None
    ctx = await user.get_context()
    edges = await ctx.find_edges_between(
        user.id, workspace.id, edge_class=HasAgentPreference
    )
    for edge in edges:
        if edge.provider_id == provider_id and edge.agent_id:
            return edge.agent_id
    return None


async def _resolve_agent_for_new_thread(
    *,
    user_id: str,
    workspace_id: str,
    provider: Any,
    body_agent_id: str,
) -> str:
    """Walk the agent-switcher resolution chain for a new thread.

    Order (agent-switcher SPEC § 2.5):

    1. ``body_agent_id`` if non-empty,
    2. user's ``HAS_AGENT_PREFERENCE`` for ``workspace_id`` (provider-scoped),
    3. provider catalog default (first entry in ``provider.list_agents()``),
    4. else raise ``UnprocessableEntityError`` (422) — multi-agent provider
       with nothing resolvable means the client must pick explicitly.
    """
    resolved = (body_agent_id or "").strip() or None
    if not resolved:
        resolved = await _resolve_workspace_agent_preference(
            user_id, workspace_id, provider.id
        )
    if not resolved:
        try:
            catalog = await provider.list_agents()
        except Exception:
            logger.exception(
                "list_agents failed during thread create for %s", provider.id
            )
            catalog = []
        if catalog:
            first = catalog[0]
            candidate = (first.get("id") if isinstance(first, dict) else "") or ""
            candidate = candidate.strip()
            if candidate:
                resolved = candidate
    if not resolved:
        raise UnprocessableEntityError(
            message="No agent available — provider catalog is empty"
        )
    return resolved


@endpoint("/chat/threads", methods=["POST"], auth=True, tags=["AI Chat"])
async def create_thread(
    request: Request,
    provider_id: str = "",
    agent_id: str = "",
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new chat thread bound to a registered provider + agent.

    The thread is stamped at create-time with a resolved ``agent_id`` so
    each conversation is sticky-bound for its lifetime. Resolution chain
    (agent-switcher Task 5):

    1. Body ``agent_id`` if present,
    2. User's per-workspace ``HAS_AGENT_PREFERENCE``,
    3. Provider catalog default (first entry),
    4. 422 if the catalog is empty.
    """
    user_id, _ = _resolve_principal(request)
    if not provider_id:
        raise BadRequestError(message="provider_id is required")
    provider = get_registry().get(provider_id)
    if provider is None:
        raise BadRequestError(message=f"Unknown provider_id: {provider_id}")
    from app.services.request_scope import resolve_workspace_id_from_request

    workspace_id = await resolve_workspace_id_from_request(request, user_id) or ""

    resolved_agent_id = await _resolve_agent_for_new_thread(
        user_id=user_id,
        workspace_id=workspace_id,
        provider=provider,
        body_agent_id=agent_id,
    )

    thread = await chat_store.create_thread(
        user_id=user_id,
        provider_id=provider_id,
        agent_id=resolved_agent_id,
        title=title or "",
        workspace_id=workspace_id,
    )
    return chat_store.thread_to_dict(thread, message_count=0)


@endpoint("/chat/threads/{thread_id}", methods=["GET"], auth=True, tags=["AI Chat"])
async def get_thread(request: Request, thread_id: str) -> Dict[str, Any]:
    """Return one thread + full transcript for the caller."""
    user_id, _ = _resolve_principal(request)
    thread = await _resolve_owned_thread(thread_id, user_id)
    messages = await chat_store.list_messages(thread)
    return {
        **chat_store.thread_to_dict(thread, message_count=len(messages)),
        "messages": [chat_store.message_to_dict(m) for m in messages],
    }


@endpoint("/chat/threads/{thread_id}", methods=["PATCH"], auth=True, tags=["AI Chat"])
async def rename_thread(
    request: Request,
    thread_id: str,
    title: str = "",
) -> Dict[str, Any]:
    """Rename one of the caller's chat threads."""
    user_id, _ = _resolve_principal(request)
    if not title:
        raise BadRequestError(message="title is required and must be non-empty")
    thread = await _resolve_owned_thread(thread_id, user_id)
    thread = await chat_store.rename_thread(thread, title)
    return chat_store.thread_to_dict(thread)


@endpoint("/chat/threads/{thread_id}", methods=["DELETE"], auth=True, tags=["AI Chat"])
async def delete_thread(
    request: Request,
    thread_id: str,
    hard: bool = False,
) -> Dict[str, Any]:
    """Archive (default) or hard-delete a thread; cascades to provider on hard."""
    user_id, _ = _resolve_principal(request)
    thread = await _resolve_owned_thread(thread_id, user_id)
    if hard:
        # Best-effort cascade into the provider so per-user agent memory
        # stays in sync with the host's thread store. Runs BEFORE the
        # local delete so a provider-side failure (logged inside the
        # adapter) doesn't leave the host with no record of which session
        # to clean up later.
        provider_session_id = thread.provider_session_id
        provider = get_registry().get(thread.provider_id)
        if provider is not None and provider_session_id and thread.agent_id:
            try:
                await provider.delete_conversation(
                    agent_id=thread.agent_id,
                    user_id=user_id,
                    session_id=provider_session_id,
                )
            except Exception:
                # Adapters are documented as MUST-NOT-RAISE here, but
                # belt-and-suspenders so a misbehaving provider can't
                # block the user from deleting their own thread.
                logger.exception(
                    "Provider %r delete_conversation raised for thread=%s",
                    thread.provider_id,
                    thread_id,
                )

        await chat_store.delete_thread_messages(thread)
        try:
            await thread.delete()
        except Exception:
            logger.exception("Hard-delete failed for thread=%s", thread_id)
            raise InternalServerError(message="Could not delete thread")
        return {"thread_id": thread_id, "deleted": True}
    thread = await chat_store.archive_thread(thread)
    return chat_store.thread_to_dict(thread)


# ---------------------------------------------------------------------------
# Streaming turn
# ---------------------------------------------------------------------------


def _derive_thread_title(text: str) -> str:
    """First-message-derived title when the thread is still untitled."""
    cleaned = " ".join(text.strip().split())
    if not cleaned:
        return "New chat"
    return (cleaned[:60] + "…") if len(cleaned) > 60 else cleaned


class _AssistantDraft:
    """Accumulates a single assistant turn from normalized stream events.

    Mirrors the React-side `useAIChatRuntime` draft: same event types, same
    part-merge rules. Lives here so the persisted transcript matches what the
    user saw in their browser.
    """

    def __init__(self) -> None:
        self.text_parts: List[str] = []
        self.reasoning_segments: Dict[str, str] = {}
        self.reasoning_order: List[str] = []
        self.tool_calls: Dict[str, Dict[str, Any]] = {}
        self.tool_call_order: List[str] = []
        self.sources: List[Dict[str, Any]] = []
        self.steps: List[Dict[str, Any]] = []
        self.timing: Optional[Dict[str, Any]] = None
        # The authoritative final answer text + full final chunk, forwarded by
        # the provider's `final-content` event (debug-view source-of-truth).
        self.final_content: Optional[str] = None
        self.final_payload: Optional[Dict[str, Any]] = None
        # Terminal failure for this bubble: ``{code, message}`` with the
        # user-facing wording the browser already saw. Persisted so a reload
        # shows why there is no answer instead of a bare user message.
        self.error: Optional[Dict[str, Any]] = None

    def apply(self, ev: Dict[str, Any]) -> None:
        kind = ev.get("type")
        if kind == "error":
            self.error = {
                "code": str(ev.get("code") or "internal_error"),
                "message": str(ev.get("message") or "The turn failed."),
            }
        elif kind == "text-delta":
            self.text_parts.append(ev.get("delta", ""))
        elif kind == "reasoning-delta":
            seg = ev.get("segmentId") or "default"
            if seg not in self.reasoning_segments:
                self.reasoning_order.append(seg)
                self.reasoning_segments[seg] = ""
            self.reasoning_segments[seg] += ev.get("delta", "")
        elif kind == "tool-call":
            tcid = ev.get("toolCallId") or "tool"
            if tcid not in self.tool_calls:
                self.tool_call_order.append(tcid)
                self.tool_calls[tcid] = {
                    "name": ev.get("name", "tool"),
                    "args": ev.get("args"),
                    "status": ev.get("status", "pending"),
                }
            existing = self.tool_calls[tcid]
            if ev.get("args") is not None:
                existing["args"] = ev["args"]
            if ev.get("result") is not None:
                existing["result"] = ev["result"]
            existing["status"] = ev.get("status", existing.get("status"))
            if ev.get("status") == "error":
                existing["isError"] = True
        elif kind == "source":
            self.sources.append(
                {
                    "type": "source",
                    "sourceType": ev.get("sourceType", "url"),
                    "id": ev.get("ref"),
                    "url": ev.get("ref"),
                    "title": ev.get("title"),
                }
            )
        elif kind == "step":
            self.steps.append(
                {
                    "usage": ev.get("usage"),
                    "modelId": ev.get("modelId"),
                    "finishReason": ev.get("finishReason"),
                }
            )
        elif kind == "message-finish":
            self.timing = ev.get("timing")
        elif kind == "final-content":
            if ev.get("content"):
                self.final_content = ev["content"]
            if ev.get("payload") is not None:
                self.final_payload = ev["payload"]

    def to_parts(self) -> List[Dict[str, Any]]:
        parts: List[Dict[str, Any]] = []
        for seg in self.reasoning_order:
            text = self.reasoning_segments.get(seg) or ""
            if text:
                parts.append({"type": "reasoning", "text": text})
        for tcid in self.tool_call_order:
            call = self.tool_calls[tcid]
            parts.append(
                {
                    "type": "tool-call",
                    "toolCallId": tcid,
                    "toolName": call.get("name"),
                    "args": call.get("args"),
                    "result": call.get("result"),
                    "isError": call.get("isError", False),
                }
            )
        text = "".join(self.text_parts)
        if not text.strip() and self.final_content:
            text = self.final_content
        if text:
            parts.append({"type": "text", "text": text})
        parts.extend(self.sources)
        if self.error:
            parts.append({"type": "error", **self.error})
        return parts


_TURN_LEVEL_EVENT_TYPES = frozenset({"step", "message-finish", "final-content"})


def _draft_is_contentful(draft: "_AssistantDraft") -> bool:
    """True when the draft has user-visible body parts (not just run stats)."""
    if draft.final_content and str(draft.final_content).strip():
        return True
    return bool(draft.to_parts())


def _draft_text(draft: "_AssistantDraft") -> str:
    return "".join(draft.text_parts)


def _collapse_duplicate_drafts(
    drafts: List["_AssistantDraft"],
) -> List["_AssistantDraft"]:
    """Drop consecutive contentful drafts whose text is identical.

    Defensive against upstream double-publishes (stream + adhoc replay) that
    slipped past the translator — persistence must not write twin bubbles.
    """
    if len(drafts) < 2:
        return drafts
    out: List[_AssistantDraft] = []
    for draft in drafts:
        if not _draft_is_contentful(draft):
            out.append(draft)
            continue
        text = _draft_text(draft).strip()
        if out:
            for prior in reversed(out):
                if not _draft_is_contentful(prior):
                    continue
                if _draft_text(prior).strip() == text:
                    if draft.steps:
                        prior.steps.extend(draft.steps)
                    if draft.timing is not None:
                        prior.timing = draft.timing
                    if draft.final_content is not None:
                        prior.final_content = draft.final_content
                    if draft.final_payload is not None:
                        prior.final_payload = draft.final_payload
                    if draft.error is not None and prior.error is None:
                        prior.error = draft.error
                    break
            else:
                out.append(draft)
        else:
            out.append(draft)
    return out


def _fold_trailing_observability(drafts: List["_AssistantDraft"]) -> None:
    """Move turn-level stats from trailing empty drafts onto the last contentful.

    A trailing ``message-boundary`` (or a boundary with no further text) leaves
    ``step`` / ``final-content`` / ``message-finish`` on an empty draft that
    callers discard — so reload would lose the meta-bar tally. Mirrors the
    browser's ``closedDraft`` fold in ``useAIChatRuntime``.
    """
    last_contentful: Optional[int] = None
    for i in range(len(drafts) - 1, -1, -1):
        if _draft_is_contentful(drafts[i]):
            last_contentful = i
            break
    if last_contentful is None:
        return
    dst = drafts[last_contentful]
    for i in range(last_contentful + 1, len(drafts)):
        src = drafts[i]
        if src.steps:
            dst.steps.extend(src.steps)
            src.steps = []
        if src.timing is not None:
            dst.timing = src.timing
            src.timing = None
        if src.final_content is not None:
            dst.final_content = src.final_content
            src.final_content = None
        if src.final_payload is not None:
            dst.final_payload = src.final_payload
            src.final_payload = None
        if src.error is not None and dst.error is None:
            dst.error = src.error
            src.error = None


def drafts_from_events(events: Iterable[Dict[str, Any]]) -> List["_AssistantDraft"]:
    """Split a normalized event stream into one ``_AssistantDraft`` per bubble.

    A ``message-boundary`` event closes the current bubble and opens a new one,
    mirroring the browser's per-message bubbles (the orchestrator can publish
    more than one user-facing message in a turn — e.g. a first-time intro
    greeting that is a distinct adhoc message from the answer). Every other
    event is applied to the current bubble. Turn-level events (``step`` /
    ``message-finish`` / ``final-content``) that land on a trailing empty
    draft are folded onto the last contentful bubble — where the UI shows
    run stats.

    Returns at least one draft. May include empty leading/trailing drafts when
    a boundary brackets no content; callers persist only drafts whose
    ``to_parts()`` is non-empty.
    """
    drafts: List[_AssistantDraft] = [_AssistantDraft()]
    for ev in events:
        if ev.get("type") == "message-boundary":
            drafts.append(_AssistantDraft())
            continue
        kind = ev.get("type")
        # Prefer the last contentful bubble for turn-level events when the
        # current draft is still empty (post-boundary / trailing split).
        if (
            kind in _TURN_LEVEL_EVENT_TYPES
            and not _draft_is_contentful(drafts[-1])
            and len(drafts) > 1
        ):
            for prior in reversed(drafts[:-1]):
                if _draft_is_contentful(prior):
                    prior.apply(ev)
                    break
            else:
                drafts[-1].apply(ev)
            continue
        drafts[-1].apply(ev)
    _fold_trailing_observability(drafts)
    return _collapse_duplicate_drafts(drafts)


async def _humanize_text(text: Optional[str]) -> Optional[str]:
    """Resolve raw node ids (n.<Type>.<hex>) in assistant prose → human names.

    Best-effort: returns the text unchanged on any failure. See
    ``app/services/id_resolver.py``.
    """
    if not text or not isinstance(text, str):
        return text
    try:
        from app.services.id_resolver import humanize_ids

        return await humanize_ids(text)
    except Exception:  # noqa: BLE001
        logger.debug("ai_chat.humanize_failed", exc_info=True)
        return text


async def _humanize_text_parts(
    parts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Humanize ids inside ``{type:'text'}`` parts; leave tool-call parts alone."""
    out: List[Dict[str, Any]] = []
    for part in parts:
        if (
            isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        ):
            new_part = dict(part)
            new_part["text"] = await _humanize_text(part["text"])
            out.append(new_part)
        else:
            out.append(part)
    return out


def _observability_metadata(
    draft: "_AssistantDraft",
    *,
    interact_payload: Dict[str, Any],
    final_content: Optional[str],
) -> Dict[str, Any]:
    """Build the provider_metadata block that powers the response meta bar.

    Strip oversized model-call payloads from ``finalPayload`` before persist —
    each observability_metrics entry historically embedded the full
    ``system_prompt`` (~20–50k chars × N calls). That bloated ChatMessage rows
    to megabytes without helping the meta bar (which only needs usage/timing).
    """
    final_payload = _slim_final_payload(draft.final_payload)
    return {
        "steps": draft.steps,
        "timing": draft.timing,
        "interactPayload": interact_payload,
        **({"finalContent": final_content} if final_content else {}),
        **({"finalPayload": final_payload} if final_payload else {}),
        **({"error": draft.error} if draft.error else {}),
        "streaming": True,
    }


def _slim_final_payload(
    payload: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not payload:
        return payload
    import copy

    slim = copy.deepcopy(payload)
    interaction = slim.get("interaction")
    if not isinstance(interaction, dict):
        return slim
    metrics = interaction.get("observability_metrics")
    if not isinstance(metrics, list):
        return slim
    drop_keys = ("system_prompt", "history", "user_prompt")
    for entry in metrics:
        if not isinstance(entry, dict):
            continue
        data = entry.get("data")
        if isinstance(data, dict):
            for k in drop_keys:
                data.pop(k, None)
    return slim


def _draft_has_observability(draft: "_AssistantDraft") -> bool:
    return bool(
        draft.timing
        or draft.steps
        or draft.final_payload
        or draft.final_content
        or draft.error
    )


async def _patch_checkpointed_observability(
    thread: Any,
    drafts: List["_AssistantDraft"],
    *,
    start_index: int,
    interact_payload: Dict[str, Any],
) -> None:
    """Write folded turn-level stats onto bubbles already persisted at boundary.

    Checkpoint-on-boundary saves closed bubbles *before* ``final-content`` /
    ``step`` / ``message-finish`` arrive. When those events fold onto a prior
    contentful draft (trailing empty bubble), the final flush would skip them
    (``start_index`` already past). Patch the matching recent assistants.
    """
    if start_index <= 0:
        return
    prior = [
        d
        for d in drafts[:start_index]
        if _draft_is_contentful(d) and _draft_has_observability(d)
    ]
    if not prior:
        return
    messages = await chat_store.list_messages(thread)
    assistants = [m for m in messages if getattr(m, "role", None) == "assistant"]
    if len(assistants) < len(prior):
        return
    window = assistants[-len(prior) :]
    for draft, msg in zip(prior, window):
        final_content = (
            await _humanize_text(draft.final_content) if draft.final_content else None
        )
        meta = dict(getattr(msg, "provider_metadata", None) or {})
        meta.update(
            _observability_metadata(
                draft,
                interact_payload=interact_payload,
                final_content=final_content,
            )
        )
        msg.provider_metadata = meta
        await msg.save()


async def _persist_assistant_drafts(
    thread: Any,
    turn_events: List[Dict[str, Any]],
    *,
    start_index: int,
    end_index: int,
    interact_payload: Dict[str, Any],
) -> int:
    """Persist assistant bubbles in ``[start_index, end_index)``; return ``end_index``."""
    drafts = drafts_from_events(turn_events)
    # Observability may have folded onto an already-checkpointed bubble.
    await _patch_checkpointed_observability(
        thread,
        drafts,
        start_index=start_index,
        interact_payload=interact_payload,
    )
    for i in range(start_index, min(end_index, len(drafts))):
        parts = await _humanize_text_parts(drafts[i].to_parts())
        if not parts:
            continue
        draft = drafts[i]
        final_content = (
            await _humanize_text(draft.final_content) if draft.final_content else None
        )
        await chat_store.append_message(
            thread=thread,
            role="assistant",
            parts=parts,
            provider_metadata=_observability_metadata(
                draft,
                interact_payload=interact_payload,
                final_content=final_content,
            ),
        )
    return end_index


async def _persist_provider_session_if_needed(
    thread: Any,
    provider_session_seen: Optional[str],
) -> None:
    if provider_session_seen and provider_session_seen != thread.provider_session_id:
        await chat_store.update_provider_session(thread, provider_session_seen)


async def _pending_staged_for_turn(user_id: str, thread) -> list:
    """Cards still awaiting this user in this conversation. Never raises.

    Approval continuity must not be able to break a turn: if the staging
    store is unreachable the chat still works, it just loses the reminder.
    """
    try:
        return await list_unresolved_for_session(
            user_id, getattr(thread, "provider_session_id", None)
        )
    except Exception:  # pragma: no cover - defensive
        logger.debug("pending-approval lookup failed", exc_info=True)
        return []


async def _dismiss_pending_design_proposal_cards(
    *,
    user_id: str,
    session_id: Optional[str],
    thread,
) -> None:
    """Revoke inbox design_proposal cards after a chat affirm.

    Chat affirm stamps ``design_proposed.approved``; leaving the staged
    design card in the inbox made the model (and UI) look like a second
    Prompt Sheet gate was still open while the batch build ran — and the
    model narrated "approve the card" instead of finishing create_app_track.
    """
    if not session_id:
        return
    try:
        from app.agentive.staging import list_pending_tokens, revoke_token
        from app.services.prompt_queue import STATUS_CANCELLED, get_queue

        for prior in await list_pending_tokens(user_id, session_id):
            if prior.kind == "design_proposal":
                try:
                    await revoke_token(user_id=user_id, token=prior.token)
                except Exception:  # noqa: BLE001 — best-effort
                    logger.debug(
                        "dismiss design_proposal token failed token=%s",
                        prior.token,
                        exc_info=True,
                    )
        queue = get_queue(thread)
        dirty = False
        for item in queue.get("items") or []:
            if (
                item.get("write_kind") == "design_proposal"
                and item.get("status") == "pending"
            ):
                item["status"] = STATUS_CANCELLED
                dirty = True
        if dirty:
            thread.prompt_queue = queue
            await thread.save()
    except Exception:  # pragma: no cover - defensive
        logger.debug("dismiss design_proposal cards failed", exc_info=True)


async def _run_scaffold_continuation_turn(
    *,
    user_id: str,
    workspace_id: Optional[str],
    thread_id: str,
    session_id: Optional[str],
    prompt: str,
) -> None:
    """Drain one internal recovery turn after its predecessor released.

    This deliberately uses the public route handler under an in-process
    principal, the same capability boundary as routine tasks.  It therefore
    gets normal turn admission, workspace validation, run observability, and
    SSE persistence rather than a second special execution path.
    """
    from app.agentive.tooling.invoke import invoke_route_in_process

    turn_started = False
    try:
        result = await invoke_route_in_process(
            agent_turn,
            principal_id=user_id,
            scope=workspace_id,
            thread_id=thread_id,
            prompt=prompt,
            origin=_SCAFFOLD_RECOVERY_ORIGIN,
            json_body={"prompt": prompt, "origin": _SCAFFOLD_RECOVERY_ORIGIN},
        )
        body_iterator = getattr(result, "body_iterator", None)
        if body_iterator is not None:
            turn_started = True
            async for _chunk in body_iterator:
                pass
    except Exception:  # noqa: BLE001 -- background recovery must not fail chat
        logger.exception(
            "scaffold recovery turn failed user=%s thread=%s", user_id, thread_id
        )
    finally:
        # Once a StreamingResponse exists, its terminal callback releases the
        # claim before scheduling the next recovery. Releasing here as well
        # would clear the *next* turn's claim after it had been acquired.
        if not turn_started:
            await release_open_batch_auto_continuation(
                user_id=user_id,
                session_id=session_id,
            )


async def _invoke_affirmed_scaffold_commit(
    *, user_id: str, workspace_id: Optional[str], session_id: str
) -> Any:
    """Invoke the normal batch-control path for deterministic recovery."""
    from app.agentive.tooling.dispatch import _dispatch_batch_control

    return await _dispatch_batch_control(
        "integral_commit_batch",
        {},
        principal_id=user_id,
        scope=workspace_id,
        session_id=session_id,
        interaction_id=None,
    )


async def _dispatch_affirmed_scaffold_commit(
    *, user_id: str, workspace_id: Optional[str], session_id: Optional[str]
) -> str:
    """Finish a complete affirmed batch when the resident stops before commit.

    The chat affirmation already authorizes this exact greenfield batch.  A
    recovery turn normally asks the resident to call ``integral_commit_batch``
    itself, but the harness can run out of tool steps after it has supplied the
    final seed or view.  Reuse the normal batch-control dispatcher here rather
    than leaving a complete, approved build stranded behind model prose.
    """
    from app.services.agent_scope import current_scope_workspace_id

    if not session_id or peek_open_batch(user_id, session_id) is None:
        return ""
    scope_token = current_scope_workspace_id.set(workspace_id)
    try:
        result = await _invoke_affirmed_scaffold_commit(
            user_id=user_id,
            workspace_id=workspace_id,
            session_id=session_id,
        )
    except Exception:  # noqa: BLE001 -- recovery must leave the batch retryable
        logger.exception(
            "affirmed scaffold deterministic commit failed user=%s session=%s",
            user_id,
            session_id,
        )
        return ""
    finally:
        current_scope_workspace_id.reset(scope_token)
    data = getattr(result, "data", None)
    return str(data.get("_kind") or "") if isinstance(data, dict) else ""


async def _schedule_scaffold_continuation(
    *,
    status: str,
    user_id: str,
    thread: Any,
    workspace_id: Optional[str],
) -> bool:
    """Start a bounded recovery turn when an affirmed scaffold stops early.

    A batch is only eligible after a successful turn, while the original
    design affirmation remains valid, and when it is a greenfield scaffold.
    The batch-level claim makes duplicate terminal notifications harmless.
    """
    if status != "succeeded":
        return False
    session_id = getattr(thread, "provider_session_id", None)
    if not await chat_store.design_chat_affirmed_for_build(session_id):
        return False
    existing = peek_open_batch(user_id, session_id)
    if existing is None:
        return False
    kinds = set(existing.get("kinds") or [])
    if not kinds.intersection({"create_app", "author_operational_model"}):
        return False
    snapshot = await claim_open_batch_auto_continuation(
        user_id=user_id,
        session_id=session_id,
        max_attempts=_MAX_SCAFFOLD_AUTO_CONTINUATIONS,
    )
    if snapshot is None:
        return False

    marker = format_open_batch_marker(snapshot)
    prompt = (
        "[SYSTEM:CONTINUE-AFFIRMED-SCAFFOLD]\n"
        "The user already affirmed this design. Continue the open scaffold "
        "autonomously now. Do not ask the user a question and do not reply "
        "with a progress update. Use tools to append every missing operation, "
        "then call integral_commit_batch. Keep calling tools until it returns "
        "batch_applied / applied=true; only then describe the created app. If "
        "commit returns incomplete_scaffold, treat its missing list as the "
        "next tool-only repair task: append those exact operations and commit "
        "again. Never use reply while the batch remains open.\n\n"
        f"{marker}"
    )
    asyncio.create_task(
        _run_scaffold_continuation_turn(
            user_id=user_id,
            workspace_id=workspace_id,
            thread_id=thread.id,
            session_id=session_id,
            prompt=prompt,
        ),
        name=f"scaffold-recovery:{thread.id}",
    )
    return True


@endpoint(
    "/chat/threads/{thread_id}/messages",
    methods=["POST"],
    auth=True,
    tags=["AI Chat"],
)
async def send_message(
    request: Request,
    thread_id: str,
    text: str = "",
    images: Optional[List[Any]] = None,
    attachment_ids: Optional[List[str]] = None,
    focused_track_id: Optional[str] = None,
    focused_space_id: Optional[str] = None,
    focused_view_id: Optional[str] = None,
    entity_refs: Optional[List[EntityRef]] = None,
    page_context: Optional[PageContext] = None,
    # Forwarded by the frontend as a consistency check; the dispatcher
    # uses thread.agent_id as source-of-truth, so this is accepted but
    # not currently honored.
    agent_id: Optional[str] = None,  # noqa: ARG001
) -> StreamingResponse:
    """Stream a turn and persist both sides of the exchange."""
    from app.schemas.api.ai_chat import SendMessageRequest
    from app.services.chat_entity_refs import (
        entities_referenced_payload,
        focused_ids_from_resolved,
        resolve_entity_refs,
    )
    from app.services.chat_page_context import (
        build_page_context_preamble,
        lightweight_page_context_metadata,
        sanitize_user_text,
        wrap_injected_context,
        wrap_system_context,
    )

    user_id, email = _resolve_principal(request)
    images_raw: Optional[List[Any]] = images if isinstance(images, list) else None
    attachment_ids_raw: Optional[List[str]] = (
        attachment_ids if isinstance(attachment_ids, list) else None
    )
    try:
        raw_body = await request.json()
        if isinstance(raw_body, dict):
            if not text and raw_body.get("text"):
                text = str(raw_body["text"])
            if entity_refs is None and raw_body.get("entity_refs"):
                entity_refs = [
                    EntityRef.model_validate(item) for item in raw_body["entity_refs"]
                ]
            if images_raw is None and isinstance(raw_body.get("images"), list):
                images_raw = raw_body["images"]
            if attachment_ids_raw is None and isinstance(
                raw_body.get("attachment_ids"), list
            ):
                attachment_ids_raw = raw_body["attachment_ids"]
            if raw_body.get("focused_track_id") and not focused_track_id:
                focused_track_id = raw_body.get("focused_track_id")
            if raw_body.get("focused_space_id") and not focused_space_id:
                focused_space_id = raw_body.get("focused_space_id")
            if raw_body.get("focused_view_id") and not focused_view_id:
                focused_view_id = raw_body.get("focused_view_id")
            if page_context is None and raw_body.get("page_context"):
                page_context = PageContext.model_validate(raw_body["page_context"])
    except Exception:
        pass
    # Validate merged body shape (best-effort) and capture parsed images.
    try:
        parsed_body = SendMessageRequest.model_validate(
            {
                "text": text,
                "images": images_raw,
                "attachment_ids": attachment_ids_raw,
                "focused_track_id": focused_track_id,
                "focused_space_id": focused_space_id,
                "focused_view_id": focused_view_id,
                "entity_refs": entity_refs,
                "page_context": page_context,
            }
        )
    except Exception as exc:
        raise BadRequestError(message=str(exc)) from exc

    images = parsed_body.images or []
    attachment_ids = parsed_body.attachment_ids or []
    page_context = parsed_body.page_context
    if page_context:
        focused_track_id = focused_track_id or page_context.focused_track_id
        focused_space_id = focused_space_id or page_context.focused_app_id
        focused_view_id = focused_view_id or page_context.focused_view_id
    if not text and not images and not attachment_ids:
        raise BadRequestError(
            message="a message must have text, an image, or an attachment"
        )
    thread = await _resolve_owned_thread(thread_id, user_id)

    # The turn runs in the THREAD's workspace. The chat provider forwards
    # this into the agent's tool-execution path so read/list tools
    # (list_tracks, query_entries, etc.) filter by the workspace the
    # conversation belongs to. See ``_resolve_turn_workspace`` for why the
    # ``X-Integral-Scope`` header is a consistency check here, not the source.
    active_workspace_id = await _resolve_turn_workspace(request, user_id, thread)

    ref_resolution = await resolve_entity_refs(
        text,
        entity_refs,
        user_id,
        active_workspace_id,
    )
    if not focused_track_id or not focused_space_id:
        derived_track, derived_app = focused_ids_from_resolved(ref_resolution.resolved)
        focused_track_id = focused_track_id or derived_track
        focused_space_id = focused_space_id or derived_app

    # Resolve attachment_ids scoped to this thread (Slice B) into persisted
    # message parts + a context note the agent reads to know the file exists.
    attachment_file_parts, attachment_context_note = (
        await chat_store.resolve_message_attachments(thread, attachment_ids)
    )

    # Give each uploaded image a stable id; its bytes are retained on the
    # message part below so the resident can attach it to an entry on demand
    # (the Attachment is materialized only when that attach is approved — no
    # attachment-storage write here). Mirrors the attachment_ids note for files.
    image_ids = [uuid4().hex for _ in images]
    image_context_note = ""
    if images:
        note_lines = ["You received the following uploaded image(s) this turn:"]
        for idx, (img, iid) in enumerate(zip(images, image_ids), start=1):
            note_lines.append(f"- image {idx} ({img.content_type}, id={iid})")
        note_lines.append(
            "To attach an uploaded image to an entry (e.g. the user asks to "
            "file/post/attach it), you MUST call "
            "integral_attach_uploaded_image_to_entry with entry_id and image_id "
            "— authoring an entry from the image's contents does NOT attach the "
            "image file. If you are creating the entry in this same turn, "
            "entry_id is a staged token, so wrap it in a batch: "
            "integral_begin_batch / integral_create_entry / "
            'integral_attach_uploaded_image_to_entry(entry_id="{{entry.id}}", '
            "image_id=…) / integral_commit_batch. Never claim the image is "
            "attached unless you actually called that tool in a committed batch."
        )
        image_context_note = "\n".join(note_lines)

    # Utterance handed to the agent. On an image/file-only turn, give the
    # model a neutral cue so it engages with the attachment rather than an
    # empty string; the vision reflex still runs off image_urls regardless.
    # The user's own words are the last block; a typed ``[SYSTEM:`` cannot
    # pose as one of the host markers below. Every injected block is
    # delimited and framed as data (``wrap_injected_context``) so the model
    # does not read a page title or entry name as an instruction.
    agent_text = sanitize_user_text(text) or (
        "(No caption — please look at the attachment.)"
    )
    if image_context_note:
        agent_text = f"{image_context_note}\n\n---\n\n{agent_text}"
    if attachment_context_note:
        agent_text = f"{attachment_context_note}\n\n---\n\n{agent_text}"
    # Pending design body as context data on correction turns (procedure is in
    # skill integral_scaffold). Affirm only stamps approved — no tutoring.
    design_marker = getattr(thread, "design_proposed", None) or {}
    prior_design_body = chat_store.pending_design_context_for_utterance(
        marker=design_marker if isinstance(design_marker, dict) else None,
        user_turns_before_this_message=await chat_store.count_user_turns(thread),
        utterance=text or "",
    )
    prior_design_preamble = wrap_injected_context(
        "pending_design_proposal", prior_design_body
    )
    if prior_design_preamble:
        agent_text = f"{prior_design_preamble}\n\n---\n\n{agent_text}"
    if await chat_store.stamp_design_approved(thread=thread, utterance=text or ""):
        thread = await chat_store.get_thread(thread.id) or thread
        # Chat affirm *is* the design approval — drop the inbox "Confirm in
        # chat" design_proposal card so the model (and user) do not treat it
        # as a still-open Prompt Sheet gate while the batch build runs.
        await _dismiss_pending_design_proposal_cards(
            user_id=user_id,
            session_id=getattr(thread, "provider_session_id", None),
            thread=thread,
        )
    page_context_preamble = wrap_injected_context(
        "page_context", build_page_context_preamble(page_context)
    )
    if page_context_preamble:
        agent_text = f"{page_context_preamble}\n\n---\n\n{agent_text}"
    entity_refs_preamble = wrap_injected_context(
        "entity_refs", ref_resolution.context_preamble or ""
    )
    if entity_refs_preamble:
        agent_text = f"{entity_refs_preamble}\n\n---\n\n{agent_text}"

    provider = get_registry().get(thread.provider_id)
    if provider is None:
        raise BadRequestError(
            message=f"Unsupported provider for this thread: {thread.provider_id}"
        )
    if not provider.is_available():
        raise ServiceUnavailableError(
            message=(
                f"Provider {provider.id!r} is registered but not available — "
                "check the harness configuration on the server."
            )
        )

    # Reserve the thread BEFORE anything is persisted. A double-submit used
    # to write the user message (and clear pending_question) and only then
    # hit the 409 from acquire_turn — leaving a duplicate user bubble behind
    # a refused turn. Everything from here to the StreamingResponse runs
    # under the reservation and releases it if it raises.
    turn_handle = await chat_turn_registry.acquire_turn(
        thread_id=thread.id, user_id=user_id
    )
    try:
        return await _start_user_turn(
            request=request,
            thread=thread,
            user_id=user_id,
            email=email,
            provider=provider,
            turn_handle=turn_handle,
            text=text,
            agent_text=agent_text,
            images=images,
            image_ids=image_ids,
            attachment_file_parts=attachment_file_parts,
            entity_refs=entity_refs,
            page_context=page_context,
            ref_resolution=ref_resolution,
            focused_track_id=focused_track_id,
            focused_space_id=focused_space_id,
            focused_view_id=focused_view_id,
            active_workspace_id=active_workspace_id,
            wrap_system_context=wrap_system_context,
            entities_referenced_payload=entities_referenced_payload,
            lightweight_page_context_metadata=lightweight_page_context_metadata,
        )
    except BaseException:
        await chat_turn_registry.release_turn(thread.id)
        raise


async def _start_user_turn(
    *,
    request: Request,
    thread: Any,
    user_id: str,
    email: str,
    provider: Any,
    turn_handle: Any,
    text: str,
    agent_text: str,
    images: List[Any],
    image_ids: List[str],
    attachment_file_parts: List[Dict[str, Any]],
    entity_refs: Optional[List[EntityRef]],
    page_context: Optional[PageContext],
    ref_resolution: Any,
    focused_track_id: Optional[str],
    focused_space_id: Optional[str],
    focused_view_id: Optional[str],
    active_workspace_id: Optional[str],
    wrap_system_context: Any,
    entities_referenced_payload: Any,
    lightweight_page_context_metadata: Any,
) -> StreamingResponse:
    """Persist the user message and open the stream, under an acquired turn."""
    started = time.monotonic()
    from app.agentive.services.execution_runs import (
        finish_run,
        record_provider_event_step,
        start_run,
    )

    # Persist the user message first so it survives even if streaming aborts.
    user_text = text
    user_provider_metadata: Dict[str, Any] = {}
    if entity_refs:
        user_provider_metadata["entity_refs"] = [
            ref.model_dump(mode="json") for ref in entity_refs
        ]
    page_context_meta = lightweight_page_context_metadata(page_context)
    if page_context_meta:
        user_provider_metadata["page_context"] = page_context_meta
    # Persist each image part WITH its base64 bytes + stable id, so an uploaded
    # image can be materialized into an Attachment and filed onto an entry on
    # demand (see integral_attach_uploaded_image_to_entry). The vision reflex
    # still runs off the ephemeral image_urls payload; this is the durable copy.
    user_parts: List[Dict[str, Any]] = []
    if user_text:
        user_parts.append({"type": "text", "text": user_text})
    for img, iid in zip(images, image_ids):
        user_parts.append(
            {
                "type": "image",
                "content_type": img.content_type,
                "image_id": iid,
                "data": img.data,
            }
        )
    user_parts.extend(attachment_file_parts)
    await chat_store.append_message(
        thread=thread,
        role="user",
        parts=user_parts,
        provider_metadata=user_provider_metadata or None,
    )
    # Prompt Sheet locks the composer while open, so a user turn here is
    # either a resume append from the sheet or a non-sheet path. Clear any
    # leftover pending question markers / skip open question items.
    from app.services.prompt_queue import queue_is_open

    had_pending_question = getattr(thread, "pending_question", None) is not None
    if had_pending_question:
        thread.pending_question = None
    # Do not auto-skip the Prompt Sheet on ordinary turns — sheet owns
    # resolution. If somehow a turn arrives while open (API clients), leave
    # the queue; the sheet will still show on reload.
    _ = queue_is_open(thread)

    if not thread.title:
        fallback_title = (
            "Image" if images else ("File" if attachment_file_parts else "")
        )
        thread.title = _derive_thread_title(user_text or fallback_title)
        await thread.save()
    elif had_pending_question:
        await thread.save()

    extra_data: Dict[str, Any] = {}
    if thread.agent_id:
        extra_data["agent_id"] = thread.agent_id

    if images:
        # Feed the vision reflex: jvagent reads visitor.data["image_urls"].
        extra_data["image_urls"] = [
            {"base64": img.data, "mime_type": img.content_type} for img in images
        ]
    if ref_resolution.resolved:
        extra_data["entities_referenced"] = entities_referenced_payload(
            ref_resolution.resolved
        )
    if page_context:
        extra_data["page_context"] = page_context.model_dump(mode="json")
        thread.last_page_context = page_context.model_dump(mode="json")
        await thread.save()

    # Cards the user has neither approved nor rejected are LIVE turn state,
    # not a one-shot event. The [SYSTEM:STAGING-RESOLVED] marker only fires
    # when the user acts, so an ignored card produced nothing at all — the
    # model saw its own "I've staged X" with no outcome and re-proposed, or
    # claimed it had landed. Carry them into every turn until resolved.
    #
    # design_proposal is conversational confirm/correct — NOT a Prompt Sheet
    # write Approve. Including it here made the model parrot "awaiting your
    # approval on the card" and refuse mid-flight amends (live 2026-09-19).
    pending_staged = await _pending_staged_for_turn(user_id, thread)
    pending_writes = [
        sc for sc in pending_staged if getattr(sc, "kind", None) != "design_proposal"
    ]
    if pending_writes:
        extra_data["pending_approvals"] = [
            {
                "token": sc.token,
                "kind": sc.kind,
                "summary": sc.summary,
                "state": sc.state,
                "created_at": sc.created_at.isoformat(),
            }
            for sc in pending_writes
        ]
        # The marker goes in the UTTERANCE, not only in data: jvagent has no
        # schema for a custom data key, so a key alone would never reach the
        # prompt. This mirrors how [SYSTEM:STAGING-RESOLVED] lands in history,
        # and is what the model actually reads.
        marker = format_staging_pending_marker(pending_writes)
        if marker:
            extra_data["pending_approvals_marker"] = marker
            staging_block = wrap_system_context(
                "staging_pending",
                f"{marker}\n"
                "(Write awaiting user Approve on the Prompt Sheet. Do not "
                "re-stage it or report it as done.)",
            )
            agent_text = f"{staging_block}\n\n---\n\n{agent_text}"
    else:
        # Open batch with ops but no minted Prompt Sheet yet (early commit
        # refused, or model still appending). Without this marker the model
        # narrates "ready for WRITE · BATCH" and stops with 0 apps.
        open_snap = peek_open_batch(
            user_id, getattr(thread, "provider_session_id", None)
        )
        if open_snap and (open_snap.get("op_count") or 0) > 0:
            marker = format_open_batch_marker(open_snap)
            open_block = wrap_system_context("open_batch_incomplete", marker)
            agent_text = f"{open_block}" + "\n\n---\n\n" + agent_text

    if looks_like_approval(text) and not pending_writes:
        # User confirmed a prior plan but nothing is waiting on the Prompt
        # Sheet. Observed failure: model re-grounds (schema reads) then
        # narrates "I'll start filing" and ends the turn — no propose call,
        # so no approval card. Mirror the staging_pending injection: put the
        # instruction in the utterance so the orchestrator actually sees it.
        confirm_block = wrap_system_context(
            "user_confirmed_plan",
            "[SYSTEM:USER-CONFIRMED]\n"
            "The user confirmed. Call propose tools THIS turn "
            "(integral_create_entry / integral_file_content / "
            "integral_begin_batch → … → integral_commit_batch). "
            "Do not re-announce the plan. Do not ask for another "
            "go-ahead. Do not re-fetch schemas you already have. "
            "A text-only reply produces no approval card.",
        )
        agent_text = f"{confirm_block}\n\n---\n\n{agent_text}"

    try:
        run = await start_run(
            thread_id=thread.id,
            user_id=user_id,
            workspace_id=active_workspace_id or "",
            provider_id=provider.id,
            agent_id=thread.agent_id or "",
            metadata={"turn_id": turn_handle.turn_id},
        )
    except Exception:
        await chat_turn_registry.release_turn(thread.id)
        raise
    extra_data["run_id"] = run.run_id

    async def _finish_run(status: str, error: Optional[Dict[str, Any]]) -> None:
        await finish_run(run.run_id, status=status, error=error)
        await _schedule_scaffold_continuation(
            status=status,
            user_id=user_id,
            thread=thread,
            workspace_id=active_workspace_id,
        )

    async def _record_run_event(event: Dict[str, Any], *, ordinal: int) -> None:
        await record_provider_event_step(run.run_id, event, ordinal=ordinal)

    turn_ctx = ChatTurnContext(
        user_id=user_id,
        user_email=email,
        text=agent_text,
        thread_id=thread.id,
        session_id=thread.provider_session_id,
        focused_track_id=focused_track_id,
        focused_space_id=focused_space_id,
        focused_view_id=focused_view_id,
        workspace_id=active_workspace_id,
        start_time=started,
        extra_data=extra_data or None,
        is_disconnected=request.is_disconnected,
    )
    interact_payload: Dict[str, Any] = {
        "provider_id": provider.id,
        "provider_label": provider.label,
        "user_id": user_id,
        "text": user_text,
        "thread_id": thread.id,
        "session_id": thread.provider_session_id,
        "focused_track_id": focused_track_id,
        "focused_space_id": focused_space_id,
        "focused_view_id": focused_view_id,
    }
    interact_payload["run_id"] = run.run_id

    if page_context:
        interact_payload["page_context"] = page_context.model_dump(mode="json")

    await notify_thread_stream_update(
        user_id,
        thread.id,
        status="started",
        workspace_id=getattr(thread, "workspace_id", None) or None,
        turn_id=turn_handle.turn_id,
    )

    return StreamingResponse(
        generate_chat_turn_sse(
            request=request,
            thread=thread,
            user_id=user_id,
            provider=provider,
            turn_handle=turn_handle,
            turn_ctx=turn_ctx,
            interact_payload=interact_payload,
            drafts_from_events=drafts_from_events,
            persist_assistant_drafts=_persist_assistant_drafts,
            persist_provider_session_if_needed=_persist_provider_session_if_needed,
            on_terminal=_finish_run,
            on_event=_record_run_event,
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@endpoint(
    "/chat/threads/{thread_id}/agent-turn",
    methods=["POST"],
    auth=True,
    tags=["AI Chat"],
)
async def agent_turn(
    request: Request,
    thread_id: str,
    prompt: str = "",
    origin: str = "",
) -> StreamingResponse:
    """Start an agent-initiated streaming turn on a thread (workstream foundation).

    ``origin`` lets a caller other than the generic workstream trigger tag its
    turn distinctly (e.g. ``routine_task_scheduler`` passes
    ``origin="routine_task"` so the frontend can badge the resulting message
    as a scheduled run rather than an ad hoc workstream). Defaults to the
    original ``"agent_workstream"`` — existing callers are unaffected.
    """
    user_id, email = _resolve_principal(request)
    thread = await _resolve_owned_thread(thread_id, user_id)
    try:
        raw_body = await request.json()
        if isinstance(raw_body, dict):
            if raw_body.get("prompt"):
                prompt = str(raw_body["prompt"])
            if raw_body.get("origin"):
                origin = str(raw_body["origin"])
    except Exception:
        pass
    prompt = (prompt or "").strip()
    origin = (origin or "").strip() or "agent_workstream"

    active_workspace_id = await _resolve_turn_workspace(request, user_id, thread)
    provider = get_registry().get(thread.provider_id)
    if provider is None:
        raise BadRequestError(
            message=f"Unsupported provider for this thread: {thread.provider_id}"
        )
    if not provider.is_available():
        raise ServiceUnavailableError(
            message=f"Provider {provider.id!r} is registered but not available"
        )

    turn_handle = await chat_turn_registry.acquire_turn(
        thread_id=thread.id, user_id=user_id, origin=origin
    )
    started = time.monotonic()
    agent_text = prompt or "[agent workstream]"
    extra_data: Dict[str, Any] = {
        "trigger": "agent_workstream",
        "system_utterance": agent_text,
        "origin": origin,
    }
    if thread.agent_id:
        extra_data["agent_id"] = thread.agent_id

    from app.agentive.services.execution_runs import (
        finish_run,
        record_provider_event_step,
        start_run,
    )

    try:
        run = await start_run(
            thread_id=thread.id,
            user_id=user_id,
            workspace_id=active_workspace_id or "",
            provider_id=provider.id,
            agent_id=thread.agent_id or "",
            origin=origin,
            metadata={"turn_id": turn_handle.turn_id},
        )
    except Exception:
        await chat_turn_registry.release_turn(thread.id)
        raise
    extra_data["run_id"] = run.run_id
    scaffold_recovery_outcome: Dict[str, str] = {"value": ""}

    async def _persist_turn_drafts(
        draft_thread: Any,
        turn_events: List[Dict[str, Any]],
        *,
        start_index: int,
        end_index: int,
        interact_payload: Dict[str, Any],
    ) -> int:
        """Keep headless recovery prose out of the visible conversation.

        A retry may correctly continue after a model replies prematurely.  Its
        prose is retained in the execution trace, while the conversation gets
        one deterministic status only after the commit receipt is terminal.
        """
        if origin != _SCAFFOLD_RECOVERY_ORIGIN:
            return await _persist_assistant_drafts(
                draft_thread,
                turn_events,
                start_index=start_index,
                end_index=end_index,
                interact_payload=interact_payload,
            )
        scaffold_recovery_outcome["value"] = _scaffold_recovery_commit_outcome(
            turn_events
        )
        return end_index

    async def _finish_run(status: str, error: Optional[Dict[str, Any]]) -> None:
        await finish_run(run.run_id, status=status, error=error)
        if origin == _SCAFFOLD_RECOVERY_ORIGIN:
            session_id = getattr(thread, "provider_session_id", None)
            # The resident may have completed every operation but exhausted
            # its turn before issuing the final commit.  A chat affirmation
            # already blesses this greenfield batch, so make one deterministic
            # attempt through the exact same control path before scheduling
            # another model turn.
            if scaffold_recovery_outcome["value"] != "batch_applied":
                deterministic_outcome = await _dispatch_affirmed_scaffold_commit(
                    user_id=user_id,
                    workspace_id=active_workspace_id,
                    session_id=session_id,
                )
                if deterministic_outcome:
                    scaffold_recovery_outcome["value"] = deterministic_outcome
            await release_open_batch_auto_continuation(
                user_id=user_id,
                session_id=session_id,
            )
            scheduled = await _schedule_scaffold_continuation(
                status=status,
                user_id=user_id,
                thread=thread,
                workspace_id=active_workspace_id,
            )
            exhausted = (
                status == "succeeded"
                and not scheduled
                and peek_open_batch(user_id, session_id) is not None
            )
            await _append_scaffold_recovery_status(
                thread=thread,
                outcome=scaffold_recovery_outcome["value"],
                exhausted=exhausted,
            )

    async def _record_run_event(event: Dict[str, Any], *, ordinal: int) -> None:
        await record_provider_event_step(run.run_id, event, ordinal=ordinal)

    turn_ctx = ChatTurnContext(
        user_id=user_id,
        user_email=email,
        text="",
        thread_id=thread.id,
        session_id=thread.provider_session_id,
        workspace_id=active_workspace_id,
        start_time=started,
        extra_data=extra_data,
        is_disconnected=request.is_disconnected,
    )
    interact_payload: Dict[str, Any] = {
        "provider_id": provider.id,
        "provider_label": provider.label,
        "user_id": user_id,
        "text": "",
        "thread_id": thread.id,
        "session_id": thread.provider_session_id,
        "origin": origin,
        "run_id": run.run_id,
    }

    notify_extra = {"origin": origin}
    await notify_thread_stream_update(
        user_id,
        thread.id,
        status="started",
        workspace_id=getattr(thread, "workspace_id", None) or None,
        turn_id=turn_handle.turn_id,
        extra=notify_extra,
    )

    return StreamingResponse(
        generate_chat_turn_sse(
            request=request,
            thread=thread,
            user_id=user_id,
            provider=provider,
            turn_handle=turn_handle,
            turn_ctx=turn_ctx,
            interact_payload=interact_payload,
            drafts_from_events=drafts_from_events,
            persist_assistant_drafts=_persist_turn_drafts,
            persist_provider_session_if_needed=_persist_provider_session_if_needed,
            notify_extra=notify_extra,
            error_log_label="Agent-turn stream",
            on_terminal=_finish_run,
            on_event=_record_run_event,
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@endpoint(
    "/chat/threads/{thread_id}/cancel",
    methods=["POST"],
    auth=True,
    tags=["AI Chat"],
)
async def cancel_thread(request: Request, thread_id: str) -> Dict[str, Any]:
    """Best-effort cancel for an in-flight assistant turn on this thread."""
    user_id, _ = _resolve_principal(request)
    await _resolve_owned_thread(thread_id, user_id)
    cancelled = await chat_turn_registry.cancel_turn(thread_id)
    return {"thread_id": thread_id, "cancelled": cancelled}


@endpoint(
    "/chat/threads/{thread_id}/system-message",
    methods=["POST"],
    auth=True,
    tags=["AI Chat"],
)
async def append_system_message(
    request: Request,
    thread_id: str,
    text: str = "",
    role: str = "assistant",
) -> Dict[str, Any]:
    """Append a non-model-generated message to a thread for persistence.

    Why this exists: the chat history is the source of truth for what
    happened in a conversation. Some affordances (specifically the
    staging-card completion confirmations) emit deterministic text
    that the user expects to find in the thread on reload. Going
    through the normal ``POST /messages`` route would require a
    model run; that's both wasteful and unreliable for fixed-text
    confirmations. This route just persists.

    Auth: caller must own the thread (same check as every other
    thread endpoint). Body is bounded to keep this from being abused
    as a generic write-anything channel — only short, well-formed
    confirmation lines are permitted.
    """
    user_id, _ = _resolve_principal(request)
    if role not in {"assistant", "system"}:
        raise BadRequestError(message="role must be 'assistant' or 'system'")
    thread = await _resolve_owned_thread(thread_id, user_id)
    text = (text or "").strip()
    if not text:
        raise BadRequestError(message="text is required")
    message = await chat_store.append_message(
        thread=thread,
        role=role,
        parts=[{"type": "text", "text": text}],
        # Marker so future code can identify these as not-from-model
        # (e.g. when re-rendering UI affordances or excluding them
        # from token-usage rollups).
        provider_metadata={"source": "system_message", "kind": "staging_confirmation"},
    )
    return {
        "id": message.id,
        "thread_id": thread.id,
        "role": message.role,
        "parts": message.parts,
        "created_at": message.created_at,
    }
