"""Canonical, transaction-bound resident assignment mutations."""

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

from jvspatial.core.context import get_default_context

from app.agentive.edges import ResidentAssignment
from app.agentive.work_models import WorkApproval, WorkItem
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
    ChatThread,
    Entry,
    EntryType,
    Track,
    Workspace,
    WorkspaceAuthorizationState,
)
from app.schemas.agentive.work_mandate import (
    MandateAssignmentSource,
    MandateResource,
    WorkMandateRevision,
)
from app.schemas.resident_assignment import (
    AssignmentReadiness,
    ResidentAssignmentRequest,
    ResidentAssignmentResponse,
    ResidentAssignmentReviewView,
    ResidentAssignmentSettings,
    ResidentAssignmentTaskSnapshot,
    ResidentAssignmentView,
)
from app.services.app_operations.event_outbox import (
    insert_revision_event,
    revision_event_fact_id,
)
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

ASSIGNMENT_REVIEW_APPROVED = "work.resident_assignment_review_approved"
_ASSIGNMENT_EVENT_NAMESPACE = "entry-resident-assignment-v1"


def _intake_receipt_id(fact_id: str) -> str:
    return f"o.ResidentAssignmentIntake.{fact_id}"


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
    from app.schemas.policy import Resource, Subject
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

    task = await _published_assignment_task(entry_id, track_id, entry_data)
    return "awaiting_execution_policy" if task else "needs_task_setup"


