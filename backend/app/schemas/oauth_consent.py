"""Request/response shapes for the SPA-driven OAuth consent endpoints (M3b-1).

These models back the two bearer-authed endpoints in
``app/api/oauth_consent.py`` that let the Integral SPA drive the OAuth
consent step (fetch consent details + approve/deny). They mirror the OAuth
parameters jvspatial's authorization server validates on the authorize request
so the SPA can re-submit the exact request the consent-details fetch validated.

Per AGENTS.md § jvspatial Object-Spatial Contract, request/response shapes live
here, never inline in the handler module.
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class ConsentDetailsResponse(BaseModel):
    """Validated consent details rendered by the SPA's consent screen.

    Returned by ``GET /oauth/consent``. The ``scopes`` are the client-filtered
    scope the authorization server validated (``grant.request.scope``), not the
    raw query value, so the SPA shows exactly what the AS would grant.
    """

    client_id: str = Field(..., description="The requesting client's id")
    client_name: str = Field(
        default="", description="Human-readable client name for the consent UI"
    )
    scopes: List[str] = Field(
        default_factory=list,
        description="Client-filtered scopes the AS validated for this request",
    )
    redirect_uri: str = Field(
        ..., description="The validated redirect URI the code will be returned to"
    )
    state: str = Field(
        default="", description="Opaque client state echoed back on redirect"
    )


class ApproveConsentRequest(BaseModel):
    """The consent decision plus the OAuth parameters to re-submit to the AS.

    Posted to ``POST /oauth/consent/approve``. The parameters re-carry the exact
    authorization request the consent-details fetch validated. ``permissions`` /
    ``scope`` here are NEVER trusted for permission computation — the granted
    permissions come solely from the server-resolved session user (see the
    handler's TRUST BOUNDARY note). ``scope`` is only the client-requested scope
    the AS intersects with the resolved permissions.
    """

    decision: str = Field(
        ...,
        description="'approve' issues a code; anything else denies",
    )
    client_id: str = Field(..., description="The requesting client's id")
    redirect_uri: str = Field(..., description="The redirect URI from the request")
    scope: str = Field(default="", description="Space-delimited requested scope")
    state: str = Field(default="", description="Opaque client state")
    code_challenge: str = Field(default="", description="PKCE code challenge (S256)")
    code_challenge_method: str = Field(
        default="S256", description="PKCE method (S256 only)"
    )
    response_type: str = Field(default="code", description="OAuth response type")


class ApproveConsentResponse(BaseModel):
    """The redirect URL the SPA should send the browser to.

    Returned by ``POST /oauth/consent/approve``. On approve it carries the
    issued ``code`` (+ ``state``); on deny it carries ``error=access_denied``.
    """

    redirect: str = Field(
        ..., description="redirect_uri with code+state, or error=access_denied"
    )


__all__ = [
    "ApproveConsentRequest",
    "ApproveConsentResponse",
    "ConsentDetailsResponse",
]
