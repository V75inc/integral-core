"""Wire shapes for the user-facing connected-agents surface (M3b-2).

Back the two bearer-authed endpoints in ``app/api/users.py`` that let a user
see and revoke the external OAuth clients (MCP agents) holding an active
refresh-token grant for them:

  * ``GET    /users/me/connected-agents``             -> ``list[ConnectedAgent]``
  * ``DELETE /users/me/connected-agents/{client_id}`` -> ``RevokeAgentResponse``

Per AGENTS.md § jvspatial Object-Spatial Contract, request/response shapes live
here, never inline in the handler module.
"""

from __future__ import annotations

from datetime import datetime
from typing import List

from pydantic import BaseModel, Field


class ConnectedAgent(BaseModel):
    """One distinct OAuth client holding an active grant for the caller.

    Multiple active refresh tokens for the same client (grant-family
    rotations) collapse into a single row; ``granted_at`` is the most-recent
    token's ``created_at``.
    """

    client_id: str = Field(..., description="The OAuth client's public id")
    client_name: str = Field(
        default="",
        description="Human-readable client name (falls back to client_id)",
    )
    scopes: List[str] = Field(
        default_factory=list,
        description="Granted scopes for this client's active grant",
    )
    granted_at: datetime = Field(
        ...,
        description="Most-recent active-token created_at for this client",
    )


class RevokeAgentResponse(BaseModel):
    """Result of revoking a client's grant for the caller.

    ``revoked`` is the number of the caller's active refresh tokens for the
    client that were deactivated by the request.
    """

    revoked: int = Field(
        ..., description="Count of the caller's active tokens deactivated"
    )


__all__ = [
    "ConnectedAgent",
    "RevokeAgentResponse",
]