async def _published_assignment_task(
    entry_id: str, track_id: str, entry_data: dict
) -> dict[str, Any] | None:
    from app.contracts.task_semantics import FieldOutcome, validate_task_semantics
    from app.services.app_graph import get_track_attached_operational_model

    type_id = entry_data.get("type_id")
    if not isinstance(type_id, str) or not type_id:
        return None
    track = await Track.find_one({"id": track_id})
    entry_type = await EntryType.find_one({"id": type_id})
    profile = await get_track_attached_operational_model(track) if track else None
    if (
        entry_type is None
        or entry_type.track_id != track_id
        or profile is None
        or profile.status != "published"
    ):
        return None
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
        return None
    schema = entry_type.form_schema or {}
    fields = schema.get("fields")
    if not isinstance(fields, list) or any(
        not isinstance(field, dict) for field in fields
    ):
        return None
    try:
        semantics = validate_task_semantics(schema.get("task_semantics"), fields)
    except ValueError:
        return None
    values = entry_data.get("custom_fields")
    values = values if isinstance(values, dict) else {}

    def mapped_value(field_id: str | None) -> Any:
        if field_id is None:
            return None
        key = next(field.get("key") for field in fields if field.get("id") == field_id)
        return values.get(key) if isinstance(key, str) and key else None

    outcome = (
        mapped_value(semantics.outcome.field_id)
        if isinstance(semantics.outcome, FieldOutcome)
        else entry_data.get("title")
    )
    if not isinstance(outcome, str) or not outcome.strip():
        return None
    return {
        "entry_type_id": type_id,
        "definition_id": profile.id,
        "definition_revision": profile.version_number,
        "definition_digest": hashlib.sha256(
            json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "outcome": outcome,
        "acceptance_criteria": mapped_value(semantics.acceptance_criteria_field_id),
        "due_date": mapped_value(semantics.due_date_field_id),
        "dependencies": mapped_value(semantics.dependencies_field_id),
    }


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
    readiness = (
        await _assignment_readiness(
            edge=edge,
            workspace_id=workspace_id,
            entry_id=entry_id,
            track_id=location.track_id,
            entry_data=(records.get(entry_id) or {}).get("context") or {},
        )
        if edge and state == "assigned"
        else None
    )
    return ResidentAssignmentView(
        state=state,
        revision=revision,
        can_assign=can_change
        and state == "withdrawn"
        and await _assignment_enabled(workspace_id),
        can_withdraw=can_change and state == "assigned",
        readiness=readiness,
        can_request_review=bool(
            can_change
            and edge
            and edge.sponsor_principal_id == user_id
            and readiness == "awaiting_execution_policy"
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
                namespace=_ASSIGNMENT_EVENT_NAMESPACE,
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


async def record_resident_assignment_intake(
    *,
    transaction: Any,
    fact_id: str,
    workspace_id: str,
    event: dict[str, Any],
) -> None:
    """Record a committed assignment under the current Entry and authority locks."""
    entry_id = event.get("resource_id")
    sponsor = event.get("actor_id")
    after = event.get("after")
    if (
        not fact_id
        or not workspace_id
        or event.get("scope") != f"ws:{workspace_id}"
        or event.get("action") != RESIDENT_ASSIGNMENT_CHANGED
        or event.get("resource_type") != "Entry"
        or event.get("actor_kind") != "human"
        or not isinstance(entry_id, str)
        or not entry_id.startswith("n.Entry.")
        or not isinstance(sponsor, str)
        or not sponsor.startswith(("o.User.", "n.User."))
        or not isinstance(after, dict)
        or after.get("state") != "assigned"
        or after.get("sponsor_principal_id") != sponsor
    ):
        return
    revision = after.get("assignment_revision")
    if type(revision) is not int or revision < 1:
        return

    receipt_id = _intake_receipt_id(fact_id)
    if await transaction.get("object", receipt_id) is not None:
        return
    if await transaction.get("node", entry_id) is None:
        return
    try:
        async with entry_effect_scope(
            user_id=sponsor,
            actor_kind="human",
            actor_id=None,
            action="entry.update",
            target_kind="entry",
            target_id=entry_id,
            workspace_id=workspace_id,
            payload={"entry_id": entry_id},
        ):
            edge = await _load_assignment(entry_id, workspace_id)
            if (
                edge is None
                or edge.state != "assigned"
                or edge.revision != revision
                or edge.sponsor_principal_id != sponsor
                or edge.resident_provider_id != after.get("resident_provider_id")
                or await can_access_workspace(sponsor, workspace_id)
                not in {"owner", "admin", "member"}
                or await resolve_role(sponsor, "entry", entry_id)
                not in {"owner", "editor"}
            ):
                return
            await transaction.insert_if_absent(
                "object",
                {
                    "id": receipt_id,
                    "entity": "ResidentAssignmentIntake",
                    "context": {
                        "fact_id": fact_id,
                        "workspace_id": workspace_id,
                        "entry_id": entry_id,
                        "assignment_revision": revision,
                        "sponsor_principal_id": sponsor,
                        "resident_provider_id": edge.resident_provider_id,
                        "received_at": utc_now_iso(),
                    },
                },
            )
    except (InsufficientPermissionsError, ResourceNotFoundError):
        # A withdrawn or inaccessible assignment can still be emitted as audit.
        return


@asynccontextmanager
async def resident_assignment_review_scope(
    *,
    workspace_id: str,
    entry_id: str,
    sponsor: str,
    assignment_revision: int,
) -> AsyncIterator[ResidentAssignmentTaskSnapshot]:
    """Hold current sponsor and Entry admission while binding a private review."""
    from app.services.chat_providers import get_registry
    from app.services.resource_location import DATA_APP_STATES, locate_resource

    database = get_default_context().database
    async with postgres_graph_transaction(database) as transaction:
        entry_row: dict[str, Any] = {}
        async with entry_effect_scope(
            user_id=sponsor,
            actor_kind="human",
            actor_id=None,
            action="entry.update",
            target_kind="entry",
            target_id=entry_id,
            workspace_id=workspace_id,
            payload={"entry_id": entry_id},
            locked_target_out=entry_row,
        ):
            if (
                not await _assignment_enabled(workspace_id)
                or await can_access_workspace(sponsor, workspace_id)
                not in {"owner", "admin", "member"}
                or await resolve_role(sponsor, "entry", entry_id)
                not in {"owner", "editor"}
            ):
                raise InsufficientPermissionsError(
                    message="Current resident assignment sponsorship required"
                )
            edge = await _load_assignment(entry_id, workspace_id)
            if (
                edge is None
                or edge.state != "assigned"
                or edge.revision != assignment_revision
                or edge.sponsor_principal_id != sponsor
                or get_registry().get(edge.resident_provider_id) is None
            ):
                raise ResourceConflictError(message="Resident assignment has changed")
            fact_id = revision_event_fact_id(
                workspace_id=workspace_id,
                namespace=_ASSIGNMENT_EVENT_NAMESPACE,
                subject_id=entry_id,
                revision=assignment_revision,
                action=RESIDENT_ASSIGNMENT_CHANGED,
            )
            receipt = await transaction.get("object", _intake_receipt_id(fact_id))
            receipt_context = (receipt or {}).get("context") or {}
            if (
                (receipt or {}).get("entity") != "ResidentAssignmentIntake"
                or receipt_context.get("fact_id") != fact_id
                or receipt_context.get("workspace_id") != workspace_id
                or receipt_context.get("entry_id") != entry_id
                or receipt_context.get("assignment_revision") != assignment_revision
                or receipt_context.get("sponsor_principal_id") != sponsor
                or receipt_context.get("resident_provider_id")
                != edge.resident_provider_id
            ):
                raise ResourceConflictError(
                    message="Resident assignment intake is pending"
                )
            entry_data = entry_row.get("context") or {}
            location = await locate_resource(
                "entry",
                entry_id,
                allowed_app_states=DATA_APP_STATES,
                selected_record=entry_row,
            )
            if location is None or location.workspace_id != workspace_id:
                raise ResourceConflictError(message="Resident assignment has moved")
            track_id = location.track_id
            entry_revision = entry_data.get("record_revision", 1)
            task = (
                await _published_assignment_task(entry_id, track_id, entry_data)
                if track_id
                else None
            )
            if (
                not track_id
                or task is None
                or type(entry_revision) is not int
                or entry_revision < 1
            ):
                raise ResourceConflictError(
                    message="Resident assignment needs task setup"
                )
            yield ResidentAssignmentTaskSnapshot(
                workspace_id=workspace_id,
                track_id=track_id,
                entry_id=entry_id,
                assignment_revision=assignment_revision,
                sponsor_principal_id=sponsor,
                resident_provider_id=edge.resident_provider_id,
                intake_fact_id=fact_id,
                entry_revision=entry_revision,
                **task,
            )


async def propose_resident_assignment_review(
    *,
    workspace_id: str,
    entry_id: str,
    sponsor: str,
    assignment_revision: int,
    revision: WorkMandateRevision,
    expected_task_fingerprint: str,
) -> tuple[WorkItem, WorkApproval]:
    """Persist a narrow preparation review with its exact sponsor and task binding."""
    from app.agentive.services.work_mandates import propose_work_mandate_review

    source = MandateResource(kind="entry", resource_id=entry_id)
    if (
        revision.assignment_source is not None
        or revision.principal_id != sponsor
        or revision.workspace_id != workspace_id
        or revision.inputs != (source,)
        or len(revision.model_routes) != 1
        or revision.limits.max_internal_writes
        or revision.limits.max_external_effects
        or revision.external_grants
        or any(
            grant.operation != "read" or grant.resources != (source,)
            for grant in revision.grants
        )
    ):
        raise InsufficientPermissionsError(
            message="Resident assignment preparation review must use exact Entry read scope"
        )

    async with postgres_graph_transaction(
        get_default_context().database
    ) as transaction:
        async with resident_assignment_review_scope(
            workspace_id=workspace_id,
            entry_id=entry_id,
            sponsor=sponsor,
            assignment_revision=assignment_revision,
        ) as task:
            await _lock_preparation_thread(
                transaction, revision, task.resident_provider_id
            )
            fingerprint = task.fingerprint()
            if (
                revision.goal != task.outcome
                or fingerprint != expected_task_fingerprint
            ):
                raise ResourceConflictError(
                    message="Resident assignment task has changed"
                )

            bound = revision.model_copy(
                update={
                    "mandate_id": f"resident-assignment:{task.intake_fact_id}",
                    "assignment_source": MandateAssignmentSource(
                        entry_id=entry_id,
                        assignment_revision=assignment_revision,
                        intake_fact_id=task.intake_fact_id,
                        task_fingerprint=fingerprint,
                        resident_provider_id=task.resident_provider_id,
                    ),
                }
            )
            return await propose_work_mandate_review(
                revision=bound,
                principal_id=sponsor,
                workspace_id=workspace_id,
                thread_id=revision.thread_id,
                transaction=transaction,
            )


async def _lock_preparation_thread(
    transaction: Any,
    revision: WorkMandateRevision,
    resident_provider_id: str,
    *,
    shared: bool = True,
) -> None:
    """Fence the sponsor's private destination during a review operation."""
    row = await transaction.lock_record("node", revision.thread_id, shared=shared)
    data = (row or {}).get("context") or {}
    if (
        (row or {}).get("entity") != ChatThread._entity_name()
        or data.get("archived") is not False
        or data.get("user_id") != revision.principal_id
        or data.get("workspace_id") != revision.workspace_id
        or data.get("provider_id") != resident_provider_id
    ):
        raise ResourceNotFoundError(message="Resident preparation thread not found")


async def approve_resident_assignment_review(
    *,
    workspace_id: str,
    reviewer_id: str,
    work_item_id: str,
    expected_review_digest: str,
) -> WorkApproval:
    """Record the human decision atomically while keeping the work parked."""
    from app.agentive.services.work_approvals import (
        _hydrate_approval,
        work_approval_object_id,
    )
    from app.agentive.services.work_outbox import build_outbox_document, outbox_id_for
    from app.schemas.policy import Resource, Subject
    from app.services.policy_engine import evaluate

    object_id = f"o.WorkItem.{work_item_id}"
    async with postgres_graph_transaction(
        get_default_context().database
    ) as transaction:
        initial = await transaction.get("object", object_id)
        plan = ((initial or {}).get("context") or {}).get("plan") or {}
        raw = plan.get("mandate_revision")
        if (initial or {}).get("entity") != "WorkItem" or not isinstance(raw, dict):
            raise ResourceNotFoundError(message="Resident assignment review not found")
        revision = WorkMandateRevision.model_validate(raw)
        source = revision.assignment_source
        if source is None or revision.workspace_id != workspace_id:
            raise ResourceNotFoundError(message="Resident assignment review not found")
        digest = revision.review_digest()
        if digest != expected_review_digest:
            raise ResourceConflictError(
                message="Resident assignment review has changed"
            )

        sponsor = revision.principal_id
        async with identity_admission((sponsor, reviewer_id), shared=True):
            async with resident_assignment_review_scope(
                workspace_id=workspace_id,
                entry_id=source.entry_id,
                sponsor=sponsor,
                assignment_revision=source.assignment_revision,
            ) as task:
                if await can_access_workspace(reviewer_id, workspace_id) not in {
                    "owner",
                    "admin",
                }:
                    raise InsufficientPermissionsError(
                        message="Workspace review authority required"
                    )
                read = await evaluate(
                    subject=Subject(kind="human", id=reviewer_id),
                    action="entry.read",
                    resource=Resource(
                        kind="entry", id=source.entry_id, scope=f"track:{task.track_id}"
                    ),
                    _dry_run=True,
                )
                if not read.allowed:
                    raise InsufficientPermissionsError(
                        message="Current Entry read access required"
                    )
                if (
                    source.task_fingerprint != task.fingerprint()
                    or source.intake_fact_id != task.intake_fact_id
                    or source.resident_provider_id != task.resident_provider_id
                ):
                    raise ResourceConflictError(
                        message="Resident assignment task has changed"
                    )
                await _lock_preparation_thread(
                    transaction, revision, task.resident_provider_id
                )

                approval_id = plan.get("mandate_approval_id")
                if not isinstance(approval_id, str) or not approval_id:
                    raise ResourceConflictError(
                        message="Resident assignment review needs reconciliation"
                    )
                approval_oid = work_approval_object_id(approval_id)
                # Use the ordinary decision path's lock order.
                row = await transaction.lock_record("object", approval_oid)
                item = await transaction.lock_record("object", object_id)
                item_data = (item or {}).get("context") or {}
                approval_data = (row or {}).get("context") or {}
                now = datetime.now(timezone.utc)
                if (
                    (row or {}).get("entity") != "WorkApproval"
                    or (item or {}).get("entity") != "WorkItem"
                    or item_data.get("status") != "waiting_for_human"
                    or item_data.get("cancel_requested_at")
                    or item_data.get("origin") != "mandate_review"
                    or item_data.get("principal_id") != sponsor
                    or item_data.get("workspace_id") != workspace_id
                    or item_data.get("thread_id") != revision.thread_id
                    or item_data.get("plan") != plan
                    or item_data.get("plan_revision") != digest
                    or item_data.get("input_fingerprint") != digest
                    or approval_data.get("work_approval_id") != approval_id
                    or approval_data.get("work_item_id") != work_item_id
                    or approval_data.get("authority_digest") != digest
                    or approval_data.get("expires_at")
                    != revision.canonical_review_payload()["limits"]["deadline_at"]
                    or revision.limits.deadline_at <= now
                ):
                    raise ResourceConflictError(
                        message="Resident assignment review is no longer current"
                    )
                if (
                    approval_data.get("status") == "approved"
                    and approval_data.get("decision") == "approved"
                    and approval_data.get("decider_id") == reviewer_id
                ):
                    return _hydrate_approval(row)
                timestamp = now.isoformat()
                decided = await transaction.find_one_and_update(
                    "object",
                    {"id": approval_oid, "context.status": "pending"},
                    {
                        "$set": {
                            "context.status": "approved",
                            "context.decision": "approved",
                            "context.decider_id": reviewer_id,
                            "context.decided_at": timestamp,
                            "context.updated_at": timestamp,
                        }
                    },
                )
                if decided is None:
                    raise ResourceConflictError(
                        message="Resident assignment review is already decided"
                    )
                topic = ASSIGNMENT_REVIEW_APPROVED
                await transaction.insert_if_absent(
                    "object",
                    build_outbox_document(
                        outbox_id=outbox_id_for(
                            work_item_id=work_item_id, topic=topic, seq=0
                        ),
                        work_item_id=work_item_id,
                        topic=topic,
                        payload={
                            "review_digest": digest,
                            "work_approval_id": approval_id,
                        },
                        created_at=timestamp,
                        deadline_at=approval_data["expires_at"],
                    ),
                )
                return _hydrate_approval(decided)


async def assert_assignment_model_authority(
    revision: WorkMandateRevision,
    *,
    transaction: Any,
    shared_destination: bool = True,
) -> ResidentAssignmentTaskSnapshot | None:
    """Fence live assignment authority through the enclosing model ledger transaction."""
    from app.schemas.agentive.work import WorkError
    from app.services.app_operations.transaction_scope import (
        current_graph_transaction_database,
    )

    source = revision.assignment_source
    if source is None:
        return None
    if transaction is not current_graph_transaction_database():
        raise WorkError("work.transaction_required")
    try:
        async with resident_assignment_review_scope(
            workspace_id=revision.workspace_id,
            entry_id=source.entry_id,
            sponsor=revision.principal_id,
            assignment_revision=source.assignment_revision,
        ) as task:
            if (
                source.task_fingerprint != task.fingerprint()
                or source.intake_fact_id != task.intake_fact_id
                or source.resident_provider_id != task.resident_provider_id
            ):
                raise WorkError("work.assignment_stale")
            await _lock_preparation_thread(
                transaction,
                revision,
                task.resident_provider_id,
                shared=shared_destination,
            )
            return task
    except (
        InsufficientPermissionsError,
        ResourceConflictError,
        ResourceNotFoundError,
    ) as exc:
        raise WorkError("work.assignment_stale") from exc


async def get_resident_assignment_review(
    *, user_id: str, workspace_id: str, entry_id: str
) -> ResidentAssignmentReviewView | None:
    """Project review metadata without private chat or credential references."""
    from app.agentive.services.work_mandates import _load_lineage_approval
    from app.agentive.services.work_outbox import _hydrate_work_item
    from app.services.host_hooks import (
        get_model_bounds_resolver,
        get_model_price_resolver,
    )

    assignment = await get_resident_assignment(
        user_id=user_id, workspace_id=workspace_id, entry_id=entry_id
    )
    if assignment.state != "assigned":
        return None
    edge = await _load_assignment(entry_id, workspace_id)
    if edge is None or edge.state != "assigned" or edge.revision != assignment.revision:
        return None
    reviewer = await can_access_workspace(user_id, workspace_id) in {"owner", "admin"}
    if not reviewer and edge.sponsor_principal_id != user_id:
        return None
    fact_id = revision_event_fact_id(
        workspace_id=workspace_id,
        namespace=_ASSIGNMENT_EVENT_NAMESPACE,
        subject_id=entry_id,
        revision=edge.revision,
        action=RESIDENT_ASSIGNMENT_CHANGED,
    )
    rows = await get_default_context().database.find(
        "object",
        {
            "entity": "WorkItem",
            "context.kind": "resident_assignment",
            "context.origin": "mandate_review",
            "context.workspace_id": workspace_id,
            "context.principal_id": edge.sponsor_principal_id,
            "context.plan.mandate_revision.assignment_source.intake_fact_id": fact_id,
        },
        sort=[("context.created_at", -1), ("id", -1)],
        limit=1,
    )
    if not rows:
        return None
    item = _hydrate_work_item(rows[0])
    revision = WorkMandateRevision.model_validate(item.plan.get("mandate_revision"))
    source = revision.assignment_source
    approval_id = item.plan.get("mandate_approval_id")
    if (
        source is None
        or source.entry_id != entry_id
        or source.assignment_revision != edge.revision
        or source.intake_fact_id != fact_id
        or revision.workspace_id != workspace_id
        or revision.principal_id != edge.sponsor_principal_id
        or revision.thread_id != item.thread_id
        or len(revision.model_routes) != 1
        or not isinstance(approval_id, str)
    ):
        return None
    digest = revision.review_digest()
    approval = await _load_lineage_approval(approval_id)
    if (
        approval is None
        or approval.work_item_id != item.work_item_id
        or approval.authority_digest != digest
        or item.plan_revision != digest
        or item.input_fingerprint != digest
    ):
        return None
    route = revision.model_routes[0]
    return ResidentAssignmentReviewView(
        work_item_id=item.work_item_id,
        review_digest=digest,
        status=item.status,
        approval_status=approval.status,
        can_approve=(
            reviewer
            and approval.status == "pending"
            and item.status == "waiting_for_human"
            and not item.cancel_requested_at
            and assignment.readiness == "awaiting_execution_policy"
            and revision.limits.deadline_at > datetime.now(timezone.utc)
        ),
        model=route.model,
        provider=route.provider,
        max_model_requests=revision.limits.max_model_requests,
        max_spend_usd=str(revision.limits.max_spend),
        deadline_at=approval.expires_at,
        needs_model_evidence=(
            approval.status == "approved"
            and item.status == "waiting_for_human"
            and (
                get_model_bounds_resolver() is None
                or get_model_price_resolver() is None
            )
        ),
        preparation_thread_id=(
            item.thread_id
            if user_id == revision.principal_id and item.status == "succeeded"
            else None
        ),
    )


async def request_resident_assignment_review(
    *, user_id: str, workspace_id: str, entry_id: str, assignment_revision: int
) -> None:
    """Create one bounded, tool-free review and a private sponsor conversation."""
    from app.agentive.services.work_items import work_item_object_id
    from app.agentive.services.work_mandates import propose_work_mandate_review
    from app.schemas.agentive.work_mandate import MandateLimits
    from app.services.chat_threads import create_thread
    from app.services.resident_assignment_execution import (
        resolve_assignment_review_route,
    )

    async with postgres_graph_transaction(
        get_default_context().database
    ) as transaction:
        async with resident_assignment_review_scope(
            workspace_id=workspace_id,
            entry_id=entry_id,
            sponsor=user_id,
            assignment_revision=assignment_revision,
        ) as task:
            mandate_id = f"resident-assignment:{task.intake_fact_id}"
            object_id = work_item_object_id(
                kind="resident_assignment",
                origin="mandate_review",
                principal_id=user_id,
                workspace_id=workspace_id,
                idempotency_key=f"mandate:{mandate_id}:1",
            )
            stored = await transaction.get("object", object_id)
            fingerprint = task.fingerprint()
            if stored is not None:
                source = (stored["context"]["plan"]["mandate_revision"]).get(
                    "assignment_source"
                ) or {}
                if source.get("task_fingerprint") != fingerprint:
                    raise ResourceConflictError(
                        message="The task changed. Withdraw and reassign it to request a new review."
                    )
                return
            if len(task.outcome) > 4000:
                raise ResourceConflictError(
                    message="Shorten the assigned outcome to 4,000 characters"
                )
            route = await resolve_assignment_review_route(workspace_id)
            thread = await create_thread(
                user_id=user_id,
                workspace_id=workspace_id,
                provider_id=task.resident_provider_id,
                title="Assignment preparation",
            )
            revision = WorkMandateRevision(
                mandate_id=mandate_id,
                revision=1,
                principal_id=user_id,
                workspace_id=workspace_id,
                thread_id=thread.id,
                goal=task.outcome,
                inputs=(MandateResource(kind="entry", resource_id=entry_id),),
                grants=(),
                model_routes=(route,),
                limits=MandateLimits.model_validate(
                    {
                        "deadline_at": datetime.now(timezone.utc) + timedelta(hours=1),
                        "max_model_requests": 1,
                        "max_tool_calls": 0,
                        "max_internal_writes": 0,
                        "max_external_effects": 0,
                        "max_spend": "0.10",
                    }
                ),
                success_criteria=(
                    "Prepare a draft that addresses the assigned outcome.",
                ),
                stop_conditions=("Identify missing information rather than guessing.",),
                result_obligations=(
                    "Deliver the draft privately to the assignment sponsor.",
                ),
                assignment_source=MandateAssignmentSource(
                    entry_id=entry_id,
                    assignment_revision=assignment_revision,
                    intake_fact_id=task.intake_fact_id,
                    task_fingerprint=fingerprint,
                    resident_provider_id=task.resident_provider_id,
                ),
            )
            await propose_work_mandate_review(
                revision=revision,
                principal_id=user_id,
                workspace_id=workspace_id,
                thread_id=thread.id,
                transaction=transaction,
            )
