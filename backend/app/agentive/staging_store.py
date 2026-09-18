"""Durable backing store for outstanding staged changes.

The staging primitive (:mod:`app.agentive.staging`) keeps its live state in an
in-memory dict for speed. That dict does not survive a process restart, which
silently orphans every pending/blessed approval card the user has not yet
actioned — a real correctness problem (a blessed card reload-approves into
``unknown_token``).

This module adds a write-through durable layer so a restart no longer drops
outstanding work. It persists ONLY *non-terminal* staged changes
(``pending`` / ``blessed``) — the cards a user can still act on. Terminal
states (``consumed`` / ``revoked`` / ``expired``) are removed from the store on
transition: their reload-correctness is already handled by the FE
transcript-snapshot rewrite (``staging._persist_terminal_state_in_transcript``),
so durability of terminal rows would only grow the table for no gain. This keeps
the collection bounded to the (small) set of genuinely outstanding cards.

Substrate note (I-GRAPH-02)
---------------------------
A staged change is a short-lived, token-keyed record that participates in no
cascade, permission resolution, walker, or graph-walk read — so it is modelled
as a :class:`jvspatial.core.Object` (the same class of primitive as ``DBLog`` /
``ChangeEvent``), NOT a ``Node``. There is therefore no ``I-GRAPH-01`` structural
edge to wire, and no cascade-delete / backup weight for 10-minute tokens.

Failure policy
--------------
Every store call is best-effort and swallows exceptions (logged): a DB hiccup
must never break an agent turn, and unit tests that run without a bootstrapped
database degrade cleanly to today's in-memory-only behaviour. A persist failure
in production is the same (rare) failure mode staging already documented as
acceptable — just far less likely than a routine restart.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core import Object
from pydantic import Field

if TYPE_CHECKING:  # avoid a runtime import cycle (staging imports this module)
    from app.agentive.staging import StagedChange

logger = logging.getLogger(__name__)


# States we keep durable. Terminal states are removed on transition.
_DURABLE_STATES = ("pending", "blessed")


class StagedChangeRecord(Object):
    """Durable mirror of a non-terminal :class:`~app.agentive.staging.StagedChange`.

    Field-for-field parallel to the in-memory dataclass. ``diff_machine`` and
    ``payload`` are JSON dicts (already serializable — they carry node ids and
    op lists). Queried by ``token`` (the only identifier that leaves the
    server); ``user_id`` + ``state`` back the pending-list reconciliation reads.
    """

    token: str = ""
    user_id: str = ""
    session_id: Optional[str] = None
    kind: str = ""
    summary: str = ""
    diff_human: str = ""
    diff_machine: Dict[str, Any] = Field(default_factory=dict)
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    expires_at: str = ""
    state: str = "pending"
    autonomy_grant_used: bool = False
    interaction_id: Optional[str] = None
    workspace_id: Optional[str] = None
    # Survives a restart with the change: a blessed-but-unapplied token is
    # exactly the kind that outlives one, and losing the reason would put the
    # user back in front of an unexplained "approved".
    last_error: Optional[Dict[str, Any]] = None
    # Multi-op executor cursor ({"completed": n, "results": [...]}). Durable for
    # the same reason ``last_error`` is: the token it belongs to stays blessed
    # across a restart, and losing the cursor makes the next approval replay
    # already-applied ops.
    progress: Optional[Dict[str, Any]] = None
    # The re-stage block measures a blessed card's grace window from HERE.
    # Without it a rehydrated bless measured from ``created_at`` and a card
    # approved late looked stale the moment the process came back.
    blessed_at: Optional[str] = None
    # When the user decided / the token went terminal (decision ledger).
    resolved_at: Optional[str] = None
    idempotency_key: Optional[str] = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _record_is_expired(
    rec: StagedChangeRecord, *, now: Optional[datetime] = None
) -> bool:
    exp = _parse_iso(rec.expires_at)
    if exp is None:
        return False
    return (now or _now()) >= exp


def _sc_to_fields(sc: "StagedChange") -> Dict[str, Any]:
    return {
        "token": sc.token,
        "user_id": sc.user_id,
        "session_id": sc.session_id,
        "kind": sc.kind,
        "summary": sc.summary,
        "diff_human": sc.diff_human,
        "diff_machine": dict(sc.diff_machine),
        "payload": dict(sc.payload),
        "created_at": sc.created_at.isoformat(),
        "expires_at": sc.expires_at.isoformat(),
        "state": sc.state,
        "autonomy_grant_used": sc.autonomy_grant_used,
        "interaction_id": sc.interaction_id,
        "workspace_id": sc.workspace_id,
        "last_error": sc.last_error,
        "progress": sc.progress,
        "blessed_at": sc.blessed_at.isoformat() if sc.blessed_at else None,
        "resolved_at": sc.resolved_at.isoformat() if sc.resolved_at else None,
        "idempotency_key": sc.idempotency_key,
    }


def _record_to_sc(rec: StagedChangeRecord) -> Optional["StagedChange"]:
    """Rebuild an in-memory ``StagedChange`` from a durable row.

    Returns ``None`` if the timestamps can't be parsed (a corrupt row is
    treated as absent rather than crashing a bless/consume path).
    """
    from app.agentive.staging import StagedChange  # local: avoid import cycle

    created = _parse_iso(rec.created_at)
    expires = _parse_iso(rec.expires_at)
    if created is None or expires is None:
        return None
    return StagedChange(
        token=rec.token,
        user_id=rec.user_id,
        session_id=rec.session_id,
        kind=rec.kind,
        summary=rec.summary,
        diff_human=rec.diff_human,
        diff_machine=dict(rec.diff_machine or {}),
        payload=dict(rec.payload or {}),
        created_at=created,
        expires_at=expires,
        state=rec.state,  # type: ignore[arg-type]
        autonomy_grant_used=rec.autonomy_grant_used,
        interaction_id=rec.interaction_id,
        workspace_id=rec.workspace_id,
        last_error=rec.last_error,
        progress=rec.progress,
        blessed_at=_parse_iso(rec.blessed_at) if rec.blessed_at else None,
        resolved_at=_parse_iso(rec.resolved_at) if rec.resolved_at else None,
        idempotency_key=getattr(rec, "idempotency_key", None),
    )


async def _find_record(token: str) -> Optional[StagedChangeRecord]:
    rec = await StagedChangeRecord.find_one({"token": token})
    return rec  # type: ignore[return-value]


async def persist(sc: "StagedChange") -> None:
    """Write-through a staged change.

    Upserts by token when the change is non-terminal (``pending`` / ``blessed``);
    removes the row when the change has reached a terminal state. Best-effort.
    """
    try:
        if sc.state not in _DURABLE_STATES:
            await remove(sc.token)
            return
        fields = _sc_to_fields(sc)
        existing = await _find_record(sc.token)
        if existing is not None:
            for key, val in fields.items():
                setattr(existing, key, val)
            await existing.save()
        else:
            await StagedChangeRecord.create(**fields)
    except Exception:  # noqa: BLE001 — durability is best-effort, never fatal
        logger.warning("staging_store.persist_failed token=%s", sc.token, exc_info=True)


async def remove(token: str) -> None:
    """Delete a token's durable row, if present. Best-effort."""
    try:
        existing = await _find_record(token)
        if existing is not None:
            await existing.delete()
    except Exception:  # noqa: BLE001
        logger.warning("staging_store.remove_failed token=%s", token, exc_info=True)


