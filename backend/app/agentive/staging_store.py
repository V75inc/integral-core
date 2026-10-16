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
so full terminal payloads do not need a second retained copy. This keeps the
actionable collection limited to genuinely outstanding cards. Sessionless
proposals also get a separate, 24-hour state-only reconciliation receipt;
it carries neither their contents nor execution authority.

Substrate note (I-GRAPH-02)
---------------------------
A staged change is a short-lived, token-keyed record that participates in no
cascade, permission resolution, walker, or graph-walk read — so it is modelled
as a :class:`jvspatial.core.Object` (the same class of primitive as ``DBLog`` /
``ChangeEvent``), NOT a ``Node``. There is therefore no ``I-GRAPH-01`` structural
edge to wire, and no cascade-delete / backup weight for 10-minute tokens.

Failure policy
--------------
Ordinary store calls are best-effort and swallow exceptions (logged): a DB hiccup
must never break an agent turn, and unit tests that run without a bootstrapped
database degrade cleanly to today's in-memory-only behaviour. A persist failure
in production is the same (rare) failure mode staging already documented as
acceptable — just far less likely than a routine restart.
Partial batch replacement is the exception: it uses strict writes inside a
graph transaction so retiring approval and binding its replacement are atomic.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from jvspatial.core import Object
from pydantic import Field

if TYPE_CHECKING:  # avoid a runtime import cycle (staging imports this module)
    from app.agentive.staging import StagedChange

logger = logging.getLogger(__name__)


# States we keep durable. Terminal states are removed on transition.
_DURABLE_STATES = ("pending", "blessed")
_TERMINAL_STATES = ("consumed", "revoked", "expired")
_DECISION_READBACK_TTL = timedelta(hours=24)


class StagingDecisionRecord(Object):
    """Bounded state-only receipt; never an executable approval or audit payload."""

    user_id: str = ""
    state: str = ""
    kind: str = ""
    created_at: str = ""
    expires_at: str = ""
    resolved_at: str = ""
    retained_until: str = ""
    # Private immutable-intent proof; never included in public decision readback.
    intent_fingerprint: str = ""


def _decision_record_id(token: str) -> str:
    return "o.StagingDecisionRecord." + hashlib.sha256(token.encode()).hexdigest()


def decision_intent_fingerprint(fields: Dict[str, Any]) -> str:
    """Bind terminal reconciliation to the exact original reviewed proposal.

    Mutable decision/progress fields are excluded. This digest is evidence for
    comparing a private snapshot, never executable approval authority. Legacy
    state-only markers cannot supply this proof or be silently backfilled.
    """
    names = (
        "token",
        "user_id",
        "workspace_id",
        "session_id",
        "kind",
        "payload",
        "created_at",
        "expires_at",
        "summary",
        "diff_human",
        "diff_machine",
        "interaction_id",
        "idempotency_key",
    )
    intent = {name: fields.get(name) for name in names}
    if fields.get("origin_proof") is not None:
        intent["origin_proof"] = fields["origin_proof"]
    value = ["staged-decision-intent-v1", intent]
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    # json.dumps silently coerces integer object keys and tuples. Such Python
    # shapes are not the same reviewed JSON intent as their normalized output.
    if json.loads(encoded) != value:
        raise ValueError("Proposal intent must contain canonical JSON values")
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def persist_decision(sc: "StagedChange") -> None:
    """Retain only the resolved state for reloads without a chat transcript.

    Terminal payloads, human diffs, summaries, keys and execution results are
    deliberately excluded. Readback is available for 24 hours; expiry cleanup
    is bounded on each write. This supplements, rather than replaces, the
    optional decision ledger and the owning operation's execution receipt.
    """
    if sc.state not in _TERMINAL_STATES:
        return
    try:
        from jvspatial.core.context import get_default_context

        now = _now()
        expired = await get_default_context().find(
            StagingDecisionRecord,
            {"context.retained_until": {"$lte": now.isoformat()}},
            limit=100,
        )
        for record in expired:
            await record.delete()
        record_id = _decision_record_id(sc.token)
        if await StagingDecisionRecord.get(record_id) is not None:
            return
        await StagingDecisionRecord.create(
            id=record_id,
            user_id=sc.user_id,
            state=sc.state,
            kind=sc.kind,
            intent_fingerprint=decision_intent_fingerprint(_sc_to_fields(sc)),
            created_at=sc.created_at.isoformat(),
            expires_at=sc.expires_at.isoformat(),
            resolved_at=(sc.resolved_at or now).isoformat(),
            retained_until=(
                max(now, sc.expires_at) + _DECISION_READBACK_TTL
            ).isoformat(),
        )
    except Exception:  # noqa: BLE001 — state readback must not authorize a write
        logger.warning("staging_store.decision_persist_failed", exc_info=True)


