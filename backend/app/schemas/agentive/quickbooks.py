"""Phase 18 — schemas for the QuickBooks OAuth + connector setup endpoints.

Mirrors the convention from ``schemas/agentive/connectors.py``: all
request/response shapes for the QuickBooks-specific routes live in
``schemas/agentive/`` per the agentive directives (I-CONV-03).

Tokens are NEVER carried in any of these wire shapes. The
``Connector.auth_state`` dict that holds ``access_token`` /
``refresh_token`` is redacted via :func:`quickbooks_safe_auth_state`
before any response leaves the server.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

# Keys that MUST be stripped from any auth_state surfaced in a wire
# response. Token material lives on the Connector node only.
_QB_AUTH_STATE_REDACTED_KEYS: frozenset = frozenset(
    {
        "access_token",
        "refresh_token",
        "access_token_expires_at",
        "refresh_token_expires_at",
        "client_secret",
    }
)


def quickbooks_safe_auth_state(auth_state: Dict[str, Any]) -> Dict[str, Any]:
    """Return an auth_state copy with all token material stripped.

    Caller-facing surfaces (``QuickBooksAuthorizeResponse``,
    ``QuickBooksCallbackResponse``, the connector list/get responses for
    QB connectors) MUST use this. The redacted keys are listed in
    ``_QB_AUTH_STATE_REDACTED_KEYS`` — if a future token field is added
    to ``auth_state`` it must be added here too.
    """
    return {
        k: v
        for k, v in (auth_state or {}).items()
        if k not in _QB_AUTH_STATE_REDACTED_KEYS
    }


class QuickBooksAuthorizeRequest(BaseModel):
    """GET /connectors/quickbooks/authorize body.

    ``connector_id`` is optional: when present the OAuth callback updates
    the existing connector; when absent the callback creates a fresh
    connector at exchange time. ``redirect_uri`` is optional — defaults
    to the QUICKBOOKS_REDIRECT_URI deployment env knob.
    """

    connector_id: Optional[str] = None
    redirect_uri: Optional[str] = None

    model_config = {"extra": "forbid"}


class QuickBooksAuthorizeResponse(BaseModel):
    """Returned to the frontend so it can open Intuit's consent URL.

    The frontend opens ``consent_url`` in a popup (Intuit-hosted). The
    ``state`` value is the signed CSRF token bound to the calling user
    — the callback endpoint validates it before exchanging the code.
    """

    consent_url: str
    state: str

    model_config = {"extra": "forbid"}


class QuickBooksCallbackRequest(BaseModel):
    """POST /connectors/quickbooks/callback body — populated by the popup.

    The frontend reads ``code`` / ``state`` / ``realmId`` from the Intuit
    redirect URL query string and POSTs them to this endpoint.
    """

    code: str
    state: str
    realm_id: str = Field(alias="realmId")
    connector_id: Optional[str] = None
    redirect_uri: Optional[str] = None

    model_config = {"extra": "forbid", "populate_by_name": True}


class QuickBooksCallbackResponse(BaseModel):
    """Result of the OAuth handshake — token material redacted.

    The frontend uses these scalars to render the post-auth settings
    panel (connected realm, environment, connection status). Token
    material lives only on the Connector node and is NEVER returned.
    """

    connector_id: str
    realm_id: str
    environment: str
    connected: bool = True
    reauth_required: bool = False
    auth_state: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}
