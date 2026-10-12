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
    entry_type_key: str = "",
):
    """Join the command transaction; hold authority until its effects finish."""
    from app.api.errors import InsufficientPermissionsError
    from app.services.app_operation_authority import permits_mutation
    from app.services.policy_engine import evaluate
    from app.services.service_delegation import (
        delegated_principal_ids,
        delegated_subject_record,
    )

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
    subject = Subject(
        kind=(
            actor_kind if actor_id and actor_kind in {"agent", "connector"} else "human"
        ),
        id=actor_id if actor_id and actor_kind in {"agent", "connector"} else user_id,
    )
    participants = (
        (subject.id,)
        if subject.kind == "human"
        else await delegated_principal_ids(subject)
    )
    service = (
        None if subject.kind == "human" else await delegated_subject_record(subject)
    )
    with isolated_permissions_cache():
        async with identity_admission(participants):
            database = get_default_context().database
            if service is not None and is_postgres_entry_transaction(database):
                model, collection, identity = service
                if (
                    await database.find_one_and_update(
                        collection,
                        {"id": identity, "entity": model._entity_name()},
                        {"$set": {}},
                    )
                    is None
                ):
                    raise AuthorizationUnavailableError()
            async with AsyncExitStack() as stack:
                if is_postgres_entry_transaction(database):
                    await stack.enter_async_context(authorization_scope(workspace_id))
                location = await locate_resource(
                    target_kind, target_id, allowed_app_states=DATA_APP_STATES
                )
                if location is None or location.workspace_id != workspace_id:
                    raise InsufficientPermissionsError(
                        message="Entry target is outside the admitted workspace"
                    )
                from app.models.nodes import Entry, EntryType
                from app.services.authority_facts import primary_node_record
                from app.services.operational_model_compile import slug_manifest_key

                type_id = payload.get("type_id") if target_kind == "track" else ""
                if target_kind == "entry":
                    entry_row = await primary_node_record(database, Entry, target_id)
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