async def load(token: str) -> Optional["StagedChange"]:
    """Load one non-terminal staged change by token, or ``None``.

    An expired or non-durable row is deleted and reported as absent (lazy
    expiry — mirrors the in-memory sweep's philosophy).
    """
    try:
        rec = await _find_record(token)
        if rec is None:
            return None
        if rec.state not in _DURABLE_STATES or _record_is_expired(rec):
            await remove(token)
            return None
        return _record_to_sc(rec)
    except Exception:  # noqa: BLE001
        logger.warning("staging_store.load_failed token=%s", token, exc_info=True)
        return None


async def load_pending_for_user(user_id: str) -> List["StagedChange"]:
    """Load all non-terminal staged changes for ``user_id`` from the store.

    Expired rows are swept (deleted) and excluded. Used to rehydrate the
    in-memory cache after a restart so the pending-inbox reads are complete.
    """
    out: List["StagedChange"] = []
    try:
        recs = await StagedChangeRecord.find({"user_id": user_id})
        now = _now()
        for rec in recs:
            if rec.state not in _DURABLE_STATES:
                continue
            if _record_is_expired(rec, now=now):
                await remove(rec.token)
                continue
            sc = _record_to_sc(rec)
            if sc is not None:
                out.append(sc)
    except Exception:  # noqa: BLE001
        logger.warning(
            "staging_store.load_pending_failed user=%s", user_id, exc_info=True
        )
    return out
