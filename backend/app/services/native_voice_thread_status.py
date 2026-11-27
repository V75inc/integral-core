"""Bounded read-only status of the exact shared voice conversation."""

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.agentive import staging_store
from app.services.app_operations.transaction_scope import postgres_graph_transaction

_RECENT_TURNS = 8
_RECENT_REVIEWS = 8
_DIGEST = re.compile(r"[a-f0-9]{64}")
_TYPED_STATUS = {
    "queued": "queued",
    "running": "in progress",
    "waiting_for_human": "waiting for review",
    "succeeded": "finished",
    "failed": "failed",
    "cancelled": "cancelled",
    "expired": "expired",
}


def _context(record, entity):
    if (
        isinstance(record, dict)
        and record.get("entity") == entity
        and isinstance(record.get("context"), dict)
    ):
        return record["context"]
    return {}


@dataclass(frozen=True)
class _ReviewProof:
    scope: tuple[str, str, str] = field(repr=False)
    token: str = field(repr=False)
    kind: str = field(repr=False)
    intent: str = field(repr=False)
    request_hash: str = field(repr=False)

    @classmethod
    def from_proposal(cls, data, scope) -> "_ReviewProof | None":
        token = data.get("token")
        kind = data.get("kind")
        payload = data.get("payload")
        if (
            not isinstance(token, str)
            or not 0 < len(token) <= 256
            or not isinstance(kind, str)
            or not kind
            or not isinstance(payload, dict)
        ):
            return None
        try:
            intent = staging_store.decision_intent_fingerprint(data)
            request = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        except (TypeError, ValueError):
            return None
        return cls(
            scope,
            hashlib.sha256(token.encode()).hexdigest(),
            kind,
            intent,
            hashlib.sha256(request.encode()).hexdigest(),
        )


@dataclass(frozen=True)
class VoiceThreadStatus:
    """Only generic counts and a typed-turn state may enter speech context."""

    typed_turn: str = ""
    awaiting: int = 0
    authorized: int = 0
    applied: int = 0
    declined: int = 0
    expired: int = 0
    unverified: int = 0
    failed: int = 0
    outcomes: tuple[tuple[_ReviewProof, str], ...] = field(
        default=(), repr=False, compare=False
    )

    receipt_cursor: tuple[str, str] | None = field(
        default=None, repr=False, compare=False
    )

    def as_context(self) -> str:
        return (
            "Quiet status update from the same Integral conversation. This replaces earlier "
            "status snapshots; do not proactively speak it or treat it as a request or approval. "
            f"Latest observed typed turn: {self.typed_turn or 'no current update'}. "
            f"Recent verified review states: {self.awaiting} awaiting review, "
            f"{self.authorized} approved but not confirmed applied, "
            f"{self.applied} applied, {self.declined} declined, {self.expired} expired; "
            f"{self.failed} failed to apply, {self.unverified} unverified. These are bounded observations, not a complete "
            "conversation or permission grant. For details, direct the caller to the chat."
        )


def _valid_time(stamp, now, *, future):
    if not isinstance(stamp, str):
        return False
    try:
        parsed = datetime.fromisoformat(stamp)
        return parsed.tzinfo is not None and (
            (parsed > now) if future else (parsed <= now)
        )
    except ValueError:
        return False


async def _decision(db, proof, principal, workspace, now):
    marker = _context(
        await db.get("object", "o.StagingDecisionRecord." + proof.token),
        "StagingDecisionRecord",
    )
    if (
        marker.get("user_id") != principal
        or marker.get("kind") != proof.kind
        or marker.get("intent_fingerprint") != proof.intent
        or not _valid_time(marker.get("retained_until"), now, future=True)
        or not _valid_time(marker.get("resolved_at"), now, future=False)
    ):
        return "unverified"
    state = marker.get("state")
    if state == "revoked":
        return "declined" if not marker.get("superseded_by") else "unverified"
    if state == "expired":
        return "expired"
    if state != "consumed":
        return "unverified"

    effect = _context(
        await db.get("object", "o.StagingExecutionRecord." + proof.token),
        "StagingExecutionRecord",
    )
    if (
        effect.get("principal_id") == principal
        and effect.get("workspace_id") == workspace
        and effect.get("kind") == proof.kind
        and effect.get("request_hash") == proof.request_hash
        and (
            effect.get("state") == "applied"
            or effect.get("local_effect_state") == "applied"
        )
        and effect.get("external_outcome") in (None, "not_dispatched", "returned")
        and isinstance(effect.get("result_ciphertext"), str)
        and effect["result_ciphertext"]
    ):
        return "applied"
    return "unverified"


