"""Independent durable link sources, never collapsed into direct collaboration."""

from datetime import datetime

from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.models.access_lifecycle import SHARE_GRANT
from app.models.nodes import ShareLink, User
from app.services.permissions import get_user_node
from app.services.workspace_permissions import read_member_facts

ROLES = {"admin", "editor", "commenter", "viewer"}


def validate_share_grant(row: dict, link: ShareLink, recipient_id: str) -> dict:
    """Require exact source/target, lifecycle and scope facts before any allow."""
    data = row.get("context")
    if (
        row.get("source") != link.id
        or row.get("target") != recipient_id
        or row.get("entity") != SHARE_GRANT.__name__
        or not isinstance(data, dict)
        or data.get("role") not in ROLES
        or link.intent != "collaborator"
        or data.get("role") != link.role
        or data.get("state") not in {"active", "revoked"}
        or type(data.get("revision")) is not int
        or data["revision"] < 1
        or data.get("workspace_id") != link.workspace_id
        or data.get("resource_type") != link.resource_type
        or data.get("resource_id") != link.resource_id
        or not isinstance(data.get("membership_id"), str)
        or not data["membership_id"]
        or any(
            not isinstance(data.get(k), str) or not data[k]
            for k in ("redeemed_at", "changed_at")
        )
    ):
        raise AuthorizationUnavailableError()
    for key in ("redeemed_at", "changed_at"):
        try:
            if datetime.fromisoformat(data[key]).tzinfo is None:
                raise ValueError("Missing timezone")
        except ValueError as exc:
            raise AuthorizationUnavailableError() from exc
    for key, value in data.items():
        if key in row and (type(row[key]) is not type(value) or row[key] != value):
            raise AuthorizationUnavailableError()
    return data


async def share_grant_row(link: ShareLink, recipient_id: str) -> dict | None:
    """Read a single source lifetime, including revocation tombstones."""
    rows = await get_default_context().database.find(
        "edge",
        {"source": link.id, "target": recipient_id, "entity": SHARE_GRANT.__name__},
        limit=2,
    )
    if len(rows) > 1:
        raise AuthorizationUnavailableError()
    if not rows:
        return None
    validate_share_grant(rows[0], link, recipient_id)
    return rows[0]


async def _current_sources(
    user_id: str,
) -> tuple[User | None, list[tuple[ShareLink, dict]]]:
    """Admit complete raw incoming sources before selecting requested scope."""
    user = await get_user_node(user_id)
    if user is None:
        return None, []
    database = get_default_context().database
    rows = await database.find(
        "edge", {"target": user.id, "entity": SHARE_GRANT.__name__}, limit=1001
    )
    if len(rows) > 1000:
        raise AuthorizationUnavailableError()
    from app.services.share_link_lifecycle import current_link

    admitted = []
    seen = set()
    for row in rows:
        source = row.get("source")
        if not isinstance(source, str) or not source or source in seen:
            raise AuthorizationUnavailableError()
        seen.add(source)
        link = await current_link(source)
        values = validate_share_grant(row, link, user.id)
        admitted.append((link, values))
    return user, admitted


async def share_grant_roles(
    user_id: str, resource_type: str, resource_id: str
) -> list[str]:
    """Union complete active sources bound to the current membership life.

    Disabling/expiring a link prevents future redemption; it does not remove
    previously accepted durable sources. Explicit source revocation does.
    """
    user, sources = await _current_sources(user_id)
    if user is None:
        return []
    roles = []
    for link, grant in sources:
        if (
            link.resource_type != resource_type
            or link.resource_id != resource_id
            or grant["state"] != "active"
        ):
            continue
        membership = await read_member_facts(user, link.workspace_id)
        if membership is not None and membership.get("id") == grant["membership_id"]:
            roles.append(grant["role"])
    return roles


async def share_grant_targets(user_id: str, resource_type: str) -> set[str]:
    """Complete bounded source candidates; corrupt facts fail unavailable."""
    if resource_type not in {"app", "track", "entry"}:
        raise AuthorizationUnavailableError()
    user, sources = await _current_sources(user_id)
    if user is None:
        return set()
    targets = set()
    from app.services.workspace_suspension import workspace_suspended

    for link, grant in sources:
        if link.resource_type != resource_type or grant["state"] != "active":
            continue
        if await workspace_suspended(user.id, link.workspace_id):
            continue
        membership = await read_member_facts(user, link.workspace_id)
        if membership is not None and membership["id"] == grant["membership_id"]:
            targets.add(link.resource_id)
    return targets


async def revoke_workspace_share_sources(user_id: str, workspace_id: str) -> int:
    """Tombstone accepted sources in the caller's held departure unit."""
    from app.services.share_link_lifecycle import current_link
    from app.utils.time import utc_now_iso

    database = get_default_context().database
    rows = await database.find(
        "edge",
        {
            "target": user_id,
            "entity": SHARE_GRANT.__name__,
            "context.workspace_id": workspace_id,
        },
        limit=1001,
    )
    if len(rows) > 1000:
        raise AuthorizationUnavailableError()
    revoked = 0
    for row in rows:
        link = await current_link(row["source"])
        values = validate_share_grant(row, link, user_id)
        if values["workspace_id"] != workspace_id:
            raise AuthorizationUnavailableError()
        if values["state"] == "revoked":
            continue
        context = await link.get_context()
        edges = await context.find_edges_between(
            link.id, user_id, edge_class=SHARE_GRANT
        )
        if len(edges) != 1 or edges[0].id != row["id"]:
            raise AuthorizationUnavailableError()
        edges[0].state = "revoked"
        edges[0].revision += 1
        edges[0].changed_at = utc_now_iso()
        await edges[0].save()
        revoked += 1
    return revoked
