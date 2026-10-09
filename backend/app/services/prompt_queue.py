"""Durable PromptQueue on ChatThread — sequester sheet source of truth.

See ``docs/backend/prompt-queue.md``.
"""

from __future__ import annotations

import logging
import re
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

# Bound sheet spam for "delete everything" style multi-propose turns.
MAX_PENDING_STAGED_WRITES = 20

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

# Legacy HTML comment that older resume turns stuffed into the user bubble.
# New resumes keep agent continuation out of the transcript entirely
# (``build_resume_agent_directive`` → ``wrap_system_context`` at send time).
_LEGACY_AGENT_DIRECTIVE_RE = re.compile(
    r"<!--\s*INTEGRAL_AGENT_DIRECTIVE[\s\S]*?-->",
    re.IGNORECASE,
)


def _short(text: str, limit: int = 90) -> str:
    t = " ".join((text or "").split())
    # Prefer curly quotes out so nested titles don't fight the outer ones.
    t = t.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    if len(t) <= limit:
        return t
    return t[: limit - 1].rstrip(" ,.—-\"'") + "…"


def _resume_episode_facts(
    queue: Dict[str, Any],
) -> tuple[List[str], bool, List[str], List[str]]:
    """Collect display bullets + flags used by title + agent directive."""
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
                bullets.append(f"{_short(ans_s, 40)} — {q}")
            elif status == STATUS_SKIPPED:
                bullets.append(f"Skipped: {q}")
            elif status == STATUS_CANCELLED:
                bullets.append(f"Dismissed: {q}")
        elif kind == ITEM_STAGED_WRITE:
            summary = _short(
                str(item.get("summary") or item.get("write_kind") or "change"),
                80,
            )
            write_kind = str(item.get("write_kind") or "")
            if status == STATUS_APPROVED:
                if write_kind == "design_proposal":
                    bullets.append(summary)
                    design_approved = True
                else:
                    bullets.append(summary)
                    approved_writes.append(summary)
                    # A revision approval applies its patch to a private draft,
                    # not to the Operational Model the user sees. Preserve the
                    # draft id from the staged envelope so the continuation
                    # turn can complete diff → publish rather than treating a
                    # read of the published profile as proof.
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
                bullets.append(f"Didn't apply — {summary}")
            elif status == STATUS_CANCELLED:
                terminal_reason = str(item.get("terminal_reason") or "")
                if terminal_reason == "expired":
                    bullets.append(f"{summary} — timed out before applying")
                elif terminal_reason == "unavailable":
                    bullets.append(f"{summary} — couldn't confirm")
                else:
                    bullets.append(f"Dismissed — {summary}")
    return (
        bullets,
        design_approved,
        approved_writes,
        approved_profile_revision_drafts,
    )


def _resume_title(
    *,
    reason: str,
    bullets: List[str],
    design_approved: bool,
    queue: Dict[str, Any],
) -> str:
    """Quiet residual-state title (ChatGPT / Cursor tone — past, factual)."""
    if reason == "cancelled":
        return "Remaining prompts dismissed"
    items = list(queue.get("items") or [])
    statuses = [str(i.get("status") or "") for i in items]
    if items and all(s == STATUS_REJECTED for s in statuses):
        return "Change not applied"
    if items and all(
        i.get("kind") == ITEM_STAGED_WRITE
        and i.get("status") == STATUS_CANCELLED
        and str(i.get("terminal_reason") or "") in ("expired", "unavailable")
        for i in items
    ):
        return "Change didn't go through"
    if items and all(i.get("kind") == ITEM_QUESTION for i in items):
        return "Got your answer" if len(bullets) <= 1 else "Got your answers"
    if design_approved and len(bullets) <= 1:
        return "Design saved"
    if len(bullets) <= 1:
        return "Updates applied"
    return "You confirmed a few changes"


