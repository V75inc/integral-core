"""Canonical, transaction-bound resident assignment mutations."""

from jvspatial.core.context import get_default_context

from app.agentive.edges import ResidentAssignment
from app.api.errors import (
    InsufficientPermissionsError,
    ResourceConflictError,
    ResourceNotFoundError,
    ServiceUnavailableError,
)
from app.contracts.task_semantics import (
    RESIDENT_ASSIGNMENT_CHANGED,
    RESIDENT_ASSIGNMENT_SETTINGS_CHANGED,
)
from app.models.edges import (
    CONTAINS,
    HAS_AUTHORIZATION_STATE,
    HAS_OPERATIONAL_MODEL,
    IS_OF_TYPE,
)
from app.models.nodes import (
    Entry,
    EntryType,
    Track,
    Workspace,
    WorkspaceAuthorizationState,
)
from app.schemas.resident_assignment import (
    AssignmentReadiness,
    ResidentAssignmentRequest,
    ResidentAssignmentResponse,
    ResidentAssignmentSettings,
    ResidentAssignmentView,
)
from app.services.app_operations.event_outbox import insert_revision_event
from app.services.app_operations.transaction_scope import (
    graph_transaction_available,
    postgres_graph_transaction,
)
from app.services.authorization_scope import authorization_scope, authorization_state_id
from app.services.entry_effect_scope import entry_effect_scope
from app.services.identity_admission import identity_admission
from app.services.permissions import resolve_role
from app.services.workspace_permissions import can_access_workspace
from app.utils.time import utc_now_iso


async def _assignment_enabled(workspace_id: str) -> bool:
    graph = get_default_context()
    state_id = authorization_state_id(workspace_id)
    row = await graph.database.get("node", state_id)
    if row is None:
        return False
    data = row.get("context") or {}
    if (
        row.get("entity") != WorkspaceAuthorizationState._entity_name()
        or data.get("workspace_id") != workspace_id
        or len(
            await graph.find_edges_between(
                workspace_id, state_id, edge_class=HAS_AUTHORIZATION_STATE
            )
        )
        != 1
    ):
        raise ResourceConflictError(
            message="Resident assignment settings need reconciliation"
        )
    return data.get("resident_assignment_enabled") is True


async def get_resident_assignment_settings(
    *, user_id: str, workspace_id: str
) -> ResidentAssignmentSettings:
    """Read the opt-in flag for a current workspace member."""
    if await can_access_workspace(user_id, workspace_id) == "none":
        raise InsufficientPermissionsError(message="Workspace access required")
    return ResidentAssignmentSettings(enabled=await _assignment_enabled(workspace_id))


async def set_resident_assignment_settings(
    *, user_id: str, workspace_id: str, settings: ResidentAssignmentSettings
) -> ResidentAssignmentSettings:
    """Order the opt-in with workspace authorization and Entry admission."""
    database = get_default_context().database
    if not graph_transaction_available(database):
        raise ServiceUnavailableError(message="Resident assignment requires PostgreSQL")
    async with postgres_graph_transaction(database) as transaction:
        async with identity_admission((user_id,)), authorization_scope(workspace_id):
            if await can_access_workspace(user_id, workspace_id) not in {
                "owner",
                "admin",
            }:
                raise InsufficientPermissionsError(
                    message="Workspace administration required"
                )
            prior = await _assignment_enabled(workspace_id)
            if prior == settings.enabled:
                return settings
            async with authorization_scope(
                workspace_id, authority_mutation=True
            ) as authority:
                updated = await transaction.find_one_and_update(
                    "node",
                    {"id": authorization_state_id(workspace_id)},
                    {"$set": {"context.resident_assignment_enabled": settings.enabled}},
                )
                if updated is None:
                    raise ResourceConflictError(
                        message="Resident assignment settings need reconciliation"
                    )
                await insert_revision_event(
                    transaction=transaction,
                    workspace_id=workspace_id,
                    namespace="workspace-resident-assignment-settings-v1",
                    subject_id=workspace_id,
                    revision=authority.revision,
                    event={
                        "actor_kind": "human",
                        "actor_id": user_id,
                        "action": RESIDENT_ASSIGNMENT_SETTINGS_CHANGED,
                        "resource_type": "Workspace",
                        "resource_id": workspace_id,
                        "scope": f"ws:{workspace_id}",
                        "before": {"enabled": prior},
                        "after": {"enabled": settings.enabled},
                    },
                )
    return settings


