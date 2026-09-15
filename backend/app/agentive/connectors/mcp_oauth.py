"""Outbound MCP OAuth 2.1 (authorization code + PKCE) for capability mounts.

Authenticated Streamable-HTTP MCP servers (Notion, etc.) answer the
initial initialize POST with 401 + ``WWW-Authenticate`` resource metadata.
This module runs protected-resource / authorization-server discovery,
dynamic client registration, PKCE, and token exchange — then stores the
result under ``Connector.auth_state["oauth"]``.

Some hosts (Google Drive MCP) return 200 on unauthenticated initialize and
publish no OAuth metadata. Those packages declare a pre-registered client
in catalog YAML; ``start_pre_registered_oauth_session`` still stores tokens
in the same ``auth_state["oauth"]`` shape so the existing callback works.

The MCP SDK's ``OAuthClientProvider`` is an httpx Auth hook that *blocks*
the caller on ``redirect_handler`` / ``callback_handler``. FastAPI cannot
wait on a browser round-trip inside a request, so the flow is split like
Gmail/QuickBooks: start → popup → callback.

Tokens, ``client_secret``, and ``code_verifier`` never appear in logs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlparse

import httpx
from mcp.client.auth.oauth2 import PKCEParameters
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    create_client_registration_request,
    create_oauth_metadata_request,
    extract_resource_metadata_from_www_auth,
    extract_scope_from_www_auth,
    get_client_metadata_scopes,
    handle_auth_metadata_response,
    handle_protected_resource_response,
    handle_registration_response,
    handle_token_response_scopes,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata
from mcp.shared.auth_utils import resource_url_from_server_url
from mcp.types import LATEST_PROTOCOL_VERSION
from pydantic import AnyHttpUrl

from app.api.errors import BadRequestError, ConnectorAuthError
from app.services.url_safety import guarded_async_client, validate_outbound_http_url

logger = logging.getLogger(__name__)

_STATE_TTL_SECONDS = 600
_PROBE_TIMEOUT_SECONDS = 15.0
OAUTH_STATUS_PENDING = "pending"
OAUTH_STATUS_AUTHORIZED = "authorized"

MCP_OAUTH_CALLBACK_PATH = "/settings/connectors/mcp/oauth/callback"


def mcp_oauth_redirect_uri() -> str:
    """Frontend callback URL for the MCP OAuth popup."""
    from app.config import settings

    override = (settings.MCP_OAUTH_REDIRECT_URI or "").strip()
    if override:
        return override.rstrip("/")
    origin = (settings.FRONTEND_ORIGIN or "http://localhost:9006").rstrip("/")
    return f"{origin}{MCP_OAUTH_CALLBACK_PATH}"


def google_oauth_client_credentials() -> Tuple[str, str]:
    """Pre-registered Google OAuth app. Falls back to the Gmail client."""
    from app.config import settings

    client_id = (
        (settings.GOOGLE_OAUTH_CLIENT_ID or "")
        or os.getenv("GOOGLE_OAUTH_CLIENT_ID")
        or (settings.GMAIL_OAUTH_CLIENT_ID or "")
        or os.getenv("GMAIL_OAUTH_CLIENT_ID")
        or ""
    ).strip()
    client_secret = (
        (settings.GOOGLE_OAUTH_CLIENT_SECRET or "")
        or os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
        or (settings.GMAIL_OAUTH_CLIENT_SECRET or "")
        or os.getenv("GMAIL_OAUTH_CLIENT_SECRET")
        or ""
    ).strip()
    return client_id, client_secret


def start_pre_registered_oauth_session(
    *,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
    authorization_endpoint: str,
    token_endpoint: str,
    scopes: List[str],
    extra_authorize_params: Optional[Dict[str, str]] = None,
    token_endpoint_auth_method: str = "client_secret_post",
) -> Dict[str, Any]:
    """PKCE session for a catalog-declared OAuth client (no DCR / 401 probe).

    Google Drive MCP returns 200 on unauthenticated initialize, so MCP OAuth
    discovery never starts. The catalog supplies Google's authorize/token
    URLs and Drive scopes; tokens still live under ``auth_state.oauth``.
    """
    pkce = PKCEParameters.generate()
    extra = {
        str(k): str(v)
        for k, v in dict(extra_authorize_params or {}).items()
        if str(k).strip() and str(v).strip()
    }
    return {
        "status": OAUTH_STATUS_PENDING,
        "authorization_endpoint": authorization_endpoint,
        "token_endpoint": token_endpoint,
        "client_id": client_id,
        "client_secret": client_secret,
        "token_endpoint_auth_method": token_endpoint_auth_method
        or "client_secret_post",
        "code_verifier": pkce.code_verifier,
        "code_challenge": pkce.code_challenge,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "include_resource": False,
        "extra_authorize_params": extra,
    }


def redact_mcp_auth_state(auth_state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Owner-visible slice of an MCP ``auth_state`` (ADR-009 §6).

    Delegates to the single allowlist implementation. This function used to
    carry a second, divergent copy of the rule and had no callers, while the
    wire path went through a weaker denylist — one redactor, one behaviour.
    """
    from app.schemas.agentive.connectors import (
        _MCP_AUTH_STATE_KEY_ONLY_MAPS,
        mcp_safe_auth_state,
    )

    safe = mcp_safe_auth_state(dict(auth_state or {}))
    # Strictest slice: not even the NAMES of configured env vars / headers.
    return {k: v for k, v in safe.items() if k not in _MCP_AUTH_STATE_KEY_ONLY_MAPS}


