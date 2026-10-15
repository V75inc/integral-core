"""Atomic proposal decisions on the same store/fence as canonical effects."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from jvspatial.core.context import get_default_context

from app.agentive import staging_store
from app.agentive.staging import StagedChange, StagingError
from app.services.app_operations.transaction_scope import postgres_graph_transaction


def _acknowledged_terminal(snapshot, data, *, token, user_id, target, expected_kind):
    """Acknowledge only an exact terminal fact; never hydrate approval authority."""
    if (
        snapshot is None
        or target not in {"consumed", "revoked", "expired"}
        or snapshot.token != token
        or snapshot.user_id != user_id
        or snapshot.state != target
        or data.get("user_id") != user_id
        or data.get("state") != target
        or snapshot.kind != data.get("kind")
        or (expected_kind is not None and data.get("kind") != expected_kind)
    ):
        return None
    try:
        resolved = datetime.fromisoformat(data.get("resolved_at") or "")
        retained = data.get("retained_until")
        if resolved.tzinfo is None or (
            retained and datetime.fromisoformat(retained) <= datetime.now(timezone.utc)
        ):
            return None
    except (TypeError, ValueError):
        return None
    return replace(snapshot, resolved_at=resolved)


async def transition_staged_decision(
    *,
    token: str,
    user_id: str,
    target: str,
    expected_kind: str | None = None,
    decision_source: str | None = None,
    terminal_snapshot: StagedChange | None = None,
) -> StagedChange:
    """Use fresh locked facts; stale caches never bless, revoke or consume.

    A closure notifier may acknowledge its matching terminal snapshot without
    raising an expected refusal inside a caller's transaction. Normal decision
    callers retain strict one-time transition semantics, including consumption.
    """
    from app.agentive.services.staging_execution import (
        StagingExecutionRecord,
        execution_record_id,
    )

    if target not in {"blessed", "consumed", "revoked", "expired"}:
        raise ValueError("Unsupported proposal decision")

    async with postgres_graph_transaction(get_default_context().database) as database:
        rows = await database.find(
            "object",
            {
                "entity": staging_store.StagedChangeRecord._entity_name(),
                "context.token": token,
            },
            limit=2,
        )
        if len(rows) != 1:
            terminal = await database.get(
                "object", staging_store._decision_record_id(token)
            )
            terminal_data = (terminal or {}).get("context") or {}
            acknowledged = _acknowledged_terminal(
                (
                    terminal_snapshot
                    if terminal is not None
                    and terminal.get("entity")
                    == staging_store.StagingDecisionRecord._entity_name()
                    else None
                ),
                terminal_data,
                token=token,
                user_id=user_id,
                target=target,
                expected_kind=expected_kind,
            )
            if acknowledged is not None:
                return acknowledged
            if terminal_data.get("user_id") == user_id and terminal_data.get(
                "state"
            ) in {
                "consumed",
                "revoked",
                "expired",
            }:
                if (
                    expected_kind is not None
                    and terminal_data.get("kind") != expected_kind
                ):
                    raise StagingError(
                        "wrong_kind", "Token is for another operation kind"
                    )
                raise StagingError(
                    f"already_{terminal_data['state']}", "Proposal is already resolved"
                )
            raise StagingError(
                "approval_unavailable", "Current proposal decision is unavailable"
            )
        row = await database.find_one_and_update(
            "object", {"id": rows[0]["id"]}, {"$set": {}}
        )
        if row is None:
            raise StagingError(
                "approval_unavailable", "Current proposal decision is unavailable"
            )
        data: dict[str, Any] = row.get("context") or {}
        if data.get("user_id") != user_id:
            raise StagingError("wrong_user", "Token does not belong to this user")
        if expected_kind is not None and data.get("kind") != expected_kind:
            raise StagingError("wrong_kind", "Token is for another operation kind")
        state = data.get("state")
        if state not in {"pending", "blessed"}:
            acknowledged = _acknowledged_terminal(
                terminal_snapshot,
                data,
                token=token,
                user_id=user_id,
                target=target,
                expected_kind=expected_kind,
            )
            if acknowledged is not None:
                return acknowledged
            raise StagingError(f"already_{state}", "Proposal is already resolved")
        now = datetime.now(timezone.utc)
        try:
            expires = datetime.fromisoformat(data["expires_at"])
            if expires.tzinfo is None:
                raise ValueError("Proposal expiry must include a timezone")
        except (KeyError, TypeError, ValueError) as exc:
            raise StagingError(
                "approval_unavailable", "Current proposal expiry is unavailable"
            ) from exc
        execution = await database.get("object", execution_record_id(token))
        outcome = (execution or {}).get("context") or {}
        applied = (
            outcome.get("local_effect_state") == "applied"
            or outcome.get("state") == "applied"
        )
        if execution is not None:
            if execution.get(
                "entity"
            ) != StagingExecutionRecord._entity_name() or outcome.get("state") not in {
                "ready",
                "claimed",
                "dispatched",
                "applied",
                "unknown",
            }:
                raise StagingError(
                    "execution_state_invalid", "Execution facts require reconciliation"
                )
            expected = {
                "principal_id": user_id,
                "kind": data.get("kind"),
                "request_hash": hashlib.sha256(
                    json.dumps(
                        data.get("payload"),
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode()
                ).hexdigest(),
            }
            if data.get("workspace_id"):
                expected["workspace_id"] = data["workspace_id"]
            if any(outcome.get(key) != value for key, value in expected.items()):
                raise StagingError(
                    "execution_identity_conflict", "Approved execution identity changed"
                )
        if (
            target == "consumed"
            and execution is not None
            and not (
                outcome.get("state") == "applied"
                and outcome.get("local_effect_state") == "applied"
                and outcome.get("result_ciphertext")
                and outcome.get("external_outcome")
                in {None, "not_dispatched", "returned"}
            )
        ):
            raise StagingError(
                "application_reconciliation_required",
                "Current execution is not fully reconciled",
            )
        if target == "expired":
            if expires > now:
                raise StagingError("not_expired", "Current proposal has not expired")
            held = (
                applied
                or outcome.get("local_effect_state") == "partial"
                or outcome.get("state") in {"dispatched", "unknown"}
            )
            if outcome.get("state") == "claimed":
                try:
                    claim_expiry = datetime.fromisoformat(outcome["expires_at"])
                    held = held or claim_expiry > now
                except (KeyError, TypeError, ValueError):
                    held = True  # Missing lease facts cannot authorize cleanup.
            if held:
                # Expiry removes permission to start new effects, not evidence
                # of a change already committed or sent to an extension.
                partial = outcome.get("local_effect_state") == "partial"
                message = (
                    "The change applied before the approval expired. Reconcile its saved result; do not replay it."
                    if applied
                    else (
                        "Some batch steps applied before the approval expired. Review completed steps before approving remaining work."
                        if partial
                        else "The approval expired while a change was applying. Reconcile its outcome; do not replay it."
                    )
                )
                error = {
                    "error_code": "application_reconciliation_required",
                    "application_state": "reconciliation_required",
                    "applied": applied,
                    "partially_applied": partial,
                    "message": message,
                }
                updated = await database.find_one_and_update(
                    "object",
                    {"id": row["id"]},
                    {"$set": {"context.last_error": error}},
                )
                sc = staging_store._record_to_sc(
                    staging_store.StagedChangeRecord(id=row["id"], **updated["context"])
                )
                if sc is None:
                    raise StagingError(
                        "approval_unavailable", "Proposal could not be decoded"
                    )
                return sc
        elif expires <= now and not (
            state == "blessed"
            and applied
            and outcome.get("result_ciphertext")
            and (
                target == "blessed"  # Idempotent recovery, never a new approval.
                or (
                    target == "consumed"
                    and outcome.get("state") == "applied"
                    and outcome.get("external_outcome")
                    in {None, "not_dispatched", "returned"}
                )
            )
        ):
            raise StagingError("expired", "Token has expired")
        if target == "blessed" and state == "blessed":
            record = staging_store.StagedChangeRecord(id=row["id"], **data)
            sc = staging_store._record_to_sc(record)
            if sc is None:
                raise StagingError(
                    "approval_unavailable", "Proposal could not be decoded"
                )
            return sc
        if target == "consumed" and state != "blessed":
            raise StagingError("not_blessed", "Token has not been approved by the user")
        if target == "revoked":
            if (
                outcome.get("local_effect_state") == "applied"
                or outcome.get("state") == "applied"
            ):
                raise StagingError(
                    "already_applied",
                    "The change already applied; rejecting the approval cannot undo it",
                )
        updates = {"state": target, "resolved_at": now.isoformat()}
        if decision_source is not None:
            updates["decision_source"] = decision_source
        if target == "blessed":
            updates.update(
                blessed_at=now.isoformat(),
                execution_contract_version="durable-staging-v1",
            )
        updated = await database.find_one_and_update(
            "object",
            {"id": row["id"]},
            {
                "$set": {f"context.{key}": value for key, value in updates.items()},
            },
        )
        if target in {"consumed", "revoked", "expired"}:
            await database.insert_if_absent(
                "object",
                {
                    "id": staging_store._decision_record_id(token),
                    "entity": staging_store.StagingDecisionRecord._entity_name(),
                    "context": {
                        "user_id": user_id,
                        "state": target,
                        "kind": data["kind"],
                        "created_at": data["created_at"],
                        "expires_at": data["expires_at"],
                        "resolved_at": now.isoformat(),
                        "retained_until": (
                            max(now, expires) + timedelta(hours=24)
                        ).isoformat(),
                    },
                },
            )
        sc = staging_store._record_to_sc(
            staging_store.StagedChangeRecord(id=row["id"], **updated["context"])
        )
        if sc is None:
            raise StagingError("approval_unavailable", "Proposal could not be decoded")
        return sc
