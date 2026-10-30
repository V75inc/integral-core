"""Current notification ownership and resource release for outbound delivery."""

from dataclasses import asdict

from jvspatial.api.exceptions import InsufficientPermissionsError
from jvspatial.core.context import get_default_context


async def owned_notification(user_id: str, notification_id: str):
    """Resolve the current record and its unique canonical recipient edge."""
    from app.models.edges import HAS_NOTIFICATION
    from app.models.nodes import Notification
    from app.services.authority_facts import primary_node_record
    from app.services.permissions import get_user_node

    caller = await get_user_node(user_id)
    if caller is None:
        return None
    context = get_default_context()
    row = await primary_node_record(context.database, Notification, notification_id)
    if row is None:
        return None
    notification = await context._deserialize_entity(Notification, row)
    if notification.user_id not in {caller.id, caller.user_id}:
        return None
    recipients = await notification.nodes(
        edge=[HAS_NOTIFICATION], direction="in", node=["User"], limit=2
    )
    if len(recipients) != 1 or recipients[0].id != caller.id:
        return None
    return notification


async def email_scope(user_id: str, workspace_id: str, notification_id: str) -> dict:
    """Recheck referenced resources; return bounded provenance, never a grant."""
    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.schemas.policy import Resource, Subject
    from app.services.policy_engine import evaluate
    from app.services.resource_location import DATA_APP_STATES, locate_resource

    notification = await owned_notification(user_id, notification_id)
    if (
        not notification
        or (notification.metadata or {}).get("email_workspace_id") != workspace_id
    ):
        raise InsufficientPermissionsError("Notification is no longer sendable")
    payload = (notification.metadata or {}).get("payload") or {}
    references = {
        (kind, str(payload[f"{kind}_id"]))
        for kind in ("entry", "track", "app")
        if payload.get(f"{kind}_id")
    }
    if payload.get("resource_id"):
        identity = str(payload["resource_id"])
        kind = str(payload.get("resource_type") or "").lower()
        if not kind:
            kind = next(
                (
                    k
                    for k, prefix in (
                        ("entry", "n.Entry."),
                        ("track", "n.Track."),
                        ("app", "n.WorkspaceApp."),
                        ("workspace", "n.Workspace."),
                    )
                    if identity.startswith(prefix)
                ),
                "",
            )
        if kind not in {"entry", "track", "app", "workspace", "view", "dashboard"}:
            raise InsufficientPermissionsError("Notification resource is unavailable")
        references.add((kind, identity))
    scopes: dict[str, dict] = {}
    with isolated_permissions_cache():
        locations = {}
        for kind, identity in sorted(references):
            location = await locate_resource(
                kind, identity, allowed_app_states=DATA_APP_STATES
            )
            if not location or location.workspace_id != workspace_id:
                raise InsufficientPermissionsError(
                    "Notification resource is unavailable"
                )
            locations[(kind, identity)] = location
        for (kind, identity), location in locations.items():
            # A direct Entry share need not grant access to its containing Track
            # or App. Validate those parent pointers against canonical containment
            # without treating them as independent recipient permissions.
            parent_only = any(
                (
                    kind == "track"
                    and child_kind == "entry"
                    and identity == child.track_id
                )
                or (
                    kind == "app"
                    and child_kind in {"entry", "track"}
                    and identity == child.app_id
                )
                for (child_kind, _), child in locations.items()
            )
            if not parent_only:
                # Dashboards inherit their App's read boundary; there is no
                # standalone dashboard.read policy action.
                policy_kind = "app" if kind == "dashboard" else kind
                policy_id = location.app_id if kind == "dashboard" else identity
                decision = await evaluate(
                    subject=Subject(kind="human", id=user_id),
                    action=f"{policy_kind}.read",
                    resource=Resource(
                        kind=policy_kind,
                        id=policy_id,
                        scope=f"{policy_kind}:{policy_id}",
                    ),
                )
                if not decision.allowed:
                    raise InsufficientPermissionsError(
                        "Notification resource access was revoked"
                    )
            scopes[f"{kind}:{identity}"] = asdict(location)
        for facts in scopes.values():
            if (
                payload.get("track_id")
                and facts["track_id"]
                and payload["track_id"] != facts["track_id"]
            ) or (
                payload.get("app_id")
                and facts["app_id"]
                and payload["app_id"] != facts["app_id"]
            ):
                raise InsufficientPermissionsError("Notification resource moved")
    return scopes