def _signing_key() -> bytes:
    # ``settings.SECRET_KEY`` reads the canonical ``JVSPATIAL_JWT_SECRET_KEY``
    # (the same trust root as JWTs). The previous ``or "integral-dev-secret"``
    # fallback meant a deployment that never set the variable signed its MCP
    # OAuth CSRF state with a value published in this repository — i.e. anyone
    # could forge a state token binding their code to another user's
    # connector. Same fix already applied to gmail_oauth / quickbooks_oauth.
    from app.config import settings

    secret = getattr(settings, "SECRET_KEY", None) or ""
    if not secret:
        raise RuntimeError(
            "JVSPATIAL_JWT_SECRET_KEY is not configured — cannot sign OAuth state"
        )
    return hashlib.sha256(str(secret).encode("utf-8")).digest()


def _b64_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64_decode(s: str) -> bytes:
    padded = s + "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def sign_mcp_oauth_state(user_id: str, connector_id: str) -> str:
    """HMAC-signed CSRF state bound to user + connector with a short TTL."""
    nonce = secrets.token_urlsafe(8)
    expires_at = int(time.time()) + _STATE_TTL_SECONDS
    payload = f"{user_id}:{connector_id}:{nonce}:{expires_at}"
    mac = hmac.new(_signing_key(), payload.encode("utf-8"), hashlib.sha256).digest()
    return _b64_encode(payload.encode("utf-8")) + "." + _b64_encode(mac)


def verify_mcp_oauth_state(state: str, user_id: str) -> Optional[str]:
    """Return connector_id when state is valid for this user, else None."""
    if not state or "." not in state:
        return None
    try:
        payload_b64, signature = state.rsplit(".", 1)
        payload = _b64_decode(payload_b64).decode("utf-8")
        parts = payload.split(":")
        if len(parts) != 4:
            return None
        bound_user, connector_id, _nonce, expires_at_str = parts
        expires_at = int(expires_at_str)
    except ValueError:
        return None
    expected = _b64_encode(
        hmac.new(_signing_key(), payload.encode("utf-8"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(expected, signature):
        return None
    if expires_at < int(time.time()):
        return None
    if bound_user != user_id:
        return None
    return connector_id


@dataclass
class McpAuthProbe:
    status_code: int
    needs_oauth: bool
    www_authenticate: Optional[str]
    response: httpx.Response


async def probe_mcp_authorization(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> McpAuthProbe:
    """POST a minimal initialize. 401 without a caller-supplied Bearer → OAuth."""
    probe_headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": LATEST_PROTOCOL_VERSION,
    }
    if headers:
        probe_headers.update(headers)
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "integral", "version": "0"},
        },
    }
    own_client = client is None
    http = client or guarded_async_client(
        timeout=_PROBE_TIMEOUT_SECONDS, follow_redirects=False
    )
    try:
        response = await http.post(url, headers=probe_headers, json=body)
    finally:
        if own_client:
            await http.aclose()
    supplied_auth = bool((headers or {}).get("Authorization"))
    needs_oauth = response.status_code == 401 and not supplied_auth
    return McpAuthProbe(
        status_code=response.status_code,
        needs_oauth=needs_oauth,
        www_authenticate=response.headers.get("WWW-Authenticate"),
        response=response,
    )