async def _load_assignment(
    entry_id: str, workspace_id: str
) -> ResidentAssignment | None:
    graph = get_default_context()
    rows = await graph.database.find(
        "edge",
        {"source": entry_id, "entity": ResidentAssignment._entity_name()},
        limit=2,
    )
    if not rows:
        return None
    if len(rows) == 1:
        edge = await graph._deserialize_entity(ResidentAssignment, rows[0])
        if edge is not None and edge.target == workspace_id and edge.revision >= 1:
            return edge
    raise ResourceConflictError(message="Resident assignment needs reconciliation")


async def _assignment_readiness(
    *,
    edge: ResidentAssignment,
    workspace_id: str,
    entry_id: str,
    track_id: str,
    entry_data: dict,
) -> AssignmentReadiness:
    """Classify current sponsor and published task input without dispatching work."""
    from app.contracts.task_semantics import FieldOutcome, validate_task_semantics
    from app.schemas.policy import Resource, Subject
    from app.services.app_graph import get_track_attached_operational_model
    from app.services.chat_providers import get_registry
    from app.services.policy_engine import evaluate

    if not await _assignment_enabled(workspace_id):
        return "paused"
    sponsor = edge.sponsor_principal_id
    if (
        not sponsor
        or await can_access_workspace(sponsor, workspace_id)
        not in {"owner", "admin", "member"}
        or await resolve_role(sponsor, "entry", entry_id) not in {"owner", "editor"}
    ):
        return "needs_reassignment"
    policy = await evaluate(
        subject=Subject(kind="human", id=sponsor),
        action="entry.update",
        resource=Resource(kind="entry", id=entry_id, scope=f"track:{track_id}"),
        payload={"entry_id": entry_id},
        _dry_run=True,
    )
    if not policy.allowed or get_registry().get(edge.resident_provider_id) is None:
        return "needs_reassignment"

    type_id = entry_data.get("type_id")
    if not isinstance(type_id, str) or not type_id:
        return "needs_task_setup"
    track = await Track.find_one({"id": track_id})
    entry_type = await EntryType.find_one({"id": type_id})
    profile = await get_track_attached_operational_model(track) if track else None
    if (
        entry_type is None
        or entry_type.track_id != track_id
        or profile is None
        or profile.status != "published"
    ):
        return "needs_task_setup"
    graph = get_default_context()
    if (
        len(
            await graph.find_edges_between(
                track_id, profile.id, edge_class=HAS_OPERATIONAL_MODEL
            )
        )
        != 1
        or len(await graph.find_edges_between(profile.id, type_id, edge_class=CONTAINS))
        != 1
        or len(await graph.find_edges_between(entry_id, type_id, edge_class=IS_OF_TYPE))
        != 1
    ):
        return "needs_task_setup"
    schema = entry_type.form_schema or {}
    fields = schema.get("fields")
    if not isinstance(fields, list) or any(
        not isinstance(field, dict) for field in fields
    ):
        return "needs_task_setup"
    try:
        semantics = validate_task_semantics(schema.get("task_semantics"), fields)
    except ValueError:
        return "needs_task_setup"
    outcome = entry_data.get("title")
    if isinstance(semantics.outcome, FieldOutcome):
        key = next(
            field.get("key")
            for field in fields
            if field.get("id") == semantics.outcome.field_id
        )
        if not isinstance(key, str) or not key:
            return "needs_task_setup"
        values = entry_data.get("custom_fields")
        outcome = values.get(key) if isinstance(values, dict) else None
    if not isinstance(outcome, str) or not outcome.strip():
        return "needs_task_setup"
    return "awaiting_execution_policy"


async def get_resident_assignment(
    *, user_id: str, workspace_id: str, entry_id: str
) -> ResidentAssignmentView:
    """Read the assignment under the current Entry and workspace scope."""
    from app.schemas.policy import Resource, Subject
    from app.services.policy_engine import evaluate
    from app.services.resource_location import DATA_APP_STATES, locate_resource

    workspace_role = await can_access_workspace(user_id, workspace_id)
    if workspace_role == "none":
        raise ResourceNotFoundError(message="Entry not found")
    records: dict[str, dict] = {}
    location = await locate_resource(
        "entry", entry_id, allowed_app_states=DATA_APP_STATES, records_out=records
    )
    if location is None or location.workspace_id != workspace_id:
        raise ResourceNotFoundError(message="Entry not found")
    subject = Subject(kind="human", id=user_id)
    resource = Resource(kind="entry", id=entry_id, scope=f"track:{location.track_id}")
    read = await evaluate(
        subject=subject, action="entry.read", resource=resource, _dry_run=True
    )
    if not read.allowed:
        raise ResourceNotFoundError(message="Entry not found")

    edge = await _load_assignment(entry_id, workspace_id)
    state = edge.state if edge else "withdrawn"
    revision = edge.revision if edge else 0
    can_change = False
    if (
        workspace_role in {"owner", "admin", "member"}
        and graph_transaction_available(get_default_context().database)
        and await resolve_role(user_id, "entry", entry_id) in {"owner", "editor"}
    ):
        write = await evaluate(
            subject=subject,
            action="entry.update",
            resource=resource,
            payload={"entry_id": entry_id},
            _dry_run=True,
        )
        can_change = write.allowed
    return ResidentAssignmentView(
        state=state,
        revision=revision,
        can_assign=can_change
        and state == "withdrawn"
        and await _assignment_enabled(workspace_id),
        can_withdraw=can_change and state == "assigned",
        readiness=(
            await _assignment_readiness(
                edge=edge,
                workspace_id=workspace_id,
                entry_id=entry_id,
                track_id=location.track_id,
                entry_data=(records.get(entry_id) or {}).get("context") or {},
            )
            if edge and state == "assigned"
            else None
        ),
    )


