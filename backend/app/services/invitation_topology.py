"""Primary rooted invitation identity and exact INVITED_TO target proof."""

from jvspatial.core import Edge
from jvspatial.core.context import get_default_context
from jvspatial.core.entities import Root

from app.exceptions import AuthorizationUnavailableError
from app.models.edges import CATALOGS, INVITED_TO
from app.models.nodes import (
    APP_NODE_ID,
    INVITATIONS_REGISTRY_ID,
    IntegralApp,
    Invitations,
)
from app.services.authority_facts import primary_node_record


async def require_invitation_topology(
    invitation_id: str,
    workspace_id: str,
    kind: str,
    target_id: str,
    *,
    legacy_resource_only: bool = False,
) -> None:
    """Catalog membership is identity provenance, never a grant by itself."""
    database = get_default_context().database
    for model, identity in (
        (Root, "n.Root.root"),
        (IntegralApp, APP_NODE_ID),
        (Invitations, INVITATIONS_REGISTRY_ID),
    ):
        if await primary_node_record(database, model, identity) is None:
            raise AuthorizationUnavailableError()
    expected = [
        ("n.Root.root", APP_NODE_ID, Edge._entity_name()),
        (APP_NODE_ID, INVITATIONS_REGISTRY_ID, Edge._entity_name()),
        (INVITATIONS_REGISTRY_ID, invitation_id, CATALOGS._entity_name()),
    ]
    rows = await database.find(
        "edge",
        {"$or": [{"source": a, "target": b, "entity": e} for a, b, e in expected]},
        limit=4,
    )
    identities = [
        (row.get("source"), row.get("target"), row.get("entity")) for row in rows
    ]
    if len(identities) != 3 or set(identities) != set(expected):
        raise AuthorizationUnavailableError()
    targets = {workspace_id}
    if kind:
        targets.add(target_id)
    if legacy_resource_only:
        targets = {target_id}
    rows = await database.find(
        "edge",
        {"source": invitation_id, "entity": INVITED_TO.__name__},
        limit=len(targets) + 1,
    )
    if len(rows) != len(targets) or {row.get("target") for row in rows} != targets:
        raise AuthorizationUnavailableError()
