"""Discover a trusted, mounted and effect-free managed share check."""

from dataclasses import dataclass
from typing import Awaitable, Callable

from fastapi import Request
from fastapi.routing import APIRoute
from starlette.routing import Match

from app.schemas.agentive.managed_channels import ManageChannelShareQuery

SHARE_READ_PATH = "/api/agentive/managed-channels/{slug}/share"
PRIVATE_TEST_PATH = "/api/agentive/managed-channels/{slug}/test"
EMBED_SHARE_READ_PATH = "/api/agentive/managed-channels/{slug}/embed-share"
MAX_EMBED_DESCRIPTORS = 8
ShareCheck = Callable[[Request, str, ManageChannelShareQuery], Awaitable[bool]]
_MARKER = "_integral_effect_free_managed_share_check"
_TEST_MARKER = "_integral_private_managed_chat_test_check"
_EMBED_MARKER = "_integral_effect_free_managed_embed_descriptors"


@dataclass(frozen=True)
class ApprovedEmbedDescriptor:
    """Management-safe approved origin and its exact policy revision."""

    parent_origin: str
    embed_policy_revision: int


EmbedDiscovery = Callable[
    [Request, str, ManageChannelShareQuery],
    Awaitable[tuple[ApprovedEmbedDescriptor, ...]],
]


def bind_mounted_embed_discovery(endpoint: Callable, discover: EmbedDiscovery) -> None:
    """Attach a read-only descriptor discovery to the existing embed share reader."""
    setattr(endpoint, _EMBED_MARKER, discover)


def bind_mounted_share_check(endpoint: Callable, check: ShareCheck) -> None:
    """Attach a server-owned readiness read to the actual share endpoint."""
    setattr(endpoint, _MARKER, check)


def _mounted_reader(
    request: Request, slug: str, template: str, marker: str, method: str = "GET"
) -> Callable | None:
    path = template.format(slug=slug)
    scope = {
        **request.scope,
        "path": path,
        "raw_path": path.encode("ascii"),
        "method": method,
        "root_path": "",
    }
    matches = [
        route
        for route in request.app.router.routes
        if route.matches(scope)[0] is Match.FULL
    ]
    if not matches:
        return None
    first = matches[0]
    if (
        not isinstance(first, APIRoute)
        or first.path != template
        or sum(
            isinstance(route, APIRoute) and route.path == template for route in matches
        )
        != 1
    ):
        return None
    check = getattr(first.endpoint, marker, None)
    return check if callable(check) else None


ClientOriginRead = Callable[[str, ManageChannelShareQuery], Awaitable[str | None]]
_CLIENT_MARKER = "_integral_managed_client_origin"


def bind_client_origin(endpoint: Callable, read: ClientOriginRead) -> None:
    """Attach the configured client-origin reader to the admitted endpoint."""
    setattr(endpoint, _CLIENT_MARKER, read)


async def mounted_client_origin(
    request: Request, slug: str, exact: ManageChannelShareQuery
) -> str | None:
    """Resolve the mounted channel’s configured client origin."""
    read = _mounted_reader(request, slug, SHARE_READ_PATH, _CLIENT_MARKER)
    if read is None:
        return None
    try:
        return await read(slug, exact)
    except Exception:
        return None


def bind_mounted_private_test(
    endpoint: Callable, check: ShareCheck, model_id: str = "host-private-model"
) -> None:
    """Attach private test availability to the exact mounted manager route."""

    async def model(request, slug, exact):
        return model_id if await check(request, slug, exact) is True else None

    setattr(endpoint, _TEST_MARKER, model)


async def mounted_private_test_model(
    request: Request, slug: str, exact: ManageChannelShareQuery
) -> str | None:
    """Read the approved model identifier without invoking the model."""
    check = _mounted_reader(request, slug, PRIVATE_TEST_PATH, _TEST_MARKER, "POST")
    if check is None:
        return None
    try:
        return await check(request, slug, exact)
    except Exception:
        return None


async def mounted_embed_descriptors(
    request: Request, slug: str, exact: ManageChannelShareQuery
) -> tuple[ApprovedEmbedDescriptor, ...]:
    """Read metadata only through the exact mounted effect-free discovery."""
    discover = _mounted_reader(request, slug, EMBED_SHARE_READ_PATH, _EMBED_MARKER)
    if discover is None:
        return ()
    try:
        value = await discover(request, slug, exact)
        if (
            isinstance(value, tuple)
            and len(value) <= MAX_EMBED_DESCRIPTORS
            and all(isinstance(entry, ApprovedEmbedDescriptor) for entry in value)
        ):
            return value
    except Exception:
        pass
    return ()


async def mounted_share_check_available(
    request: Request, slug: str, exact: ManageChannelShareQuery
) -> bool:
    """Report only a current proof from the first eligible mounted GET route."""
    check = _mounted_reader(request, slug, SHARE_READ_PATH, _MARKER)
    if check is None:
        return False
    try:
        return await check(request, slug, exact) is True
    except Exception:
        # Unavailable/stale share proof does not prevent normal configuration.
        return False