def review_run_id(work):
    """A terminal WorkItem clears its lease run; recover its exact last attempt."""
    if work.get("run_id"):
        return work["run_id"]
    from app.schemas.agentive.work import TERMINAL_WORK_STATUSES

    if (
        work.get("status") not in TERMINAL_WORK_STATUSES
        or type(work.get("attempt")) is not int
    ):
        return ""
    from app.agentive.services.work_execution import deterministic_run_id

    return deterministic_run_id(
        work_item_id=work["work_item_id"], attempt=work["attempt"]
    )


def _native_work(row, scope):
    work = _context(row, "WorkItem")
    payload = work.get("input_payload")
    digest = payload.get("client_payload_digest") if isinstance(payload, dict) else None
    if isinstance(payload, dict) and not digest:
        digest = payload.get("request_fingerprint")
    work_id = work.get("work_item_id")
    if (
        work.get("kind") == "chat_turn"
        and work.get("origin") == "interactive_chat"
        and (
            work.get("principal_id"),
            work.get("workspace_id"),
            work.get("thread_id"),
        )
        == scope
        and isinstance(digest, str)
        and _DIGEST.fullmatch(digest)
        and payload.get("message_role", "user") == "user"
        and isinstance(work_id, str)
        and work_id
        and row.get("id") == f"o.WorkItem.{work_id}"
    ):
        return work
    return None


