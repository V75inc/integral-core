"""Persistence helpers for AI ChatThread / ChatMessage nodes.

Thin wrapper around jvspatial Node.find / Node.create. Lives here (not inline
in `app/api/ai_chat.py`) so future callers (skill engine, agent webhooks)
can share the same primitives.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.models.edges import CONTAINS, HAS_ATTACHMENT
from app.models.nodes import Attachment, ChatMessage, ChatThread
from app.utils.time import utc_now_iso


def _now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------


async def create_thread(
    *,
    user_id: str,
    provider_id: str,
    title: str = "",
    workspace_id: str = "",
    agent_id: str = "",
) -> ChatThread:
    """Create a new ChatThread node owned by ``user_id`` inside ``workspace_id``.

    ``workspace_id`` is the active workspace at thread-create time —
    resolved server-side from the ``X-Integral-Scope`` header by the
    HTTP handler before calling this helper. Default empty so legacy
    callers compile; new code paths must pass a real workspace.

    ``agent_id`` is the resolved agent binding (see agent-switcher Task 5
    resolution chain — body → preference → catalog default → 422). Default
    empty so legacy callers compile; multi-agent provider routes must pass
    a real id resolved at the HTTP boundary.
    """
    from app.services.app_graph import catalog_chat_thread

    now = _now()
    thread = await ChatThread.create(
        user_id=user_id,
        workspace_id=workspace_id,
        provider_id=provider_id,
        agent_id=agent_id or "",
        title=title or "",
        archived=False,
        created_at=now,
        updated_at=now,
        last_message_at=now,
    )
    if not workspace_id:
        raise ValueError(
            "workspace_id is required to create a graph-contiguous ChatThread"
        )
    await catalog_chat_thread(thread)
    return thread


async def get_thread(thread_id: str) -> Optional[ChatThread]:
    """Fetch a ChatThread by id, returning ``None`` on miss / error."""
    try:
        return await ChatThread.get(thread_id)
    except Exception:
        return None


async def list_threads(
    *,
    user_id: str,
    include_archived: bool = False,
    workspace_id: Optional[str] = None,
    provider_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> List[ChatThread]:
    """Return ``user_id``'s threads ordered by most recent activity.

    When ``workspace_id`` is set, narrow to that workspace.
    When BOTH ``provider_id`` and ``agent_id`` are set, narrow to threads
    stamped with that (provider, agent) pair. Either being absent means
    the agent dimension is not filtered — used by single-agent providers
    where the switcher is hidden.
    """
    base: Dict[str, Any] = {"context.user_id": user_id}
    if not include_archived:
        base["context.archived"] = False

    if workspace_id:
        base["context.workspace_id"] = workspace_id
    if provider_id and agent_id:
        base["context.provider_id"] = provider_id
        base["context.agent_id"] = agent_id
    threads = await ChatThread.find(base)

    threads.sort(
        key=lambda t: t.last_message_at or t.created_at or "",
        reverse=True,
    )
    return threads


async def rename_thread(thread: ChatThread, title: str) -> ChatThread:
    """Update a thread's title, refreshing ``updated_at``."""
    thread.title = title or ""
    thread.updated_at = _now()
    await thread.save()
    return thread


async def archive_thread(thread: ChatThread) -> ChatThread:
    """Soft-delete: mark the thread archived, keep the record."""
    thread.archived = True
    thread.updated_at = _now()
    await thread.save()
    return thread


async def unarchive_thread(thread: ChatThread) -> ChatThread:
    """Reverse of :func:`archive_thread`."""
    thread.archived = False
    thread.updated_at = _now()
    await thread.save()
    return thread


async def update_provider_session(thread: ChatThread, provider_session_id: str) -> None:
    """Persist the provider-side session id captured on the first turn."""
    if thread.provider_session_id == provider_session_id:
        return
    thread.provider_session_id = provider_session_id
    thread.updated_at = _now()
    await thread.save()


async def clear_provider_session(thread: ChatThread) -> None:
    """Drop a stale/orphaned provider session so the next turn can rebind."""
    if not thread.provider_session_id:
        return
    thread.provider_session_id = None
    thread.updated_at = _now()
    await thread.save()


async def touch_last_message(thread: ChatThread) -> None:
    """Bump ``last_message_at`` so the thread sorts to the top."""
    thread.last_message_at = _now()
    thread.updated_at = thread.last_message_at
    await thread.save()


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


