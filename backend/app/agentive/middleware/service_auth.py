"""Service-to-service authentication middleware for agentive API calls.

Validates the X-Integral-Service-Key header against INTEGRAL_SERVICE_KEY,
then sets request.state.user to the user identified by X-Integral-User-Id.
This allows jvagent's IntegralAction to call Integral's API on behalf of
a specific user without requiring a user JWT.

D-01 (Plan 01-02): HMAC signature + timestamp are MANDATORY on every
user-context path. Only `/api/agentive/uplink/register-system` and
`/api/agentive/uplink/heartbeat-system` accept a bare service-key without
a signature; every other agentive path requires the full signed envelope.

D-02 (Plan 01-02): Auto-create of graph User nodes on a service-auth call
with an unknown X-Integral-User-Id is gated by env var
`INTEGRAL_SERVICE_AUTO_CREATE_USERS` (default OFF). Flag-on emits an INFO
log line carrying caller-key fingerprint + the new auth_user_id; flag-off
returns 404 + the canonical envelope error_code
`agentive.auth.user_node_missing`.

D-04 (Plan 01-03): JWT handoff. After a service-auth request is verified
the middleware mints a short-lived JWT for the resolved user and injects
it into the request as `Authorization: Bearer <jwt>`. The downstream
jvspatial AuthenticationMiddleware then validates the JWT through its
normal path so endpoints decorated with `auth=True` (e.g. /api/tracks,
/api/entries, /api/apps, /api/auth/me) accept the call without any
per-route changes. The handoff token is tagged in the JWT payload
(`svc_auth=True`, `caller_key_fp=<8-char fingerprint>`) for forensics.

Security: INTEGRAL_SERVICE_KEY must be a strong secret shared between
Integral and jvagent. Never expose it to clients. The raw key never
appears in logs — `_key_fingerprint()` emits the first 8 chars of
`sha256(service_key)` for operational identity without leakage.
"""

import hashlib
import hmac
import logging
import os
import time
from typing import Any, Callable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

_SERVICE_KEY: Optional[str] = None
_SERVICE_KEY_LOADED = False

# D-01 user-context exemption set: only these two paths may be called with
# a bare service-key (no HMAC signature + timestamp).
_SIGNATURE_EXEMPT_PATHS = (
    "/api/agentive/uplink/register-system",
    "/api/agentive/uplink/heartbeat-system",
)


def _get_service_key() -> Optional[str]:
    global _SERVICE_KEY, _SERVICE_KEY_LOADED
    if not _SERVICE_KEY_LOADED:
        _SERVICE_KEY = os.getenv("INTEGRAL_SERVICE_KEY", "").strip() or None
        _SERVICE_KEY_LOADED = True
    return _SERVICE_KEY


def _reset_service_key_cache() -> None:
    """Test-helper: clear the cached service key so a monkeypatched env can take effect."""
    global _SERVICE_KEY, _SERVICE_KEY_LOADED
    _SERVICE_KEY = None
    _SERVICE_KEY_LOADED = False


def verify_service_key(provided_key: str) -> bool:
    """Constant-time comparison of the provided key against the configured service key."""
    expected = _get_service_key()
    if not expected:
        return False
    return hmac.compare_digest(provided_key.encode("utf-8"), expected.encode("utf-8"))