def build_resume_agent_directive(queue: Dict[str, Any]) -> Optional[str]:
    """Host-only continuation for the resident after a sheet drains.

    Injected at send time via ``wrap_system_context`` — never persisted in the
    user-visible resume bubble.
    """
    (
        _bullets,
        design_approved,
        approved_writes,
        approved_profile_revision_drafts,
    ) = _resume_episode_facts(queue)

    if design_approved:
        # Bless only stamps the marker — apps/tracks land on the follow-on
        # batch build. Spell that out so a bare residual does not leave a
        # consumed design with 0 apps (AGENT-17).
        return (
            "Design confirmed. Call integral_begin_batch, then "
            "integral_create_app and integral_create_app_track "
            '(with app_id="{{app.id}}") for each track, then '
            "integral_commit_batch and STOP — wait for the user to Approve "
            "the build card. Do not claim apps exist until that approval."
        )
    if approved_profile_revision_drafts:
        draft_ids = ", ".join(approved_profile_revision_drafts)
        reviewed_ids = {
            str(item.get("profile_revision_draft_id") or "")
            for item in queue.get("items") or []
            if item.get("profile_revision_review")
        }
        missing_review = [
            draft_id
            for draft_id in approved_profile_revision_drafts
            if draft_id not in reviewed_ids
        ]
        if missing_review:
            return (
                "The approved profile revision changed only an unpublished "
                f"draft ({', '.join(missing_review)}). Its server-computed diff "
                "could not be prepared for the user-visible review turn. Do not "
                "stage publication or claim the schema is live. Explain that "
                "the draft remains unpublished and needs review."
            )
        return (
            "The approved profile revision above changed only an unpublished "
            f"draft ({draft_ids}). Its exact server-computed diff and entry "
            "impact are already shown in the user-visible review message. Do "
            "not claim the schema is live or substitute another profile "
            "mutation. Stage integral_publish_model_draft for the same draft "
            "as a separate approval, then STOP and wait for that approval. "
            "Only after publish is consumed may you read back the live schema."
        )
    if approved_writes:
        return (
            "The approved writes above have already been applied. Do not "
            "repeat, re-stage, or cancel them. First read back the affected "
            "resource using the appropriate Integral read tool. Continue only "
            "with a separate, still-unfulfilled part of the user's request. "
            "UI focus may still point at the resource you just mutated — for "
            "any remaining work that names a different app or track, call "
            "integral_list_tracks (or list_apps) and pass an explicit "
            "track_id or track_hint; do not rely on focused_track_id."
        )
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
        return (
            "A prior approval record is unavailable. Do not claim its "
            "change was applied. Read the affected resource before "
            "proposing or retrying anything."
        )
    if expired:
        return (
            "The expired writes above were not applied. Do not claim they "
            "were applied or retry them without a fresh user request."
        )
    if (
        any(
            item.get("status") in {STATUS_REJECTED, STATUS_CANCELLED}
            for item in queue.get("items") or []
        )
        or queue.get("close_reason") == "cancelled"
    ):
        return (
            "The user rejected or cancelled the changes above. They were not "
            "applied. Acknowledge that outcome; do not re-stage, retry, or "
            "continue those writes without a fresh user request. This host "
            "continuation is not a new approval or instruction to write."
        )
    return None


def build_resume_summary(queue: Dict[str, Any]) -> str:
    """User-visible residual state after a Prompt Sheet drains.

    Prefixed with ``RESUME_MARKER`` for FE detection. Body is a quiet title +
    bullets (ChatGPT / Cursor tone). Agent continuation lives in
    ``build_resume_agent_directive`` and is wrapped at send time — not here.
    """
    reason = queue.get("close_reason") or "drained"
    (
        bullets,
        design_approved,
        _approved_writes,
        _drafts,
    ) = _resume_episode_facts(queue)
    title = _resume_title(
        reason=reason,
        bullets=bullets,
        design_approved=design_approved,
        queue=queue,
    )
    lines = [RESUME_MARKER, title]
    if bullets:
        lines.extend(f"• {b}" for b in bullets)
    # A profile revision approval changes only a private draft. Compute its
    # persisted diff before the continuation can stage the separate publish
    # approval, and put that diff in this user-visible resume message. This
    # keeps the review boundary deterministic even if the resident skips the
    # diff tool call or answers the stale request instead.
    for item in queue.get("items") or []:
        if (
            item.get("kind") == ITEM_STAGED_WRITE
            and item.get("status") == STATUS_APPROVED
            and item.get("write_kind") == "propose_profile_revision"
        ):
            review = str(item.get("profile_revision_review") or "").strip()
            if review:
                lines.append(f"• {review}")
            elif item.get("profile_revision_review_error"):
                lines.append(
                    "• The profile draft diff could not be computed. The draft "
                    "remains unpublished; no publish approval can be staged yet."
                )
    return "\n".join(lines)


