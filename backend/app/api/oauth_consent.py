"""SPA-driven OAuth consent endpoints (M3b-1).

Two bearer-authed endpoints that let the Integral SPA drive the OAuth consent
step of the authorization-code flow. jvspatial M3a redirects an unauthenticated
browser hitting ``GET /api/oauth/authorize`` to the SPA
(``oauth_authorize_login_redirect``); the SPA then calls these endpoints with
the caller's bearer to (1) fetch validated consent details and (2)
approve/deny — issuing the authorization code the browser is redirected to.

These wrap the jvspatial authorization server's already-public async methods.
A separately-built ``JvSpatialAuthorizationServer`` instance shares the
DB-persisted ``OAuthClient`` / ``AuthorizationCode`` records and the active
RS256 signing key, so a code this module issues is accepted at jvspatial's
``POST /api/oauth/token`` endpoint unchanged.

TRUST BOUNDARY (critical): the resource owner is resolved ONLY from the
bearer-authenticated principal (``resolve_principal_id``), and the granted
permissions come ONLY from the server-resolved auth user's
``get_effective_permissions``. Any ``permissions`` / ``scope`` value in the
request body is NEVER used to compute the grant — the AS intersects the
client-requested scope with the server-resolved permissions
(``scope ∩ permissions``), so the issued token can never carry a scope the
resource owner lacks. This mirrors jvspatial's own ``POST /authorize`` handler.
"""

from typing import Any, Dict
from urllib.parse import urlencode

from authlib.oauth2 import OAuth2Error
from fastapi import Request
from jvspatial.api import endpoint
from jvspatial.api.auth.models import User as AuthUser
from jvspatial.api.auth.oauth.requests import StarletteOAuth2Request
from jvspatial.api.auth.oauth.server import build_authorization_server
from jvspatial.api.auth.rbac import get_effective_permissions

from app.api.errors import BadRequestError, MissingAuthenticationError
from app.api.utils import resolve_principal_id
from app.config import settings
from app.schemas.oauth_consent import (
    ApproveConsentResponse,
    ConsentDetailsResponse,
)

# A separately-built AS instance bound to the SAME issuer/resource as the one
# jvspatial mounts under /api/oauth/* (main.py AuthConfig.oauth_issuer_url ==
# settings.OAUTH_ISSUER_URL, resource == the same origin). Because all storage
# is DB-persisted Objects + the shared signing key, codes this instance issues
# are accepted at jvspatial's /api/oauth/token. Built once at import time.
_AS = build_authorization_server(
    issuer=settings.OAUTH_ISSUER_URL,
    resource=settings.OAUTH_ISSUER_URL,
    # M4 ceiling parity: declare the same scope ceiling the mounted server gets
    # via AuthConfig.oauth_supported_scopes (main.py). Without it the AS's
    # _supported_scopes is empty and M4's '*'-wildcard branch in _intersect_scope
    # would grant the FULL requested scope (e.g. "admin") to a wildcard-permission
    # admin — bypassing the intended ["integral"] ceiling.
    supported_scopes=settings.OAUTH_SUPPORTED_SCOPES,
)


def _role_permission_mapping() -> Dict[str, Any]:
    """Return the role->permission mapping the AS intersects scope against.

    integral's ``AuthConfig`` does not override ``role_permission_mapping``, so
    the effective mapping is jvspatial's default (``{"admin": ["*"], "user":
    []}``). Resolve it from the rbac module's default so this matches exactly
    what jvspatial's own ``POST /authorize`` handler uses.
    """
    from jvspatial.api.auth.rbac import DEFAULT_ROLE_PERMISSION_MAPPING

    return dict(DEFAULT_ROLE_PERMISSION_MAPPING)


def _request_from_query(request: Request) -> StarletteOAuth2Request:
    """Build a GET ``StarletteOAuth2Request`` from the request's query params."""
    return StarletteOAuth2Request(
        method="GET",
        uri=str(request.url),
        query=dict(request.query_params),
        form={},
        headers=dict(request.headers),
    )


def _request_from_params(
    request: Request, params: Dict[str, str]
) -> StarletteOAuth2Request:
    """Build a POST ``StarletteOAuth2Request`` carrying *params* as form data.

    Mirrors jvspatial's ``authorize_post``: the OAuth parameters the AS needs
    (client_id, redirect_uri, scope, state, PKCE challenge, response_type) are
    placed in ``form`` so the authorization server validates the exact request
    the consent-details fetch validated.
    """
    return StarletteOAuth2Request(
        method="POST",
        uri=str(request.url),
        query=dict(request.query_params),
        form={k: v for k, v in params.items() if v is not None},
        headers=dict(request.headers),
    )


