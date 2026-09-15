"""M3b-1 — SPA-driven OAuth consent endpoints (get + approve).

The Integral SPA drives the OAuth consent step of the authorization-code flow
via two bearer-authed endpoints that wrap jvspatial's authorization server:

  * ``GET  /api/oauth/consent``          — validated consent details
  * ``POST /api/oauth/consent/approve``  — approve (issue code) / deny

A separately-built ``JvSpatialAuthorizationServer`` instance shares the
DB-persisted ``OAuthClient`` / ``AuthorizationCode`` records and the active
RS256 signing key, so a code these endpoints issue is accepted at jvspatial's
``POST /api/oauth/token`` — proven below by exchanging an issued code for an
access token whose ``sub`` is the bearer's principal.

TRUST BOUNDARY (the core security property): the granted permissions come ONLY
from the server-resolved auth user. A forged ``permissions`` body field is
ignored — the issued code's scope is the server-computed intersection of the
client-requested scope and the user's effective permissions, never the forged
value.

The endpoints are plain ``@endpoint`` handlers (NOT the MCP mount), so they need
no ``LifespanManager`` — a bearer-bearing ``AsyncClient`` over ``ASGITransport``
suffices. ``AUTHLIB_INSECURE_TRANSPORT=1`` is set because the in-process request
URI is ``http://test`` (Authlib otherwise requires the authorize request over
TLS); this mirrors jvspatial's own authorize HTTP tests.
"""

import base64
import hashlib
import secrets
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True)
def _allow_insecure_transport(monkeypatch):
    """Allow http://test: Authlib requires TLS for authorize unless this is set."""
    monkeypatch.setenv("AUTHLIB_INSECURE_TRANSPORT", "1")


def _pkce() -> tuple[str, str]:
    """Return a (verifier, S256-challenge) PKCE pair."""
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


async def _mint_public_client(
    *, redirect_uri: str = "https://spa.example/oauth/cb", scope: str = "integral"
):
    """Create a public PKCE ``OAuthClient`` Object directly (no DCR round-trip).

    Public client: ``token_endpoint_auth_method="none"`` (PKCE-only, no secret),
    a single registered ``redirect_uri`` (exact-match), and the auth-code +
    refresh grant types. Returns the persisted record.
    """
    from jvspatial.api.auth.oauth.models import OAuthClient

    client = await OAuthClient.create(
        client_id=f"cli_{uuid.uuid4().hex}",
        client_secret_hash=None,
        client_name="Integral SPA Test Client",
        redirect_uris=[redirect_uri],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope=scope,
        token_endpoint_auth_method="none",
    )
    return client


async def _bootstrap_user_with_permissions(*, email: str, permissions: list[str]):
    """Bootstrap an auth user + JWT, then set its DIRECT permissions server-side.

    The default integral role mapping (``{"admin": ["*"], "user": []}``) grants
    the ``user`` role no permissions, so we set direct permissions on the
    persisted auth ``User`` Object — the source of truth the approve handler
    resolves via ``get_effective_permissions``. Returns ``(token, auth_user_id)``.
    """
    from jvspatial.api.auth.models import User as AuthUser

    from tests.conftest import _bootstrap_test_user_fast

    token, user_node = await _bootstrap_test_user_fast(
        email=email, password="testpassword123", name="OAuth Consent"
    )
    auth_user_id = user_node.user_id
    auth_user = await AuthUser.get(auth_user_id)
    assert auth_user is not None, "bootstrap must persist an auth User"
    auth_user.permissions = list(permissions)
    await auth_user.save()
    return token, auth_user_id


def _client():
    """Return an ASGITransport ``AsyncClient`` (closed by the caller)."""
    from tests.conftest import get_app

    return AsyncClient(
        transport=ASGITransport(app=get_app()),
        base_url="http://test",
        timeout=15.0,
    )


async def _ensure_signing_key():
    """Provision the RS256 signing key the token endpoint signs with.

    The key store is populated by an app-startup hook the in-process
    ``ASGITransport`` does NOT run (no lifespan). ``ensure_signing_key`` is
    idempotent — it returns the active key or generates one — so calling it
    directly gives the token-exchange path a key to sign with without driving
    the full ``LifespanManager``.
    """
    from jvspatial.api.auth.oauth.keys import ensure_signing_key

    await ensure_signing_key()


