"""Phase 19 — schemas for Gmail OAuth + label-list endpoints.

Mirrors the schemas/agentive/quickbooks.py convention. Tokens are
NEVER returned in any wire shape — ``gmail_safe_auth_state`` strips
token material before the response leaves the server.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

_GMAIL_AUTH_STATE_REDACTED_KEYS: frozenset = frozenset(
    {
        "access_token",
        "refresh_token",
        "access_token_expires_at",
        "client_secret",
    }
)


def gmail_safe_auth_state(auth_state: Dict[str, Any]) -> Dict[str, Any]:
    """Strip token fields from any auth_state surfaced in a wire response."""
    return {
        k: v
        for k, v in (auth_state or {}).items()
        if k not in _GMAIL_AUTH_STATE_REDACTED_KEYS
    }


class GmailOAuthStartRequest(BaseModel):
    connector_id: Optional[str] = None
    redirect_uri: Optional[str] = None

    model_config = {"extra": "forbid"}


class GmailOAuthStartResponse(BaseModel):
    consent_url: str
    state: str

    model_config = {"extra": "forbid"}


class GmailOAuthCallbackRequest(BaseModel):
    code: str
    state: str
    connector_id: Optional[str] = None
    redirect_uri: Optional[str] = None

    model_config = {"extra": "forbid"}


class GmailOAuthCallbackResponse(BaseModel):
    connector_id: str
    connected: bool = True
    reauth_required: bool = False
    auth_state: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class GmailLabel(BaseModel):
    id: str
    name: str
    type: str = "user"


class GmailLabelsResponse(BaseModel):
    labels: List[GmailLabel] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class GmailSetLabelsRequest(BaseModel):
    """Body for the consent-acknowledge / label-selection step."""

    label_ids: List[str]
    consent_acknowledged: bool = False

    model_config = {"extra": "forbid"}


class GmailSetLabelsResponse(BaseModel):
    connector_id: str
    label_ids: List[str]
    consent_acknowledged: bool
    auth_state: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}