def compute_signature(timestamp: str, user_id: str) -> str:
    """Canonical HMAC-SHA256 over `{timestamp}.{user_id}` with the service key.

    Production middleware AND test fixtures share this function — a single
    canonicalization site keeps signature drift impossible.
    """
    key = _get_service_key() or ""
    return hmac.new(
        key.encode("utf-8"),
        f"{timestamp}.{user_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _key_fingerprint() -> str:
    """First 8 chars of sha256(service_key) — used for log audit without leaking the secret."""
    key = _get_service_key()
    if not key:
        return "<no-key>"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def _envelope_response(
    *,
    request: Request,
    http_status: int,
    error_code: str,
    message: str,
    details: Any,
) -> JSONResponse:
    """Emit the canonical 5-key error envelope (D-03) from middleware.

    Middleware short-circuits before route resolution so the FastAPI
    422-handler at `app.agentive.api.errors.install_agentive_error_handlers`
    never fires for these errors. This sibling helper produces the same
    shape so error responses are uniform across the layer.
    """
    return JSONResponse(
        status_code=http_status,
        content={
            "error_code": error_code,
            "message": message,
            "details": details,
            "timestamp": utc_now_iso(),
            "path": str(request.url.path),
        },
    )


class ServiceAuthMiddleware(BaseHTTPMiddleware):
    """Middleware that authenticates service-to-service requests.

    Intercepts requests with the X-Integral-Service-Key header and, if valid,
    resolves the X-Integral-User-Id header to set request.state.user so that
    downstream handlers see an authenticated user context.

    Only applies to /api/agentive/* paths. Other paths are unaffected.

    Mandatory signature path (D-01): every user-context path requires a valid
    X-Integral-Timestamp + X-Integral-Signature pair. The signature is
    HMAC-SHA256(service_key, `{timestamp}.{user_id}`) (see compute_signature),
    and the timestamp must be within MAX_TIMESTAMP_SKEW seconds of server time.
    Only register-system and heartbeat-system are exempt.

    Auto-create gate (D-02): when the user_id resolves to no graph User node,
    auto-create only proceeds if `INTEGRAL_SERVICE_AUTO_CREATE_USERS=1`
    (read at request-time so env mutations apply immediately). Flag-off
    returns 404 + `agentive.auth.user_node_missing`. Flag-on emits an INFO
    log carrying caller-key fingerprint + auth_user_id + user_node_id.
    """

    MAX_TIMESTAMP_SKEW = 300  # 5 minutes

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Authenticate service requests and inject a resolved user context."""
        service_key = request.headers.get("x-integral-service-key", "")
        user_id = request.headers.get("x-integral-user-id", "")
        signature = request.headers.get("x-integral-signature", "")
        timestamp = request.headers.get("x-integral-timestamp", "")

        if not service_key:
            return await call_next(request)

        # Docstring contract: service auth only on /api/agentive/* — never mint
        # a handoff JWT for the rest of the API surface.
        path = request.url.path
        if not (path == "/api/agentive" or path.startswith("/api/agentive/")):
            return await call_next(request)

        if not _get_service_key():
            logger.warning(
                "Service auth attempted but INTEGRAL_SERVICE_KEY not configured"
            )
            return _envelope_response(
                request=request,
                http_status=500,
                error_code="agentive.auth.service_key_not_configured",
                message="Service authentication not configured",
                details=None,
            )

        if not verify_service_key(service_key):
            logger.warning(
                "Invalid service key from %s",
                request.client.host if request.client else "unknown",
            )
            return _envelope_response(
                request=request,
                http_status=401,
                error_code="agentive.auth.invalid_service_key",
                message="Invalid service key",
                details=None,
            )

        is_exempt_path = path.endswith(_SIGNATURE_EXEMPT_PATHS)

        # System uplink: exempt path may proceed with bare service-key.
        if not user_id and is_exempt_path:
            return await call_next(request)

        # D-01 mandatory signature path — every non-exempt request must carry a
        # valid HMAC signature + timestamp tuple. No fallback to optional sig.
        if not is_exempt_path:
            if not signature or not timestamp:
                missing = [
                    h
                    for h, v in (
                        ("X-Integral-Signature", signature),
                        ("X-Integral-Timestamp", timestamp),
                    )
                    if not v
                ]
                return _envelope_response(
                    request=request,
                    http_status=401,
                    error_code="agentive.auth.signature_required",
                    message="HMAC signature and timestamp required on user-context paths",
                    details={"missing_headers": missing},
                )

            try:
                ts = int(timestamp)
                now_unix = int(time.time())
                if abs(now_unix - ts) > self.MAX_TIMESTAMP_SKEW:
                    logger.warning(
                        "service_auth: stale timestamp on %s (skew=%ds, key_fp=%s)",
                        path,
                        abs(now_unix - ts),
                        _key_fingerprint(),
                    )
                    return _envelope_response(
                        request=request,
                        http_status=401,
                        error_code="agentive.auth.signature_stale",
                        message="Timestamp too old or too far in future",
                        details={"max_skew_seconds": self.MAX_TIMESTAMP_SKEW},
                    )
            except (ValueError, TypeError):
                return _envelope_response(
                    request=request,
                    http_status=401,
                    error_code="agentive.auth.signature_invalid",
                    message="Invalid timestamp format",
                    details=None,
                )

            expected_sig = compute_signature(timestamp, user_id)
            if not hmac.compare_digest(signature, expected_sig):
                logger.warning(
                    "service_auth: signature mismatch on %s for user_id=%s, key_fp=%s",
                    path,
                    user_id,
                    _key_fingerprint(),
                )
                return _envelope_response(
                    request=request,
                    http_status=401,
                    error_code="agentive.auth.signature_invalid",
                    message="Signature verification failed",
                    details=None,
                )

        if not user_id:
            return _envelope_response(
                request=request,
                http_status=400,
                error_code="agentive.auth.missing_user_id",
                message="X-Integral-User-Id header required with service key",
                details=None,
            )

        # Full Sweep S1: optional impersonation allowlist. When configured,
        # the service key may only mint handoff JWTs for listed principals.
        allowed_raw = os.getenv("INTEGRAL_SERVICE_ALLOWED_USER_IDS", "").strip()
        if not allowed_raw:
            try:
                from app.config import settings as _settings

                allowed_raw = (
                    _settings.INTEGRAL_SERVICE_ALLOWED_USER_IDS or ""
                ).strip()
            except Exception:
                allowed_raw = ""
        if allowed_raw:
            allowed = {p.strip() for p in allowed_raw.split(",") if p.strip()}
            if user_id not in allowed:
                logger.warning(
                    "service_auth: user_id=%s rejected by allowlist key_fp=%s",
                    user_id,
                    _key_fingerprint(),
                )
                return _envelope_response(
                    request=request,
                    http_status=403,
                    error_code="agentive.auth.user_not_allowed",
                    message="Service key is not permitted to act as this user",
                    details={"user_id": user_id},
                )

        from jvspatial.api.auth.models import User as AuthUser

        from app.models.nodes import User

        user_node = await User.get(user_id)
        if not user_node and ("@" in user_id or not user_id.startswith("u_")):
            auth_user = await AuthUser.get(user_id)
            if auth_user:
                nodes = await User.find({"context.user_id": auth_user.id})
                user_node = nodes[0] if nodes else None

        # D-02 auto-create gate. Read env at request-time so monkeypatch.setenv
        # applies without importlib.reload.
        if not user_node:
            auto_create = os.getenv(
                "INTEGRAL_SERVICE_AUTO_CREATE_USERS", "0"
            ).lower() in ("1", "true", "yes")
            if not auto_create:
                return _envelope_response(
                    request=request,
                    http_status=404,
                    error_code="agentive.auth.user_node_missing",
                    message="User not found — auto-create disabled",
                    details={"user_id": user_id},
                )

            # Flag is on — auto-create the graph User node. MANDATORY INFO log.
            try:
                auth_user = await AuthUser.get(user_id)
                if auth_user is None:
                    # No upstream AuthUser — cannot derive display_name. Fail-closed.
                    logger.warning(
                        "service_auth: auto-create blocked — no AuthUser for %s",
                        user_id,
                    )
                    return _envelope_response(
                        request=request,
                        http_status=404,
                        error_code="agentive.auth.user_node_missing",
                        message="User not found — no upstream auth record",
                        details={"user_id": user_id},
                    )

                now_iso = utc_now_iso()
                user_node = await User.create(
                    user_id=auth_user.id,
                    display_name=auth_user.name or auth_user.email or "",
                    created_at=now_iso,
                    updated_at=now_iso,
                )
                # I-GRAPH-01: catalog under Users registry so the new
                # User node is reachable from Root → IntegralApp → Users.
                # Mirrors the canonical signup path in app/api/auth.py.
                # Fail closed with rollback — never leave an orphan User.
                try:
                    from app.services.app_graph import catalog_user

                    await catalog_user(user_node)
                except Exception as e:
                    logger.warning(
                        "service_auth: catalog_user failed for %s: %s; "
                        "rolling back orphaned User",
                        user_node.id,
                        e,
                    )
                    try:
                        await user_node.delete()
                    except Exception:
                        logger.exception(
                            "service_auth: rollback delete failed for user=%s",
                            user_node.id,
                        )
                    return _envelope_response(
                        request=request,
                        http_status=500,
                        error_code="agentive.auth.user_node_missing",
                        message="Failed to provision user",
                        details=None,
                    )
                logger.info(
                    "service_auth: auto-created User graph node",
                    extra={
                        "auto_create_used": True,
                        "caller_key_fingerprint": _key_fingerprint(),
                        "auth_user_id": auth_user.id,
                        "user_node_id": user_node.id,
                        "path": str(request.url.path),
                    },
                )
            except Exception as e:
                logger.warning(
                    "service_auth: auto-create failed for %s: %s", user_id, e
                )
                return _envelope_response(
                    request=request,
                    http_status=500,
                    error_code="agentive.auth.user_node_missing",
                    message="Failed to provision user",
                    details=None,
                )

        from datetime import datetime

        from jvspatial.api.auth.models import UserResponse

        from app.utils.time import utc_now

        created_at_raw = getattr(user_node, "created_at", None) or utc_now_iso()
        if isinstance(created_at_raw, str):
            try:
                created_at_dt: datetime = datetime.fromisoformat(created_at_raw)
            except (ValueError, TypeError):
                created_at_dt = utc_now()
        elif isinstance(created_at_raw, datetime):
            created_at_dt = created_at_raw
        else:
            created_at_dt = utc_now()

        auth_user_id = getattr(user_node, "user_id", None) or user_node.id
        try:
            auth_user_obj = await AuthUser.get(auth_user_id) if auth_user_id else None
        except Exception as e:
            logger.warning(
                "service_auth: AuthUser lookup failed for %s: %s", auth_user_id, e
            )
            auth_user_obj = None

        resolved_id = str(auth_user_id or user_node.id)
        resolved_email = getattr(auth_user_obj, "email", "") if auth_user_obj else ""
        resolved_roles = (
            list(getattr(auth_user_obj, "roles", []) or [])
            if auth_user_obj
            else ["user"]
        )
        resolved_permissions = (
            list(getattr(auth_user_obj, "permissions", []) or [])
            if auth_user_obj
            else []
        )

        request.state.user = UserResponse(
            id=resolved_id,
            email=resolved_email,
            name=(
                getattr(auth_user_obj, "name", "")
                if auth_user_obj
                else getattr(user_node, "display_name", "")
            ),
            created_at=created_at_dt,
            is_active=True,
            roles=resolved_roles,
            permissions=resolved_permissions,
        )
        request.state.service_auth = True

        # D-04 — JWT handoff. Mint a short-lived JWT for the resolved user
        # and inject it as the Authorization header so the downstream
        # jvspatial AuthenticationMiddleware validates the request through
        # its normal JWT path. Without this, `request.state.user` is
        # silently dropped on `auth=True`-decorated endpoints (jvspatial
        # only honours pre-set state when auth_config.test_mode=True).
        try:
            handoff_token = _mint_handoff_token(
                user_id=resolved_id,
                email=resolved_email,
                roles=resolved_roles,
                permissions=resolved_permissions,
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.error(
                "service_auth: JWT handoff mint failed for %s (key_fp=%s): %s",
                resolved_id,
                _key_fingerprint(),
                exc,
                exc_info=True,
            )
            return _envelope_response(
                request=request,
                http_status=500,
                error_code="agentive.auth.handoff_mint_failed",
                message="Service auth succeeded but JWT handoff failed",
                details=None,
            )

        _replace_authorization_header(request, handoff_token)

        response = await call_next(request)
        return response


def _mint_handoff_token(
    *,
    user_id: str,
    email: str,
    roles: list,
    permissions: list,
) -> str:
    """Mint a short-lived JWT carrying the resolved service-auth identity.

    Reuses jvspatial's :class:`AuthenticationService` so the token signs
    with the same secret + algorithm that downstream
    :class:`AuthenticationMiddleware` validates against. Adds two custom
    claims for forensics:

    - ``svc_auth=True`` — distinguishes this token from a real user login
    - ``caller_key_fp`` — first 8 chars of sha256(INTEGRAL_SERVICE_KEY)

    The expiration honours :class:`AuthenticationService.jwt_expire_minutes`
    (typically 60). The token is single-request — never cached or returned
    to a client — so a long expiry is harmless; we keep the default for
    code simplicity rather than burning seconds on a per-request override.
    """
    from jvspatial.api.auth.service import AuthenticationService
    from jvspatial.core.context import GraphContext
    from jvspatial.db import get_prime_database

    from app.config import settings

    ctx = GraphContext(database=get_prime_database())
    auth_service = AuthenticationService(ctx, jwt_secret=settings.SECRET_KEY)
    token, _expires_at = auth_service._generate_jwt_token(
        user_id=user_id,
        email=email,
        roles=roles,
        permissions=permissions,
    )

    # Re-encode with the two extra claims. Cheaper than subclassing the
    # service or duplicating its claim logic; we decode and re-issue with
    # the same secret/algorithm.
    import jwt as _jwt

    payload = _jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[auth_service.jwt_algorithm],
    )
    payload["svc_auth"] = True
    payload["caller_key_fp"] = _key_fingerprint()
    return _jwt.encode(
        payload, settings.SECRET_KEY, algorithm=auth_service.jwt_algorithm
    )


def _replace_authorization_header(request: Request, token: str) -> None:
    """Replace any existing Authorization header in the ASGI scope.

    Starlette's :pyclass:`Request.headers` is a read-only view backed by
    ``scope["headers"]`` (a list of ``(name, value)`` byte tuples). We
    mutate the scope list in place so downstream middleware sees the new
    header. ``Request.headers`` is not cached aggressively — re-reading it
    after the swap reflects the change.
    """
    bearer = f"Bearer {token}".encode("utf-8")
    auth_name = b"authorization"
    new_headers: list[tuple[bytes, bytes]] = []
    for name, value in request.scope.get("headers", []):
        if name.lower() == auth_name:
            continue
        new_headers.append((name, value))
    new_headers.append((auth_name, bearer))
    request.scope["headers"] = new_headers