async def append_message(
    *,
    thread: ChatThread,
    role: str,
    parts: List[Dict[str, Any]],
    parent_id: Optional[str] = None,
    provider_metadata: Optional[Dict[str, Any]] = None,
) -> ChatMessage:
    """Create a ChatMessage on ``thread`` and bump its last-activity timestamp.

    Wires a ``CONTAINS`` edge from ``thread`` → message so traversal stays
    on the object-spatial graph. ``message.thread_id`` is refreshed in the
    same write as a denormalized pointer cache (see ``ChatMessage`` docstring).
    """
    now = _now()
    message = await ChatMessage.create(
        thread_id=thread.id,
        role=role,
        parts=parts,
        parent_id=parent_id,
        provider_metadata=provider_metadata or {},
        created_at=now,
    )
    # Refresh transient tool-written markers before any save below. A tool
    # (record_design_proposed, record_pending_question) may set its marker on
    # a *separate* ChatThread instance mid-turn; jvspatial's row cache is not
    # an identity map, so the caller's instance is stale for that field.
    # Without this re-read, thread.connect / touch_last_message would persist
    # the stale None and clobber the marker — breaking the next turn's
    # commit_batch gate, or erasing the question the resident just asked so
    # its card reconciles to "already answered" on remount.
    if thread.id:
        current = await ChatThread.get(thread.id)
        if current is not None:
            thread.design_proposed = current.design_proposed
            thread.pending_question = current.pending_question
            thread.prompt_queue = current.prompt_queue
    await thread.connect(message, edge=CONTAINS, added_at=now)
    await touch_last_message(thread)
    return message


async def resolve_message_attachments(
    thread: ChatThread, attachment_ids: Optional[List[str]]
) -> "tuple[List[Dict[str, Any]], str]":
    """Resolve ``attachment_ids`` scoped to ``thread`` for one turn (Slice B).

    Silently drops any id that isn't actually attached to this thread —
    mirrors the soft-fail pattern used elsewhere on the chat surface
    (a stale/foreign id shouldn't fail the whole message). Returns
    ``(file_parts, context_note)``: ``file_parts`` are persisted onto the
    user's ``ChatMessage.parts`` so the read contract + reload show the
    file; ``context_note`` is prepended to the turn's utterance so the
    agent knows the file exists and how to read it (``integral_get_attachment_text``).
    """
    if not attachment_ids:
        return [], ""
    owned = await thread.nodes(
        edge=[HAS_ATTACHMENT], node=["Attachment"], direction="out"
    )
    owned_by_id = {a.id: a for a in owned if isinstance(a, Attachment)}
    valid = [owned_by_id[i] for i in attachment_ids if i in owned_by_id]
    if not valid:
        return [], ""

    file_parts: List[Dict[str, Any]] = []
    lines = ["The user attached the following file(s) to this message:"]
    for att in valid:
        size_kb = (att.size or 0) / 1024
        lines.append(
            f"- {att.filename} ({att.mime_type}, {size_kb:.1f} KB, id={att.id})"
        )
        file_parts.append(
            {
                "type": "file",
                "attachment_id": att.id,
                "filename": att.filename,
                "mime_type": att.mime_type,
                "size": att.size,
            }
        )
    lines.append(
        "Call integral_get_attachment_text with the attachment id to read a "
        "file's extracted text before answering questions about its contents."
    )
    lines.append(
        "If the user asks to attach/file/post this into an entry, track, or "
        "app, you MUST call integral_attach_uploaded_file_to_entry with "
        "entry_id and attachment_id after creating (or identifying) the "
        "entry — do not just mention the filename in the entry body or "
        "claim it is attached without calling that tool."
    )
    lines.append(
        "If that entry does not exist yet (you are creating it in this same "
        "turn), integral_create_entry is a proposal — its result is a "
        "staged_token, NOT the entry's real id, until the user approves it. "
        "You MUST wrap both calls in integral_begin_batch(...) / "
        "integral_create_entry(...) / "
        'integral_attach_uploaded_file_to_entry(entry_id="{{entry.id}}", '
        "attachment_id=...) / integral_commit_batch(...) so entry_id "
        "resolves to the real id once approved. NEVER pass a staged_token, "
        "or any id you are not certain is real, as entry_id — that fails "
        "with 'Entry not found'."
    )
    return file_parts, "\n".join(lines)


async def list_messages(thread: ChatThread) -> List[ChatMessage]:
    """Return all messages on a thread, oldest first.

    Traverses the ``CONTAINS`` edge (thread → message) — the
    ``thread_id`` scalar is a cache, not the lookup key.
    """
    messages = await thread.nodes(
        edge=[CONTAINS],
        node=["ChatMessage"],
        direction="out",
    )
    messages.sort(key=lambda m: m.created_at or "")
    return messages


async def find_uploaded_image(
    thread: ChatThread, image_id: Optional[str] = None
) -> Optional["tuple[str, str, str]"]:
    """Locate an image uploaded in this thread and return
    ``(filename, mime_type, base64_data)``.

    Images are persisted as ``{"type": "image", "image_id", "content_type",
    "data"}`` parts on the user's ChatMessage (see ai_chat.send_message). With
    ``image_id`` set, returns that exact image; otherwise the most recently
    uploaded one. Returns ``None`` when no matching image with retained bytes
    exists — used by the on-demand image-attach stager.
    """
    messages = await list_messages(thread)  # oldest first
    images: List[Dict[str, Any]] = []
    for m in messages:
        for part in getattr(m, "parts", None) or []:
            if (
                isinstance(part, dict)
                and part.get("type") == "image"
                and part.get("data")
            ):
                images.append(part)
    if not images:
        return None
    if image_id:
        part = next((p for p in images if p.get("image_id") == image_id), None)
    else:
        part = images[-1]  # most recently uploaded
    if part is None:
        return None
    mime = str(part.get("content_type") or "image/png")
    ext = mime.split("/")[-1] if "/" in mime else "png"
    stem = str(part.get("image_id") or "")[:8] or "1"
    return (f"pasted-image-{stem}.{ext}", mime, str(part.get("data") or ""))


