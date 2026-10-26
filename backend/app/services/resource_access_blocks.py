"""Absolute subtree blocks distinct from inherited exclusions and source removal."""

from datetime import datetime

from jvspatial.core.context import get_default_context

from app.api.errors import BadRequestError, InsufficientPermissionsError
from app.exceptions import AuthorizationUnavailableError
from app.models.access_lifecycle import RESOURCE_ACCESS_BLOCK
from app.models.edges import IS_MEMBER_OF, OWNS
from app.models.nodes import Workspace
from app.services.authority_facts import relationship_literal
from app.services.identity_admission import resolve_current_profile
from app.services.membership_write_scope import membership_write_scope
from app.services.permissions import get_user_node, resolve_role
from app.services.permissions_process_cache import invalidate_user_aliases
from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource
from app.services.workspace_membership import _deliver, _record_change
from app.services.workspace_permissions import read_member_facts
from app.utils.time import utc_now_iso


def _fact(row: dict, *, user_id: str, resource_id: str, workspace_id: str) -> dict:
    data = row.get("context")
    if (
        row.get("source") != user_id
        or row.get("target") != resource_id
        or row.get("entity") != RESOURCE_ACCESS_BLOCK.__name__
        or not isinstance(data, dict)
        or data.get("workspace_id") != workspace_id
        or data.get("resource_type") not in {"app", "track", "entry"}
        or data.get("state") not in {"blocked", "released"}
        or type(data.get("revision")) is not int
        or data["revision"] < 1
        or any(
            not isinstance(data.get(k), str) or not data[k]
            for k in ("actor_id", "changed_at")
        )
    ):
        raise AuthorizationUnavailableError()
    try:
        if datetime.fromisoformat(data["changed_at"]).tzinfo is None:
            raise ValueError("Missing timezone")
    except ValueError as exc:
        raise AuthorizationUnavailableError() from exc
    for key, value in data.items():
        if key in row and (type(row[key]) is not type(value) or row[key] != value):
            raise AuthorizationUnavailableError()
    return data


async def resource_blocked(
    user_id: str, resource_type: str, resource_id: str, *, location=None
) -> bool:
    """Apply each complete ancestor deny to identified ordinary grant paths."""
    user = await get_user_node(user_id)
    if user is None:
        return False
    location = location or await locate_resource(
        resource_type, resource_id, allowed_app_states=MANAGEMENT_APP_STATES
    )
    if location is None:
        raise AuthorizationUnavailableError()
    targets = {resource_id: resource_type}
    if location.track_id:
        targets[location.track_id] = "track"
    if location.app_id:
        targets[location.app_id] = "app"
    rows = await get_default_context().database.find(
        "edge",
        {
            "source": user.id,
            "target": {"$in": sorted(targets)},
            "entity": RESOURCE_ACCESS_BLOCK.__name__,
        },
        limit=4,
    )
    if len(rows) > len(targets):
        raise AuthorizationUnavailableError()
    seen = set()
    for row in rows:
        target = row.get("target")
        if target in seen or target not in targets:
            raise AuthorizationUnavailableError()
        seen.add(target)
        data = _fact(
            row, user_id=user.id, resource_id=target, workspace_id=location.workspace_id
        )
        if data["resource_type"] != targets[target]:
            raise AuthorizationUnavailableError()
        if data["state"] == "blocked":
            return True
    return False


async def blocked_entry_ids(user, locations):
    """Read complete current block facts for one canonical admitted window.

    No grant/deny result survives this call. Reuse the point reader's fact
    validation, including released rows, ancestor blocks and duplicate rejection.
    # deviation: batch one-hop block edges — 2,501-record count took 9.35s
    # with individual block probes; canonical containment remains per window.
    """
    targets = {}
    for entry_id, location in locations.items():
        for node_id, kind in (
            (entry_id, "entry"),
            (location.track_id, "track"),
            (location.app_id, "app"),
        ):
            if not node_id:
                continue
            value = (kind, location.workspace_id)
            if node_id in targets and targets[node_id] != value:
                raise AuthorizationUnavailableError()
            targets[node_id] = value
    if not targets:
        return set()
    rows = await get_default_context().database.find(
        "edge",
        {
            "source": user.id,
            "target": {"$in": sorted(targets)},
            "entity": RESOURCE_ACCESS_BLOCK.__name__,
        },
        limit=len(targets) + 1,
    )
    if len(rows) > len(targets):
        raise AuthorizationUnavailableError()
    seen, blocked = set(), set()
    for row in rows:
        target = row.get("target")
        if target in seen or target not in targets:
            raise AuthorizationUnavailableError()
        seen.add(target)
        kind, workspace = targets[target]
        fact = _fact(row, user_id=user.id, resource_id=target, workspace_id=workspace)
        if fact["resource_type"] != kind:
            raise AuthorizationUnavailableError()
        if fact["state"] == "blocked":
            blocked.add(target)
    return {
        entry_id
        for entry_id, location in locations.items()
        if {entry_id, location.track_id, location.app_id} & blocked
    }


