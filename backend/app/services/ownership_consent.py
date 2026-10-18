"""Recipient consent and atomic ownership transitions on the workspace fence."""

from datetime import datetime, timedelta, timezone

from jvspatial.core.context import get_default_context

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceConflictError,
)
from app.exceptions import AuthorizationUnavailableError
from app.models.access_lifecycle import HAS_OWNERSHIP_PROPOSAL, OwnershipProposal
from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF, OWNS
from app.models.nodes import App, Track, User, Workspace
from app.services.app_operations.transaction_scope import graph_transaction_available
from app.services.authority_facts import primary_node_record
from app.services.email_identity import verified_current_email
from app.services.grant_history import grant_baseline, record_grant_change
from app.services.invitation_grants import _current_grant
from app.services.membership_write_scope import membership_write_scope
from app.services.permissions import get_user_node
from app.services.permissions_process_cache import invalidate_user_aliases
from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource
from app.services.workspace_governance import owner_governance_allowed
from app.services.workspace_kind import is_collaborative_kind
from app.services.workspace_membership import _deliver, _record_change
from app.services.workspace_permissions import can_access_workspace, read_member_facts
from app.services.workspace_suspension import require_unsuspended
from app.utils.time import utc_now_iso

KINDS = {"workspace": Workspace, "app": App, "track": Track}


async def _proposal(proposal_id: str):
    """Require complete raw proposal facts before hydration/terminal admission."""
    row = await primary_node_record(
        get_default_context().database, OwnershipProposal, proposal_id
    )
    if row is None:
        return None
    data = row["context"]
    if (
        data.get("resource_type") not in KINDS
        or data.get("status") not in {"pending", "accepted", "declined", "revoked"}
        or type(data.get("revision")) is not int
        or data["revision"] < 0
    ):
        raise AuthorizationUnavailableError()
    for key in (
        "workspace_id",
        "resource_id",
        "issuer_id",
        "previous_owner_id",
        "recipient_id",
        "recipient_membership_id",
        "owner_edge_id",
        "expires_at",
        "created_at",
    ):
        if not isinstance(data.get(key), str) or not data[key]:
            raise AuthorizationUnavailableError()
        if key in row and row[key] != data[key]:
            raise AuthorizationUnavailableError()
    for key in ("expires_at", "created_at"):
        try:
            if datetime.fromisoformat(data[key]).tzinfo is None:
                raise ValueError("Missing timezone")
        except ValueError as exc:
            raise AuthorizationUnavailableError() from exc
    return await OwnershipProposal.find_one({"id": proposal_id})


async def _target(kind: str, resource_id: str):
    """Resolve canonical target and one owner; raw candidates precede hydration."""
    if kind not in KINDS:
        raise BadRequestError(message="Invalid ownership target")
    location = await locate_resource(
        kind, resource_id, allowed_app_states=MANAGEMENT_APP_STATES
    )
    if location is None:
        raise AuthorizationUnavailableError()
    resource = await KINDS[kind].find_one({"id": resource_id})
    if resource is None:
        raise AuthorizationUnavailableError()
    family = IS_MEMBER_OF if kind == "workspace" else OWNS
    query = {"entity": family.__name__, "target": resource_id}
    if kind == "workspace":
        query["context.role"] = "owner"
    rows = await get_default_context().database.find("edge", query, limit=2)
    if len(rows) != 1 or not rows[0].get("source"):
        raise AuthorizationUnavailableError()
    return resource, location.workspace_id, rows[0]


