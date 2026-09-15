"""Phase 5 Plan 05-03 — core on-demand connector sync endpoint.

``POST /api/connectors/{id}/sync`` — trigger a synchronous sync for one
connector. Distinct from ``backend/app/agentive/api/connectors.py`` (Connector
CRUD), which stays AGENTIVE_ENABLED-gated. This handler is registered
unconditionally via ``@endpoint`` per locked decision #12 so a non-agentive
deployment with the agentive layer disabled can still expose the sync trigger
when its operator opts in.

Policy gate: ``policy_engine.evaluate(action="connector.sync",
resource=Resource(kind="connector", id=<id>))``. The per-connector Policy
materialized at Connector-create time (I-CON-04 from Plan 05-01) controls
which subjects may invoke this endpoint.
"""

import logging
from typing import Any, Dict

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import (
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceNotFoundError,
)
from app.api.utils import resolve_principal_id
from app.schemas.policy import Resource, Subject
from app.services.policy_engine import evaluate

logger = logging.getLogger(__name__)


@endpoint(
    "/connectors/{connector_id}/sync",
    methods=["POST"],
    auth=True,
    tags=["Connectors"],
)
async def sync_connector(request: Request, connector_id: str) -> Dict[str, Any]:
    """Trigger an on-demand sync for the connector.

    Auth — requires authenticated principal (``MissingAuthenticationError``
    surfaces as 401). Policy — gated by ``connector.sync`` PolicyAction
    on the per-connector resource scope (``InsufficientPermissionsError``
    surfaces as 403). On success, awaits ``sync_one_connector`` and returns
    the stats payload (``{created, updated, conflict, skipped, failed}``).

    Note — this handler emits ``connector.sync.start`` /
    ``connector.sync.complete`` ChangeEvents via the runtime's internal
    ``emit_change_event`` calls; it does NOT need an additional emit at the
    REST boundary (D-05 single-emission preserved). The runtime is the
    audit source-of-record for sync activity.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    # Lazy import — the Connector Node lives in agentive/. The import
    # works regardless of AGENTIVE_ENABLED (the class is always importable),
    # but only succeeds in resolving real rows when the agentive layer is up.
    from app.agentive.nodes import Connector

    connector = await Connector.get(connector_id)
    if connector is None:
        raise ResourceNotFoundError(message="Connector not found")

    decision = await evaluate(
        subject=Subject(kind="human", id=user_id),
        action="connector.sync",
        resource=Resource(
            kind="connector",
            id=connector_id,
            scope=f"connector:{connector_id}",
        ),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(message="Policy denied connector.sync")

    from app.services.connectors.sync_runtime import sync_one_connector

    stats = await sync_one_connector(connector)
    return {"connector_id": connector_id, "stats": stats}
