"""Private route lifecycle for the first hosted public text channel.

The future hosted adapter must establish an opaque visitor identity before it
opens a scope. This service never runs a model.
"""

from dataclasses import dataclass

from pydantic import ValidationError

from app.agentive.services import profile_management
from app.agentive.services.connection_profiles import ProfileDenied
from app.agentive.services.profile_host import profile_host
from app.api.errors import BadRequestError
from app.schemas.agentive.connection_profiles import PublicWebChatRoute


def _eligible_publication(profiles, key):
    published = profiles.get(key, {}).get("published")
    if not profile_management.eligible_public_web_chat_profile(published):
        return None
    version = published.get("version")
    if published.get("key") != key or type(version) is not int or version < 1:
        return None
    return published


async def transport_ready(workspace: str) -> bool:
    """Readiness is supplied by the trusted host, never by the request body."""
    try:
        check = profile_host().public_web_chat_ready
        return check is not None and await check(workspace) is True
    except ProfileDenied:
        return False


async def describe(actor: str, workspace: str) -> dict:
    """Return guarded workspace settings for this surface."""
    await profile_management.require_profile_manager(actor, workspace)
    found = await profile_management.record(workspace)
    route = PublicWebChatRoute.model_validate(found.public_web_chat if found else {})
    profiles = found.profiles if found else {}
    available = []
    for key in profiles:
        published = _eligible_publication(profiles, key)
        if published is not None:
            available.append({"key": key, "name": published["name"]})
    ready = await transport_ready(workspace)
    return {
        "revision": found.revision if found else 0,
        "profile_key": route.profile_key,
        "enabled": route.enabled,
        "transport_ready": ready,
        "active": (
            route.enabled
            and ready
            and _eligible_publication(profiles, route.profile_key) is not None
        ),
        "eligible_profiles": sorted(
            available, key=lambda item: (item["name"].casefold(), item["key"])
        ),
    }


async def assign_profile(
    actor: str, workspace: str, revision: int, profile_key: str | None
) -> None:
    """Change the assignment of a disabled route without enabling it."""

    async def assign(profiles, _bindings, _native, route):
        if route.enabled:
            raise BadRequestError("Disable Public Web Chat before changing its profile")
        if profile_key is not None:
            if _eligible_publication(profiles, profile_key) is None:
                raise BadRequestError(
                    "Select a published public profile without tool capabilities"
                )
        if route.profile_key != profile_key:
            route.profile_key = profile_key
            route.generation += 1

    await profile_management.mutate_config(actor, workspace, revision, assign)


async def set_enabled(
    actor: str,
    workspace: str,
    revision: int,
    enabled: bool,
    *,
    transport_ready: bool,
) -> None:
    """Change availability independently. Readiness belongs to the trusted host."""

    async def change(profiles, _bindings, _native, route):
        if enabled:
            if (
                not transport_ready
                or _eligible_publication(profiles, route.profile_key) is None
            ):
                raise BadRequestError(
                    "Public Web Chat needs a ready transport and an eligible published profile"
                )
        if route.enabled != enabled:
            route.enabled = enabled
            route.generation += 1

    await profile_management.mutate_config(actor, workspace, revision, change)


@dataclass(frozen=True)
class PublicWebChatScope:
    """Server-owned conversation association, scoped to the hosted visitor."""

    workspace_id: str
    visitor_id: str
    profile_key: str
    published_version: int
    route_generation: int


async def admit(
    workspace: str,
    visitor: str,
    *,
    transport_ready: bool,
    existing: PublicWebChatScope | None = None,
) -> PublicWebChatScope:
    """Check admission before loading history or starting a model turn."""

    if not workspace or not visitor or not transport_ready:
        raise ProfileDenied("Public Web Chat unavailable")
    try:
        found = await profile_management.record(workspace)
        route = PublicWebChatRoute.model_validate(
            found.public_web_chat if found else {}
        )
    except (BadRequestError, ValidationError):
        raise ProfileDenied("Public Web Chat unavailable") from None
    if not route.enabled or not route.profile_key or found is None:
        raise ProfileDenied("Public Web Chat unavailable")
    published = _eligible_publication(found.profiles, route.profile_key)
    if published is None:
        raise ProfileDenied("Public Web Chat unavailable")
    scope = PublicWebChatScope(
        workspace, visitor, route.profile_key, published["version"], route.generation
    )
    if existing is not None and existing != scope:
        raise ProfileDenied("Conversation unavailable")
    return scope
