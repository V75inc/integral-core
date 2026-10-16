"""Read-only recovery of original command outcomes outside effect transactions.

No handler, target resolver, broker command, alias repair or claim is invoked.
Current result disclosure and own-status admission are separate short scopes.
Unknown authority/storage errors are never converted into successful status.
"""

import json

from jvspatial.core.context import get_default_context

from app.api.errors import (
    BadRequestError,
    EntitlementRequiredError,
    InsufficientPermissionsError,
    ResourceNotFoundError,
)
from app.contracts.operations import canonical_request_hash
from app.contracts.runtime import ExecutionScope
from app.exceptions import (
    AuthorizationUnavailableError,
)
from app.exceptions import BadRequestError as DomainBadRequestError
from app.models.nodes import App
from app.schemas.app_operations import (
    AppOperationInvokeResponse,
    AppOperationOutputWithheld,
    AppOperationResponse,
)
from app.schemas.policy import Subject
from app.services.app_operations.execution_receipts import (
    OperationOutcomeObservation,
    OperationReceiptConflict,
    OperationReceiptIncomplete,
    _validate_receipt_identity,
    lookup_operation_outcome,
    receipt_object_id,
)
from app.services.app_operations.history import operation_history_admission
from app.services.app_operations.output_references import (
    MAX_OUTPUT_TEXT,
    output_json_within_bounds,
)
from app.services.app_operations.result_context import OperationResultContext
from app.services.app_operations.transaction_scope import (
    postgres_graph_transaction,
    require_outside_graph_transaction,
)
from app.services.authority_facts import primary_node_record

# Only errors from the result-disclosure phase qualify. Never catch journal
# conflict/incompleteness, transport authentication or arbitrary storage errors.
_RESULT_WITHHELD = (
    InsufficientPermissionsError,
    EntitlementRequiredError,
    BadRequestError,
    DomainBadRequestError,
    ResourceNotFoundError,
    AuthorizationUnavailableError,
)


async def _saved_result(
    observation: OperationOutcomeObservation, request_hash: str
) -> dict | None:
    async with postgres_graph_transaction() as transaction:
        row = await transaction.get("object", receipt_object_id(observation.identity))
        if not isinstance(row, dict):
            raise OperationReceiptIncomplete(
                "Original operation receipt is unavailable"
            )
        _validate_receipt_identity(row, observation.identity)
        body = row["context"]
        if body.get("request_hash") != request_hash:
            raise OperationReceiptConflict(
                "Idempotency key reused with different payload"
            )
        if body.get("status") != "succeeded":
            raise OperationReceiptIncomplete(
                "Original operation receipt requires reconciliation"
            )
        encoded = body.get("result_json")
        if not isinstance(encoded, str) or len(encoded) > MAX_OUTPUT_TEXT:
            return None
        try:
            if len(encoded.encode("utf-8")) > MAX_OUTPUT_TEXT:
                return None
            result = json.loads(encoded)
        except (ValueError, RecursionError):
            return None
        if (
            not isinstance(result, dict)
            or not output_json_within_bounds(result)
            or result.get("app_id") != observation.identity.app_id
            or result.get("operation_key") != observation.identity.operation_key
            or not isinstance(result.get("output"), dict)
            or (
                result.get("object_refs") is not None
                and (
                    not isinstance(result["object_refs"], list)
                    or any(not isinstance(ref, dict) for ref in result["object_refs"])
                )
            )
            or (
                result.get("evidence") is not None
                and not isinstance(result["evidence"], dict)
            )
            or not set(result)
            <= {
                "app_id",
                "operation_key",
                "output",
                "object_refs",
                "evidence",
                "_operation_contract",
            }
        ):
            return None
        return result


