"""Join a reviewed local App command to its existing canonical receipt.

No second effect journal is created. Original input/key/declaration and current
proposal authority govern consumption. Observation cannot authorize execution;
unknown external effects remain the owning operation's explicit contract.
"""

import copy
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from app.agentive import staging_store
from app.agentive.staging import StagedChange, StagingError
from app.contracts.operations import OperationIdentity, canonical_request_hash
from app.contracts.runtime import ExecutionScope
from app.schemas.policy import Subject
from app.services.app_operations.execution_receipts import (
    OperationReceiptConflict,
    OperationReceiptIncomplete,
    lookup_operation_outcome,
)
from app.services.app_operations.history import operation_history_admission
from app.services.app_operations.transaction_scope import (
    current_graph_transaction_database,
)


@dataclass(frozen=True)
class OperationApplication:
    result: dict
    staged: StagedChange | None


def enabled(sc: StagedChange) -> bool:
    """Use native canonical operation receipts instead of a competing journal."""
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
    )

    return sc.kind == "invoke_app_operation" and graph_transaction_available()


def _intent(sc: StagedChange) -> str:
    return staging_store.decision_intent_fingerprint(staging_store._sc_to_fields(sc))


async def _lock_decision(
    sc: StagedChange, *, recovering: bool, original_completion: bool = False
) -> tuple[dict | None, StagedChange]:
    database = current_graph_transaction_database()
    rows = await database.find(
        "object",
        {
            "entity": staging_store.StagedChangeRecord._entity_name(),
            "context.token": sc.token,
        },
        limit=2,
    )
    if not rows and recovering:
        marker = await database.get(
            "object", staging_store._decision_record_id(sc.token)
        )
        data = (marker or {}).get("context") or {}
        try:
            retained = datetime.fromisoformat(data["retained_until"])
            resolved = datetime.fromisoformat(data["resolved_at"])
            valid = (
                retained.tzinfo is not None
                and resolved.tzinfo is not None
                and retained > datetime.now(timezone.utc)
            )
        except (KeyError, TypeError, ValueError):
            valid = False
        if (
            valid
            and marker["entity"] == staging_store.StagingDecisionRecord._entity_name()
            and data.get("user_id") == sc.user_id
            and data.get("kind") == sc.kind
            and data.get("state") == "consumed"
            and data.get("intent_fingerprint") == _intent(sc)
        ):
            return None, replace(
                sc, state="consumed", resolved_at=resolved, executing=False
            )
    if len(rows) != 1:
        raise StagingError(
            "approval_unavailable", "Original approved decision is unavailable"
        )
    row = await database.find_one_and_update(
        "object", {"id": rows[0]["id"]}, {"$set": {}}
    )
    data = (row or {}).get("context") or {}
    if (
        row is None
        or row.get("entity") != staging_store.StagedChangeRecord._entity_name()
        or data.get("user_id") != sc.user_id
        or data.get("workspace_id") != sc.workspace_id
        or data.get("kind") != sc.kind
        or staging_store.decision_intent_fingerprint(data) != _intent(sc)
    ):
        raise StagingError(
            "approval_identity_conflict", "Original reviewed intent changed"
        )
    current = staging_store._record_to_sc(
        staging_store.StagedChangeRecord(id=row["id"], **data)
    )
    if current is None:
        raise StagingError(
            "approval_unavailable", "Current decision could not be decoded"
        )
    if recovering and current.state == "consumed":
        return None, current
    marker = await database.get("object", staging_store._decision_record_id(sc.token))
    if marker is not None and current.state == "blessed":
        raise StagingError(
            "application_reconciliation_required",
            "Live and terminal decision facts conflict",
        )
    if current.state != "blessed":
        raise StagingError(
            "approval_not_current", "Current decision does not permit this command"
        )
    if current.expires_at.tzinfo is None or (
        (not recovering or not original_completion)
        and current.expires_at <= datetime.now(timezone.utc)
    ):
        raise StagingError("expired", "Original approval has expired")
    return row, current


async def _consume(database, row: dict | None, current: StagedChange) -> StagedChange:
    if row is None:
        return current
    from app.agentive.services.staging_decision_store import _write_locked_decision

    return await _write_locked_decision(
        database,
        row=row,
        data=row["context"],
        expires=current.expires_at,
        updates={
            "state": "consumed",
            "resolved_at": datetime.now(timezone.utc).isoformat(),
            "last_error": None,
        },
    )