async def load_decision_state(token: str, user_id: str) -> Optional[Dict[str, Any]]:
    """Read a principal's terminal state; never hydrate executable staging."""
    try:
        from jvspatial.core.context import get_default_context

        from app.services.app_operations.transaction_scope import (
            graph_transaction_available,
            postgres_graph_transaction,
        )

        if graph_transaction_available():
            async with postgres_graph_transaction(
                get_default_context().database
            ) as held:
                row = await held.get("object", _decision_record_id(token))
            record = (
                StagingDecisionRecord(id=row["id"], **row["context"])
                if row is not None
                and row.get("entity") == StagingDecisionRecord._entity_name()
                else None
            )
            if record is not None:
                await record.set_context(get_default_context())
        else:
            record = await StagingDecisionRecord.get(_decision_record_id(token))
        if record is None or record.user_id != user_id:
            return None
        until = _parse_iso(record.retained_until)
        if until is None or until <= _now():
            await record.delete()
            return None
        if record.state not in _TERMINAL_STATES:
            return None
        return {
            "_kind": "staged_change",
            "token": token,
            "state": record.state,
            "kind": record.kind,
            "summary": "Resolved change",
            "diff_human": "",
            "diff_machine": {},
            "created_at": record.created_at,
            "expires_at": record.expires_at,
            "resolved_at": record.resolved_at,
            "autonomy_grant_used": False,
            "requires_strong_confirmation": False,
            "decision_state_only": True,
        }
    except Exception as exc:  # noqa: BLE001
        from app.services.app_operations.transaction_scope import (
            graph_transaction_available,
        )

        if graph_transaction_available():
            from app.api.errors import QueryUnavailableError

            raise QueryUnavailableError(
                message="Proposal decision read is unavailable"
            ) from exc
        logger.warning("staging_store.decision_load_failed", exc_info=True)
        return None


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
    decision_source: Optional[str] = None
    execution_contract_version: str = ""
    origin_proof: Optional[Dict[str, Any]] = None


class OpenBatchRecord(Object):
    """Durable snapshot of one still-being-assembled batch."""

    user_id: str = ""
    session_id: Optional[str] = None
    label: str = ""
    batch_id: str = ""
    ops: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: str = ""
    expires_at: str = ""
    auto_continuation_attempts: int = 0


def _open_batch_record_id(user_id: str, session_id: Optional[str]) -> str:
    key = json.dumps([user_id, session_id], separators=(",", ":"))
    return "o.OpenBatchRecord." + hashlib.sha256(key.encode("utf-8")).hexdigest()


def _open_batch_fields(
    *, user_id: str, session_id: Optional[str], batch: Dict[str, Any]
) -> Dict[str, Any]:
    created = batch.get("created_at")
    if isinstance(created, datetime):
        created = created.isoformat()
    expires = batch.get("expires_at")
    if isinstance(expires, datetime):
        expires = expires.isoformat()
    return {
        "id": _open_batch_record_id(user_id, session_id),
        "user_id": user_id,
        "session_id": session_id,
        "label": str(batch.get("label") or ""),
        "batch_id": str(batch.get("batch_id") or ""),
        "ops": [dict(op) for op in batch.get("ops") or []],
        "created_at": str(created or ""),
        "expires_at": str(expires or ""),
        "auto_continuation_attempts": int(batch.get("auto_continuation_attempts") or 0),
    }


