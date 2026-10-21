"""Loopback transport adapter for a private installation runtime."""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.responses import JSONResponse

from app.config import settings
from app.runtime.composition import runtime


def validate_process(workers: int) -> None:
    """Validate transport prerequisites before binding an application listener."""
    import os

    from app.runtime.composition import owner_ids

    if runtime().preset != "local":
        return
    owner_ids()
    origin = urlsplit(settings.INTEGRAL_DESKTOP_WEB_ORIGIN)
    try:
        valid_origin = (
            origin.scheme == "http"
            and origin.hostname == "127.0.0.1"
            and origin.port is not None
            and not any(
                (
                    origin.username,
                    origin.password,
                    origin.path,
                    origin.query,
                    origin.fragment,
                )
            )
        )
    except ValueError:
        valid_origin = False
    if (
        workers != 1
        or os.getenv("JVSPATIAL_HOST") != "127.0.0.1"
        or len(settings.INTEGRAL_DESKTOP_CONTROL_TOKEN) < 32
        or os.getenv("TESTING")
        or not valid_origin
    ):
        raise RuntimeError(
            "Local identity requires managed loopback transport, a controller and one authenticated worker"
        )


def blocked_path(path: str, method: str) -> bool:
    """Close legacy, public and platform identity surfaces in desktop mode."""
    parts = path.strip("/").split("/")
    if parts and parts[0] == "api":
        parts = parts[1:]
    if not parts:
        return False
    if parts[0] == "auth":
        operations = {
            ("local-session", "POST"),
            ("me", "GET"),
            ("refresh", "POST"),
            ("logout", "POST"),
            ("update-profile", "PUT"),
            ("ws-ticket", "POST"),
        }
        return len(parts) != 2 or (parts[1], method) not in operations
    if parts[0] in {"public", "shares", "invitations", "shared", "organizations"}:
        return True
    if parts[0] == "admin" and len(parts) > 1 and parts[1] in {"users", "workspaces"}:
        return True
    if parts[0] == "users" and method not in {"GET", "HEAD", "OPTIONS"}:
        # Local settings still use authenticated, caller-bound /users/me routes.
        # Keep identity/provisioning and unknown user mutations closed; this
        # transport exception grants no principal or resource authority.
        local_settings = {
            ("pinned", "POST"),
            ("scope", "PUT"),
            ("timezone", "PATCH"),
            ("notification-preferences", "PATCH"),
            ("speech-preferences", "PATCH"),
            ("model-credentials", "POST"),
            ("model-credentials", "DELETE"),
            ("model-credentials/validate", "POST"),
        }
        return (
            len(parts) < 3
            or parts[1] != "me"
            or ("/".join(parts[2:]), method) not in local_settings
        )
    if method in {"GET", "HEAD", "OPTIONS"}:
        return any(
            part in {"public-share", "invitations", "ownership-proposals", "shared"}
            for part in parts
        )
    return any(
        part
        in {
            "members",
            "membership",
            "invitations",
            "collaborators",
            "exclusions",
            "shares",
            "share-links",
            "public-share",
            "transfer-ownership",
            "ownership-proposals",
            "ownership-transfers",
            "shared",
            "provision-member",
        }
        for part in parts
    )


class LocalTransport:
    """Apply before route auth, including to public paths and WebSockets."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if runtime().preset != "local" or scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        origin = settings.INTEGRAL_DESKTOP_WEB_ORIGIN
        web = urlsplit(origin)
        host = headers.get(b"host", b"").decode("latin1")
        server = scope.get("server") or ("", 0)
        valid_hosts = {web.netloc, f"127.0.0.1:{server[1]}"}
        supplied_origin = headers.get(b"origin")
        allowed = (
            web.scheme == "http"
            and web.hostname == "127.0.0.1"
            and host in valid_hosts
            and (supplied_origin is None or supplied_origin.decode("latin1") == origin)
            and headers.get(b"sec-fetch-site") not in {b"cross-site"}
            and not blocked_path(scope.get("path", ""), scope.get("method", "GET"))
        )
        if not allowed:
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            return await JSONResponse(
                {
                    "error_code": "deployment_profile_denied",
                    "message": "This operation is unavailable in the local desktop installation.",
                },
                status_code=403,
            )(scope, receive, send)
        return await self.app(scope, receive, send)