async def read_voice_thread_status(
    principal, workspace, thread, previous=(), *, receipt_cursor=None
):
    """Read existing durable native-turn/review evidence after caller admission.

    `previous` is private, short-lived correlation from an earlier read of
    this pinned thread. No chat text, proposal JSON or credential is returned
    to the realtime model. A missing live proposal never implies completion.
    """
    now = datetime.now(timezone.utc)
    scope = (principal, workspace, thread)
    async with postgres_graph_transaction() as db:
        typed = await db.find(
            "object",
            {
                "entity": "WorkItem",
                "context.kind": "chat_turn",
                "context.origin": "interactive_chat",
                "context.principal_id": principal,
                "context.workspace_id": workspace,
                "context.thread_id": thread,
                "context.input_payload.message_role": {"$ne": "system"},
            },
            limit=_RECENT_TURNS,
            sort=[("context.updated_at", -1), ("id", 1)],
        )
        works = {}
        for row in typed:
            work = _native_work(row, scope)
            if work:
                works[work["work_item_id"]] = work
        proposals = (
            await db.find(
                "object",
                {
                    "entity": "StagedChangeRecord",
                    "context.user_id": principal,
                    "context.workspace_id": workspace,
                    "context.session_id": thread,
                    "context.origin_proof.work_item_id": {"$in": list(works)},
                },
                limit=_RECENT_REVIEWS,
                sort=[("context.created_at", -1), ("id", 1)],
            )
            if works
            else []
        )
        outcomes = {}
        for row in proposals:
            data = _context(row, "StagedChangeRecord")
            origin = data.get("origin_proof")
            if (
                not isinstance(origin, dict)
                or origin.get("version") != "work-proposal-v1"
                or (
                    data.get("user_id"),
                    data.get("workspace_id"),
                    data.get("session_id"),
                )
                != scope
                or (
                    origin.get("principal_id"),
                    origin.get("workspace_id"),
                    origin.get("thread_id"),
                )
                != scope
                or not isinstance(origin.get("work_item_id"), str)
                or (work := works.get(origin["work_item_id"])) is None
                or not isinstance(origin.get("run_id"), str)
                or not origin["run_id"]
                or origin["run_id"] != review_run_id(work)
            ):
                continue
            proof = _ReviewProof.from_proposal(data, scope)
            if proof is None:
                continue
            if proof.token in outcomes:
                old = outcomes[proof.token][0]
                outcomes[proof.token] = (old, "unverified")
                continue
            state = data.get("state")
            if (
                state == "pending"
                and (
                    origin.get("requires_deferred_link") is not True
                    or work.get("status") == "waiting_for_human"
                )
                and _valid_time(data.get("expires_at"), now, future=True)
            ):
                label = "awaiting"
            elif state == "blessed" and _valid_time(
                data.get("expires_at"), now, future=True
            ):
                label = "failed" if data.get("last_error") else "authorized"
            else:
                label = await _decision(db, proof, principal, workspace, now)
            outcomes[proof.token] = (proof, label)

        # Terminal receipts retain scope/intent correlation without tokens even
        # when a card resolves before the first observation of its live row.
        terminal_query = {
            "entity": "StagingDecisionRecord",
            "context.user_id": principal,
            "context.workspace_id": workspace,
            "context.conversation_id": thread,
            "context.retained_until": {"$gt": now.isoformat()},
        }
        if receipt_cursor:
            stamp, row_id = receipt_cursor
            terminal_query["$or"] = [
                {"context.resolved_at": {"$gt": stamp}},
                {"context.resolved_at": stamp, "id": {"$gt": row_id}},
            ]
        direction = 1 if receipt_cursor else -1
        terminals = await db.find(
            "object",
            terminal_query,
            limit=_RECENT_REVIEWS,
            sort=[("context.resolved_at", direction), ("id", direction)],
        )
        next_cursor = receipt_cursor or (now.isoformat(), "")
        if terminals:
            boundary = terminals[-1] if receipt_cursor else terminals[0]
            next_cursor = (boundary["context"]["resolved_at"], boundary["id"])
        for row in terminals:
            data = _context(row, "StagingDecisionRecord")
            try:
                saved = data["conversation_proof"]
                origin = saved["origin"]
                work_id = origin["work_item_id"]
                if not isinstance(work_id, str) or not work_id:
                    continue
                work = works.get(work_id)
                if work is None:
                    # A recent decision may belong to an older turn. Recheck
                    # its exact native admission without widening the snapshot.
                    work = _native_work(
                        await db.get("object", f"o.WorkItem.{work_id}"), scope
                    )
                if (
                    (
                        origin["principal_id"],
                        origin["workspace_id"],
                        origin["thread_id"],
                    )
                    != scope
                    or work is None
                    or origin["run_id"] != review_run_id(work)
                    or not row["id"].startswith("o.StagingDecisionRecord.")
                    or saved["intent"] != data.get("intent_fingerprint")
                    or saved["kind"] != data.get("kind")
                    or not _DIGEST.fullmatch(saved["request_hash"])
                ):
                    continue
                proof = _ReviewProof(
                    scope,
                    row["id"].removeprefix("o.StagingDecisionRecord."),
                    saved["kind"],
                    saved["intent"],
                    saved["request_hash"],
                )
            except (KeyError, TypeError, ValueError):
                continue
            if proof.token not in outcomes:
                outcomes[proof.token] = (
                    proof,
                    await _decision(db, proof, principal, workspace, now),
                )

        for proof in previous[:_RECENT_REVIEWS]:
            if proof.scope == scope and proof.token not in outcomes:
                # Reconcile only a previously observed, immutable in-scope
                # proposal. A missing or changed receipt is not a success.
                label = await _decision(db, proof, principal, workspace, now)
                outcomes[proof.token] = (proof, label)

    latest = next(
        (
            work
            for work in works.values()
            if (work.get("input_payload") or {}).get("client_payload_digest")
        ),
        {},
    )
    return (
        VoiceThreadStatus(
            typed_turn=_TYPED_STATUS.get(latest.get("status"), ""),
            **Counter(label for _proof, label in outcomes.values()),
            outcomes=tuple(outcomes.values()),
            receipt_cursor=next_cursor,
        ),
        tuple(proof for proof, _label in outcomes.values()),
    )