@pytest.mark.asyncio
async def test_get_consent_returns_client_name_scopes_redirect(
    bind_fresh_graph_context_for_async_tests,
):
    """A valid bearer + valid params -> 200 with client_name, scopes, redirect_uri."""
    token, _uid = await _bootstrap_user_with_permissions(
        email="consent-get@example.com", permissions=["integral"]
    )
    client = await _mint_public_client()
    _verifier, challenge = _pkce()

    async with _client() as ac:
        resp = await ac.get(
            "/api/oauth/consent",
            params={
                "response_type": "code",
                "client_id": client.client_id,
                "redirect_uri": client.redirect_uris[0],
                "scope": "integral",
                "state": "st-get-1",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["client_id"] == client.client_id, body
    assert body["client_name"] == "Integral SPA Test Client", body
    assert "integral" in body["scopes"], body
    assert body["redirect_uri"] == client.redirect_uris[0], body
    assert body["state"] == "st-get-1", body


@pytest.mark.asyncio
async def test_get_consent_bad_client_is_4xx_not_500(
    bind_fresh_graph_context_for_async_tests,
):
    """An unknown client_id surfaces a 4xx (JVSpatialAPIException), never a 500."""
    token, _uid = await _bootstrap_user_with_permissions(
        email="consent-bad-client@example.com", permissions=["integral"]
    )
    _verifier, challenge = _pkce()

    async with _client() as ac:
        resp = await ac.get(
            "/api/oauth/consent",
            params={
                "response_type": "code",
                "client_id": "cli_does_not_exist",
                "redirect_uri": "https://spa.example/oauth/cb",
                "scope": "integral",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert 400 <= resp.status_code < 500, resp.text
    assert resp.status_code != 500, resp.text


@pytest.mark.asyncio
async def test_get_consent_bad_redirect_uri_is_4xx_not_500(
    bind_fresh_graph_context_for_async_tests,
):
    """A redirect_uri not registered for the client is a 4xx, never a 500."""
    token, _uid = await _bootstrap_user_with_permissions(
        email="consent-bad-redirect@example.com", permissions=["integral"]
    )
    client = await _mint_public_client()
    _verifier, challenge = _pkce()

    async with _client() as ac:
        resp = await ac.get(
            "/api/oauth/consent",
            params={
                "response_type": "code",
                "client_id": client.client_id,
                "redirect_uri": "https://evil.attacker.example/phish",
                "scope": "integral",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert 400 <= resp.status_code < 500, resp.text
    assert resp.status_code != 500, resp.text


@pytest.mark.asyncio
async def test_get_consent_no_bearer_is_401(
    bind_fresh_graph_context_for_async_tests,
):
    """No Authorization header -> 401 (the endpoint is auth-gated, not exempt)."""
    client = await _mint_public_client()
    _verifier, challenge = _pkce()

    async with _client() as ac:
        resp = await ac.get(
            "/api/oauth/consent",
            params={
                "response_type": "code",
                "client_id": client.client_id,
                "redirect_uri": client.redirect_uris[0],
                "scope": "integral",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )

    assert resp.status_code in (401, 403), resp.text


@pytest.mark.asyncio
async def test_approve_issues_code_and_token_exchange_succeeds(
    bind_fresh_graph_context_for_async_tests,
):
    """Approve -> {redirect} with code+state; the code exchanges at /token.

    Proves end-to-end that an integral-issued authorization code is accepted by
    jvspatial's own ``POST /api/oauth/token`` (shared DB-persisted records +
    signing key), and that the access token's ``sub`` is the bearer's principal.
    """
    token, uid = await _bootstrap_user_with_permissions(
        email="consent-approve@example.com", permissions=["integral"]
    )
    await _ensure_signing_key()
    client = await _mint_public_client()
    verifier, challenge = _pkce()
    params = {
        "response_type": "code",
        "client_id": client.client_id,
        "redirect_uri": client.redirect_uris[0],
        "scope": "integral",
        "state": "st-approve-1",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }

    async with _client() as ac:
        approved = await ac.post(
            "/api/oauth/consent/approve",
            json={**params, "decision": "approve"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert approved.status_code == 200, approved.text
        redirect = approved.json()["redirect"]
        assert redirect.startswith(client.redirect_uris[0]), redirect
        q = parse_qs(urlparse(redirect).query)
        assert "code" in q, redirect
        assert q.get("state") == ["st-approve-1"], redirect
        code = q["code"][0]

        # Exchange the integral-issued code at jvspatial's token endpoint.
        tok = await ac.post(
            "/api/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": client.redirect_uris[0],
                "client_id": client.client_id,
                "code_verifier": verifier,
            },
        )

    assert tok.status_code == 200, tok.text
    token_body = tok.json()
    access = token_body["access_token"]
    assert access, token_body

    # Decode the access token (RS256, verified against the served JWKS) and
    # assert sub == the bearer's principal and scope ⊆ user permissions.
    import jwt as pyjwt

    async with _client() as ac:
        jwks = (await ac.get("/.well-known/jwks.json")).json()
    header = pyjwt.get_unverified_header(access)
    jwk = next(k for k in jwks["keys"] if k["kid"] == header["kid"])
    public_key = pyjwt.algorithms.RSAAlgorithm.from_jwk(jwk)
    from app.config import settings

    decoded = pyjwt.decode(
        access,
        public_key,
        algorithms=["RS256"],
        audience=settings.OAUTH_ISSUER_URL,
    )
    assert decoded["sub"] == uid, decoded
    granted = set((decoded.get("scope") or "").split())
    assert granted <= {"integral"}, decoded
    assert "integral" in granted, decoded


@pytest.mark.asyncio
async def test_approve_overbroad_scope_request_does_not_escalate(
    bind_fresh_graph_context_for_async_tests,
):
    """An over-broad requested ``scope`` cannot widen the grant past the user.

    The user has ONLY ``integral`` as an effective permission. The client is
    registered for ``integral admin`` and the request asks for ``integral
    admin`` scope. The issued code's scope MUST be the server-computed
    intersection (``{integral}``) — ``admin`` is filtered because the resource
    owner lacks it. The permission set comes solely from the bearer-resolved
    auth user, NEVER from request input.
    """
    token, _uid = await _bootstrap_user_with_permissions(
        email="consent-forged@example.com", permissions=["integral"]
    )
    await _ensure_signing_key()
    client = await _mint_public_client(scope="integral admin")
    verifier, challenge = _pkce()
    params = {
        "response_type": "code",
        "client_id": client.client_id,
        "redirect_uri": client.redirect_uris[0],
        "scope": "integral admin",
        "state": "st-forge",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }

    async with _client() as ac:
        approved = await ac.post(
            "/api/oauth/consent/approve",
            json={**params, "decision": "approve"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert approved.status_code == 200, approved.text
        redirect = approved.json()["redirect"]
        code = parse_qs(urlparse(redirect).query)["code"][0]

        tok = await ac.post(
            "/api/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": client.redirect_uris[0],
                "client_id": client.client_id,
                "code_verifier": verifier,
            },
        )

    assert tok.status_code == 200, tok.text
    access = tok.json()["access_token"]

    import jwt as pyjwt

    async with _client() as ac:
        jwks = (await ac.get("/.well-known/jwks.json")).json()
    header = pyjwt.get_unverified_header(access)
    jwk = next(k for k in jwks["keys"] if k["kid"] == header["kid"])
    public_key = pyjwt.algorithms.RSAAlgorithm.from_jwk(jwk)
    from app.config import settings

    decoded = pyjwt.decode(
        access, public_key, algorithms=["RS256"], audience=settings.OAUTH_ISSUER_URL
    )
    granted = set((decoded.get("scope") or "").split())
    # Escalation blocked: the granted scope is bounded by the user's effective
    # permissions ({integral}); the requested ``admin`` never lands.
    assert "admin" not in granted, decoded
    assert granted <= {"integral"}, decoded


@pytest.mark.asyncio
async def test_approve_zero_permission_user_scope_bound_to_supported_scopes(
    bind_fresh_graph_context_for_async_tests,
):
    """A ZERO-permission user cannot have ``admin`` widened into the grant.

    This is the case the existing escalation test missed: it gave the user
    ``["integral"]`` (a non-empty effective set), so the AS intersected against
    a non-empty list. A genuinely zero-permission user (default ``user`` role,
    NO direct permissions -> empty effective set) would, WITHOUT the
    integral-side bound, trip jvspatial's ``_intersect_scope(requested, [])``
    back-compat branch and have the FULL requested scope (``integral admin``)
    pass through unchanged -> an admin-scoped token for a normal user.

    The fix unions the user's effective permissions with
    ``OAUTH_SUPPORTED_SCOPES`` (``["integral"]``) before handing them to the AS,
    so the grantable list is non-empty and a real ``scope ∩ grantable``
    intersection runs: the issued token carries ``integral`` but NOT ``admin``.
    """
    # NO direct permissions -> empty effective set (default user role).
    token, _uid = await _bootstrap_user_with_permissions(
        email="consent-zero-perm@example.com", permissions=[]
    )
    await _ensure_signing_key()
    client = await _mint_public_client(scope="integral admin")
    verifier, challenge = _pkce()
    params = {
        "response_type": "code",
        "client_id": client.client_id,
        "redirect_uri": client.redirect_uris[0],
        "scope": "integral admin",
        "state": "st-zero",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }

    async with _client() as ac:
        approved = await ac.post(
            "/api/oauth/consent/approve",
            json={**params, "decision": "approve"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert approved.status_code == 200, approved.text
        redirect = approved.json()["redirect"]
        code = parse_qs(urlparse(redirect).query)["code"][0]

        tok = await ac.post(
            "/api/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": client.redirect_uris[0],
                "client_id": client.client_id,
                "code_verifier": verifier,
            },
        )

    assert tok.status_code == 200, tok.text
    access = tok.json()["access_token"]

    import jwt as pyjwt

    async with _client() as ac:
        jwks = (await ac.get("/.well-known/jwks.json")).json()
    header = pyjwt.get_unverified_header(access)
    jwk = next(k for k in jwks["keys"] if k["kid"] == header["kid"])
    public_key = pyjwt.algorithms.RSAAlgorithm.from_jwk(jwk)
    from app.config import settings

    decoded = pyjwt.decode(
        access, public_key, algorithms=["RS256"], audience=settings.OAUTH_ISSUER_URL
    )
    granted = set((decoded.get("scope") or "").split())
    # The empty-list back-compat branch is NOT tripped: the bound (supported
    # scopes) is non-empty, so ``admin`` is filtered and only the base
    # ``integral`` scope (which any authenticated user may grant) lands.
    assert "admin" not in granted, decoded
    assert granted <= {"integral"}, decoded
    assert "integral" in granted, decoded


@pytest.mark.asyncio
async def test_approve_forged_permissions_field_is_rejected_not_honoured(
    bind_fresh_graph_context_for_async_tests,
):
    """A body carrying a ``permissions`` field cannot inject a permission set.

    The approve contract declares ONLY the OAuth params (decision + the
    authorize parameters). An attacker-supplied ``permissions`` field is NOT a
    declared param, so it is rejected by request validation (422) — it can never
    reach the handler to influence the grant. Defence-in-depth: even if it were
    accepted, the handler ignores the body for permission computation (it reads
    the server-resolved auth user only). This pins that the body offers no
    permission-injection surface at all.
    """
    from fastapi.exceptions import RequestValidationError

    token, _uid = await _bootstrap_user_with_permissions(
        email="consent-forged-field@example.com", permissions=["integral"]
    )
    client = await _mint_public_client(scope="integral admin")
    _verifier, challenge = _pkce()
    body = {
        "decision": "approve",
        "response_type": "code",
        "client_id": client.client_id,
        "redirect_uri": client.redirect_uris[0],
        "scope": "integral admin",
        "state": "st-forge-field",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # Forged escalation field — not a declared param.
        "permissions": ["admin", "integral"],
    }

    # The undeclared field is rejected by request validation ("extra inputs are
    # not permitted") before the handler runs — so the body can never inject a
    # permission set. The app's validation surfaces as a 4xx response or (under
    # the in-process ASGI transport's handler-reraise) a propagated
    # RequestValidationError; either way the request does NOT succeed and no
    # forged permission is ever honoured.
    try:
        async with _client() as ac:
            resp = await ac.post(
                "/api/oauth/consent/approve",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
    except RequestValidationError as exc:
        assert any(e.get("loc")[-1] == "permissions" for e in exc.errors()), exc
    else:
        assert resp.status_code == 422, resp.text
        assert "permissions" in resp.text, resp.text


@pytest.mark.asyncio
async def test_deny_redirects_with_access_denied_and_no_code(
    bind_fresh_graph_context_for_async_tests,
):
    """decision=deny -> {redirect} carrying error=access_denied + state, no code."""
    token, _uid = await _bootstrap_user_with_permissions(
        email="consent-deny@example.com", permissions=["integral"]
    )
    client = await _mint_public_client()
    _verifier, challenge = _pkce()
    params = {
        "response_type": "code",
        "client_id": client.client_id,
        "redirect_uri": client.redirect_uris[0],
        "scope": "integral",
        "state": "st-deny-1",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }

    async with _client() as ac:
        denied = await ac.post(
            "/api/oauth/consent/approve",
            json={**params, "decision": "deny"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert denied.status_code == 200, denied.text
    redirect = denied.json()["redirect"]
    q = parse_qs(urlparse(redirect).query)
    assert q.get("error") == ["access_denied"], redirect
    assert q.get("state") == ["st-deny-1"], redirect
    assert "code" not in q, redirect


@pytest.mark.asyncio
async def test_approve_no_bearer_is_401(
    bind_fresh_graph_context_for_async_tests,
):
    """No bearer on approve -> 401 (auth-gated)."""
    client = await _mint_public_client()
    _verifier, challenge = _pkce()

    async with _client() as ac:
        resp = await ac.post(
            "/api/oauth/consent/approve",
            json={
                "decision": "approve",
                "response_type": "code",
                "client_id": client.client_id,
                "redirect_uri": client.redirect_uris[0],
                "scope": "integral",
                "state": "x",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )

    assert resp.status_code in (401, 403), resp.text