async def set_resident_assignment(
    *,
    user_id: str,
    workspace_id: str,
    entry_id: str,
    request: ResidentAssignmentRequest,
) -> ResidentAssignmentResponse:
    """Change a resident assignment and write its durable event in one transaction."""
    if not graph_transaction_available(get_default_context().database):
        raise ServiceUnavailableError(message="Resident assignment requires PostgreSQL")
    async with postgres_graph_transaction(
        get_default_context().database
    ) as transaction:
        async with entry_effect_scope(
            user_id=user_id,
            actor_kind="human",
            actor_id=None,
            action="entry.update",
            target_kind="entry",
            target_id=entry_id,
            workspace_id=workspace_id,
            payload={"entry_id": entry_id},
        ):
            if await can_access_workspace(user_id, workspace_id) not in {
                "owner",
                "admin",
                "member",
            } or await resolve_role(user_id, "entry", entry_id) not in {
                "owner",
                "editor",
            }:
                raise InsufficientPermissionsError(message="Entry edit access required")

            workspace = await Workspace.get(workspace_id)
            if workspace is None or (
                request.state == "assigned"
                and not await _assignment_enabled(workspace_id)
            ):
                raise InsufficientPermissionsError(
                    message="Resident assignment is disabled"
                )

            edge = await _load_assignment(entry_id, workspace_id)
            revision = edge.revision if edge else 0
            state = edge.state if edge else "withdrawn"
            if revision != request.expected_assignment_revision:
                error = ResourceConflictError(
                    message="Resident assignment has changed",
                    details={"current_assignment_revision": revision},
                )
                error.error_code = "resident_assignment_revision_conflict"
                raise error
            if state == request.state:
                return ResidentAssignmentResponse(state=state, revision=revision)

            now = utc_now_iso()
            if request.state == "assigned":
                from app.services.chat_providers import get_registry

                provider = get_registry().get("integral_native")
                if provider is None:
                    raise ServiceUnavailableError(
                        message="Integral resident is unavailable"
                    )
                provider_id, sponsor, assigned_at = provider.id, user_id, now
            else:
                assert edge is not None
                provider_id = edge.resident_provider_id
                sponsor = edge.sponsor_principal_id
                assigned_at = edge.assigned_at

            fields = {
                "state": request.state,
                "revision": revision + 1,
                "resident_provider_id": provider_id,
                "sponsor_principal_id": sponsor,
                "changed_by_principal_id": user_id,
                "assigned_at": assigned_at,
                "changed_at": now,
            }
            if edge is None:
                entry = await Entry.get(entry_id)
                if entry is None:
                    raise ResourceConflictError(message="Entry needs reconciliation")
                await entry.connect(workspace, edge=ResidentAssignment, **fields)
            else:
                for name, value in fields.items():
                    setattr(edge, name, value)
                await edge.save()
            await insert_revision_event(
                transaction=transaction,
                workspace_id=workspace_id,
                namespace="entry-resident-assignment-v1",
                subject_id=entry_id,
                revision=revision + 1,
                event={
                    "actor_kind": "human",
                    "actor_id": user_id,
                    "action": RESIDENT_ASSIGNMENT_CHANGED,
                    "resource_type": "Entry",
                    "resource_id": entry_id,
                    "scope": f"ws:{workspace_id}",
                    "before": {"assignment_revision": revision, "state": state},
                    "after": {
                        "assignment_revision": revision + 1,
                        "state": request.state,
                        "resident_provider_id": provider_id,
                        "sponsor_principal_id": sponsor,
                    },
                },
            )
            return ResidentAssignmentResponse(
                state=request.state, revision=revision + 1
            )