def _summarize_profile_revision_diff(payload: Dict[str, Any]) -> str:
    """Turn a server-computed draft diff into a compact user-visible review."""
    diff = payload.get("diff") or {}
    changes: List[str] = []
    for section in (
        "entry_types",
        "fields",
        "views",
        "tags",
        "relations",
        "field_types",
        "view_types",
    ):
        section_diff = diff.get(section) or {}
        for action in ("added", "removed", "changed"):
            rows = section_diff.get(action) or []
            names = [
                str(row.get("name") or row.get("key") or row.get("id") or "unnamed")
                for row in rows
                if isinstance(row, dict)
            ]
            if names:
                detail = f"{action} {section.replace('_', ' ')}: {', '.join(names)}"
                if section == "entry_types":
                    nested_details = []
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        type_name = str(row.get("name") or row.get("key") or "")
                        field_delta = row.get("fields")
                        if isinstance(field_delta, list):
                            field_names = [
                                str(field.get("name") or field.get("key"))
                                for field in field_delta
                                if isinstance(field, dict)
                                and (field.get("name") or field.get("key"))
                            ]
                            if field_names:
                                nested_details.append(
                                    f"{type_name} fields: {', '.join(field_names)}"
                                )
                        elif isinstance(field_delta, dict):
                            for field_action in ("added", "removed", "changed"):
                                field_rows = field_delta.get(field_action) or []
                                field_names = [
                                    str(field.get("name") or field.get("key"))
                                    for field in field_rows
                                    if isinstance(field, dict)
                                    and (field.get("name") or field.get("key"))
                                ]
                                if field_names:
                                    nested_details.append(
                                        f"{type_name} {field_action} fields: "
                                        f"{', '.join(field_names)}"
                                    )
                    if nested_details:
                        detail += f" ({'; '.join(nested_details)})"
                changes.append(detail)

    impacts = payload.get("entry_impact") or []
    if impacts:
        total = sum(int(row.get("total") or 0) for row in impacts)
        failures = sum(int(row.get("would_fail_validation") or 0) for row in impacts)
        migrations = sum(int(row.get("would_need_migration") or 0) for row in impacts)
        impact_text = (
            f"impact: {total} entries across {len(impacts)} Track(s), "
            f"{failures} validation {'failure' if failures == 1 else 'failures'}, "
            f"{migrations} {'migration' if migrations == 1 else 'migrations'}"
        )
    else:
        impact_text = "impact: no attached entries required review"

    change_text = "; ".join(changes) if changes else "no structural changes"
    return (
        f"Draft diff (not published): {change_text}; {impact_text}. "
        "A separate publish approval is still required."
    )


async def profile_revision_publish_ready(
    *, user_id: str, session_id: str, draft_id: str
) -> bool:
    """Require the approved draft's computed diff in the visible resume turn."""
    thread = await get_thread_by_session(session_id)
    if thread is None or (getattr(thread, "user_id", "") or "") != user_id:
        return False
    revisions = [
        item
        for item in get_queue(thread).get("items") or []
        if (
            item.get("kind") == ITEM_STAGED_WRITE
            and item.get("status") == STATUS_APPROVED
            and item.get("write_kind") == "propose_profile_revision"
        )
    ]
    if not revisions:
        return True
    for item in revisions:
        if str((item.get("diff_machine") or {}).get("draft_id") or "") == draft_id:
            return bool(
                item.get("profile_revision_review")
                and item.get("profile_revision_draft_id") == draft_id
            )
    return False