async def get_thread_by_session(
    provider_session_id: str,
) -> Optional[ChatThread]:
    """Resolve the ChatThread for a provider session id (the staging/dispatch
    session key). jvspatial stores node scalars under ``context.<field>``."""
    if not provider_session_id:
        return None
    threads = await ChatThread.find(
        {"context.provider_session_id": provider_session_id}
    )
    return threads[0] if threads else None


async def count_user_turns(thread: ChatThread) -> int:
    """Number of role=="user" messages on the thread."""
    messages = await list_messages(thread)
    return sum(1 for m in messages if getattr(m, "role", "") == "user")


# Minimum body length for the user-visible design expansion. A one-liner
# summary alone is the audit trail; the proposal is what the chat UI renders.
# Soft floor — enough to force tracks/fields, not a novel.
_MIN_PROPOSAL_CHARS = 120


def _normalize_proposal_body(raw: Any) -> str:
    """Coerce proposal text; blank/whitespace-only becomes empty."""
    if raw is None:
        return ""
    return str(raw).strip()


async def design_awaiting_user_response(session_id: Optional[str]) -> bool:
    """True when a design was proposed and the user has not yet replied.

    Used by dispatch to refuse further tools on the propose turn (hard STOP),
    mirroring ``session_queue_is_open`` for Prompt Sheet.
    """
    if not session_id:
        return False
    thread = await get_thread_by_session(session_id)
    if thread is None:
        return False
    marker = getattr(thread, "design_proposed", None) or {}
    proposed_at = marker.get("proposed_at_user_turn")
    if not isinstance(proposed_at, int):
        return False
    current = await count_user_turns(thread)
    return current <= proposed_at


async def design_proposed_pending(session_id: Optional[str]) -> bool:
    """True while an unconsumed ``design_proposed`` marker sits on the thread.

    Cleared by ``commit_batch`` after a greenfield scaffold bless is minted.
    While set, create/apply staging must go through an open batch — otherwise
    each tool mints its own Prompt Sheet card and blocks the rest of the build.
    """
    if not session_id:
        return False
    thread = await get_thread_by_session(session_id)
    if thread is None:
        return False
    marker = getattr(thread, "design_proposed", None) or {}
    return isinstance(marker.get("proposed_at_user_turn"), int) and not marker.get(
        "build_receipt"
    )


async def record_design_build_receipt(
    *, session_id: str, user_id: str, batch_token: str
) -> bool:
    """Bind a successful scaffold batch to its affirmed design exactly once."""
    thread = await get_thread_by_session(session_id)
    if thread is None or getattr(thread, "user_id", None) != user_id:
        return False
    marker = dict(getattr(thread, "design_proposed", None) or {})
    if not marker.get("approved") or marker.get("build_receipt") or not batch_token:
        return False
    marker["build_receipt"] = {
        "batch_token": batch_token,
        "applied_at": utc_now_iso(),
        "user_turn": await count_user_turns(thread),
    }
    thread.design_proposed = marker
    await thread.save()
    return True


async def record_design_partial_build(
    *, session_id: str, user_id: str, batch_token: str
) -> bool:
    """Fence a partially applied design against a second App creation."""
    thread = await get_thread_by_session(session_id)
    if thread is None or getattr(thread, "user_id", None) != user_id:
        return False
    marker = dict(getattr(thread, "design_proposed", None) or {})
    if not marker.get("approved") or marker.get("build_receipt") or not batch_token:
        return False
    marker["partial_build"] = {"batch_token": batch_token, "failed_at": utc_now_iso()}
    thread.design_proposed = marker
    await thread.save()
    return True