async def request_ownership_transfer(
    *,
    actor_id: str,
    resource_type: str,
    resource_id: str,
    recipient_id: str,
) -> dict:
    """Issue a 24-hour proposal; no owner or ordinary grant changes occur."""
    from app.runtime.composition import require_capability

    require_capability("collaboration")
    _, workspace_id, preflight_owner = await _target(resource_type, resource_id)
    async with membership_write_scope(
        workspace_id,
        principal_ids=(actor_id, recipient_id, preflight_owner["source"]),
        active_principal_ids=(actor_id, recipient_id),
    ):
        resource, current_workspace, ownership = await _target(
            resource_type, resource_id
        )
        if current_workspace != workspace_id:
            raise AuthorizationUnavailableError()
        actor = await get_user_node(actor_id)
        recipient = await get_user_node(recipient_id)
        if actor is None or recipient is None:
            raise InsufficientPermissionsError(message="Access denied")
        actor_workspace_role = await can_access_workspace(actor.id, workspace_id)
        governed = actor_workspace_role == "owner" and owner_governance_allowed()
        if not governed and actor.id != ownership["source"]:
            raise InsufficientPermissionsError(
                message="Only the owner can transfer stewardship"
            )
        if recipient.id == ownership["source"]:
            raise BadRequestError(message="Cannot transfer ownership to current owner")
        if resource_type == "workspace" and not is_collaborative_kind(resource.kind):
            raise BadRequestError(
                message="Personal workspace ownership cannot transfer"
            )
        await require_unsuspended(recipient.id, workspace_id)
        membership = await read_member_facts(recipient, workspace_id)
        if membership is None or not await verified_current_email(recipient):
            raise BadRequestError(
                message="Recipient must be a verified current workspace member"
            )
        proposal = await OwnershipProposal.create(
            workspace_id=workspace_id,
            resource_type=resource_type,
            resource_id=resource_id,
            issuer_id=actor.id,
            previous_owner_id=ownership["source"],
            recipient_id=recipient.id,
            recipient_membership_id=membership["id"],
            owner_edge_id=ownership["id"],
            created_at=utc_now_iso(),
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
        )
        await resource.connect(proposal, edge=HAS_OWNERSHIP_PROPOSAL)
        return {
            "proposal_id": proposal.id,
            "status": "pending",
            "revision": 0,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "recipient_id": recipient.id,
            "expires_at": proposal.expires_at,
        }


async def respond_ownership_transfer(
    *,
    actor_id: str,
    proposal_id: str,
    revision: int,
    action: str,
) -> dict:
    """Commit exact consent, ownership/history and audit together on PostgreSQL."""
    from app.runtime.composition import require_capability

    require_capability("collaboration")
    if type(revision) is not int or revision < 0 or action not in {"accept", "decline"}:
        raise BadRequestError(message="Invalid ownership response")
    proposal = await _proposal(proposal_id)
    if proposal is None:
        raise BadRequestError(message="Unknown ownership proposal")
    if not graph_transaction_available(get_default_context().database):
        raise AuthorizationUnavailableError()
    outbox = None
    async with membership_write_scope(
        proposal.workspace_id,
        principal_ids=(
            actor_id,
            proposal.issuer_id,
            proposal.previous_owner_id,
            proposal.recipient_id,
        ),
        active_principal_ids=(actor_id, proposal.issuer_id),
    ):
        proposal = await _proposal(proposal_id)
        actor = await get_user_node(actor_id)
        if proposal is None or actor is None or actor.id != proposal.recipient_id:
            raise InsufficientPermissionsError(message="Access denied")
        resource, workspace_id, owner_row = await _target(
            proposal.resource_type, proposal.resource_id
        )
        if workspace_id != proposal.workspace_id:
            raise AuthorizationUnavailableError()
        context = get_default_context()
        rooted = await context.database.find(
            "edge",
            {"target": proposal.id, "entity": HAS_OWNERSHIP_PROPOSAL.__name__},
            limit=2,
        )
        if len(rooted) != 1 or rooted[0].get("source") != resource.id:
            raise AuthorizationUnavailableError()
        if proposal.status != "pending":
            terminal = "accepted" if action == "accept" else "declined"
            if proposal.status != terminal or revision != proposal.revision - 1:
                raise ResourceConflictError(
                    message="Ownership proposal already completed"
                )
            return {
                "proposal_id": proposal.id,
                "status": terminal,
                "revision": proposal.revision,
                "changed": False,
                "current_owner_id": owner_row["source"],
            }
        if proposal.revision != revision:
            raise ResourceConflictError(message="Stale ownership proposal")
        if datetime.fromisoformat(proposal.expires_at) <= datetime.now(timezone.utc):
            raise ResourceConflictError(message="Ownership proposal expired")
        membership = await read_member_facts(actor, workspace_id)
        await require_unsuspended(actor.id, workspace_id)
        if (
            membership is None
            or membership["id"] != proposal.recipient_membership_id
            or not await verified_current_email(actor)
        ):
            raise InsufficientPermissionsError(
                message="Recipient identity or membership changed"
            )
        issuer = await get_user_node(proposal.issuer_id)
        issuer_role = (
            await can_access_workspace(issuer.id, workspace_id) if issuer else "none"
        )
        if (
            issuer is None
            or issuer_role == "none"
            or (issuer.id != owner_row["source"] and issuer_role != "owner")
        ):
            raise ResourceConflictError(message="Transfer issuer no longer authorized")
        if (
            owner_row["id"] != proposal.owner_edge_id
            or owner_row["source"] != proposal.previous_owner_id
        ):
            raise ResourceConflictError(message="Resource ownership changed")
        if action == "accept":
            await _apply_transfer(resource, proposal, actor, membership, owner_row)
        proposal.status = "accepted" if action == "accept" else "declined"
        proposal.revision += 1
        proposal.completed_at = utc_now_iso()
        await proposal.save()
        workspace = await Workspace.find_one({"id": workspace_id})
        if workspace is None:
            raise AuthorizationUnavailableError()
        outbox = await _record_change(
            actor,
            workspace,
            "workspace.member_update",
            None,
            {
                "membership_id": proposal.id,
                "grant_revision": proposal.revision,
                "operation": "ownership." + proposal.status,
                "resource_type": proposal.resource_type,
                "resource_id": resource.id,
                "previous_owner_id": proposal.previous_owner_id,
                "new_owner_id": (
                    actor.id if action == "accept" else proposal.previous_owner_id
                ),
            },
        )
        result = {
            "proposal_id": proposal.id,
            "status": proposal.status,
            "revision": proposal.revision,
            "changed": True,
            "current_owner_id": actor.id if action == "accept" else owner_row["source"],
        }
    invalidate_user_aliases(actor)
    await _deliver(outbox)
    return result