async def _current_result_context(
    scope: ExecutionScope,
    subject: Subject,
    app_id: str,
    operation_key: str,
    payload: dict,
) -> OperationResultContext | None:
    from app.services.app_operations.dispatch import operation_declaration_fingerprint
    from app.services.app_operations.registry import get_app_operation

    row = await primary_node_record(get_default_context().database, App, app_id)
    spec = get_app_operation(scope.workspace_id, app_id, operation_key)
    if (
        row is None
        or row["context"].get("workspace_id") != scope.workspace_id
        or spec is None
        or str(spec.get("kind") or "execute")
        not in {"execute", "propose", "destructive"}
    ):
        return None
    app = await get_default_context()._deserialize_entity(App, row)
    if app is None:
        return None
    issuer_id = subject.id
    if subject.kind != "human":
        if not spec.get("authorization"):
            return None
        from app.services.app_operation_authority import operation_read_principal

        issuer_id = await operation_read_principal(
            subject=subject,
            workspace_id=scope.workspace_id,
            app_id=app_id,
            operation_key=operation_key,
        )
    context = OperationResultContext(
        subject=subject.model_copy(deep=True),
        issuer_id=issuer_id,
        workspace_id=scope.workspace_id,
        app_id=app_id,
        operation_key=operation_key,
        payload=dict(payload),
        declaration_fingerprint=operation_declaration_fingerprint(spec, app),
    )
    await context.admit_current_function()
    return context


async def recover_recorded_operation_response(
    *,
    scope: ExecutionScope,
    subject: Subject,
    app_id: str,
    operation_key: str,
    payload: dict,
    idempotency_key: str,
    expected_effect_contract_fingerprint: str | None = None,
    expected_decision_intent_fingerprint: str | None = None,
) -> AppOperationResponse | None:
    """Resolve an exact prior command; None means no qualifying journal row.

    The scope/subject must come from the authenticated transport or admitted
    immutable run, never request body/preferences. This coordinator must run
    before an effect transaction. Callers keep ordinary execution admission on
    absence. Current own-history denial/outage/conflict must not become absence.
    """
    require_outside_graph_transaction()
    subject = subject.model_copy(deep=True)
    request_hash = canonical_request_hash(payload)
    from app.contracts.operations import OperationIdentity

    async def observe(principal):
        return await lookup_operation_outcome(
            identity=OperationIdentity.create(
                workspace_id=scope.workspace_id,
                app_id=app_id,
                operation_key=operation_key,
                principal_id=principal.principal_id,
                idempotency_key=idempotency_key,
            ),
            principal_aliases=principal.aliases,
            request_hash=request_hash,
            expected_effect_contract_fingerprint=expected_effect_contract_fingerprint,
            expected_decision_intent_fingerprint=expected_decision_intent_fingerprint,
        )

    async with operation_history_admission(
        scope=scope, subject=subject, app_id=app_id
    ) as principal:
        observation = await observe(principal)
    if observation is None:
        return None

    async def original_still_present(principal):
        current = await observe(principal)
        if current is None or current.identity != observation.identity:
            raise OperationReceiptIncomplete(
                "Original operation outcome changed during recovery"
            )
        return current

    try:
        context = await _current_result_context(
            scope, subject, app_id, operation_key, payload
        )
    except _RESULT_WITHHELD:
        context = None
    if context is not None:
        # Read private output only after current function/read eligibility.
        result = await _saved_result(observation, request_hash)
        if result is not None:
            from app.services.app_operations.result_access import (
                authorize_replayed_result,
            )

            try:
                # An ordinary output denial must exit/roll back these read
                # scopes before fallback, never poison a caller's effect txn.
                async with operation_history_admission(
                    scope=scope, subject=subject, app_id=app_id
                ) as principal:
                    await original_still_present(principal)
                    await authorize_replayed_result(
                        result,
                        user_id=context.issuer_id,
                        workspace_id=scope.workspace_id,
                        app_id=app_id,
                        admit_context=context.admit,
                    )
                return AppOperationInvokeResponse(
                    app_id=app_id,
                    operation_key=operation_key,
                    output=result["output"],
                    object_refs=result.get("object_refs") or [],
                    evidence=result.get("evidence"),
                    operation_receipt=observation.reference(),
                )
            except _RESULT_WITHHELD:
                pass

    # Re-admit current identity/workspace/history and the original receipt after
    # the disclosure attempt. No private output or denial reasons enter this DTO.
    async with operation_history_admission(
        scope=scope, subject=subject, app_id=app_id
    ) as principal:
        current = await original_still_present(principal)
    return AppOperationOutputWithheld(operation_receipt=current.reference())