async def recover_visible_design_for_affirmed_build(
    *, user_id: str, session_id: Optional[str], summary: str
) -> Optional[Dict[str, str]]:
    """Record an immediately preceding visible design when its user affirms.

    Tool use is the normal design contract.  This narrow recovery path handles
    a model that showed the full design in chat but omitted
    ``integral_propose_design`` before the user said "build it".  It only
    accepts an adjacent assistant proposal with enough operational structure,
    so a generic answer can never become a retroactive build authorization.
    """
    if not session_id:
        return None
    thread = await get_thread_by_session(session_id)
    if thread is None or (getattr(thread, "user_id", "") or "") != user_id:
        return None
    if getattr(thread, "design_proposed", None):
        return None

    messages = await list_messages(thread)
    latest_user_index = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if getattr(messages[index], "role", "") == "user"
            and _message_plain_text(messages[index])
        ),
        None,
    )
    if latest_user_index is None:
        return None
    user_text = _message_plain_text(messages[latest_user_index])
    if not looks_like_design_affirm(user_text):
        return None
    prior_assistant = next(
        (
            message
            for message in reversed(messages[:latest_user_index])
            if getattr(message, "role", "") == "assistant"
            and _message_plain_text(message)
        ),
        None,
    )
    proposal = _message_plain_text(prior_assistant) if prior_assistant else ""
    proposal_lower = proposal.lower()
    if (
        len(proposal) < _MIN_PROPOSAL_CHARS
        or "app" not in proposal_lower
        or not any(token in proposal_lower for token in ("track", "entry", "field"))
        or not any(token in proposal_lower for token in ("view", "table", "calendar"))
    ):
        return None

    current_turns = await count_user_turns(thread)
    if current_turns < 2:
        return None
    summary_text = (summary or "").strip() or "Affirmed app design"
    thread.design_proposed = {
        "proposed_at_user_turn": current_turns - 1,
        "summary": summary_text,
        "proposal": proposal,
        "proposed_at": utc_now_iso(),
        "approved": True,
        "approved_at": utc_now_iso(),
        "approved_via": "visible_chat_design_affirm",
    }
    await thread.save()
    return {"summary": summary_text, "proposal": proposal}