async def _attach_profile_revision_review(
    *, user_id: str, item: Dict[str, Any]
) -> None:
    """Attach a permission-checked draft diff after the staged patch is consumed."""
    machine = item.get("diff_machine")
    draft_id = str((machine or {}).get("draft_id") or "").strip()
    if not draft_id:
        item["profile_revision_review_error"] = "missing draft id"
        return
    try:
        from app.services.operational_model_authoring import diff_draft

        result = await diff_draft(
            user_id=user_id,
            draft_id=draft_id,
            include_entry_impact=True,
            sample_limit=20,
        )
    except Exception:  # noqa: BLE001 — keep the consumed approval recorded
        logger.exception("prompt_queue: profile revision diff failed")
        item["profile_revision_review_error"] = "diff unavailable"
        return
    if not isinstance(result, dict) or result.get("error"):
        item["profile_revision_review_error"] = "diff unavailable"
        return
    item.pop("profile_revision_review_error", None)
    item["profile_revision_review"] = _summarize_profile_revision_diff(result)
    item["profile_revision_draft_id"] = draft_id


def strip_prompt_sheet_directive(text: str) -> str:
    """Drop legacy HTML agent directives from a resume blob (display / persist)."""
    if not text:
        return text
    cleaned = _LEGACY_AGENT_DIRECTIVE_RE.sub("", text)
    # Pre-comment era: unbounded lead-in still hanging off some old turns.
    cleaned = re.sub(
        r"\n*The approved writes above have already been applied\.[\s\S]*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\n*Please continue\.?\s*$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.rstrip()


def prompt_sheet_agent_residual(text: str) -> str:
    """Natural residual body for the agent — marker and directives removed."""
    body = strip_prompt_sheet_directive(text or "")
    stripped = body.lstrip()
    if stripped.startswith(RESUME_MARKER):
        body = stripped[len(RESUME_MARKER) :].lstrip("\n")
    return body.strip()


def extract_legacy_resume_directive(text: str) -> Optional[str]:
    """Pull the body of a legacy ``INTEGRAL_AGENT_DIRECTIVE`` comment, if any."""
    match = re.search(
        r"<!--\s*INTEGRAL_AGENT_DIRECTIVE\s*([\s\S]*?)-->",
        text or "",
        re.IGNORECASE,
    )
    if not match:
        return None
    body = (match.group(1) or "").strip()
    return body or None


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

    pending_writes = sum(
        1
        for item in queue.get("items") or []
        if item.get("kind") == ITEM_STAGED_WRITE
        and item.get("status") == STATUS_PENDING
    )
    if pending_writes >= MAX_PENDING_STAGED_WRITES:
        logger.warning(
            "prompt_queue: refusing enqueue — %s pending staged writes already "
            "(cap=%s) session=%s",
            pending_writes,
            MAX_PENDING_STAGED_WRITES,
            session_id,
        )
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
    if (
        status == STATUS_APPROVED
        and item.get("status") == STATUS_APPROVED
        and item.get("write_kind") == "propose_profile_revision"
        and not item.get("profile_revision_review")
    ):
        await _attach_profile_revision_review(user_id=user_id, item=item)

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
            # Keep execution failure/progress visible across reloads without
            # treating approval as proof that the write landed.
            snapshot = {}
            if state != item.get("staged_state", state):
                snapshot["staged_state"] = state
            last_error = getattr(staged, "last_error", None)
            if last_error is not None or "last_error" in item:
                snapshot["last_error"] = last_error
            progress = getattr(staged, "progress", None)
            if progress is not None or "completed_operations" in item:
                snapshot["completed_operations"] = (progress or {}).get("completed", 0)
            for key, value in snapshot.items():
                if item.get(key) != value:
                    item[key] = value
                    changed = True
            # ``pending`` and ``blessed`` remain actionable. An unfamiliar
            # non-terminal state is safer left visible than guessed at.
            continue
        item["status"], item["terminal_reason"] = terminal
        item["resolved_at"] = utc_now_iso()
        if (
            item["status"] == STATUS_APPROVED
            and item.get("write_kind") == "propose_profile_revision"
        ):
            await _attach_profile_revision_review(user_id=user_id, item=item)
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