def _auth_base_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


# ---------------------------------------------------------------------------
# F-3 — discovery is attacker-influenced input.
#
# Everything below the initial 401 comes from the remote MCP server: the
# ``resource_metadata`` URL in its ``WWW-Authenticate`` header, the
# ``authorization_servers`` in the PRM document it serves, and the
# ``registration_endpoint`` / ``authorization_endpoint`` / ``token_endpoint``
# in the AS metadata that answers. Those last three receive the authorization
# ``code``, the ``code_verifier`` and the ``client_secret`` — so a server that
# can name an arbitrary host can both walk the backend around the network AND
# have Integral hand its credentials to that host.
#
# Two independent controls, both required:
#   * every candidate goes through ``validate_outbound_http_url`` (no private
#     / link-local / metadata addresses, http(s) only, port 80/443);
#   * origin binding — PRM and AS metadata must be same-origin with the MCP
#     server URL (RFC 9728 / RFC 8414 place them there), and each endpoint the
#     flow POSTs to must be on the discovered issuer's own host.
#
# ``INTEGRAL_MCP_OAUTH_TRUSTED_HOSTS`` (comma-separated) is the operator
# escape hatch for a provider that legitimately delegates to a separate
# identity host.
# ---------------------------------------------------------------------------


def _trusted_oauth_hosts() -> frozenset:
    raw = (os.environ.get("INTEGRAL_MCP_OAUTH_TRUSTED_HOSTS") or "").strip()
    if not raw:
        return frozenset()
    return frozenset(chunk.strip().lower() for chunk in raw.split(",") if chunk.strip())


def _origin(url: Any) -> str:
    parsed = urlparse(str(url or ""))
    host = (parsed.hostname or "").lower()
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return f"{parsed.scheme.lower()}://{host}:{port}"


def _host(url: Any) -> str:
    return (urlparse(str(url or "")).hostname or "").lower()


def _require_same_origin(candidate: Any, reference: str, label: str) -> None:
    """Refuse a discovery URL that is not on the MCP server's own origin."""
    if _origin(candidate) == _origin(reference):
        return
    if _host(candidate) in _trusted_oauth_hosts():
        return
    raise BadRequestError(
        message=(
            f"Refusing MCP OAuth {label}: {_host(candidate) or 'unknown host'} is "
            f"not the MCP server's origin ({_host(reference)}). Add it to "
            "INTEGRAL_MCP_OAUTH_TRUSTED_HOSTS if this delegation is intended."
        ),
        details={"reason": "oauth_discovery_origin_mismatch", "label": label},
    )


def _require_same_host(candidate: Any, reference: Any, label: str) -> None:
    """Refuse an endpoint whose host differs from the authorization server's."""
    if _host(candidate) and _host(candidate) == _host(reference):
        return
    if _host(candidate) in _trusted_oauth_hosts():
        return
    raise BadRequestError(
        message=(
            f"Refusing MCP OAuth {label}: {_host(candidate) or 'unknown host'} is "
            f"not the authorization server's host ({_host(reference)})."
        ),
        details={"reason": "oauth_endpoint_host_mismatch", "label": label},
    )


async def _guard_discovery_url(candidate: Any, reference: str, label: str) -> str:
    """Origin-bind + SSRF-validate one discovery URL before it is fetched."""
    url = str(candidate or "")
    _require_same_origin(url, reference, label)
    await validate_outbound_http_url(url)
    return url


