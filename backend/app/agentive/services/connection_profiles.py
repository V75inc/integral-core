"""Server-owned execution boundary for explicitly provisioned connection profiles.

This opt-in boundary is independent of model, persona and transport. Hosts supply
current authenticated grants and narrow handlers; it does not confer substrate
permissions or bypass a handler's canonical policy/staging checks. No public
connection is provisioned by Core's personal WhatsApp bridge.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Mapping


class ProfileDenied(PermissionError):
    """The connection's current server policy does not admit this operation."""


@dataclass(frozen=True)
class ConnectionProfile:
    """A server declaration; omissions always win over the allowlist."""

    key: str
    audience: Literal["public", "staff"]
    tools: frozenset[str]
    omitted_tools: frozenset[str] = frozenset()
    revision: str = "1"
    human_only_tools: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ConnectionGrant:
    """Current host authority, resolved without trusting message/profile claims."""

    connection_id: str
    principal_id: str
    workspace_id: str
    profile_key: str
    audience: Literal["public", "staff"]
    verified_staff: bool = False
    enabled: bool = True
    service_principal_id: str = ""


@dataclass(frozen=True)
class ProfileSession:
    """Opaque scope token; history and retrieval must use this complete scope."""

    scope_key: str


GrantResolver = Callable[[str, str], Awaitable[ConnectionGrant | None]]
Handler = Callable[[ConnectionGrant, ProfileSession, Mapping[str, Any]], Awaitable[Any]]
Authorizer = Callable[[ConnectionGrant, str], Awaitable[bool]]


class ConnectionProfileExecutor:
    """Intersect server profile and fresh host authority on every operation.

    The host's handlers retain normal resource policy and write review. Public
    handlers must be separate published projections or narrow create-only flows;
    never register generic workspace tools for a public profile. This registry
    is deliberately not populated from prompts, tool arguments or App personas.
    """

    def __init__(
        self,
        profiles: tuple[ConnectionProfile, ...],
        resolve_grant: GrantResolver,
        authorize: Authorizer,
        handlers: Mapping[str, Handler],
    ) -> None:
        self._profiles = {profile.key: profile for profile in profiles}
        if len(self._profiles) != len(profiles):
            raise ValueError("Duplicate profile key")
        self._resolve_grant = resolve_grant
        self._authorize = authorize
        self._handlers = dict(handlers)
        for profile in profiles:
            if (
                not profile.key
                or not profile.revision
                or profile.audience not in {"public", "staff"}
                or not profile.human_only_tools <= profile.tools
                or not profile.tools <= self._handlers.keys()
            ):
                raise ValueError("Invalid profile declaration")

    async def _current(self, connection_id: str, principal_id: str):
        grant = await self._resolve_grant(connection_id, principal_id)
        if (
            grant is None
            or not grant.enabled
            or not connection_id
            or not principal_id
            or grant.connection_id != connection_id
            or grant.principal_id != principal_id
            or not grant.workspace_id
        ):
            raise ProfileDenied("Connection unavailable")
        profile = self._profiles.get(grant.profile_key)
        if (
            profile is None
            or grant.audience != profile.audience
            or (profile.audience == "staff" and not grant.verified_staff)
        ):
            raise ProfileDenied("Profile unavailable")
        # JSON encoding avoids delimiter collisions; profile revision invalidates
        # history/confirmation state when policy changes. No phone number is a
        # principal here: identity establishment belongs to the trusted host.
        scope = json.dumps(
            [
                connection_id,
                principal_id,
                grant.workspace_id,
                profile.key,
                profile.audience,
                profile.revision,
                grant.service_principal_id,
            ],
            separators=(",", ":"),
        )
        session = ProfileSession(hashlib.sha256(scope.encode()).hexdigest())
        return grant, profile, session

    async def open(self, connection_id: str, principal_id: str) -> ProfileSession:
        """Resolve the server-selected profile; callers cannot select a persona."""
        _, _, session = await self._current(connection_id, principal_id)
        return session

    async def execute(
        self,
        connection_id: str,
        principal_id: str,
        session: ProfileSession,
        tool: str,
        arguments: Mapping[str, Any],
        *,
        origin: Literal["agent", "human"] = "agent",
    ) -> Any:
        """Re-resolve scope and authority, including for reads and confirmations.

        ``origin`` is supplied by authenticated host routing, never deserialized
        from model arguments. Human-only operations cannot run on the default
        agent execution path, even if their arguments claim confirmation.
        """
        grant = await self.authorize_operation(
            connection_id, principal_id, session, tool, origin=origin
        )
        return await self._handlers[tool](grant, session, arguments)

    async def current_grant(
        self, connection_id: str, principal_id: str, session: ProfileSession
    ) -> ConnectionGrant:
        """Revalidate the bound session before native history/model access."""
        grant, _, current = await self._current(connection_id, principal_id)
        if session != current:
            raise ProfileDenied("Session unavailable")
        return grant

    async def authorize_operation(
        self,
        connection_id: str,
        principal_id: str,
        session: ProfileSession,
        tool: str,
        *,
        origin: Literal["agent", "human"] = "agent",
    ) -> ConnectionGrant:
        """Check native disclosure/execution without invoking a handler."""
        grant, profile, current_session = await self._current(
            connection_id, principal_id
        )
        if (
            session != current_session
            or origin not in {"agent", "human"}
            or (tool in profile.human_only_tools and origin != "human")
            or tool not in profile.tools
            or tool in profile.omitted_tools
            or not await self._authorize(grant, tool)
        ):
            raise ProfileDenied("Operation unavailable")
        return grant