async def _apply_transfer(resource, proposal, recipient, membership, owner_row) -> None:
    """Apply a validated transition inside the caller's transaction/fence."""
    from app.runtime.composition import require_capability

    require_capability("collaboration")
    previous = await User.find_one({"id": owner_row["source"]})
    if previous is None:
        raise AuthorizationUnavailableError()
    context = get_default_context()
    if proposal.resource_type == "workspace":
        for user, before, role in (
            (previous, owner_row, "admin"),
            (recipient, membership, "owner"),
        ):
            await grant_baseline(
                source_id=user.id,
                target_id=resource.id,
                grant_class=IS_MEMBER_OF,
                grant=before,
            )
            updated = await context.database.find_one_and_update(
                "edge",
                {"id": before["id"], "entity": IS_MEMBER_OF.__name__},
                {
                    "$set": {
                        "context.role": role,
                        **({"role": role} if "role" in before else {}),
                    },
                    "$inc": {"context.grant_revision": 1},
                },
            )
            if updated is None:
                raise AuthorizationUnavailableError()
            after = await read_member_facts(user, resource.id)
            await record_grant_change(
                source=user,
                target=resource,
                grant_class=IS_MEMBER_OF,
                before=before,
                after=after,
            )
    else:
        before_previous = await _current_grant(previous.id, resource.id, OWNS)
        before_recipient = await _current_grant(recipient.id, resource.id, OWNS)
        if before_previous is None or before_recipient is not None:
            raise AuthorizationUnavailableError()
        for user, before in (
            (previous, before_previous),
            (recipient, before_recipient),
        ):
            await grant_baseline(
                source_id=user.id, target_id=resource.id, grant_class=OWNS, grant=before
            )
        edges = await context.find_edges_between(
            previous.id, resource.id, edge_class=OWNS
        )
        if len(edges) != 1 or edges[0].id != owner_row["id"]:
            raise AuthorizationUnavailableError()
        await edges[0].delete()
        await recipient.connect(
            resource, edge=OWNS, role="owner", granted_at=utc_now_iso()
        )
        if proposal.resource_type == "app":
            resource.owner_user_id = recipient.id
        else:
            resource.owner_id = recipient.id
        await resource.save()
        for user, before in (
            (previous, before_previous),
            (recipient, before_recipient),
        ):
            after = await _current_grant(user.id, resource.id, OWNS)
            await record_grant_change(
                source=user,
                target=resource,
                grant_class=OWNS,
                before=before,
                after=after,
            )
        # The previous steward retains an explicit editor grant. Independent
        # recipient collaborator grants remain independently removable.
        before = await _current_grant(previous.id, resource.id, COLLABORATES_ON)
        await grant_baseline(
            source_id=previous.id,
            target_id=resource.id,
            grant_class=COLLABORATES_ON,
            grant=before,
        )
        if before is None:
            await previous.connect(
                resource,
                edge=COLLABORATES_ON,
                role="editor",
                invited_at=utc_now_iso(),
                invited_by=recipient.id,
            )
        else:
            values = {"context.role": "editor"}
            if "role" in before:
                values["role"] = "editor"
            await context.database.find_one_and_update(
                "edge",
                {"id": before["id"]},
                {"$set": values, "$inc": {"context.grant_revision": 1}},
            )
        after = await _current_grant(previous.id, resource.id, COLLABORATES_ON)
        await record_grant_change(
            source=previous,
            target=resource,
            grant_class=COLLABORATES_ON,
            before=before,
            after=after,
        )
    invalidate_user_aliases(previous)
    invalidate_user_aliases(recipient)