def _message_plain_text(message: ChatMessage) -> str:
    """Concatenate text parts from a chat message (empty if none)."""
    parts = getattr(message, "parts", None) or []
    chunks: List[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type", "text") in ("text", None, ""):
            t = part.get("text")
            if isinstance(t, str) and t.strip():
                chunks.append(t.strip())
    return "\n".join(chunks)


async def latest_user_message_text(thread: ChatThread) -> str:
    """Plain text of the most recent user message on the thread.

    Skips empty-bodied user turns (fixtures / focus markers often create
    role=user rows with no parts). ``list_messages`` is oldest-first; when
    ``created_at`` ties after heavy Object churn, reversed scan still
    prefers the newest message that actually has text.
    """
    messages = await list_messages(thread)
    for message in reversed(messages):
        if getattr(message, "role", "") != "user":
            continue
        text = _message_plain_text(message)
        if text:
            return text
    return ""


# Affirm vs correction — used to refuse re-propose on a pure "yes, build"
# turn. False positives (blocking a real amend) are worse than false
# negatives, so correction cues win when both match.
_DESIGN_AFFIRM_RE = re.compile(
    r"(?i)\b("
    r"go ahead|do it|build it|build that|"
    r"build (?:the|this|that) app|"
    r"looks good|lgtm|ship it|confirmed|confirm|as[- ]is|stage the build|"
    r"use the revised|use that|proceed|approve|"
    r"go for it|let'?s go|set it up|that works|works for me|all good|love it|"
    r"(?:sounds|looks) (?:good|great|fine|perfect|right)"
    r")\b"
)
_DESIGN_CORRECTION_RE = re.compile(
    r"(?i)\b("
    r"add|drop|remove|delete|change|alter|amend|instead|without|rename|"
    r"replace|swap|move|keep .+ on|fields? on|also include|please alter|"
    r"update the design|revise|tweaked?|different|not that|rather than|"
    r"i want|i need|i don'?t need|track when|daily rate|sometimes|"
    r"except|however|what about|how about"
    r")\b"
)
# A bare yes/ok affirms only as the entire reply: inside a longer message it
# too often prefixes a correction ("ok, make the notes private").
_BARE_AFFIRM_RE = re.compile(
    r"(?i)^\s*(?:yes|yep|yeah|yup|ok|okay|sure)(?:\s+please)?[\s.!]*$"
)
# Outbound effects no Core tool performs. A proposal that promises one with no
# blueprint operation is promising behaviour the build cannot deliver.
_CODE_ONLY_EFFECT_RE = re.compile(
    r"(?i)\b("
    r"sms|text messages?|texting|texts? (?:the |a |each )?(?:customers?|clients?)|"
    r"sends? (?:a |an )?(?:text|sms)|twilio|stripe|paypal|webhooks?|"
    r"charges? (?:the |their )?(?:card|customer)|payment (?:processing|gateway)"
    r")\b"
)


def _prior_proposal_excerpt(marker: Dict[str, Any], *, limit: int = 6000) -> str:
    """Pending design body for amend context (data, not procedure)."""
    prior = str(marker.get("proposal") or "").strip()
    if not prior:
        return ""
    if len(prior) > limit:
        prior = prior[:limit] + "\n…(prior proposal truncated)"
    if isinstance(marker.get("blueprint"), dict):
        prior += (
            f"\n\nPrior blueprint (revision {marker.get('blueprint_revision')}); "
            "keep the ids of unchanged items:\n"
            + json.dumps(marker["blueprint"], separators=(",", ":"))
        )
    return prior


def looks_like_design_affirm(text: str) -> bool:
    """True when the latest user reply is confirming a pending design."""
    t = (text or "").strip()
    if not t:
        return False
    if _DESIGN_CORRECTION_RE.search(t):
        return False
    return bool(_BARE_AFFIRM_RE.match(t) or _DESIGN_AFFIRM_RE.search(t))


async def design_amend_required(session_id: Optional[str]) -> bool:
    """True when a pending design needs a re-propose before build.

    After ``integral_propose_design``, the user must either affirm (then
    ``begin_batch``) or correct (then re-propose). A non-affirm reply while
    the marker is still unapproved means the old card is stale — tools other
    than ``integral_propose_design`` must not proceed until it is replaced.
    """
    if not session_id:
        return False
    thread = await get_thread_by_session(session_id)
    if thread is None:
        return False
    marker = getattr(thread, "design_proposed", None) or {}
    if not marker or marker.get("approved"):
        return False
    proposed_at = marker.get("proposed_at_user_turn")
    if not isinstance(proposed_at, int):
        return False
    current = await count_user_turns(thread)
    if current <= proposed_at:
        return False
    latest = await latest_user_message_text(thread)
    return not looks_like_design_affirm(latest)


async def design_chat_affirmed_for_build(session_id: Optional[str]) -> bool:
    """True when a pending design was chat-affirmed and is ready to build.

    Used by ``integral_commit_batch`` to auto-apply the greenfield scaffold
    without a second Prompt Sheet bless — the chat affirm *is* the approval.
    """
    if not session_id:
        return False
    thread = await get_thread_by_session(session_id)
    if thread is None:
        return False
    marker = getattr(thread, "design_proposed", None) or {}
    if not marker:
        return False
    if marker.get("build_receipt"):
        return False
    proposed_at = marker.get("proposed_at_user_turn")
    if not isinstance(proposed_at, int):
        return False
    # Affirm stamped on this turn (before the user message is persisted) still
    # counts — chat affirm IS the approval for the greenfield scaffold.
    if marker.get("approved"):
        return True
    current = await count_user_turns(thread)
    if current <= proposed_at:
        return False
    latest = await latest_user_message_text(thread)
    return looks_like_design_affirm(latest)


async def stamp_design_approved(
    *,
    thread: ChatThread,
    utterance: str,
) -> bool:
    """Stamp ``design_proposed.approved`` when the user affirms in chat.

    Returns True when the stamp was applied. No-op when there is no pending
    design or the utterance is not a pure affirm.
    """
    marker = dict(getattr(thread, "design_proposed", None) or {})
    if not marker or marker.get("approved"):
        return False
    if not looks_like_design_affirm(utterance):
        return False
    marker["approved"] = True
    marker["approved_at"] = utc_now_iso()
    marker["approved_via"] = "chat_affirm"
    thread.design_proposed = marker
    await thread.save()
    return True


def pending_design_context_for_utterance(
    *,
    marker: Optional[Dict[str, Any]],
    user_turns_before_this_message: int,
    utterance: str,
) -> str:
    """Pending design body when this turn is a correction (context data only).

    Procedure lives in skill ``integral_scaffold`` and tool refuse messages.
    The host only supplies the prior proposal text so an amend can merge
    deltas without rewriting from scratch.
    """
    if not marker or marker.get("approved"):
        return ""
    proposed_at = marker.get("proposed_at_user_turn")
    if not isinstance(proposed_at, int):
        return ""
    if user_turns_before_this_message < proposed_at:
        return ""
    if not (utterance or "").strip():
        return ""
    if looks_like_design_affirm(utterance):
        return ""
    return _prior_proposal_excerpt(marker)


# Back-compat alias used by older tests / call sites.
def design_amend_hint_for_utterance(
    *,
    marker: Optional[Dict[str, Any]],
    user_turns_before_this_message: int,
    utterance: str,
) -> str:
    """Alias for :func:`pending_design_context_for_utterance`."""
    return pending_design_context_for_utterance(
        marker=marker,
        user_turns_before_this_message=user_turns_before_this_message,
        utterance=utterance,
    )


async def record_design_proposed(
    *,
    user_id: str,
    session_id: Optional[str],
    summary: str,
    proposal: str = "",
    acceptance_assertions: Optional[List[str]] = None,
    target_app_id: Optional[str] = None,
    blueprint: Optional[Dict[str, Any]] = None,
) -> dict:
    """Record a design-proposal marker on the thread for this session.

    Ephemeral conversation state (no substrate write, no staged change) —
    mirrors set_focus_for_dispatch's fail-closed + ownership shape. The
    commit_batch greenfield gate reads + clears this marker.

    ``proposal`` is the full plain-language design. The agent must put that
    body in chat reply text (no design card). ``summary`` is the one-line
    audit label. ``acceptance_assertions`` carries the concrete facts the
    later verification readback must prove. Both text fields are required.

    Re-propose rules:
    - Marker already **approved** → refuse (``already_proposed``). User confirmed
      the shape; the next step is ``begin_batch`` + build, not another propose.
    - Marker pending and user replied with a **pure affirm** ("yes", "build it")
      → refuse (``affirm_build_instead``). Build via begin_batch, do not re-outline.
    - Marker pending and user has replied with a **correction** → allow replace
      (``replaced=True``). Mid-flight amends must land a new outline.
    - Same turn as original (before any user reply) → allow replace, keep earliest
      ``proposed_at_user_turn``.

    ``blueprint`` is the typed design (``app.schemas.design_blueprint``). Each
    replacement bumps ``blueprint_revision`` and records the item-ID diff; once
    a pending design has a blueprint, its amendments must carry one too.
    """
    if not session_id:
        return {
            "error": "session_required",
            "detail": (
                "propose_design needs a conversation session; the resident "
                "supplies it. The external dispatch contract carries none."
            ),
        }
    thread = await get_thread_by_session(session_id)
    if thread is None:
        return {"error": "not_found", "detail": "Chat thread not found"}
    if (getattr(thread, "user_id", "") or "") != user_id:
        return {
            "error": "forbidden",
            "detail": "Thread does not belong to the caller",
        }

    proposal_body = _normalize_proposal_body(proposal)
    summary_text = (summary or "").strip()
    if not summary_text:
        return {
            "error": "summary_required",
            "detail": "summary must be a non-empty one-line description of the shape.",
        }
    if len(proposal_body) < _MIN_PROPOSAL_CHARS:
        return {
            "error": "proposal_required",
            "detail": (
                "proposal must be the full plain-language design the user will "
                f"see (tracks, key fields, views) — at least {_MIN_PROPOSAL_CHARS} "
                "characters. Do not put the expansion only in reasoning; put it "
                "here so you can paste it into your reply."
            ),
        }

    assertions = [
        str(item).strip() for item in (acceptance_assertions or []) if str(item).strip()
    ][:32]

    existing = getattr(thread, "design_proposed", None) or {}
    prior_turn = existing.get("proposed_at_user_turn")
    current_turns = await count_user_turns(thread)
    receipt = existing.get("build_receipt") if isinstance(existing, dict) else None
    completed_prior_design = bool(
        isinstance(receipt, dict)
        and isinstance(receipt.get("user_turn"), int)
        and current_turns > receipt["user_turn"]
    )
    unbuildable = bool(((existing or {}).get("blueprint") or {}).get("open_decisions"))
    if (
        existing
        and existing.get("approved")
        and not completed_prior_design
        and not unbuildable
    ):
        return {
            "error": "already_proposed",
            "detail": (
                "The design is already approved. Do NOT call "
                "integral_propose_design again; build the approved shape "
                "unless its applied receipt has already been recorded."
            ),
        }

    if completed_prior_design:
        existing = {}
        prior_turn = None

    # Pending design + user replied with a pure affirm → build, don't re-outline.
    # Without this, the model often calls propose_design again with a slightly
    # different summary on "yes", which replaces the outline and never reaches
    # begin_batch → commit_batch.
    if (
        existing
        and not existing.get("approved")
        and isinstance(prior_turn, int)
        and current_turns > prior_turn
    ):
        latest = await latest_user_message_text(thread)
        if looks_like_design_affirm(latest):
            return {
                "error": "affirm_build_instead",
                "detail": (
                    "The user affirmed the pending design — do NOT call "
                    "integral_propose_design again. Call "
                    "integral_build_approved_design with the approved shape. "
                    "Chat affirmation applies that build without a second Prompt "
                    "Sheet; report it only when the receipt says applied."
                ),
            }

    canonical_blueprint: Optional[Dict[str, Any]] = None
    if blueprint is not None:
        from app.services.design_blueprint import validate_blueprint

        canonical_blueprint, blueprint_error = validate_blueprint(blueprint)
        if blueprint_error:
            return {
                "error": "invalid_blueprint",
                "detail": (
                    f"{blueprint_error}. The design is NOT recorded: fix the "
                    "blueprint and call integral_propose_design again now, "
                    "before presenting the design to the user."
                ),
            }
        from app.services.design_coverage import check_design_coverage

        coverage = await check_design_coverage(user_id, canonical_blueprint)
        if coverage["unsupported"]:
            return {
                "error": "unsupported_design",
                "unsupported": coverage["unsupported"],
                "detail": (
                    "The live substrate cannot build: "
                    + "; ".join(
                        f"{row['requirement']} ({row['detail']})"
                        for row in coverage["unsupported"]
                    )
                    + ". The design is NOT recorded: swap each for a supported "
                    "type, or move behaviour that needs custom code into "
                    "operations, then call integral_propose_design again before "
                    "presenting the design to the user."
                ),
            }
        spoken = f"{summary_text}\n{proposal_body}".casefold()
        promised = _CODE_ONLY_EFFECT_RE.search(spoken)
        if promised and not canonical_blueprint["operations"]:
            return {
                "error": "code_needs_unlisted",
                "detail": (
                    f"The proposal promises {promised.group(0)!r}, which no "
                    "built-in tool performs: it needs custom code from a trusted "
                    "App package. List it under blueprint operations and tell the "
                    "user in plain words that this part needs a custom add-on that "
                    "can't be set up from chat (never say package or integration), "
                    "or drop it from the design, then call integral_propose_design "
                    "again. The design is NOT recorded."
                ),
            }
        unnamed = [
            row["name"]
            for row in coverage["requires_trusted_package"]
            if row["name"].casefold() not in spoken
        ]
        if unnamed:
            return {
                "error": "trusted_package_unnamed",
                "detail": (
                    f"{', '.join(unnamed)} need custom code from a trusted App "
                    "package. Name each one in the proposal and tell the user in "
                    "plain words it needs a custom add-on that can't be set up "
                    "from chat (never say package or integration), then call "
                    "integral_propose_design again. The design is NOT recorded."
                ),
            }
    elif existing.get("blueprint"):
        return {
            "error": "blueprint_required",
            "detail": (
                "This design has a typed blueprint; an amendment must pass the "
                "complete revised blueprint, keeping item ids of unchanged items."
            ),
        }

    prior_proposal = _prior_proposal_excerpt(existing) if existing else ""
    replaced = bool(existing) and (
        (existing.get("summary") or "") != summary_text
        or (existing.get("proposal") or "") != proposal_body
        or existing.get("blueprint") != canonical_blueprint
    )

    # Same-turn re-propose keeps the earliest turn; an amend after the user
    # replies re-anchors so design_awaiting waits for the next reaction.
    if existing and isinstance(prior_turn, int) and current_turns > prior_turn:
        proposed_at_user_turn = current_turns
    else:
        proposed_at_user_turn = (
            prior_turn if isinstance(prior_turn, int) else current_turns
        )
    thread.design_proposed = {
        "proposed_at_user_turn": proposed_at_user_turn,
        "summary": summary_text,
        "proposal": proposal_body,
        "acceptance_assertions": assertions,
        "target_app_id": (target_app_id or "").strip(),
        "proposed_at": utc_now_iso(),
        # Clear any prior approve stamp when replacing a pending design.
        "approved": False,
    }
    blueprint_fields: Dict[str, Any] = {}
    if canonical_blueprint is not None:
        from app.services.design_blueprint import blueprint_diff, blueprint_digest

        prior_blueprint = existing.get("blueprint")
        blueprint_fields = {
            "blueprint": canonical_blueprint,
            "blueprint_revision": int(existing.get("blueprint_revision") or 0) + 1,
            "blueprint_digest": blueprint_digest(canonical_blueprint),
            "blueprint_diff": blueprint_diff(prior_blueprint, canonical_blueprint),
            "coverage": {
                "status": coverage["status"],
                "requires_trusted_package": coverage["requires_trusted_package"],
            },
        }
        thread.design_proposed.update(blueprint_fields)
    await thread.save()
    out = {
        "ok": True,
        "_kind": "design_outline",
        "summary": summary_text,
        "proposal": proposal_body,
        "acceptance_assertions": assertions,
        "replaced": replaced,
        "message": (
            "Design outline recorded. Put the FULL proposal markdown in your "
            "reply text for the user, then STOP — wait for confirm or correct. "
            "Do not begin_batch until they reply."
        ),
    }
    if prior_proposal and replaced:
        out["prior_proposal"] = prior_proposal
    out.update({k: v for k, v in blueprint_fields.items() if k != "blueprint"})
    return out


# Bound on how many choices a single question may offer. A model that wants
# twenty options is really asking an open question and should just ask it in
# prose — a twenty-button card is worse UI than a sentence.
MAX_QUESTION_OPTIONS = 6


def _normalize_question_options(raw: Any) -> List[Dict[str, str]]:
    """Coerce the model's ``options`` argument into ``[{label, description}]``.

    Accepts plain strings as well as objects, because that is what models
    actually emit under load. Blank labels are dropped, duplicates collapse
    (a card with two identical buttons is unanswerable), and the list is
    truncated rather than rejected — losing the seventh option is a better
    failure than losing the whole question.
    """
    options: List[Dict[str, str]] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, (list, tuple)) else []:
        if isinstance(item, str):
            label, description = item, ""
        elif isinstance(item, dict):
            label = str(item.get("label") or "")
            description = str(item.get("description") or "")
        else:
            continue
        label = label.strip()
        if not label or label in seen:
            continue
        seen.add(label)
        options.append({"label": label, "description": description.strip()})
        if len(options) >= MAX_QUESTION_OPTIONS:
            break
    return options