async def persist_open_batch(
    *, user_id: str, session_id: Optional[str], batch: Dict[str, Any]
) -> bool:
    """Persist the complete current batch snapshot; return False on store error."""
    fields = _open_batch_fields(user_id=user_id, session_id=session_id, batch=batch)
    try:
        # ``id`` is a top-level database key. Object.find_one maps ordinary
        # field names under ``context`` unless the model declares them as
        # top-level query fields, so use the primary-key API here.
        record = await OpenBatchRecord.get(fields["id"])
        if record is None:
            await OpenBatchRecord.create(**fields)
        else:
            for key, value in fields.items():
                if key == "id":
                    continue
                setattr(record, key, value)
            await record.save()
        return True
    except Exception:  # noqa: BLE001
        logger.warning("staging_store.open_batch_persist_failed", exc_info=True)
        return False


async def remove_open_batch(user_id: str, session_id: Optional[str]) -> bool:
    """Remove one durable open batch after cancel or successful commit."""
    try:
        record = await OpenBatchRecord.get(_open_batch_record_id(user_id, session_id))
        if record is not None:
            await record.delete()
        return True
    except Exception:  # noqa: BLE001
        logger.warning("staging_store.open_batch_remove_failed", exc_info=True)
        return False


async def load_open_batches() -> List[Dict[str, Any]]:
    """Load unexpired open-batch snapshots for startup rehydration."""
    from datetime import datetime, timezone

    try:
        records = list(await OpenBatchRecord.find({}))
    except Exception:  # noqa: BLE001
        logger.warning("staging_store.open_batch_load_failed", exc_info=True)
        return []
    now = datetime.now(timezone.utc)
    batches: List[Dict[str, Any]] = []
    for record in records:
        try:
            expires = datetime.fromisoformat(record.expires_at.replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
        except (AttributeError, TypeError, ValueError):
            await remove_open_batch(record.user_id, record.session_id)
            continue
        if expires <= now:
            await remove_open_batch(record.user_id, record.session_id)
            continue
        batches.append(
            {
                "user_id": record.user_id,
                "session_id": record.session_id,
                "label": record.label,
                "batch_id": record.batch_id,
                "ops": [dict(op) for op in record.ops or []],
                "created_at": datetime.fromisoformat(record.created_at),
                "expires_at": expires,
                "auto_continuation_attempts": record.auto_continuation_attempts,
                "auto_continuation_in_flight": False,
            }
        )
    return batches


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
        "decision_source": sc.decision_source,
        "execution_contract_version": sc.execution_contract_version,
        "origin_proof": dict(sc.origin_proof) if sc.origin_proof else None,
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
        decision_source=getattr(rec, "decision_source", None),
        execution_contract_version=getattr(rec, "execution_contract_version", ""),
        origin_proof=dict(rec.origin_proof) if rec.origin_proof else None,
    )


async def _find_record(token: str) -> Optional[StagedChangeRecord]:
    rec = await StagedChangeRecord.find_one({"token": token})
    return rec  # type: ignore[return-value]


async def persist(sc: "StagedChange", *, allow_create: bool = False) -> None:
    """Persist an existing proposal without resurrecting a terminal decision.

    Only minting may create a row. PostgreSQL locks it before inspecting or
    changing state; a stale instance cannot recreate a row after deletion.
    Readback/cosmetic persistence remains best-effort, never execution proof.
    """
    from jvspatial.core.context import get_default_context

    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
        postgres_graph_transaction,
    )

    try:
        database = get_default_context().database
        if graph_transaction_available(database):
            async with postgres_graph_transaction(database):
                await _persist_existing(sc, allow_create=allow_create, lock=True)
        else:
            await _persist_existing(sc, allow_create=allow_create, lock=False)
    except (
        Exception
    ):  # noqa: BLE001 — authoritative apply still requires the stored decision
        logger.warning("staging_store.persist_failed token=%s", sc.token, exc_info=True)