async def _discover_prm(
    mcp_url: str,
    probe_response: httpx.Response,
    http: httpx.AsyncClient,
) -> Any:
    www_url = extract_resource_metadata_from_www_auth(probe_response)
    if www_url:
        # Server-supplied and fetched with no checks at all before F-3.
        _require_same_origin(www_url, mcp_url, "resource_metadata URL")
    for candidate in build_protected_resource_metadata_discovery_urls(www_url, mcp_url):
        await _guard_discovery_url(candidate, mcp_url, "protected-resource metadata")
        request = create_oauth_metadata_request(candidate)
        response = await http.send(request)
        metadata = await handle_protected_resource_response(response)
        if metadata is not None:
            return metadata
    return None


async def _discover_as_metadata(
    mcp_url: str,
    auth_server_url: Optional[str],
    http: httpx.AsyncClient,
) -> Any:
    if auth_server_url:
        _require_same_origin(auth_server_url, mcp_url, "authorization server")
    for candidate in build_oauth_authorization_server_metadata_discovery_urls(
        auth_server_url, mcp_url
    ):
        await _guard_discovery_url(candidate, mcp_url, "authorization-server metadata")
        request = create_oauth_metadata_request(candidate)
        response = await http.send(request)
        keep_going, metadata = await handle_auth_metadata_response(response)
        if metadata is not None:
            return metadata
        if not keep_going:
            break
    return None