async def record_pending_question(
    *,
    user_id: str,
    session_id: Optional[str],
    question: str,
    options: Any,
    header: str = "",
    multi_select: bool = False,
) -> dict:
    """Enqueue a clarifying question onto the Prompt Sheet queue.

    Kept as the ask_user entrypoint for dispatch / tests. Multiple questions
    go through ``enqueue_questions`` via ``questions[]`` in dispatch.
    """
    from app.services.prompt_queue import enqueue_questions

    return await enqueue_questions(
        user_id=user_id,
        session_id=session_id,
        questions=[
            {
                "question": question,
                "options": options,
                "header": header,
                "multi_select": multi_select,
            }
        ],
    )


async def resolve_pending_question(
    *,
    user_id: str,
    thread: ChatThread,
    question_id: str = "",
    choices: Optional[List[str]] = None,
) -> dict:
    """Resolve a question item on the Prompt Sheet queue (compat wrapper)."""
    from app.services.prompt_queue import (
        ITEM_QUESTION,
        STATUS_PENDING,
        get_queue,
        resolve_question_item,
    )

    if (getattr(thread, "user_id", "") or "") != user_id:
        return {
            "error": "forbidden",
            "detail": "Thread does not belong to the caller",
        }

    queue = get_queue(thread)
    pending_qs = [
        i
        for i in queue["items"]
        if i.get("kind") == ITEM_QUESTION and i.get("status") == STATUS_PENDING
    ]
    if not pending_qs and not getattr(thread, "pending_question", None):
        return {"ok": True, "cleared": False, "detail": "No question pending"}

    item_id = question_id
    if not item_id and pending_qs:
        item_id = str(pending_qs[0].get("id") or "")
    # Legacy pending_question id path.
    legacy = getattr(thread, "pending_question", None) or {}
    if not item_id and legacy.get("question_id"):
        item_id = str(legacy["question_id"])

    if not item_id:
        return {"ok": True, "cleared": False, "detail": "No question pending"}

    # Stale id against open pending set.
    if (
        question_id
        and pending_qs
        and all(i.get("id") != question_id for i in pending_qs)
    ):
        if legacy.get("question_id") != question_id:
            return {
                "error": "stale_question",
                "detail": "That question is no longer the pending one",
            }

    result = await resolve_question_item(
        user_id=user_id,
        thread=thread,
        item_id=item_id,
        choices=choices,
        skip=False,
    )
    if result.get("error"):
        return result
    return {
        "ok": True,
        "cleared": True,
        "question_id": item_id,
        "choices": list(choices or []),
        "resume_text": result.get("resume_text"),
    }