async def list_ownership_proposals(
    *, actor_id: str, workspace_id: str | None = None
) -> dict:
    """Bounded current rooted inbox; owners inspect their workspace, recipients their own."""
    actor = await get_user_node(actor_id)
    if actor is None:
        raise InsufficientPermissionsError(message="Access denied")
    query = {"entity": OwnershipProposal._entity_name()}
    if workspace_id is None:
        query["context.recipient_id"] = actor.id
    else:
        if (
            await can_access_workspace(actor.id, workspace_id) != "owner"
            or not owner_governance_allowed()
        ):
            raise InsufficientPermissionsError(
                message="Only the workspace owner may list proposals"
            )
        query["context.workspace_id"] = workspace_id
    rows = await get_default_context().database.find(
        "node", query, limit=257, sort=[("id", 1)]
    )
    if len(rows) > 256:
        raise AuthorizationUnavailableError()
    result = []
    for row in rows:
        proposal = await _proposal(row["id"])
        if proposal is None:
            raise AuthorizationUnavailableError()
        if workspace_id is None and proposal.recipient_id != actor.id:
            raise AuthorizationUnavailableError()
        if workspace_id is not None and proposal.workspace_id != workspace_id:
            raise AuthorizationUnavailableError()
        async with membership_write_scope(
            proposal.workspace_id, principal_ids=(actor.id,)
        ):
            proposal = await _proposal(row["id"])
            if (
                proposal is None
                or (workspace_id is None and proposal.recipient_id != actor.id)
                or (workspace_id is not None and proposal.workspace_id != workspace_id)
            ):
                raise AuthorizationUnavailableError()
            if workspace_id is not None and (
                await can_access_workspace(actor.id, workspace_id) != "owner"
                or not owner_governance_allowed()
            ):
                raise InsufficientPermissionsError(
                    message="Only the workspace owner may list proposals"
                )
            resource, actual_workspace, owner = await _target(
                proposal.resource_type, proposal.resource_id
            )
            roots = await get_default_context().database.find(
                "edge",
                {"entity": HAS_OWNERSHIP_PROPOSAL.__name__, "target": proposal.id},
                limit=2,
            )
            if (
                actual_workspace != proposal.workspace_id
                or len(roots) != 1
                or roots[0]["source"] != resource.id
            ):
                raise AuthorizationUnavailableError()
            result.append(
                {
                    "proposal_id": proposal.id,
                    "workspace_id": actual_workspace,
                    "resource_type": proposal.resource_type,
                    "resource_id": resource.id,
                    "recipient_id": proposal.recipient_id,
                    "issuer_id": proposal.issuer_id,
                    "previous_owner_id": proposal.previous_owner_id,
                    "status": proposal.status,
                    "revision": proposal.revision,
                    "expires_at": proposal.expires_at,
                    "expired": datetime.fromisoformat(proposal.expires_at)
                    <= datetime.now(timezone.utc),
                    "ownership_current": owner["id"] == proposal.owner_edge_id,
                }
            )
    return {"proposals": result, "complete": True}