async def require_unblocked(user_id: str, resource_type: str, resource_id: str) -> None:
    """Prevent invitation/token producers from bypassing a retained subtree deny."""
    if await resource_blocked(user_id, resource_type, resource_id):
        raise InsufficientPermissionsError(message="Resource access is blocked")


async def set_resource_block(
    *,
    actor_id: str,
    resource_type: str,
    resource_id: str,
    subject_id: str,
    blocked: bool,
    reason: str = "",
) -> dict:
    """Managers block ordinary audiences; protected owners cannot be blocked."""
    if type(blocked) is not bool or type(reason) is not str or len(reason) > 1000:
        raise BadRequestError(message="Invalid resource block")
    location = await locate_resource(
        resource_type, resource_id, allowed_app_states=MANAGEMENT_APP_STATES
    )
    if location is None:
        raise AuthorizationUnavailableError()
    outbox = None
    async with membership_write_scope(
        location.workspace_id,
        principal_ids=(actor_id, subject_id),
        active_principal_ids=(actor_id,),
    ):
        from app.services.sharing import _load_resource, _require_share_authority

        await _require_share_authority(
            actor_id,
            resource_type,
            resource_id,
            policy_action=f"{resource_type}.exclusion_add",
        )
        actor = await get_user_node(actor_id)
        subject = await resolve_current_profile(subject_id, require_active=False)
        resource = await _load_resource(resource_type, resource_id)
        if actor is None or resource is None:
            raise AuthorizationUnavailableError()
        current = await locate_resource(
            resource_type, resource_id, allowed_app_states=MANAGEMENT_APP_STATES
        )
        if current is None or current.workspace_id != location.workspace_id:
            raise AuthorizationUnavailableError()
        owns = await get_default_context().database.find(
            "edge",
            {"source": subject.id, "target": resource.id, "entity": OWNS.__name__},
            limit=2,
        )
        membership = await read_member_facts(subject, location.workspace_id)
        owners = await get_default_context().database.find(
            "edge",
            {
                "target": location.workspace_id,
                "entity": IS_MEMBER_OF.__name__,
                "$or": [{"context.role": "owner"}, {"role": "owner"}],
            },
            limit=2,
        )
        if (
            len(owners) != 1
            or relationship_literal(
                owners[0], "role", {"owner", "admin", "member", "guest"}
            )
            != "owner"
        ):
            raise AuthorizationUnavailableError()
        if (
            owns
            or owners[0].get("source") == subject.id
            or (membership and membership["context"].get("role") == "owner")
        ):
            raise BadRequestError(
                message="Protected ownership requires a separate governed lifecycle operation"
            )
        rows = await get_default_context().database.find(
            "edge",
            {
                "source": subject.id,
                "target": resource.id,
                "entity": RESOURCE_ACCESS_BLOCK.__name__,
            },
            limit=2,
        )
        if len(rows) > 1:
            raise AuthorizationUnavailableError()
        before = rows[0] if rows else None
        state = "blocked" if blocked else "released"
        data = (
            _fact(
                before,
                user_id=subject.id,
                resource_id=resource.id,
                workspace_id=location.workspace_id,
            )
            if before
            else None
        )
        changed = data is None or data["state"] != state
        revision = data["revision"] if data else 0
        if changed:
            values = {
                "workspace_id": location.workspace_id,
                "resource_type": resource_type,
                "state": state,
                "actor_id": actor.id,
                "reason": reason,
                "changed_at": utc_now_iso(),
                "revision": revision + 1,
            }
            context = get_default_context()
            if before:
                edges = await context.find_edges_between(
                    subject.id, resource.id, edge_class=RESOURCE_ACCESS_BLOCK
                )
                if len(edges) != 1 or edges[0].id != before["id"]:
                    raise AuthorizationUnavailableError()
                for key, value in values.items():
                    setattr(edges[0], key, value)
                await edges[0].save()
                block_id = edges[0].id
            else:
                block = await subject.connect(
                    resource, edge=RESOURCE_ACCESS_BLOCK, **values
                )
                block_id = block.id
            revision += 1
            workspace = await Workspace.find_one({"id": location.workspace_id})
            if workspace is None:
                raise AuthorizationUnavailableError()
            outbox = await _record_change(
                actor,
                workspace,
                "workspace.member_update",
                before,
                {
                    "membership_id": block_id,
                    "grant_revision": revision,
                    "operation": "resource." + state,
                    "resource_type": resource_type,
                    "resource_id": resource.id,
                    "subject_id": subject.id,
                },
            )
        invalidate_user_aliases(subject)
        result = {
            "resource_type": resource_type,
            "resource_id": resource.id,
            "subject_id": subject.id,
            "state": state,
            "revision": revision,
            "changed": changed,
            "effective_role": await resolve_role(
                subject.id, resource_type, resource.id
            ),
            "anonymous_public_limit": "Anonymous public audiences cannot enforce identity-specific blocks",
        }
    await _deliver(outbox)
    return result
