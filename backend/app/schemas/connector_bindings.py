"""Phase 8 Plan 08-02 Task 3 — IS_CONNECTED_TO binding schemas.

Per CLAUDE.md jvspatial pillar #2 (semantics on edges): the binding's
``mapping_profile_yaml`` and ``bidirectional`` flag live on the
``IsConnectedTo`` edge, NEVER on the ``Connector`` or ``Track`` Node.

Three wire shapes:

- ``CreateConnectorBindingRequest`` — POST body. ``track_id`` required;
  ``mapping_profile_yaml`` + ``bidirectional`` are optional with sensible
  defaults. ``extra: forbid`` blocks unknown fields (T-08-02-S01 spillover).
- ``ConnectorBindingResponse`` — projection of one IS_CONNECTED_TO edge
  with the bound Track's title + workspace for UI rendering.
- ``ConnectorBindingListResponse`` — GET response with ``total`` mirror.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class CreateConnectorBindingRequest(BaseModel):
    """POST /api/agentive/connectors/{id}/bindings body.

    Per CLAUDE.md jvspatial pillar #2 — ``mapping_profile_yaml`` and
    ``bidirectional`` are typed edge fields on ``IsConnectedTo``. They are
    accepted here at the API boundary and forwarded to ``connector.connect(
    target, edge=IS_CONNECTED_TO, ...)``.
    """

    track_id: str = Field(..., min_length=1)
    mapping_profile_yaml: Optional[str] = None
    bidirectional: Optional[bool] = False

    model_config = {"extra": "forbid"}


class ConnectorBindingResponse(BaseModel):
    """Wire shape for one IS_CONNECTED_TO edge.

    The pair ``(connector_id, track_id)`` uniquely identifies a binding;
    additional fields project the bound Track's title + workspace so the UI
    can render a stable label without a second lookup.
    """

    connector_id: str
    track_id: str
    track_title: Optional[str] = None
    workspace_id: Optional[str] = None
    mapping_profile_yaml: str = ""
    bidirectional: bool = False

    model_config = {"extra": "forbid"}


class ConnectorBindingListResponse(BaseModel):
    """GET /api/agentive/connectors/{id}/bindings response."""

    bindings: List[ConnectorBindingResponse]
    total: int

    model_config = {"extra": "forbid"}
