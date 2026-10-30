"""Exact ephemeral permits for declared App effects, never an ambient bypass."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, AsyncIterator

from app.exceptions import AuthorizationUnavailableError
from app.schemas.access_delegation import ActionAuthorization
from app.schemas.policy import Decision, Resource, Subject
from app.services.access_delegation import action_decision, current_definition
from app.services.resource_location import locate_resource


@dataclass(frozen=True)
class OperationPermit:
    principal_id: str
    workspace_id: str
    app_id: str
    operation_key: str
    definition_id: str
    fingerprint: str
    payload: dict
    declaration: ActionAuthorization | None
    task: Any
    subject: Subject
    delegator_id: str


_permit: ContextVar[OperationPermit | None] = ContextVar(
    "integral_exact_operation_permit", default=None
)


async def operation_decision(
    *, subject: Subject, app_id: str, operation_key: str, payload: dict
) -> tuple[Decision, str]:
    """Resolve current exact function authority and its human author independently of actor provenance."""
    if subject.kind == "human":
        return (
            await action_decision(
                principal_id=subject.id,
                app_id=app_id,
                operation_key=operation_key,
                payload=payload,
            ),
            subject.id,
        )
    if subject.kind not in {"agent", "connector"}:
        return Decision(allowed=False, reason="app_operation_subject_denied"), ""
    from jvspatial.core.context import get_default_context

    from app.models.nodes import Policy
    from app.services.authority_facts import primary_node_record, required_node_text
    from app.services.permissions import get_user_node
    from app.services.policy_engine import evaluate
    from app.services.policy_registry import current_policy_body
    from app.services.service_delegation import delegated_subject_record

    await delegated_subject_record(subject)
    decision = await evaluate(
        subject=subject,
        action=f"app:{app_id}:{operation_key}",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
        payload=payload,
        _dry_run=True,
        _allow_owner_governance=False,
    )
    if not decision.allowed:
        return decision, ""
    row = await primary_node_record(
        get_default_context().database, Policy, decision.matched_policy_id
    )
    if row is None:
        raise AuthorizationUnavailableError()
    body = current_policy_body(row, subject_kind=subject.kind, subject_id=subject.id)
    if body["requires_human_approval"]:
        _, current = await current_definition(app_id)
        declarations = [
            operation
            for operation in (
                current["context"]["canonical_manifest"].get("app") or {}
            ).get("operations", [])
            if operation.get("key") == operation_key
        ]
        if len(declarations) != 1:
            raise AuthorizationUnavailableError()
        if declarations[0].get("kind", "execute") != "read":
            from app.services.approved_effect import approved_policy_replay

            if not await approved_policy_replay(
                subject=subject,
                action=f"app:{app_id}:{operation_key}",
                resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
                payload=payload,
                policy_id=decision.matched_policy_id,
            ):
                return (
                    Decision(
                        allowed=False,
                        reason="requires_human_approval",
                        matched_policy_id=decision.matched_policy_id,
                    ),
                    "",
                )
    author = await get_user_node(required_node_text(row, "created_by"))
    if author is None or body["is_active"] is not True:
        raise AuthorizationUnavailableError()
    return decision, author.id


async def operation_read_principal(
    *,
    subject: Subject,
    workspace_id: str,
    app_id: str,
    operation_key: str,
    required_delegator_id: str | None = None,
) -> str:
    """Select a current bounded issuer audience for read-only selector resolution."""
    from app.services.permissions import get_user_node

    if subject.kind == "human":
        user = await get_user_node(subject.id)
        if user is None:
            raise AuthorizationUnavailableError()
        return user.id
    from jvspatial.core.context import get_default_context

    from app.services.access_delegation import action_role_delegable
    from app.services.authority_facts import primary_node_record
    from app.services.policy_engine import (
        _action_matches,
        _filter_matches,
        _scope_matches,
    )
    from app.services.policy_registry import list_policies_for_subject
    from app.services.service_delegation import delegated_subject_record

    model, _, identity = await delegated_subject_record(subject)
    row = await primary_node_record(get_default_context().database, model, identity)
    if (
        row is None
        or (
            row["context"].get("workspace_id")
            and row["context"]["workspace_id"] != workspace_id
        )
        or (row["context"].get("app_id") and row["context"]["app_id"] != app_id)
    ):
        raise AuthorizationUnavailableError()
    resource = Resource(kind="app", id=app_id, scope=f"app:{app_id}")
    for policy in await list_policies_for_subject(subject.kind, subject.id):
        if (
            not policy.is_active
            or not _scope_matches(policy.scope, resource.scope)
            or not _action_matches(policy.actions, f"app:{app_id}:{operation_key}")
            or not _filter_matches(policy, resource)[0]
        ):
            continue
        author = await get_user_node(policy.created_by)
        if author is None or (
            required_delegator_id is not None and author.id != required_delegator_id
        ):
            continue
        if not row["context"].get("workspace_id"):
            owner = await get_user_node(
                row["context"].get("user_id") or row["context"].get("owner") or ""
            )
            if owner is None or owner.id != author.id:
                continue
        if await action_role_delegable(
            principal_id=author.id, app_id=app_id, operation_key=operation_key
        ):
            return author.id
    from app.api.errors import InsufficientPermissionsError

    raise InsufficientPermissionsError(
        message="Current bounded service function delegation required"
    )


@asynccontextmanager
async def operation_permit(
    *,
    principal_id: str,
    workspace_id: str,
    app_id: str,
    operation_key: str,
    payload: dict,
    subject: Subject | None = None,
) -> AsyncIterator[OperationPermit]:
    """Admit one exact operation definition and its declared target effects."""
    app, definition = await current_definition(app_id)
    if app["context"].get("workspace_id") != workspace_id:
        raise AuthorizationUnavailableError()
    operations = (
        (definition["context"].get("canonical_manifest") or {}).get("app") or {}
    ).get("operations", [])
    specs = [op for op in operations if op.get("key") == operation_key]
    if len(specs) != 1:
        raise AuthorizationUnavailableError()
    declaration = (
        ActionAuthorization.model_validate(specs[0]["authorization"])
        if specs[0].get("authorization")
        else None
    )
    bound_subject = subject or Subject(kind="human", id=principal_id)
    if bound_subject.id != principal_id or bound_subject.kind not in {
        "human",
        "agent",
        "connector",
    }:
        raise AuthorizationUnavailableError()
    delegator_id = principal_id
    if declaration is not None:
        decision, delegator_id = await operation_decision(
            subject=bound_subject,
            app_id=app_id,
            operation_key=operation_key,
            payload=payload,
        )
        if not decision.allowed:
            from app.api.errors import InsufficientPermissionsError

            raise InsufficientPermissionsError(
                message="Current App action authority required"
            )
    elif bound_subject.kind != "human":
        from app.api.errors import InsufficientPermissionsError

        raise InsufficientPermissionsError(
            message="Service operations require explicit function declarations"
        )
    import copy

    permit = OperationPermit(
        principal_id,
        workspace_id,
        app_id,
        operation_key,
        definition["id"],
        definition["context"]["manifest_fingerprint"],
        copy.deepcopy(payload),
        declaration,
        asyncio.current_task(),
        bound_subject,
        delegator_id,
    )
    token = _permit.set(permit)
    try:
        yield permit
    finally:
        _permit.reset(token)


async def permits_mutation(
    *,
    workspace_id: str,
    entry_type_key: str,
    fields: set[str],
    track_id: str = "",
    entry_id: str = "",
    mutation_kind: str = "update",
    top_level: set[str] | None = None,
    principal_id: str = "",
) -> bool:
    """Check current action, definition and exact target before an effect."""
    permit = _permit.get()
    if (
        permit is None
        or not principal_id
        or permit.task is not asyncio.current_task()
        or permit.workspace_id != workspace_id
    ):
        return False
    if principal_id != permit.principal_id:
        from app.services.permissions import get_user_node

        supplied = await get_user_node(principal_id)
        bound = await get_user_node(permit.principal_id)
        if supplied is None or bound is None or supplied.id != bound.id:
            return False
    kind, identity = ("entry", entry_id) if entry_id else ("track", track_id)
    if not identity:
        return False
    location = await locate_resource(kind, identity)
    if (
        location is None
        or location.workspace_id != workspace_id
        or location.app_id != permit.app_id
    ):
        return False
    _, definition = await current_definition(permit.app_id)
    if (
        definition["id"] != permit.definition_id
        or definition["context"]["manifest_fingerprint"] != permit.fingerprint
    ):
        return False
    declaration = permit.declaration
    if declaration is None:
        # Legacy declarations cannot invent function grants, but their existing
        # explicit protected-state operation list can constrain a scoped permit.
        protected = (
            (definition["context"].get("canonical_manifest") or {}).get("app") or {}
        ).get("protected_state", {})
        state = protected.get(entry_type_key, {})
        if not isinstance(state, dict) or permit.operation_key not in state.get(
            "operations", []
        ):
            return False
        return (
            not top_level
            and mutation_kind == "update"
            and fields <= set(state.get("fields", []))
            and identity in permit.payload.values()
        )
    decision, delegator_id = await operation_decision(
        subject=permit.subject,
        app_id=permit.app_id,
        operation_key=permit.operation_key,
        payload=permit.payload,
    )
    if not decision.allowed or (
        permit.subject.kind != "human" and delegator_id != permit.delegator_id
    ):
        return False
    targets = {
        permit.payload.get(t.input) for t in declaration.targets if t.kind == kind
    }
    if identity not in targets:
        return False
    return any(
        m.entry_type == entry_type_key
        and m.kind == mutation_kind
        and fields <= set(m.fields)
        and (top_level or set()) <= set(m.top_level)
        for m in declaration.mutations
    )


async def entry_mutation_permitted(
    *,
    principal_id: str,
    workspace_id: str,
    app_id: str,
    entry_id: str,
    custom_fields: dict,
    mutation_kind: str = "update",
    top_level: dict | None = None,
) -> bool:
    """Authorize a facade transition without granting generic editor authority."""
    from jvspatial.core.context import get_default_context

    from app.models.nodes import Entry, EntryType
    from app.services.authority_facts import primary_node_record, required_node_text
    from app.services.operational_model_compile import _slug

    permit = _permit.get()
    if permit is None or permit.app_id != app_id:
        return False
    database = get_default_context().database
    entry = await primary_node_record(database, Entry, entry_id)
    if entry is None:
        return False
    from app.models.edges import IS_OF_TYPE

    type_id = required_node_text(entry, "type_id")
    type_edges = await database.find(
        "edge", {"source": entry_id, "entity": IS_OF_TYPE._entity_name()}, limit=2
    )
    if len(type_edges) != 1 or type_edges[0].get("target") != type_id:
        raise AuthorizationUnavailableError()
    entry_type = await primary_node_record(
        database, EntryType, required_node_text(entry, "type_id")
    )
    if entry_type is None:
        return False
    fields = entry_type["context"].get("form_schema") or {}
    key = _slug(
        str(
            fields.get("_manifest_entry_type_key")
            or entry_type["context"].get("name")
            or ""
        )
    )
    return await permits_mutation(
        workspace_id=workspace_id,
        entry_type_key=key,
        fields=set(custom_fields),
        entry_id=entry_id,
        mutation_kind=mutation_kind,
        top_level=set(top_level or {}),
        principal_id=principal_id,
    )


async def current_operation_delegator(
    *,
    subject: Subject,
    workspace_id: str,
    entry_type_key: str,
    fields: set[str],
    track_id: str = "",
    entry_id: str = "",
    mutation_kind: str = "update",
    top_level: set[str] | None = None,
) -> str | None:
    """Return current human author only after exact whole service mutation admission."""
    permit = _permit.get()
    if (
        permit is None
        or permit.subject.kind != subject.kind
        or permit.subject.id != subject.id
    ):
        return None
    if not await permits_mutation(
        principal_id=subject.id,
        workspace_id=workspace_id,
        entry_type_key=entry_type_key,
        fields=fields,
        track_id=track_id,
        entry_id=entry_id,
        mutation_kind=mutation_kind,
        top_level=top_level,
    ):
        return None
    decision, author = await operation_decision(
        subject=subject,
        app_id=permit.app_id,
        operation_key=permit.operation_key,
        payload=permit.payload,
    )
    return author if decision.allowed else None


async def current_operation_actor(
    *, subject: Subject, workspace_id: str, app_id: str, operation_key: str
) -> str | None:
    """Admit audit/read release only inside the original exact task and issuer."""
    permit = _permit.get()
    if (
        permit is None
        or permit.task is not asyncio.current_task()
        or permit.subject != subject
        or permit.workspace_id != workspace_id
        or permit.app_id != app_id
        or permit.operation_key != operation_key
    ):
        return None
    _, definition = await current_definition(app_id)
    if (
        definition["id"] != permit.definition_id
        or definition["context"]["manifest_fingerprint"] != permit.fingerprint
    ):
        return None
    if permit.declaration is None:
        return permit.delegator_id if subject.kind == "human" else None
    specs = [
        operation
        for operation in (
            definition["context"]["canonical_manifest"].get("app") or {}
        ).get("operations", [])
        if operation.get("key") == operation_key
    ]
    if len(specs) != 1:
        return None
    if specs[0].get("kind", "execute") == "read":
        decision, author = await operation_decision(
            subject=subject,
            app_id=app_id,
            operation_key=operation_key,
            payload=permit.payload,
        )
        return (
            author
            if decision.allowed
            and (subject.kind == "human" or author == permit.delegator_id)
            else None
        )
    # This is an audit/read-release check, never mutation authority. A successful
    # transition can intentionally consume its own before-state predicate;
    # every mutation still uses permits_mutation's complete current conditions.
    from app.services.access_delegation import action_role_delegable

    if subject.kind == "human":
        return (
            permit.delegator_id
            if await action_role_delegable(
                principal_id=subject.id, app_id=app_id, operation_key=operation_key
            )
            else None
        )
    from app.api.errors import InsufficientPermissionsError

    try:
        author = await operation_read_principal(
            subject=subject,
            workspace_id=workspace_id,
            app_id=app_id,
            operation_key=operation_key,
            required_delegator_id=permit.delegator_id,
        )
    except InsufficientPermissionsError:
        return None
    return author


async def email_enqueue_actor(
    *, subject: Subject, workspace_id: str, app_id: str, operation_key: str
) -> str | None:
    """Admit the declared enqueue effect under the current exact reviewed command.

    Unlike audit/read release this rechecks the full current operation decision,
    including its before-state conditions and any required approval replay.
    The caller must enqueue inside that command's task-owned transaction.
    """
    permit = _permit.get()
    if (
        permit is None
        or permit.task is not asyncio.current_task()
        or permit.subject != subject
        or permit.workspace_id != workspace_id
        or permit.app_id != app_id
        or permit.operation_key != operation_key
        or permit.declaration is None
        or "email.enqueue" not in permit.declaration.effects
    ):
        return None
    _, definition = await current_definition(app_id)
    if (
        definition["id"] != permit.definition_id
        or definition["context"]["manifest_fingerprint"] != permit.fingerprint
    ):
        return None
    specs = [
        op
        for op in (definition["context"]["canonical_manifest"].get("app") or {}).get(
            "operations", []
        )
        if op.get("key") == operation_key
    ]
    if len(specs) != 1 or specs[0].get("kind", "execute") == "read":
        return None
    decision, author = await operation_decision(
        subject=subject,
        app_id=app_id,
        operation_key=operation_key,
        payload=permit.payload,
    )
    return author if decision.allowed and author == permit.delegator_id else None