async def _persist_existing(
    sc: "StagedChange", *, allow_create: bool, lock: bool
) -> None:
    if sc.state not in _DURABLE_STATES:
        await remove(sc.token)
        return
    fields = _sc_to_fields(sc)
    # A stale process must not resurrect a revoked/consumed proposal or
    # downgrade a newer decision while attaching cosmetic bookkeeping.
    terminal = await StagingDecisionRecord.get(_decision_record_id(sc.token))
    if terminal is not None and terminal.state in _TERMINAL_STATES:
        return
    existing = await _find_record(sc.token)
    if existing is not None:
        if lock:
            from jvspatial.core.context import get_default_context

            context = get_default_context()
            row = await context.database.find_one_and_update(
                "object",
                {"id": existing.id, "entity": StagedChangeRecord._entity_name()},
                {"$set": {}},
            )
            if row is None:
                return
            existing = StagedChangeRecord(id=row["id"], **row["context"])
            await existing.set_context(context)
        if existing.state not in _DURABLE_STATES:
            return
        immutable = {
            "token",
            "user_id",
            "session_id",
            "kind",
            "payload",
            "created_at",
            "expires_at",
            "workspace_id",
            "idempotency_key",
            "origin_proof",
        }
        if any(getattr(existing, key) != fields[key] for key in immutable):
            logger.warning("staging_store.immutable_intent_conflict")
            return
        if existing.state == "blessed" and sc.state == "pending":
            fields["state"] = existing.state
            fields["blessed_at"] = existing.blessed_at
            fields["resolved_at"] = existing.resolved_at
            fields["decision_source"] = existing.decision_source
        if existing.execution_contract_version:
            fields["execution_contract_version"] = existing.execution_contract_version
        elif existing.state == "blessed":
            fields["execution_contract_version"] = ""
        for key, val in fields.items():
            setattr(existing, key, val)
        await existing.save()
    elif sc.state == "pending" and allow_create:
        await StagedChangeRecord.create(**fields)
    else:
        logger.warning("staging_store.missing_approved_decision")


async def replace_partial(old: "StagedChange", new: "StagedChange") -> None:
    """Strict writes inside the caller's transaction; never swallow rollback errors."""
    existing = await _find_record(old.token)
    if existing is None or _sc_to_fields(old) != _sc_to_fields(_record_to_sc(existing)):
        raise RuntimeError("The durable partial batch changed; reload before repair.")
    await StagedChangeRecord.create(**_sc_to_fields(new))
    await existing.delete()


async def remove(token: str) -> None:
    """Delete only an authoritative terminal row on shared stores."""
    from jvspatial.core.context import get_default_context

    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
        postgres_graph_transaction,
    )

    try:
        database = get_default_context().database
        if graph_transaction_available(database):
            async with postgres_graph_transaction(database) as held:
                rows = await held.find(
                    "object",
                    {
                        "entity": StagedChangeRecord._entity_name(),
                        "context.token": token,
                    },
                    limit=2,
                )
                if len(rows) != 1:
                    return
                row = await held.find_one_and_update(
                    "object", {"id": rows[0]["id"]}, {"$set": {}}
                )
                if row is None:
                    return
                current = row.get("context") or {}
                terminal = await held.get("object", _decision_record_id(token))
                decision = (terminal or {}).get("context") or {}
                if current.get("state") not in _TERMINAL_STATES or any(
                    current.get(key) != decision.get(key)
                    for key in ("state", "user_id", "kind")
                ):
                    return
                await held.delete("object", row["id"])
        else:
            existing = await _find_record(token)
            if existing is not None:
                await existing.delete()
    except Exception:  # noqa: BLE001
        logger.warning("staging_store.remove_failed token=%s", token, exc_info=True)


