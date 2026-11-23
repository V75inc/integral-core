"""Discover a trusted, mounted and effect-free managed share check."""

from typing import Awaitable, Callable

from fastapi import Request
from fastapi.routing import APIRoute
from starlette.routing import Match

from app.schemas.agentive.managed_channels import ManageChannelShareQuery

SHARE_READ_PATH = "/api/agentive/managed-channels/{slug}/share"
ShareCheck = Callable[[Request, str, ManageChannelShareQuery], Awaitable[bool]]
_MARKER = "_integral_effect_free_managed_share_check"


def bind_mounted_share_check(endpoint: Callable, check: ShareCheck) -> None:
    """Attach a server-owned readiness read to the actual share endpoint."""
    setattr(endpoint, _MARKER, check)


async def mounted_share_check_available(
    request: Request, slug: str, exact: ManageChannelShareQuery
) -> bool:
    """Report only a current proof from the first eligible mounted GET route."""
    path = SHARE_READ_PATH.format(slug=slug)
    scope = {
        **request.scope,
        "path": path,
        "raw_path": path.encode("ascii"),
        "method": "GET",
        "root_path": "",
    }
    matches = [
        route
        for route in request.app.router.routes
        if route.matches(scope)[0] is Match.FULL
    ]
    if not matches:
        return False
    first = matches[0]
    if (
        not isinstance(first, APIRoute)
        or first.path != SHARE_READ_PATH
        or sum(
            isinstance(route, APIRoute) and route.path == SHARE_READ_PATH
            for route in matches
        )
        != 1
    ):
        return False
    check = getattr(first.endpoint, _MARKER, None)
    if not callable(check):
        return False
    try:
        return await check(request, slug, exact) is True
    except Exception:
        # Unavailable/stale share proof does not prevent normal configuration.
        return False
