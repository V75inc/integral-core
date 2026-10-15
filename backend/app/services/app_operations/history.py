"""Current own-journal admission, separate from execution and output authority.

This service accepts a server-bound authenticated ExecutionScope and Subject,
never a client-selected principal/workspace. Constructing ExecutionScope is not
authentication. Transport adapters must establish that binding and revalidate
their credential before using this service or releasing its opaque reference.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from jvspatial.core.context import get_default_context

from app.api.errors import InsufficientPermissionsError
from app.contracts.operations import OperationIdentity, canonical_request_hash
from app.contracts.runtime import ExecutionScope
from app.exceptions import AuthorizationUnavailableError
from app.middleware.permissions_cache import isolated_permissions_cache
from app.schemas.policy import Resource, Subject
from app.services.app_operations.execution_receipts import (
    OperationOutcomeObservation,
    lookup_operation_outcome,
)
from app.services.app_operations.principal import (
    OperationPrincipal,
    resolve_operation_principal,
)
from app.services.app_operations.transaction_scope import postgres_graph_transaction
from app.services.authority_facts import primary_node_record, required_node_text
from app.services.authorization_scope import authorization_scope
from app.services.identity_admission import identity_admission
from app.services.policy_engine import evaluate
from app.services.service_delegation import (
    delegated_principal_ids,
    delegated_subject_record,
)
from app.services.workspace_governance import ordinary_authority_only


@asynccontextmanager
async def operation_history_admission(
    *, scope: ExecutionScope, subject: Subject, app_id: str
) -> AsyncIterator[OperationPrincipal]:
    """Fence current identities/delegation and selected workspace for own status.

    Human members can observe only their own namespace. Services need a current,
    direct ``operation.history.read_own`` delegation, independently of function
    execution. Their issuer is fenced but can never become a receipt alias.
    No network/provider call or transport send belongs inside these locks.
    """
    if (
        not isinstance(scope, ExecutionScope)
        or subject.kind not in {"human", "agent", "connector"}
        or scope.principal_id != subject.id
        or not scope.workspace_id
        or type(app_id) is not str
        or not app_id
        or app_id != app_id.strip()
    ):
        raise InsufficientPermissionsError(
            message="Current own-history binding required"
        )
    # Copy before any await so a mutable caller Subject cannot change authority.
    subject = subject.model_copy(deep=True)
    with isolated_permissions_cache(), ordinary_authority_only():
        async with postgres_graph_transaction():
            participants = (
                (subject.id,)
                if subject.kind == "human"
                else await delegated_principal_ids(subject)
            )
            service = (
                None
                if subject.kind == "human"
                else await delegated_subject_record(subject)
            )
            async with identity_admission(participants):
                database = get_default_context().database
                target_kind, target_id = "workspace", scope.workspace_id
                if service is not None:
                    model, collection, identity = service
                    row = await database.find_one_and_update(
                        collection,
                        {"id": identity, "entity": model._entity_name()},
                        {"$set": {}},
                    )
                    if row is None:
                        raise AuthorizationUnavailableError()
                    await delegated_subject_record(subject)
                    body = row.get("context") or {}
                    if (
                        body.get("workspace_id")
                        and body["workspace_id"] != scope.workspace_id
                    ):
                        raise InsufficientPermissionsError(
                            message="History workspace mismatch"
                        )
                    if body.get("app_id"):
                        if body["app_id"] != app_id:
                            raise InsufficientPermissionsError(
                                message="History App mismatch"
                            )
                        target_kind, target_id = "app", app_id
                async with authorization_scope(scope.workspace_id):
                    from app.services.resource_location import (
                        MANAGEMENT_APP_STATES,
                        locate_resource,
                    )

                    location = await locate_resource(
                        target_kind, target_id, allowed_app_states=MANAGEMENT_APP_STATES
                    )
                    if location is None or location.workspace_id != scope.workspace_id:
                        raise InsufficientPermissionsError(
                            message="History workspace mismatch"
                        )
                    decision = await evaluate(
                        subject=subject,
                        action="operation.history.read_own",
                        resource=Resource(
                            kind=target_kind,
                            id=target_id,
                            scope=f"{target_kind}:{target_id}",
                        ),
                        _dry_run=True,
                        _allow_owner_governance=False,
                    )
                    if not decision.allowed:
                        raise InsufficientPermissionsError(
                            message="Own-history access denied"
                        )
                    if service is not None:
                        from app.models.nodes import Policy
                        from app.services.permissions import get_user_node
                        from app.services.policy_registry import current_policy_body

                        policy = await primary_node_record(
                            database, Policy, decision.matched_policy_id
                        )
                        if policy is None:
                            raise AuthorizationUnavailableError()
                        body = current_policy_body(
                            policy, subject_kind=subject.kind, subject_id=subject.id
                        )
                        issuer = await get_user_node(
                            required_node_text(policy, "created_by")
                        )
                        if (
                            issuer is None
                            or issuer.id not in participants
                            or body["requires_human_approval"]
                            or body["scope"] != f"{target_kind}:{target_id}"
                            or "operation.history.read_own" not in body["actions"]
                        ):
                            raise InsufficientPermissionsError(
                                message="Current direct history delegation required"
                            )
                    yield await resolve_operation_principal(subject)


async def observe_own_operation_outcome(
    *,
    scope: ExecutionScope,
    subject: Subject,
    app_id: str,
    operation_key: str,
    payload: dict,
    idempotency_key: str,
) -> OperationOutcomeObservation | None:
    """Read original completion independently of current domain/handler access.

    The private return is not a transport response. After any intervening await,
    transports must repeat this admission before releasing its reference and
    explicitly state that saved output/current domain state are unverified.
    Absence is not execution authority; preserve normal fresh-command admission.
    """
    async with operation_history_admission(
        scope=scope, subject=subject, app_id=app_id
    ) as principal:
        return await lookup_operation_outcome(
            identity=OperationIdentity.create(
                workspace_id=scope.workspace_id,
                app_id=app_id,
                operation_key=operation_key,
                principal_id=principal.principal_id,
                idempotency_key=idempotency_key,
            ),
            principal_aliases=principal.aliases,
            request_hash=canonical_request_hash(payload),
        )