async def load_current(token: str) -> Optional["StagedChange"]:
    """Read shared decision facts without lazy expiry or caller entity caches."""
    from jvspatial.core.context import get_default_context

    from app.api.errors import QueryUnavailableError
    from app.services.app_operations.transaction_scope import postgres_graph_transaction

    try:
        async with postgres_graph_transaction(get_default_context().database) as held:
            rows = await held.find(
                "object",
                {
                    "entity": StagedChangeRecord._entity_name(),
                    "context.token": token,
                },
                limit=2,
            )
    except Exception as exc:
        raise QueryUnavailableError(message="Proposal read is unavailable") from exc
    if not rows:
        return None
    from app.api.errors import QueryUnavailableError

    if len(rows) != 1:
        raise QueryUnavailableError(message="Proposal identity is ambiguous")
    sc = _record_to_sc(StagedChangeRecord(id=rows[0]["id"], **rows[0]["context"]))
    if sc is None:
        raise QueryUnavailableError(message="Proposal facts could not be decoded")
    return sc


async def _resolve_expired_record(rec: StagedChangeRecord) -> Optional["StagedChange"]:
    from app.agentive.services.staging_decision_store import transition_staged_decision
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
    )

    if not graph_transaction_available():
        await remove(rec.token)
        return None
    fresh = await transition_staged_decision(
        token=rec.token,
        user_id=rec.user_id,
        target="expired",
    )
    if fresh.state == "expired":
        await remove(rec.token)
        return None
    # Preserve a current owner or committed/uncertain outcome for reconciliation.
    return fresh


async def load(token: str) -> Optional["StagedChange"]:
    """Read a current proposal; native store failures never become absence."""
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
    )

    if graph_transaction_available():
        sc = await load_current(token)
        if sc is None or sc.state not in _DURABLE_STATES:
            return None
        if sc.is_expired():
            rec = StagedChangeRecord(**_sc_to_fields(sc))
            return await _resolve_expired_record(rec)
        return sc
    try:
        rec = await _find_record(token)
        if rec is None:
            return None
        if rec.state not in _DURABLE_STATES or _record_is_expired(rec):
            await remove(token)
            return None
        return _record_to_sc(rec)
    except Exception:  # noqa: BLE001 — legacy single-process readback
        logger.warning("staging_store.load_failed token=%s", token, exc_info=True)
        return None


async def load_pending_for_user(user_id: str) -> List["StagedChange"]:
    """Load all non-terminal staged changes for ``user_id`` from the store.

    Expired rows are swept (deleted) and excluded. Used to rehydrate the
    in-memory cache after a restart so the pending-inbox reads are complete.
    """
    from jvspatial.core.context import get_default_context

    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
        postgres_graph_transaction,
    )

    if graph_transaction_available():
        from app.api.errors import QueryUnavailableError

        try:
            async with postgres_graph_transaction(
                get_default_context().database
            ) as held:
                rows = await held.find(
                    "object",
                    {
                        "entity": StagedChangeRecord._entity_name(),
                        "context.user_id": user_id,
                        "context.state": {"$in": list(_DURABLE_STATES)},
                    },
                )
        except Exception as exc:
            raise QueryUnavailableError(
                message="Proposal inbox read is unavailable"
            ) from exc
        current: List["StagedChange"] = []
        for row in rows:
            rec = StagedChangeRecord(id=row["id"], **row["context"])
            sc = (
                await _resolve_expired_record(rec)
                if _record_is_expired(rec)
                else _record_to_sc(rec)
            )
            if sc is None:
                if _record_is_expired(rec):
                    continue
                from app.api.errors import QueryUnavailableError

                raise QueryUnavailableError(
                    message="Proposal facts could not be decoded"
                )
            current.append(sc)
        return current
    out: List["StagedChange"] = []
    try:
        recs = await StagedChangeRecord.find({"user_id": user_id})
        now = _now()
        for rec in recs:
            if rec.state not in _DURABLE_STATES:
                continue
            if _record_is_expired(rec, now=now):
                sc = await _resolve_expired_record(rec)
            else:
                sc = _record_to_sc(rec)
            if sc is not None:
                out.append(sc)
    except Exception:  # noqa: BLE001
        logger.warning(
            "staging_store.load_pending_failed user=%s", user_id, exc_info=True
        )
    return out
