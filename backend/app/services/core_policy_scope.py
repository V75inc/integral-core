"""Canonical execution binding for Core App/Track/Entry/View policy targets."""

from app.contracts.runtime import ExecutionScope
from app.schemas.policy import Resource
from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource

CORE_DATA_KINDS = frozenset({"app", "track", "entry", "view"})
PARENT_KINDS = frozenset({"workspace", "app", "track", "entry", "view"})


async def core_policy_scope_matches(scope: ExecutionScope, resource: Resource) -> bool:
    """Bind a Core data target and its claimed parent before policy evaluation.

    Management lifecycle states establish location without granting a data
    or execution capability: the policy engine retains those action gates.
    Empty-id creation requests bind their existing parent, never an invented
    target. Related resource kinds require their own canonical ownership
    adapters; this helper's contract covers only CORE_DATA_KINDS.
    """
    if resource.kind not in CORE_DATA_KINDS:
        raise ValueError("Core policy scope requires App, Track, Entry or View")
    parts = resource.scope.split(":", 1)
    if (
        len(parts) != 2
        or parts[0] not in PARENT_KINDS
        or not parts[1]
        or parts[1] != parts[1].strip()
        or resource.id != resource.id.strip()
    ):
        return False
    parent_kind, parent_id = parts
    if resource.id:
        kind, target_id = resource.kind, resource.id
    else:
        # Creation can name only an existing structural parent.
        allowed_parents = {
            "app": {"workspace"},
            "track": {"workspace", "app"},
            "entry": {"track"},
            "view": {"track"},
        }
        if parent_kind not in allowed_parents[resource.kind]:
            return False
        kind, target_id = parent_kind, parent_id
    location = await locate_resource(
        kind, target_id, allowed_app_states=MANAGEMENT_APP_STATES
    )
    if location is None or location.workspace_id != scope.workspace_id:
        return False
    if not resource.id:
        return True
    expected = {
        "workspace": location.workspace_id,
        "app": location.app_id,
        "track": location.track_id,
        "entry": resource.id if resource.kind == "entry" else "",
        "view": resource.id if resource.kind == "view" else "",
    }
    return bool(expected[parent_kind]) and expected[parent_kind] == parent_id
