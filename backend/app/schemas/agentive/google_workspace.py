"""Schemas for native Google Workspace OAuth (drive_native / sheets_native).

Mirrors the schemas/agentive/gmail.py convention. One endpoint pair serves
both providers — the ``provider`` field (``drive_native`` / ``sheets_native``)
selects the scope set and the connector subclass slug. Tokens are NEVER
returned in any wire shape.
"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

GoogleWorkspaceProvider = Literal["drive_native", "sheets_native"]

NATIVE_GOOGLE_PROVIDERS: frozenset = frozenset({"drive_native", "sheets_native"})

_GOOGLE_AUTH_STATE_REDACTED_KEYS: frozenset = frozenset(
    {
        "access_token",
        "refresh_token",
        "access_token_expires_at",
        "client_secret",
    }
)


def google_safe_auth_state(auth_state: Dict[str, Any]) -> Dict[str, Any]:
    """Strip token fields from any auth_state surfaced in a wire response."""
    return {
        k: v
        for k, v in (auth_state or {}).items()
        if k not in _GOOGLE_AUTH_STATE_REDACTED_KEYS
    }


class GoogleOAuthStartRequest(BaseModel):
    provider: GoogleWorkspaceProvider
    connector_id: Optional[str] = None
    redirect_uri: Optional[str] = None

    model_config = {"extra": "forbid"}


class GoogleOAuthStartResponse(BaseModel):
    provider: GoogleWorkspaceProvider
    consent_url: str
    state: str

    model_config = {"extra": "forbid"}


class GoogleOAuthCallbackRequest(BaseModel):
    # Provider is optional: the popup callback page does not know which
    # provider it serves (one route for both), so the endpoint infers it
    # from the signed state token when absent.
    provider: Optional[GoogleWorkspaceProvider] = None
    code: str
    state: str
    connector_id: Optional[str] = None
    redirect_uri: Optional[str] = None

    model_config = {"extra": "forbid"}


class GoogleOAuthCallbackResponse(BaseModel):
    connector_id: str
    provider: GoogleWorkspaceProvider
    connected: bool = True
    reauth_required: bool = False
    auth_state: Dict[str, Any] = Field(default_factory=dict)
    connection_mode: str = "per_user"

    model_config = {"extra": "forbid"}
