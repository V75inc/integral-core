"""Complete bounded source inventory for the existing governed access snapshot."""

from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.models.access_lifecycle import RESOURCE_ACCESS_BLOCK, SHARE_GRANT
from app.models.edges import (
    COLLABORATES_ON,
    EXCLUDED_FROM,
    HAS_SHARE_LINK,
    IS_MEMBER_OF,
    OWNS,
)
from app.services.permissions import resolve_role
from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource


async def resource_access_sources(resource_type: str, resource_id: str) -> dict:
    """Enumerate source facts; the caller must already hold access-review authority.

    Current effective roles remain resolver-derived. A retained source is not
    labelled effective merely because its edge exists or its token was accepted.
    """
    location = await locate_resource(
        resource_type, resource_id, allowed_app_states=MANAGEMENT_APP_STATES
    )
    if location is None:
        raise AuthorizationUnavailableError()
    database = get_default_context().database
    targets = {resource_id, location.workspace_id}
    targets.update(value for value in (location.app_id, location.track_id) if value)
    rows = await database.find(
        "edge",
        {
            "target": {"$in": sorted(targets)},
            "entity": {
                "$in": [
                    edge._entity_name()
                    for edge in (
                        OWNS,
                        COLLABORATES_ON,
                        EXCLUDED_FROM,
                        IS_MEMBER_OF,
                        RESOURCE_ACCESS_BLOCK,
                    )
                ]
            },
        },
        limit=1001,
    )
    if len(rows) > 1000:
        raise AuthorizationUnavailableError()
    sources, subjects = [], set()
    for row in rows:
        user_id = row.get("source")
        if not isinstance(user_id, str) or not user_id:
            raise AuthorizationUnavailableError()
        values = row.get("context")
        if not isinstance(values, dict):
            raise AuthorizationUnavailableError()
        subjects.add(user_id)
        sources.append(
            {
                "source_id": row["id"],
                "user_id": user_id,
                "resource_id": row["target"],
                "source": row["entity"],
                "role": (
                    "owner"
                    if row["entity"] == OWNS._entity_name()
                    else values.get("role")
                ),
                "state": values.get("state"),
                "reason": values.get("reason", ""),
            }
        )
    links = await database.find(
        "edge",
        {
            "source": {"$in": sorted(targets - {location.workspace_id})},
            "entity": HAS_SHARE_LINK._entity_name(),
        },
        limit=1001,
    )
    if len(links) > 1000:
        raise AuthorizationUnavailableError()
    from app.services.share_grant_sources import validate_share_grant
    from app.services.share_link_lifecycle import current_link

    for relationship in links:
        link = await current_link(relationship["target"])
        grants = await database.find(
            "edge",
            {"source": link.id, "entity": SHARE_GRANT._entity_name()},
            limit=1001,
        )
        if len(grants) > 1000 or len(sources) + len(grants) > 1000:
            raise AuthorizationUnavailableError()
        for row in grants:
            values = validate_share_grant(row, link, row["target"])
            subjects.add(row["target"])
            sources.append(
                {
                    "source_id": row["id"],
                    "link_id": link.id,
                    "user_id": row["target"],
                    "resource_id": link.resource_id,
                    "source": "share_link",
                    "role": values["role"],
                    "state": values["state"],
                }
            )
    effective = []
    for user_id in sorted(subjects):
        effective.append(
            {
                "user_id": user_id,
                "role": await resolve_role(user_id, resource_type, resource_id),
            }
        )
    return {"sources": sources, "effective_access": effective, "complete": True}
