"""Stable canonical command batch journals; a progress hint never authorizes replay."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from jvspatial.core.context import get_default_context

from app.agentive.services.staging_command_apply import (
    CANONICAL_COMMAND_KINDS,
    _identity,
    apply_staged_command_once,
)
from app.agentive.services.staging_execution import (
    BatchStepApproval,
    _lock_current,
    claim_staging_execution,
    load_staging_execution_result,
    record_staging_execution_result,
    release_unapplied_staging_claim,
    renew_staging_execution,
    staging_execution_effect,
)
from app.agentive.staging import StagedChange, StagingError
from app.agentive.staging_store import StagedChangeRecord
from app.services.app_operations.transaction_scope import postgres_graph_transaction


def supports_canonical_batch(sc: StagedChange) -> bool:
    """Use the same durable command inventory for individual and batch writes."""
    operations = sc.payload.get("operations")
    return (
        sc.kind == "batch"
        and isinstance(operations, list)
        and all(
            isinstance(op, dict) and op.get("kind") in CANONICAL_COMMAND_KINDS
            for op in operations
        )
    )


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


async def _assert_current(
    sc: StagedChange, claim: Any, *, recovery: bool = False
) -> None:
    async with postgres_graph_transaction(get_default_context().database) as database:
        if claim is not None:
            await _lock_current(database, claim)
        rows = await database.find(
            "object",
            {
                "entity": StagedChangeRecord._entity_name(),
                "context.token": sc.token,
            },
            limit=2,
        )
        if len(rows) != 1:
            raise StagingError(
                "approval_unavailable", "Current batch approval is unavailable"
            )
        row = await database.find_one_and_update(
            "object", {"id": rows[0]["id"]}, {"$set": {}}
        )
        data = (row or {}).get("context") or {}
        if (
            data.get("state") != "blessed"
            or data.get("user_id") != sc.user_id
            or data.get("kind") != "batch"
            or _digest(data.get("payload")) != _digest(sc.payload)
            or (
                not recovery
                and datetime.fromisoformat(data.get("expires_at") or "")
                <= datetime.now(timezone.utc)
            )
        ):
            raise StagingError("approval_unavailable", "Current batch approval changed")


async def apply_command_batch_once(*, sc: StagedChange, request: Any) -> dict[str, Any]:
    """Recover committed steps even when the old progress cursor was lost."""
    from app.agentive.staging_executors import (
        _capture_batch_refs,
        _resolve_batch_refs,
        dispatch_under_scope,
    )

    identity = await _identity(sc, request)
    root_result = await load_staging_execution_result(**identity)
    if sc.execution_contract_version != "durable-staging-v1" and root_result is None:
        raise StagingError(
            "legacy_execution_reconciliation_required",
            "Reconcile this earlier approved batch before replaying any step",
        )
    claim = (
        None if root_result is not None else await claim_staging_execution(**identity)
    )
    references: dict[str, str] = {}
    results: list[dict[str, Any]] = []
    operations = sc.payload["operations"]
    try:
        for index, operation in enumerate(operations):
            await _assert_current(sc, claim, recovery=root_result is not None)
            if claim is not None:
                await renew_staging_execution(claim)
            child = replace(
                sc,
                token=f"{sc.token}:step:{index}",
                kind=operation["kind"],
                payload=_resolve_batch_refs(operation.get("payload") or {}, references),
            )
            if (
                root_result is not None
                and await load_staging_execution_result(
                    **await _identity(child, request)
                )
                is None
            ):
                raise StagingError(
                    "execution_receipt_unavailable",
                    "A completed batch step lacks its saved receipt",
                )

            async def execute_child(child: StagedChange = child) -> dict[str, Any]:
                return await dispatch_under_scope(
                    user_id=sc.user_id,
                    kind=child.kind,
                    payload=child.payload,
                    workspace_id=identity["workspace_id"],
                )

            result = await apply_staged_command_once(
                sc=child,
                request=request,
                execute=execute_child,
                batch_step=(
                    None
                    if claim is None
                    else BatchStepApproval(claim, sc.token, index, dict(references))
                ),
            )
            results.append(
                {
                    "kind": child.kind,
                    "summary": operation.get("summary"),
                    "result": result,
                }
            )
            if result.get("error") or result.get("ok") is False:
                if claim is not None:
                    await release_unapplied_staging_claim(claim)
                    claim = None
                return {
                    "error": True,
                    "error_code": result.get("error_code") or "batch_partial_failure",
                    "application_state": result.get("application_state")
                    or "partially_applied",
                    "completed": index,
                    "total": len(operations),
                    "results": results,
                    "message": "Batch stopped at the first unresolved step. Committed steps will not replay.",
                }
            reference_result = result
            reference_kind = {
                "create_entry": "entry",
                "update_entry": "entry",
                "create_app": "app",
                "update_app": "app",
                "create_track": "track",
                "update_track": "track",
                "add_comment": "comment",
                "save_view": "view",
                "create_dashboard": "dashboard",
                "update_dashboard": "dashboard",
            }.get(child.kind)
            if reference_kind is not None:
                # Keep command references private and independent of a current
                # UI projection. A later approved deletion or ACL change must
                # not erase the identity needed to reconcile saved child receipts.
                evidence = await load_staging_execution_result(
                    **await _identity(child, request)
                )
                resource_id = (evidence or {}).get(f"{reference_kind}_id")
                if resource_id:
                    name_key = (
                        "name"
                        if reference_kind in {"app", "view", "dashboard"}
                        else "title"
                    )
                    reference_result = {
                        reference_kind: {
                            "id": resource_id,
                            name_key: child.payload.get(name_key, ""),
                        }
                    }
            _capture_batch_refs(references, index, reference_result)
        if claim is not None:
            async with staging_execution_effect(claim) as database:
                await _assert_current(sc, claim)
                await record_staging_execution_result(
                    claim=claim,
                    database=database,
                    result={"completed": len(operations), "total": len(operations)},
                )
        return {
            "batched": True,
            "completed": len(operations),
            "total": len(operations),
            "results": results,
        }
    except BaseException:
        if claim is not None:
            # Only the undispatched parent is released. Every child retains
            # its independent immutable graph result or dispatch intent.
            await release_unapplied_staging_claim(claim)
        raise