@endpoint("/oauth/consent", methods=["GET"], auth=True, tags=["OAuth"])
async def get_consent(request: Request) -> ConsentDetailsResponse:
    """Return validated OAuth consent details for the SPA's consent screen.

    The caller MUST be bearer-authenticated (the SPA forwards the user's
    session token). The OAuth parameters arrive as query params (the SPA
    forwards the original authorize query). The authorization server validates
    the request (client, redirect_uri, PKCE) and returns the client-filtered
    scope; an invalid request surfaces as a 400 ``BadRequestError`` rather than
    leaking a 500.
    """
    uid = resolve_principal_id(request)
    if not uid:
        raise MissingAuthenticationError(message="Authentication required")

    req = _request_from_query(request)
    try:
        grant = await _AS.async_get_consent_grant(req, end_user={"id": uid})
    except OAuth2Error as err:
        raise BadRequestError(
            message=err.get_error_description() or err.error or "invalid_request"
        ) from err

    # grant.client is the OAuthClientAdapter; .client is the OAuthClient record.
    client = grant.client.client
    granted_scope = grant.request.scope or req.args.get("scope", "") or ""
    return ConsentDetailsResponse(
        client_id=client.client_id,
        client_name=getattr(client, "client_name", "") or "",
        scopes=granted_scope.split(),
        redirect_uri=getattr(grant, "redirect_uri", "")
        or req.args.get("redirect_uri", "")
        or "",
        state=req.args.get("state", "") or "",
    )


@endpoint("/oauth/consent/approve", methods=["POST"], auth=True, tags=["OAuth"])
async def approve_consent(
    request: Request,
    decision: str = "deny",
    client_id: str = "",
    redirect_uri: str = "",
    scope: str = "",
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
    response_type: str = "code",
) -> ApproveConsentResponse:
    """Complete the consent decision; issue an authorization code on approve.

    On ``decision == "approve"`` an authorization code is minted and the SPA is
    handed the ``redirect_uri?code=...&state=...`` URL to send the browser to.
    Any other decision denies: the request is re-validated through the AS (so
    the redirect target is proven to belong to the registered client — never an
    open redirect) and the SPA is handed ``redirect_uri?error=access_denied``.

    TRUST BOUNDARY: ``grant_user["permissions"]`` comes ONLY from the
    server-resolved auth user's effective permissions. The ``scope`` field is
    the client-requested scope the AS intersects with those permissions; no
    ``permissions``/``scope`` value in the body can widen the grant. Identity is
    ``resolve_principal_id`` only.
    """
    uid = resolve_principal_id(request)
    if not uid:
        raise MissingAuthenticationError(message="Authentication required")

    # Resolve the auth user's roles/permissions server-side — the source of
    # truth for the grant, NEVER read from the request body.
    auth_user: Any = await AuthUser.get(uid)
    roles = list(getattr(auth_user, "roles", None) or []) if auth_user else []
    direct_perms = (
        list(getattr(auth_user, "permissions", None) or []) if auth_user else []
    )
    effective = set(
        get_effective_permissions(roles, direct_perms, _role_permission_mapping())
    )
    # Bound the grantable scope to the AS's supported scopes PLUS any RBAC
    # permissions the user actually holds. For a normal ``user`` role the
    # default mapping (``{"admin": ["*"], "user": []}``) yields an EMPTY
    # effective set; passing that straight through trips jvspatial's
    # ``_intersect_scope(requested_scope, [])`` back-compat branch, which
    # returns the client-requested scope UNCHANGED — so a malicious DCR client
    # requesting ``scope:admin`` would be issued an admin-scoped token for a
    # normal user. Unioning in ``OAUTH_SUPPORTED_SCOPES`` (the base ``integral``
    # scope every authenticated user may grant) makes the bound list non-empty,
    # so the AS performs a real ``scope ∩ grantable`` intersection and ``admin``
    # is dropped for any user who lacks it.
    #
    # NOTE (jvspatial backlog): the empty-list back-compat branch in
    # ``_intersect_scope`` + DCR not filtering the requested scope against the
    # client's registered scope are jvspatial-layer latent issues; this
    # integral-side bound closes the M3-issued-code exposure without touching
    # jvspatial.
    permissions = sorted(effective | set(settings.OAUTH_SUPPORTED_SCOPES))

    params = {
        "response_type": response_type,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
    }

    if decision != "approve":
        # Re-validate before redirecting: the body-supplied redirect_uri has not
        # been proven to belong to the client, so honouring it as-is would be an
        # open redirect. Validate via the consent-grant path and redirect ONLY
        # to the client-validated redirect_uri; on failure raise a 400.
        deny_req = _request_from_params(request, params)
        try:
            grant = await _AS.async_get_consent_grant(deny_req, end_user={"id": uid})
        except OAuth2Error as err:
            raise BadRequestError(
                message=err.get_error_description() or err.error or "invalid_request"
            ) from err
        validated_redirect = getattr(grant, "redirect_uri", "") or redirect_uri
        query = urlencode(
            {k: v for k, v in {"error": "access_denied", "state": state}.items() if v}
        )
        sep = "&" if "?" in validated_redirect else "?"
        return ApproveConsentResponse(redirect=f"{validated_redirect}{sep}{query}")

    grant_user = {"id": uid, "permissions": permissions}
    req = _request_from_params(request, params)
    try:
        resp = await _AS.async_create_authorization_response(req, grant_user=grant_user)
    except OAuth2Error as err:
        raise BadRequestError(
            message=err.get_error_description() or err.error or "invalid_request"
        ) from err

    location = resp.headers.get("location")
    if not location:
        # The AS returned a non-redirect (validation error rendered as a body).
        body = resp.body_json or {}
        raise BadRequestError(
            message=str(body.get("error_description") or body.get("error"))
            or "authorization request rejected"
        )
    return ApproveConsentResponse(redirect=location)
