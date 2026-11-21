"""App-neutral trusted connection host contract.

Hosts register during boot. Workspace owners select only eligible connections;
registration is never accepted through an HTTP request or model arguments.
"""

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.agentive.services.connection_profiles import ProfileDenied


@dataclass(frozen=True)
class ProfileHost:
    catalogue: dict[str, dict[str, Any]]
    # A host may set publish_assignable=True only when
    # changing the profile binding is sufficient; no provider-side work runs.
    # Verification fixtures are never selectable through that workflow.
    connections: Callable[[str], Awaitable[list[dict[str, Any]]]]
    resolve_principal: Callable[..., Awaitable[Any]]
    authorize: Callable[..., Awaitable[bool]]
    handlers: Callable[..., dict]
    # Set only by a host that implements the managed hosted visitor transport.
    # The standalone deterministic preview never registers here.
    public_web_chat_ready: Callable[[str], Awaitable[bool]] | None = None


_host: ProfileHost | None = None


def register_profile_host(host: ProfileHost) -> None:
    """Register the trusted deployment profile host once."""
    global _host
    if _host is not None:
        raise ValueError("Connection profile host already registered")
    _host = host


def profile_host() -> ProfileHost:
    """Return the configured deployment profile host or deny."""
    if _host is None:
        raise ProfileDenied("No eligible connection host is configured")
    return _host