async def start_mcp_oauth_session(
    mcp_url: str,
    *,
    probe_response: httpx.Response,
    redirect_uri: str,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """PRM + AS discovery + DCR + PKCE. Returns a persistable oauth dict."""
    own_client = client is None
    http = client or guarded_async_client(
        timeout=_PROBE_TIMEOUT_SECONDS, follow_redirects=False
    )
    try:
        prm = await _discover_prm(mcp_url, probe_response, http)
        auth_server_url = None
        if prm is not None and getattr(prm, "authorization_servers", None):
            auth_server_url = str(prm.authorization_servers[0])
        as_metadata = await _discover_as_metadata(mcp_url, auth_server_url, http)
        if as_metadata is None:
            raise BadRequestError(
                message=(
                    "This MCP server requires OAuth but did not publish "
                    "authorization-server metadata. Paste a Bearer token in the "
                    "Authorization header, or register Integral as an OAuth client "
                    "with the provider."
                )
            )
        registration_endpoint = getattr(as_metadata, "registration_endpoint", None)
        if not registration_endpoint:
            raise BadRequestError(
                message=(
                    "This MCP server requires OAuth but does not allow dynamic "
                    "client registration. Provide a Bearer token in the "
                    "Authorization header."
                )
            )
        # Every endpoint below receives credential material (the DCR POST, and
        # later the ``code`` + ``code_verifier`` + ``client_secret`` at the
        # token endpoint). Bind each to the issuer that the AS metadata itself
        # declares, and SSRF-validate before anything is sent (F-3).
        issuer = getattr(as_metadata, "issuer", None) or _auth_base_url(
            auth_server_url or mcp_url
        )
        _require_same_origin(issuer, mcp_url, "issuer")
        for label, endpoint_url in (
            ("registration_endpoint", registration_endpoint),
            ("authorization_endpoint", as_metadata.authorization_endpoint),
            ("token_endpoint", as_metadata.token_endpoint),
        ):
            _require_same_host(endpoint_url, issuer, label)
            await validate_outbound_http_url(str(endpoint_url))
        www_scope = extract_scope_from_www_auth(probe_response)
        scope = get_client_metadata_scopes(www_scope, prm, as_metadata)
        client_metadata = OAuthClientMetadata(
            redirect_uris=[AnyHttpUrl(redirect_uri)],
            client_name="Integral MCP Connector",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
            scope=scope,
        )
        reg_request = create_client_registration_request(
            as_metadata, client_metadata, _auth_base_url(str(registration_endpoint))
        )
        await validate_outbound_http_url(str(registration_endpoint))
        reg_response = await http.send(reg_request)
        client_info: OAuthClientInformationFull = await handle_registration_response(
            reg_response
        )
        pkce = PKCEParameters.generate()
        if prm is not None and getattr(prm, "resource", None):
            resource = str(prm.resource)
        else:
            resource = resource_url_from_server_url(mcp_url)
        logger.info("mcp_oauth: DCR succeeded for host %s", urlparse(mcp_url).netloc)
        return {
            "status": OAUTH_STATUS_PENDING,
            "resource": resource,
            # Persisted so the token/refresh POSTs can re-bind to the origin
            # discovery already validated — ``auth_state`` is writable, and a
            # rewritten ``token_endpoint`` otherwise receives the auth code,
            # the PKCE verifier and the client secret (F-3).
            "issuer": str(issuer),
            "authorization_endpoint": str(as_metadata.authorization_endpoint),
            "token_endpoint": str(as_metadata.token_endpoint),
            "client_id": client_info.client_id,
            "client_secret": client_info.client_secret,
            "token_endpoint_auth_method": (
                client_info.token_endpoint_auth_method or "none"
            ),
            "code_verifier": pkce.code_verifier,
            "code_challenge": pkce.code_challenge,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "include_resource": prm is not None,
        }
    finally:
        if own_client:
            await http.aclose()


def authorization_url_for_oauth(oauth: Dict[str, Any], state: str) -> str:
    """Build the IdP authorize URL from a pending oauth session dict."""
    params = {
        "response_type": "code",
        "client_id": oauth["client_id"],
        "redirect_uri": oauth["redirect_uri"],
        "state": state,
        "code_challenge": oauth["code_challenge"],
        "code_challenge_method": "S256",
    }
    if oauth.get("scope"):
        params["scope"] = str(oauth["scope"])
    if oauth.get("include_resource") and oauth.get("resource"):
        params["resource"] = str(oauth["resource"])
    extra = oauth.get("extra_authorize_params") or {}
    if isinstance(extra, dict):
        reserved = frozenset(params)
        for key, value in extra.items():
            name = str(key).strip()
            if not name or name in reserved or value is None:
                continue
            params[name] = str(value)
    return f"{oauth['authorization_endpoint']}?{urlencode(params)}"


def _token_request_auth(
    oauth: Dict[str, Any], data: Dict[str, str]
) -> Tuple[Dict[str, str], Dict[str, str]]:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    method = oauth.get("token_endpoint_auth_method") or "none"
    secret = oauth.get("client_secret") or ""
    if method == "client_secret_basic" and oauth.get("client_id") and secret:
        raw = f"{oauth['client_id']}:{secret}".encode("utf-8")
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
    elif secret:
        data["client_secret"] = str(secret)
    return data, headers


def _tokens_from_oauth_token(token: Any) -> Dict[str, Any]:
    expires_in = getattr(token, "expires_in", None)
    expires_at = None
    if expires_in:
        expires_at = int(time.time()) + int(expires_in)
    return {
        "access_token": token.access_token,
        "refresh_token": getattr(token, "refresh_token", None),
        "token_type": getattr(token, "token_type", None) or "Bearer",
        "expires_at": expires_at,
        "scope": getattr(token, "scope", None),
    }


async def _guard_token_endpoint(oauth: Dict[str, Any]) -> str:
    """Return the token endpoint, bound to the origin discovery validated.

    ``validate_outbound_http_url`` alone only rejects private/link-local
    targets — an attacker-supplied *public* ``token_endpoint`` written into
    ``auth_state`` passes it and then receives the authorization code, the
    PKCE verifier and the client secret. Re-bind to the persisted ``issuer``
    (falling back to the authorization endpoint for rows written before the
    issuer was stored) so a rewritten endpoint cannot be reached at all.
    """
    endpoint = str(oauth.get("token_endpoint") or "")
    reference = str(oauth.get("issuer") or oauth.get("authorization_endpoint") or "")
    if not reference:
        # Fail closed. Making the bind conditional on a reference being present
        # hands the bypass straight back: ``auth_state`` is writable, so an
        # attacker simply PATCHes an ``oauth`` blob with a ``token_endpoint``
        # and no ``issuer`` and the same-origin check never runs.
        raise ConnectorAuthError(
            message=(
                "MCP OAuth state is missing its issuer; re-authorize this "
                "connector before exchanging tokens"
            ),
            details={"reauth_required": True, "reason": "missing_issuer"},
        )
    _require_same_origin(endpoint, reference, "token_endpoint")
    await validate_outbound_http_url(endpoint)
    return endpoint


async def exchange_mcp_oauth_code(
    oauth: Dict[str, Any],
    code: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """Trade the authorization code for tokens. Secrets are never logged."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": str(oauth["redirect_uri"]),
        "client_id": str(oauth["client_id"]),
        "code_verifier": str(oauth["code_verifier"]),
    }
    if oauth.get("include_resource") and oauth.get("resource"):
        data["resource"] = str(oauth["resource"])
    data, headers = _token_request_auth(oauth, data)
    # This POST carries the authorization code, the PKCE verifier and (when
    # the client is confidential) the client secret. Re-validate the persisted
    # endpoint before sending: the connector row may have been written before
    # discovery was origin-bound, or patched since (F-3).
    token_endpoint = await _guard_token_endpoint(oauth)
    own_client = client is None
    http = client or guarded_async_client(
        timeout=_PROBE_TIMEOUT_SECONDS, follow_redirects=False
    )
    try:
        response = await http.post(token_endpoint, data=data, headers=headers)
        if response.status_code != 200:
            logger.info(
                "mcp_oauth: token exchange failed status=%s", response.status_code
            )
            raise ConnectorAuthError(
                message="MCP OAuth token exchange failed",
                details={"reauth_required": True, "status": response.status_code},
            )
        token = await handle_token_response_scopes(response)
        return _tokens_from_oauth_token(token)
    finally:
        if own_client:
            await http.aclose()


async def refresh_mcp_oauth_tokens(
    oauth: Dict[str, Any],
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """Refresh expired MCP OAuth tokens; keep the prior refresh token if omitted."""
    tokens = dict(oauth.get("tokens") or {})
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise ConnectorAuthError(
            message="MCP OAuth refresh token is missing",
            details={"reauth_required": True},
        )
    data = {
        "grant_type": "refresh_token",
        "refresh_token": str(refresh_token),
        "client_id": str(oauth["client_id"]),
    }
    if oauth.get("include_resource") and oauth.get("resource"):
        data["resource"] = str(oauth["resource"])
    data, headers = _token_request_auth(oauth, data)
    token_endpoint = await _guard_token_endpoint(oauth)
    own_client = client is None
    http = client or guarded_async_client(
        timeout=_PROBE_TIMEOUT_SECONDS, follow_redirects=False
    )
    try:
        response = await http.post(token_endpoint, data=data, headers=headers)
        if response.status_code != 200:
            logger.info(
                "mcp_oauth: token refresh failed status=%s", response.status_code
            )
            raise ConnectorAuthError(
                message="MCP OAuth token refresh failed",
                details={"reauth_required": True, "status": response.status_code},
            )
        token = await handle_token_response_scopes(response)
        merged = _tokens_from_oauth_token(token)
        if not merged.get("refresh_token"):
            merged["refresh_token"] = refresh_token
        return merged
    finally:
        if own_client:
            await http.aclose()


def bearer_headers_from_oauth(oauth: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Authorization header from stored tokens, or empty when none."""
    if not oauth:
        return {}
    tokens = oauth.get("tokens") or {}
    access = tokens.get("access_token")
    if not access:
        return {}
    token_type = str(tokens.get("token_type") or "Bearer")
    if token_type.lower() == "bearer":
        token_type = "Bearer"
    return {"Authorization": f"{token_type} {access}"}


def mcp_request_headers(auth_state: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Merge static headers with a stored OAuth access token (token wins last)."""
    auth = dict(auth_state or {})
    raw = auth.get("headers") or {}
    headers = {str(k): str(v) for k, v in dict(raw).items() if k and v}
    for key, value in bearer_headers_from_oauth(auth.get("oauth")).items():
        headers[key] = value
    return headers


def oauth_access_expired(
    oauth: Optional[Dict[str, Any]], *, skew_seconds: int = 30
) -> bool:
    """True when access_token expiry is at or past now plus skew."""
    if not oauth:
        return False
    tokens = oauth.get("tokens") or {}
    expires_at = tokens.get("expires_at")
    if not expires_at:
        return False
    try:
        return int(expires_at) <= int(time.time()) + skew_seconds
    except (TypeError, ValueError):
        return False