async def apply_staged_operation_once(
    *, sc: StagedChange, execute
) -> OperationApplication:
    """Apply or reconcile exactly one reviewed local operation without replay."""
    from app.agentive import staging
    from app.services.app_operations.decision_binding import bind_operation_decision
    from app.services.app_operations.recorded_results import (
        recover_recorded_operation_response,
    )

    # The caller already binds the authenticated human to this stored proposal.
    # Copy before await so cache/UI mutations cannot replace that original intent.
    from app.services.app_operations.transaction_scope import (
        require_outside_graph_transaction,
    )

    require_outside_graph_transaction()
    sc = copy.deepcopy(sc)
    body = sc.payload
    required = (
        "app_id",
        "operation_key",
        "operation_idempotency_key",
        "declaration_fingerprint",
    )
    if (
        sc.kind != "invoke_app_operation"
        or not sc.workspace_id
        or any(
            type(body.get(k)) is not str or not body[k] or body[k] != body[k].strip()
            for k in required
        )
        or not isinstance(body.get("input"), dict)
        or not isinstance(body.get("resolved_input"), dict)
    ):
        raise StagingError(
            "operation_review_required",
            "Complete original operation review is required",
        )
    if re.fullmatch(r"[0-9a-f]{64}", body["declaration_fingerprint"]) is None:
        raise StagingError(
            "operation_review_required", "Exact reviewed declaration is required"
        )
    try:
        _intent(sc)
    except (TypeError, ValueError, RecursionError) as exc:
        raise StagingError(
            "operation_review_required", "Canonical reviewed input is required"
        ) from exc
    scope = ExecutionScope.create(
        principal_id=sc.user_id,
        workspace_id=sc.workspace_id,
        origin="staged_operation_recovery",
    )
    subject = Subject(kind="human", id=sc.user_id)
    contract = body["declaration_fingerprint"]
    request_hash = canonical_request_hash(body["input"])

    def identity(principal):
        return OperationIdentity.create(
            principal_id=principal.principal_id,
            workspace_id=sc.workspace_id,
            app_id=body["app_id"],
            operation_key=body["operation_key"],
            idempotency_key=body["operation_idempotency_key"],
        )

    async def lookup(original, aliases, *, original_decision=True):
        return await lookup_operation_outcome(
            identity=original,
            request_hash=request_hash,
            principal_aliases=aliases,
            expected_effect_contract_fingerprint=contract,
            expected_decision_intent_fingerprint=(
                _intent(sc) if original_decision else None
            ),
        )

    async def observe():
        async with operation_history_admission(
            scope=scope, subject=subject, app_id=body["app_id"]
        ) as principal:
            return await lookup(
                identity(principal), principal.aliases, original_decision=False
            )

    @asynccontextmanager
    async def admit_decision(original, aliases):
        row, current = await _lock_decision(sc, recovering=False)
        yield
        completed = await lookup(original, aliases)
        if completed is None:
            raise OperationReceiptIncomplete("Canonical effect receipt is missing")
        await _consume(current_graph_transaction_database(), row, current)

    result = None
    try:
        observation = await observe()
        if observation is None:
            if (sc.last_error or {}).get(
                "application_state"
            ) == "reconciliation_required":
                raise OperationReceiptIncomplete(
                    "Earlier completion has no qualifying receipt"
                )
            async with bind_operation_decision(
                principal_id=sc.user_id,
                workspace_id=sc.workspace_id,
                app_id=body["app_id"],
                operation_key=body["operation_key"],
                idempotency_key=body["operation_idempotency_key"],
                request_hash=request_hash,
                contract_fingerprint=contract,
                intent_fingerprint=_intent(sc),
                admit=admit_decision,
            ):
                result = await execute()
            observation = await observe()
            if observation is None:
                if result.get("error_code") == "application_reconciliation_required":
                    raise OperationReceiptIncomplete(
                        "Current decision facts require reconciliation"
                    )
                if result.get("error") or result.get("ok") is False:
                    return OperationApplication(result, None)
                raise OperationReceiptIncomplete(
                    "Successful return has no canonical effect receipt"
                )

        async with operation_history_admission(
            scope=scope, subject=subject, app_id=body["app_id"]
        ) as principal:
            current_observation = await lookup(
                identity(principal), principal.aliases, original_decision=False
            )
            if (
                current_observation is None
                or current_observation.identity != observation.identity
                or current_observation.original_decision_fingerprint
                != observation.original_decision_fingerprint
            ):
                raise OperationReceiptIncomplete("Original operation receipt changed")
            original_completion = (
                current_observation.original_decision_fingerprint == _intent(sc)
            )
            row, current = await _lock_decision(
                sc, recovering=True, original_completion=original_completion
            )
            completed = await _consume(
                current_graph_transaction_database(), row, current
            )
        # Cache/notifications are never part of the graph/receipt transaction.
        await staging._cache_durable_decision(completed)
        recovered = await recover_recorded_operation_response(
            scope=scope,
            subject=subject,
            app_id=body["app_id"],
            operation_key=body["operation_key"],
            payload=body["input"],
            idempotency_key=body["operation_idempotency_key"],
            expected_effect_contract_fingerprint=contract,
            expected_decision_intent_fingerprint=(
                _intent(sc) if original_completion else None
            ),
        )
        if recovered is None:
            raise OperationReceiptIncomplete("Original completion is unavailable")
        public = recovered.model_dump()
        if not original_completion:
            # A fresh approval may acknowledge an already committed exact
            # operation. It cannot claim to have authorized that earlier effect,
            # rewrite its decision proof, or invoke the handler again.
            public.update(
                already_completed=True,
                message="This operation completed earlier. No new changes were made.",
            )
        if (
            result
            and not result.get("error")
            and recovered.output_disposition == "available"
            and result.get("operation_receipt", {}).get("id")
            == public["operation_receipt"]["id"]
            and result["operation_receipt"].get("replayed") is False
        ):
            public["operation_receipt"]["replayed"] = False
        return OperationApplication(public, completed)
    except (OperationReceiptConflict, OperationReceiptIncomplete) as exc:
        raise StagingError("application_reconciliation_required", str(exc)) from exc
