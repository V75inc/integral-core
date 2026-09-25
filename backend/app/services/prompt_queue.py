"""Durable PromptQueue on ChatThread — sequester sheet source of truth.

See ``docs/superpowers/specs/2026-09-08-prompt-sheet-design.md``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from app.models.nodes import ChatThread
from app.services.change_event import emit_change_event
from app.services.chat_threads import (
    _normalize_question_options,
    count_user_turns,
    get_thread_by_session,
)
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

QUEUE_STATUS_OPEN = "open"
QUEUE_STATUS_CLOSED = "closed"

ITEM_QUESTION = "question"
ITEM_STAGED_WRITE = "staged_write"

STATUS_PENDING = "pending"
STATUS_ANSWERED = "answered"
STATUS_SKIPPED = "skipped"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_CANCELLED = "cancelled"

_RESOLVED = frozenset(
    {
        STATUS_ANSWERED,
        STATUS_SKIPPED,
        STATUS_APPROVED,
        STATUS_REJECTED,
        STATUS_CANCELLED,
    }
)


def empty_queue() -> Dict[str, Any]:
    """Closed queue with no items — default / reset shape."""
    return {
        "status": QUEUE_STATUS_CLOSED,
        "opened_at": None,
        "closed_at": None,
        "close_reason": None,
        "items": [],
    }


def get_queue(thread: ChatThread) -> Dict[str, Any]:
    """Normalize ``ChatThread.prompt_queue`` into a durable queue dict."""
    raw = getattr(thread, "prompt_queue", None)
    if not isinstance(raw, dict):
        return empty_queue()
    items = raw.get("items")
    if not isinstance(items, list):
        items = []
    return {
        "status": raw.get("status") or QUEUE_STATUS_CLOSED,
        "opened_at": raw.get("opened_at"),
        "closed_at": raw.get("closed_at"),
        "close_reason": raw.get("close_reason"),
        "items": list(items),
    }


def queue_is_open(thread: ChatThread) -> bool:
    """True when the queue is open and still has pending items."""
    q = get_queue(thread)
    if q["status"] != QUEUE_STATUS_OPEN:
        return False
    return any(i.get("status") == STATUS_PENDING for i in q["items"])


def _ensure_open(queue: Dict[str, Any]) -> None:
    """Open a fresh sheet episode.

    Resolved items from a prior drained/cancelled sheet must not stack into
    the next open — resume already captured that episode. Mid-open enqueue
    (more questions / writes while pending remain) keeps the current items.
    """
    starting_fresh = queue["status"] != QUEUE_STATUS_OPEN or not any(
        i.get("status") == STATUS_PENDING for i in queue.get("items") or []
    )
    if starting_fresh:
        queue["status"] = QUEUE_STATUS_OPEN
        queue["opened_at"] = utc_now_iso()
        queue["closed_at"] = None
        queue["close_reason"] = None
        queue["items"] = []


async def _load_owned_thread(
    *, user_id: str, session_id: Optional[str]
) -> tuple[Optional[ChatThread], Optional[Dict[str, Any]]]:
    if not session_id:
        return None, {
            "error": "session_required",
            "detail": (
                "prompt queue needs a conversation session; the resident supplies "
                "it. The external dispatch contract carries none."
            ),
        }
    thread = await get_thread_by_session(session_id)
    if thread is None:
        return None, {"error": "not_found", "detail": "Chat thread not found"}
    if (getattr(thread, "user_id", "") or "") != user_id:
        return None, {
            "error": "forbidden",
            "detail": "Thread does not belong to the caller",
        }
    return thread, None


async def session_queue_is_open(session_id: Optional[str]) -> bool:
    """Dispatch gate helper — open queue for this provider session?"""
    if not session_id:
        return False
    thread = await get_thread_by_session(session_id)
    if thread is None:
        return False
    # The dispatch gate must use the same reconciled decision as the HTTP
    # reader. Otherwise a freshly restarted browser can submit before its
    # first poll and be blocked by a dead prompt card.
    result = await reconcile_staged_write_items(
        user_id=getattr(thread, "user_id", "") or "", thread=thread
    )
    if result.get("error"):
        return False
    return not bool(result.get("closed")) and queue_is_open(thread)


RESUME_MARKER = "[PROMPT_SHEET]"


def _short(text: str, limit: int = 90) -> str:
    t = " ".join((text or "").split())
    # Prefer curly quotes out so nested titles don't fight the outer ones.
    t = t.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    if len(t) <= limit:
        return t
    return t[: limit - 1].rstrip(" ,.—-\"'") + "…"


def build_resume_summary(queue: Dict[str, Any]) -> str:
    """Human-readable resume for the transcript + model.

    Prefixed with ``RESUME_MARKER``. Body is a title + bullet list so the
    chat UI can render items neatly (not one dense paragraph).
    """
    reason = queue.get("close_reason") or "drained"
    bullets: List[str] = []
    design_approved = False
    approved_writes: List[str] = []
    approved_profile_revision_drafts: List[str] = []
    for item in queue.get("items") or []:
        kind = item.get("kind")
        status = item.get("status")
        if kind == ITEM_QUESTION:
            q = _short(str(item.get("question") or "question"), 70)
            if status == STATUS_ANSWERED:
                ans = item.get("answer")
                if isinstance(ans, list):
                    ans_s = ", ".join(str(a) for a in ans)
                else:
                    ans_s = str(ans or "")
                bullets.append(f"Chose {_short(ans_s, 40)} — {q}")
            elif status == STATUS_SKIPPED:
                bullets.append(f"Skipped — {q}")
            elif status == STATUS_CANCELLED:
                bullets.append(f"Cancelled — {q}")
        elif kind == ITEM_STAGED_WRITE:
            summary = _short(
                str(item.get("summary") or item.get("write_kind") or "change"),
                80,
            )
            write_kind = str(item.get("write_kind") or "")
            if status == STATUS_APPROVED:
                if write_kind == "design_proposal":
                    bullets.append(f"Approved design — {summary}")
                    design_approved = True
                else:
                    bullets.append(f"Approved — {summary}")
                    approved_writes.append(summary)
                    # A revision approval applies its patch to a private draft,
                    # not to the Operational Model the user sees. Preserve the draft id
                    # from the staged envelope so the continuation turn can
                    # complete the mandatory diff -> publish lifecycle rather
                    # than treating a read of the published profile as proof.
                    if write_kind == "propose_profile_revision":
                        diff_machine = item.get("diff_machine")
                        if isinstance(diff_machine, dict):
                            draft_id = str(diff_machine.get("draft_id") or "").strip()
                            if (
                                draft_id
                                and draft_id not in approved_profile_revision_drafts
                            ):
                                approved_profile_revision_drafts.append(draft_id)
            elif status == STATUS_REJECTED:
                bullets.append(f"Rejected — {summary}")
            elif status == STATUS_CANCELLED:
                terminal_reason = str(item.get("terminal_reason") or "")
                if terminal_reason == "expired":
                    bullets.append(f"Expired without applying — {summary}")
                elif terminal_reason == "unavailable":
                    bullets.append(f"Approval record unavailable — {summary}")
                else:
                    bullets.append(f"Cancelled — {summary}")

    if reason == "cancelled":
        title = "Cancelled remaining prompts"
    elif len(bullets) <= 1:
        title = "Prompt resolved"
    else:
        title = "Resolved prompts"

    lines = [RESUME_MARKER, title]
    if bullets:
        lines.extend(f"• {b}" for b in bullets)
    # The resume turn is rendered in the user-visible transcript as a quiet
    # confirmation. Keep agent-only continuation instructions available to
    # the resident without showing a patronising implementation checklist to
    # the person who just pressed Approve.
    agent_directive: str | None = None
    if design_approved:
        # Bless only stamps the marker — apps/tracks land on the follow-on
        # batch build. Spell that out so "Please continue" alone does not
        # leave a consumed design with 0 apps (AGENT-17).
        agent_directive = (
            "Design confirmed. Call integral_begin_batch, then "
            "integral_create_app and integral_create_app_track "
            '(with app_id="{{app.id}}") for each track, then '
            "integral_commit_batch and STOP — wait for the user to Approve "
            "the build card. Do not claim apps exist until that approval."
        )
    else:
        if approved_profile_revision_drafts:
            draft_ids = ", ".join(approved_profile_revision_drafts)
            agent_directive = (
                "The approved profile revision above changed only an unpublished "
                f"draft ({draft_ids}). Do not claim the schema is live, read the "
                "published resource as validation, or substitute another profile "
                "mutation. Call integral_diff_model_draft for each draft id, "
                "explain the impact, then stage integral_publish_model_draft "
                "for the same draft and STOP for that separate approval. Only "
                "after the publish is consumed may you read back the live schema."
            )
        elif approved_writes:
            agent_directive = (
                "The approved writes above have already been applied. Do not "
                "repeat, re-stage, or cancel them. First read back the affected "
                "resource using the appropriate Integral read tool. Continue only "
                "with a separate, still-unfulfilled part of the user's request. "
                "UI focus may still point at the resource you just mutated — for "
                "any remaining work that names a different app or track, call "
                "integral_list_tracks (or list_apps) and pass an explicit "
                "track_id or track_hint; do not rely on focused_track_id."
            )
        else:
            unavailable = [
                item
                for item in queue.get("items") or []
                if item.get("kind") == ITEM_STAGED_WRITE
                and item.get("terminal_reason") == "unavailable"
            ]
            expired = [
                item
                for item in queue.get("items") or []
                if item.get("kind") == ITEM_STAGED_WRITE
                and item.get("terminal_reason") == "expired"
            ]
            if unavailable:
                agent_directive = (
                    "A prior approval record is unavailable. Do not claim its "
                    "change was applied. Read the affected resource before "
                    "proposing or retrying anything."
                )
            elif expired:
                agent_directive = (
                    "The expired writes above were not applied. Do not claim they "
                    "were applied or retry them without a fresh user request."
                )
            else:
                lines.append("Please continue.")
    if agent_directive:
        lines.extend(
            [
                "<!-- INTEGRAL_AGENT_DIRECTIVE",
                agent_directive,
                "-->",
            ]
        )
    return "\n".join(lines)


def _maybe_close(queue: Dict[str, Any], *, reason: str) -> Optional[str]:
    """Close when no pending items remain. Returns resume summary or None."""
    if any(i.get("status") == STATUS_PENDING for i in queue["items"]):
        return None
    queue["status"] = QUEUE_STATUS_CLOSED
    queue["closed_at"] = utc_now_iso()
    queue["close_reason"] = reason
    return build_resume_summary(queue)


async def enqueue_questions(
    *,
    user_id: str,
    session_id: Optional[str],
    questions: List[Dict[str, Any]],
) -> dict:
    """Enqueue one or more clarifying questions. Opens the queue if needed."""
    thread, err = await _load_owned_thread(user_id=user_id, session_id=session_id)
    if err:
        return err
    assert thread is not None

    if not questions:
        return {"error": "invalid_question", "detail": "questions must not be empty"}

    prepared: List[Dict[str, Any]] = []
    for q in questions:
        question = str((q or {}).get("question") or "").strip()
        if not question:
            return {
                "error": "invalid_question",
                "detail": "question must not be empty",
            }
        normalized = _normalize_question_options((q or {}).get("options"))
        if len(normalized) < 2:
            return {
                "error": "invalid_options",
                "detail": (
                    "ask_user needs at least 2 distinct options; ask in prose "
                    "instead when the answer is open-ended."
                ),
            }
        prepared.append(
            {
                "id": uuid.uuid4().hex,
                "kind": ITEM_QUESTION,
                "status": STATUS_PENDING,
                "created_at": utc_now_iso(),
                "resolved_at": None,
                "question": question,
                "header": str((q or {}).get("header") or "").strip(),
                "options": normalized,
                "allow_other": bool((q or {}).get("allow_other") or False),
                "multi_select": bool((q or {}).get("multi_select") or False),
                "asked_at_user_turn": await count_user_turns(thread),
                "answer": None,
            }
        )

    queue = get_queue(thread)
    _ensure_open(queue)
    queue["items"].extend(prepared)
    thread.prompt_queue = queue
    # Clear legacy single-slot marker if present.
    thread.pending_question = None
    await thread.save()

    first = prepared[0]
    return {
        "_kind": "user_question",
        "question_id": first["id"],
        "question": first["question"],
        "options": first["options"],
        "header": first["header"],
        "multi_select": first["multi_select"],
        "allow_other": first["allow_other"],
        "state": "pending",
        "queue_item_ids": [p["id"] for p in prepared],
        "queue_open": True,
    }


async def enqueue_staged_write(
    *,
    user_id: str,
    session_id: Optional[str],
    staged: Dict[str, Any],
) -> None:
    """Enqueue a pending staged write. No-op without session or if auto-blessed."""
    if not session_id:
        return
    if staged.get("state") != "pending":
        return
    if staged.get("autonomy_grant_used"):
        return
    token = staged.get("token")
    if not token:
        return

    thread, err = await _load_owned_thread(user_id=user_id, session_id=session_id)
    if err or thread is None:
        return

    queue = get_queue(thread)
    # Dedupe by token if already present.
    for item in queue["items"]:
        if item.get("kind") == ITEM_STAGED_WRITE and item.get("token") == token:
            return

    _ensure_open(queue)
    queue["items"].append(
        {
            "id": uuid.uuid4().hex,
            "kind": ITEM_STAGED_WRITE,
            "status": STATUS_PENDING,
            "created_at": utc_now_iso(),
            "resolved_at": None,
            "token": token,
            "write_kind": staged.get("kind") or "",
            "summary": staged.get("summary") or "",
            "diff_human": staged.get("diff_human"),
            "diff_machine": staged.get("diff_machine"),
            "staged_state": staged.get("state"),
            "autonomy_grant_used": bool(staged.get("autonomy_grant_used")),
            "expires_at": staged.get("expires_at"),
        }
    )
    thread.prompt_queue = queue
    await thread.save()


def _queue_event_kwargs(
    *,
    action: str,
    user_id: str,
    thread: ChatThread,
    after: Dict[str, Any],
) -> Dict[str, Any]:
    """Shared ChangeEvent payload for a prompt-queue mutation.

    Only the payload is shared; each caller invokes ``emit_change_event``
    itself. The D-05 guard follows exactly one level of delegation
    (handler -> service function that emits), so routing the call through a
    second helper would read as a bypass — and the guard is right to say so:
    the emit belongs at the write.
    """
    return {
        "actor_kind": "human",
        "actor_id": user_id,
        "action": action,
        "resource_type": "ChatThread",
        "resource_id": thread.id,
        "before": None,
        "after": after,
        "scope": f"thread:{thread.id}",
    }


async def resolve_question_item(
    *,
    user_id: str,
    thread: ChatThread,
    item_id: str,
    choices: Optional[List[str]] = None,
    skip: bool = False,
) -> dict:
    """Answer or skip a pending question item; may close the queue."""
    if (getattr(thread, "user_id", "") or "") != user_id:
        return {
            "error": "forbidden",
            "detail": "Thread does not belong to the caller",
        }
    queue = get_queue(thread)
    item = next((i for i in queue["items"] if i.get("id") == item_id), None)
    if item is None:
        return {"error": "not_found", "detail": "Prompt item not found"}
    if item.get("kind") != ITEM_QUESTION:
        return {"error": "wrong_kind", "detail": "Item is not a question"}
    if item.get("status") != STATUS_PENDING:
        return {
            "ok": True,
            "already_resolved": True,
            "item": item,
            "queue": queue,
            "resume_text": None,
        }

    if skip:
        item["status"] = STATUS_SKIPPED
        item["answer"] = None
    else:
        picks = list(choices or [])
        if not picks:
            return {
                "error": "invalid_answer",
                "detail": "choices required unless skip=true",
            }
        item["status"] = STATUS_ANSWERED
        item["answer"] = picks
    item["resolved_at"] = utc_now_iso()

    resume = _maybe_close(queue, reason="drained")
    thread.prompt_queue = queue
    await thread.save()
    try:
        await emit_change_event(
            **_queue_event_kwargs(
                action="prompt_queue.resolve_question",
                user_id=user_id,
                thread=thread,
                after={
                    "item_id": item_id,
                    "status": item.get("status"),
                    "closed": resume is not None,
                },
            )
        )
    except Exception:  # noqa: BLE001 — audit must not block the mutation
        logger.exception(
            "prompt_queue: change-event emit failed for prompt_queue.resolve_question"
        )
    return {
        "ok": True,
        "item": item,
        "queue": queue,
        "resume_text": resume,
        "closed": resume is not None,
    }


async def mark_write_item(
    *,
    user_id: str,
    thread: ChatThread,
    token: str,
    status: str,
) -> dict:
    """Mark a staged_write item approved/rejected after bless/revoke."""
    if status not in (STATUS_APPROVED, STATUS_REJECTED):
        return {"error": "invalid_status", "detail": f"bad status {status}"}
    if (getattr(thread, "user_id", "") or "") != user_id:
        return {
            "error": "forbidden",
            "detail": "Thread does not belong to the caller",
        }
    queue = get_queue(thread)
    item = next(
        (
            i
            for i in queue["items"]
            if i.get("kind") == ITEM_STAGED_WRITE and i.get("token") == token
        ),
        None,
    )
    if item is None:
        # Write may be Approvals-only (no session enqueue).
        return {"ok": True, "matched": False, "queue": queue, "resume_text": None}

    if item.get("status") == STATUS_PENDING:
        # "approved" must reflect a real bless, not the caller's assertion.
        # This endpoint runs *after* bless/revoke, but nothing verified that,
        # so a plain mark-write drained the sheet and produced a resume line
        # reading "Approved — <summary>" while the StagedChange sat pending —
        # telling the model a write had landed when it had not.
        from app.agentive.staging import get_token

        sc = await get_token(token)
        actual = getattr(sc, "state", None) if sc is not None else None
        # A blessing records the human decision, but it is not evidence that
        # the executor landed the mutation.  Keeping a merely-blessed card in
        # the sheet prevents the continuation prompt from asserting a write
        # happened after a validation, migration, or scope failure.
        expected = {
            STATUS_APPROVED: ("consumed",),
            STATUS_REJECTED: ("revoked", "rejected"),
        }[status]
        if actual not in expected:
            # Both directions matter. Checking only the approved path left the
            # mirror image open: the sheet could report "Rejected — <summary>"
            # and close while the token stayed live and blessable.
            return {
                "error": "state_mismatch",
                "detail": (
                    f"staged change is {actual!r}; wait for a successful "
                    "executor result before marking the queue item"
                ),
                "staged_state": actual,
            }
        item["status"] = status
        item["resolved_at"] = utc_now_iso()

    resume = _maybe_close(queue, reason="drained")
    thread.prompt_queue = queue
    await thread.save()
    try:
        await emit_change_event(
            **_queue_event_kwargs(
                action="prompt_queue.mark_write",
                user_id=user_id,
                thread=thread,
                after={
                    "token": token,
                    "status": item.get("status"),
                    "closed": resume is not None,
                },
            )
        )
    except Exception:  # noqa: BLE001 — audit must not block the mutation
        logger.exception(
            "prompt_queue: change-event emit failed for prompt_queue.mark_write"
        )
    return {
        "ok": True,
        "matched": True,
        "item": item,
        "queue": queue,
        "resume_text": resume,
        "closed": resume is not None,
    }


async def cancel_all(*, user_id: str, thread: ChatThread) -> dict:
    """Revoke pending writes, cancel open questions, keep already-approved."""
    if (getattr(thread, "user_id", "") or "") != user_id:
        return {
            "error": "forbidden",
            "detail": "Thread does not belong to the caller",
        }
    queue = get_queue(thread)
    if queue["status"] != QUEUE_STATUS_OPEN and not any(
        i.get("status") == STATUS_PENDING for i in queue["items"]
    ):
        return {
            "ok": True,
            "cancelled": False,
            "detail": "No open prompt queue",
            "queue": queue,
            "resume_text": None,
        }

    from app.agentive.staging import revoke_token

    # Collect now, revoke after the save. ``await revoke_token`` inside the
    # read-modify-write window was the only yield point in this function: a
    # concurrent enqueue that resumed there re-saved a pre-cancel snapshot,
    # silently undoing the cancel while the StagedChange stayed revoked — the
    # sheet reopened showing a "pending" item whose token was already dead.
    to_revoke: List[str] = []
    revoked: List[str] = []
    for item in queue["items"]:
        if item.get("status") != STATUS_PENDING:
            continue
        if item.get("kind") == ITEM_STAGED_WRITE:
            token = str(item.get("token") or "")
            if token:
                to_revoke.append(token)
            item["status"] = STATUS_CANCELLED
            item["resolved_at"] = utc_now_iso()
        elif item.get("kind") == ITEM_QUESTION:
            item["status"] = STATUS_CANCELLED
            item["resolved_at"] = utc_now_iso()

    resume = _maybe_close(queue, reason="cancelled")
    if resume is None:
        # Force close even if somehow pending remained.
        queue["status"] = QUEUE_STATUS_CLOSED
        queue["closed_at"] = utc_now_iso()
        queue["close_reason"] = "cancelled"
        resume = build_resume_summary(queue)

    thread.prompt_queue = queue
    await thread.save()

    # Revoke AFTER the queue is persisted — see the note above `to_revoke`.
    for token in to_revoke:
        try:
            await revoke_token(user_id=user_id, token=token)
            revoked.append(token)
        except Exception:  # noqa: BLE001 — the sheet must still close
            logger.exception("prompt_queue: revoke failed for token %s", token)

    try:
        await emit_change_event(
            **_queue_event_kwargs(
                action="prompt_queue.cancel_all",
                user_id=user_id,
                thread=thread,
                after={"revoked_tokens": revoked, "closed": True},
            )
        )
    except Exception:  # noqa: BLE001 — audit must not block the mutation
        logger.exception(
            "prompt_queue: change-event emit failed for prompt_queue.cancel_all"
        )
    return {
        "ok": True,
        "cancelled": True,
        "revoked_tokens": revoked,
        "queue": queue,
        "resume_text": resume,
        "closed": True,
    }


async def reconcile_staged_write_items(*, user_id: str, thread: ChatThread) -> dict:
    """Close prompt items whose durable staging decision is already terminal.

    The Prompt Sheet is durable on ``ChatThread`` while staging decisions are
    durable in the staging store. They can therefore be observed independently
    after a restart or a delayed browser poll. A pending sheet item is only
    actionable while its staging decision remains pending or blessed. Reconcile
    the two sources before returning a sheet so an unavailable approval never
    traps the composer behind controls that can no longer work.
    """
    if (getattr(thread, "user_id", "") or "") != user_id:
        return {
            "error": "forbidden",
            "detail": "Thread does not belong to the caller",
        }

    queue = get_queue(thread)
    if not queue_is_open(thread):
        return {
            "ok": True,
            "reconciled": False,
            "queue": queue,
            "resume_text": None,
            "closed": False,
        }

    from app.agentive.staging import get_token

    changed = False
    for item in queue["items"]:
        if (
            item.get("kind") != ITEM_STAGED_WRITE
            or item.get("status") != STATUS_PENDING
        ):
            continue
        token = str(item.get("token") or "")
        staged = await get_token(token) if token else None
        state = getattr(staged, "state", None) if staged is not None else None
        terminal = {
            "consumed": (STATUS_APPROVED, "consumed"),
            "revoked": (STATUS_REJECTED, "revoked"),
            "expired": (STATUS_CANCELLED, "expired"),
            None: (STATUS_CANCELLED, "unavailable"),
        }.get(state)
        if terminal is None:
            # ``pending`` and ``blessed`` remain actionable. An unfamiliar
            # non-terminal state is safer left visible than guessed at.
            continue
        item["status"], item["terminal_reason"] = terminal
        item["resolved_at"] = utc_now_iso()
        changed = True

    if not changed:
        return {
            "ok": True,
            "reconciled": False,
            "queue": queue,
            "resume_text": None,
            "closed": False,
        }

    resume = _maybe_close(queue, reason="reconciled")
    thread.prompt_queue = queue
    await thread.save()
    try:
        await emit_change_event(
            **_queue_event_kwargs(
                action="prompt_queue.reconcile_staged_writes",
                user_id=user_id,
                thread=thread,
                after={
                    "closed": resume is not None,
                    "resolved_tokens": [
                        str(item.get("token") or "")
                        for item in queue["items"]
                        if item.get("kind") == ITEM_STAGED_WRITE
                        and item.get("terminal_reason")
                    ],
                },
            )
        )
    except Exception:  # noqa: BLE001 — audit must not block sheet recovery
        logger.exception("prompt_queue: change-event emit failed for reconciliation")
    return {
        "ok": True,
        "reconciled": True,
        "queue": queue,
        "resume_text": resume,
        "closed": resume is not None,
    }


async def get_open_queue_for_thread(*, user_id: str, thread: ChatThread) -> dict:
    """Ownership-checked, reconciled open-queue snapshot for HTTP callers."""
    result = await reconcile_staged_write_items(user_id=user_id, thread=thread)
    if result.get("error"):
        return result
    queue = result["queue"]
    open_ = queue_is_open(thread) if not result.get("closed") else False
    return {
        "ok": True,
        "open": open_,
        "queue": queue if open_ else empty_queue(),
        "resume_text": result.get("resume_text"),
        "closed": bool(result.get("closed")),
    }


# Re-export for callers that still normalize options the old way.
__all__ = [
    "cancel_all",
    "enqueue_questions",
    "enqueue_staged_write",
    "get_open_queue_for_thread",
    "get_queue",
    "mark_write_item",
    "reconcile_staged_write_items",
    "queue_is_open",
    "resolve_question_item",
    "session_queue_is_open",
]