async def clear_pending_question_for_session(session_id: Optional[str]) -> None:
    """Best-effort: skip/cancel open question items when the user replies in prose.

    Composer is locked while the Prompt Sheet is open, so this mainly covers
    legacy paths and non-sheet surfaces. Marks pending questions skipped so
    the queue can drain without inventing answers.
    """
    if not session_id:
        return
    thread = await get_thread_by_session(session_id)
    if thread is None:
        return
    from app.services.prompt_queue import (
        ITEM_QUESTION,
        STATUS_PENDING,
        get_queue,
        resolve_question_item,
    )

    queue = get_queue(thread)
    for item in list(queue["items"]):
        if item.get("kind") == ITEM_QUESTION and item.get("status") == STATUS_PENDING:
            await resolve_question_item(
                user_id=getattr(thread, "user_id", "") or "",
                thread=thread,
                item_id=str(item.get("id") or ""),
                skip=True,
            )
            # reload after each resolve
            thread = await ChatThread.get(thread.id) or thread
    if getattr(thread, "pending_question", None):
        thread.pending_question = None
        await thread.save()


async def delete_thread_messages(thread: ChatThread) -> int:
    """Hard-delete all messages for a thread via CONTAINS traversal."""
    messages = await thread.nodes(
        edge=[CONTAINS],
        node=["ChatMessage"],
        direction="out",
    )
    count = 0
    for msg in messages:
        try:
            await msg.delete()
            count += 1
        except Exception:
            continue
    return count


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def thread_to_dict(
    thread: ChatThread, message_count: Optional[int] = None
) -> Dict[str, Any]:
    """Serialize a ChatThread for the public ``/api/chat`` wire shape."""
    return {
        "id": thread.id,
        "provider_id": thread.provider_id,
        "agent_id": getattr(thread, "agent_id", "") or "",
        "workspace_id": getattr(thread, "workspace_id", "") or "",
        # Provider-side session id captured after the first turn (via the
        # `_meta` event the chat router consumes). Surfaced so admin /
        # debug clients can correlate a host thread with the harness's
        # own conversation graph; the FE runtime ignores it.
        "provider_session_id": thread.provider_session_id or None,
        "title": thread.title or "",
        "archived": bool(thread.archived),
        "created_at": thread.created_at,
        "updated_at": thread.updated_at,
        "last_message_at": thread.last_message_at,
        "message_count": message_count,
    }


def message_to_dict(message: ChatMessage) -> Dict[str, Any]:
    """Serialize a ChatMessage for the public ``/api/chat`` wire shape."""
    return {
        "id": message.id,
        "thread_id": message.thread_id,
        "role": message.role,
        "parts": message.parts or [],
        "parent_id": message.parent_id,
        "provider_metadata": message.provider_metadata or {},
        "created_at": message.created_at,
    }
