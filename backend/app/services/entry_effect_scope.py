"""Current identity, workspace and exact mutation admission for Entry commands."""

from contextlib import AsyncExitStack, asynccontextmanager

from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.middleware.permissions_cache import isolated_permissions_cache
from app.schemas.policy import Resource, Subject
from app.services.authorization_scope import authorization_scope
from app.services.entry_write_scope import is_postgres_entry_transaction
from app.services.identity_admission import identity_admission
from app.services.resource_location import DATA_APP_STATES, locate_resource


class ExclusiveEntryCreateRequired(Exception):
    """Abort an optimistic create before retrying with exclusive admission."""


@asynccontextmanager
async def entry_effect_scope(
    *,
    user_id: str,
    actor_kind: str,
    actor_id: str | None,
    action: str,
    target_kind: str,
    target_id: str,
    workspace_id: str,
    payload: dict,
    authorization_subject: Subject | None = None,
    locked_target_out: dict | None = None,
    shared_create: bool = False,
):
    """Join the command transaction; hold authority until its effects finish."""
    from app.api.errors import InsufficientPermissionsError
    from app.services.app_operation_authority import permits_mutation
    from app.services.app_operations.transaction_scope import (
        measure_write_lock,
        trace_entry_write,
    )
    from app.services.policy_engine import evaluate
    from app.services.service_delegation import (
        delegated_principal_ids,
        delegated_subject_record,
    )

    trace_entry_write(action)
    if user_id == "public":
        from app.services.public_entry_effect import public_entry_effect_permitted

        if not await public_entry_effect_permitted(
            action=action,
            target_kind=target_kind,
            target_id=target_id,
            workspace_id=workspace_id,
        ):
            raise InsufficientPermissionsError(
                message="Current public submission authority required"
            )
        yield user_id
        return
    # Agentive provenance is independent of actor authority. A canonical
    # human alias retains that human; other service claims must be admitted.
    # Opaque provenance requires an explicit human authorization subject.
    human = Subject(kind="human", id=user_id)
    if authorization_subject is not None and authorization_subject.kind in {
        "agent",
        "connector",
    }:
        subject = authorization_subject
    elif authorization_subject is not None and authorization_subject.kind == "human":
        from app.services.permissions import get_user_node

        caller = await get_user_node(user_id)
        author = await get_user_node(authorization_subject.id)
        if caller is None or author is None or caller.id != author.id:
            raise AuthorizationUnavailableError()
        subject = human
    elif actor_id and actor_kind in {"agent", "connector"}:
        subject = Subject(kind=actor_kind, id=actor_id)
        if actor_id == user_id or actor_id.startswith(("n.User.", "o.User.")):
            from app.services.permissions import get_user_node

            caller = await get_user_node(user_id)
            author = await get_user_node(actor_id)
            if caller is not None and author is not None and caller.id == author.id:
                subject = human
    else:
        subject = human
    participants = (
        (subject.id,)
        if subject.kind == "human"
        else await delegated_principal_ids(subject)
    )
    service = (
        None if subject.kind == "human" else await delegated_subject_record(subject)
    )
    # Type changes, custom fields and cascades may affect other records.
    # Only metadata updates and guarded standalone creates can share admission.
    shared = (
        action == "entry.update"
        and target_kind == "entry"
        and "custom_fields" not in payload
        and "type_id" not in payload
    )
    shared = shared or (
        shared_create
        and action == "entry.create"
        and target_kind == "track"
        and subject.kind == "human"
    )
    with isolated_permissions_cache():
        async with identity_admission(participants, shared=shared):
            database = get_default_context().database
            if service is not None and is_postgres_entry_transaction(database):
                model, collection, identity = service
                with measure_write_lock("delegation_row", shared=shared):
                    locked = await database.lock_record(
                        collection, identity, shared=shared
                    )
                if locked is None or locked.get("entity") != model._entity_name():
                    raise AuthorizationUnavailableError()
            async with AsyncExitStack() as stack:
                locked_target = None
                if is_postgres_entry_transaction(database):
                    await stack.enter_async_context(
                        authorization_scope(workspace_id, shared=shared)
                    )
                    if target_kind == "entry":
                        with measure_write_lock("entry_row"):
                            locked_target = await database.lock_record(
                                "node", target_id
                            )
                        if locked_target is None:
                            raise AuthorizationUnavailableError()
                    elif shared_create:
                        with measure_write_lock("entry_create_track_row", shared=True):
                            locked_target = await database.lock_record(
                                "node", target_id, shared=True
                            )
                        if locked_target is None:
                            raise ExclusiveEntryCreateRequired()
                records: dict[str, dict] = {}
                location = await locate_resource(
                    target_kind,
                    target_id,
                    allowed_app_states=DATA_APP_STATES,
                    records_out=(
                        records if target_kind == "entry" or shared_create else None
                    ),
                    selected_record=locked_target,
                )
                if location is None or location.workspace_id != workspace_id:
                    raise InsufficientPermissionsError(
                        message="Entry target is outside the admitted workspace"
                    )
                if shared_create and location.app_id:
                    raise ExclusiveEntryCreateRequired()
                from app.models.nodes import EntryType
                from app.services.authority_facts import primary_node_record
                from app.services.operational_model_compile import slug_manifest_key

                type_id = payload.get("type_id") if target_kind == "track" else ""
                if target_kind == "entry":
                    entry_row = records.get(target_id)
                    if entry_row is None:
                        raise AuthorizationUnavailableError()
                    type_id = entry_row["context"].get("type_id")
                entry_type_key = ""
                if type_id:
                    type_row = await primary_node_record(database, EntryType, type_id)
                    if type_row is None:
                        from app.api.errors import ResourceNotFoundError

                        raise ResourceNotFoundError(message="Entry type not found")
                    schema = type_row["context"].get("form_schema")
                    if not isinstance(schema, dict):
                        raise AuthorizationUnavailableError()
                    entry_type_key = slug_manifest_key(
                        str(
                            schema.get("_manifest_entry_type_key")
                            or type_row["context"].get("name")
                            or ""
                        )
                    )
                resource = Resource(
                    kind="entry",
                    id=target_id if target_kind == "entry" else "",
                    scope=f"track:{location.track_id or target_id}",
                )
                decision = await evaluate(
                    subject=subject,
                    action=action,
                    resource=resource,
                    payload=payload,
                    _dry_run=True,
                )
                exact = False
                mutation = action.split(".", 1)[1]
                exact_shape = (
                    not any(
                        key in payload
                        for key in (
                            "tags",
                            "attachment_ids",
                            "type_id",
                            "replace_custom_fields",
                            "provenance",
                        )
                    )
                    if mutation != "create"
                    else not any(
                        payload.get(key)
                        for key in ("tags", "attachment_ids", "provenance")
                    )
                )
                if (
                    not decision.allowed
                    and subject.kind == "human"
                    and entry_type_key
                    and exact_shape
                ):
                    exact = await permits_mutation(
                        principal_id=subject.id,
                        workspace_id=workspace_id,
                        entry_type_key=entry_type_key,
                        fields=set(payload.get("custom_fields") or {}),
                        track_id=(
                            target_id if target_kind == "track" else location.track_id
                        ),
                        entry_id=target_id if target_kind == "entry" else "",
                        mutation_kind=mutation,
                        top_level={
                            key
                            for key in ("title", "body", "status")
                            if key in payload
                            and (mutation != "create" or payload[key] not in (None, ""))
                        },
                    )
                exact_principal = None
                if (
                    not decision.allowed
                    and subject.kind in {"agent", "connector"}
                    and entry_type_key
                    and exact_shape
                ):
                    from app.services.app_operation_authority import (
                        current_operation_delegator,
                    )

                    exact_principal = await current_operation_delegator(
                        subject=subject,
                        workspace_id=workspace_id,
                        entry_type_key=entry_type_key,
                        fields=set(payload.get("custom_fields") or {}),
                        track_id=(
                            target_id if target_kind == "track" else location.track_id
                        ),
                        entry_id=target_id if target_kind == "entry" else "",
                        mutation_kind=mutation,
                        top_level={
                            key
                            for key in ("title", "body", "status")
                            if key in payload
                            and (mutation != "create" or payload[key] not in (None, ""))
                        },
                    )
                    exact = exact_principal is not None
                if not decision.allowed and not exact:
                    raise InsufficientPermissionsError(
                        message="Current Entry effect authority denied"
                    )
                if locked_target_out is not None and locked_target is not None:
                    locked_target_out.update(records[target_id])
                if exact_principal is not None:
                    if exact_principal not in participants:
                        raise AuthorizationUnavailableError()
                    yield exact_principal
                elif subject.kind == "human":
                    from app.services.permissions import get_user_node

                    principal = await get_user_node(subject.id)
                    if principal is None:
                        raise AuthorizationUnavailableError()
                    yield subject.id
                else:
                    from app.models.nodes import Policy
                    from app.services.authority_facts import required_node_text

                    policy_row = await primary_node_record(
                        database, Policy, decision.matched_policy_id
                    )
                    if policy_row is None:
                        raise AuthorizationUnavailableError()
                    principal_id = required_node_text(policy_row, "created_by")
                    from app.services.permissions import get_user_node

                    principal = await get_user_node(principal_id)
                    if principal is None or principal.id not in participants:
                        raise AuthorizationUnavailableError()
                    yield principal.id
