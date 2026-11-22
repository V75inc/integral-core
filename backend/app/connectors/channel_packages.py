"""Versioned managed channel package registration, separate from sync and MCP.

Only reviewed bootstrap code may register a package. This module does not
mount transports or authorize management requests, and has no registrations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from inspect import iscoroutinefunction
from threading import Lock
from typing import Awaitable, Callable, Literal
from urllib.parse import urlsplit

from app.agentive.connectors.external_chat import VerifiedExternalChatMessage

_SLUG = re.compile(r"[a-z][a-z0-9_]{0,63}")
ChannelState = Literal["registered", "inactive", "unavailable", "active"]
ServingState = Literal["ready", "unavailable", "accepting"]


def _text(value: str) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip()


@dataclass(frozen=True)
class ChannelDefinition:
    """Non-secret catalogue metadata for a workspace singleton channel."""

    slug: str
    provider: str
    display_name: str
    description: str
    setup_kind: Literal["managed-text-v1"]
    version: Literal[1] = 1


@dataclass(frozen=True)
class ChannelScope:
    """Verified manager and workspace supplied by the owning application."""

    actor_id: str
    workspace_id: str


@dataclass(frozen=True)
class ChannelInstallation:
    """Owning service's explicit durable provision, not profile settings."""

    workspace_id: str
    slug: str
    provision_id: str
    route_epoch: str
    enabled: bool


@dataclass(frozen=True)
class ChannelServingStatus:
    """Fresh serving evidence scoped to the exact observed provision or absence."""

    workspace_id: str
    slug: str
    provision_id: str | None
    route_epoch: str | None
    state: ServingState


Observe = Callable[[ChannelScope], Awaitable[ChannelInstallation | None]]
Install = Callable[[ChannelScope], Awaitable[ChannelInstallation]]
SetEnabled = Callable[[ChannelScope, ChannelInstallation, bool], Awaitable[None]]
Remove = Callable[[ChannelScope, ChannelInstallation], Awaitable[None]]
Probe = Callable[
    [ChannelScope, ChannelInstallation | None], Awaitable[ChannelServingStatus]
]
Receive = Callable[[VerifiedExternalChatMessage], Awaitable[None]]
FullPageLink = Callable[[ChannelScope, ChannelInstallation], Awaitable[str | None]]


@dataclass(frozen=True)
class ChannelLifecycle:
    """Complete lifecycle callbacks; owners enforce authorization and CAS."""

    observe: Observe
    install: Install
    set_enabled: SetEnabled
    remove: Remove


@dataclass(frozen=True)
class ChannelServing:
    """Concrete ingress and readiness for an inbound text runtime."""

    probe: Probe
    receive: Receive
    full_page_link: FullPageLink | None = None


@dataclass(frozen=True)
class ChannelPackage:
    """A definition, durable lifecycle, and serving contract supplied together."""

    definition: ChannelDefinition
    lifecycle: ChannelLifecycle
    serving: ChannelServing


@dataclass(frozen=True)
class ChannelListing:
    """Redacted, generic Add/Configure/Connected metadata for one workspace."""

    definition: ChannelDefinition
    state: ChannelState
    connected: bool
    action: Literal["add", "configure"]
    can_enable: bool
    full_page_url: str | None = None


def _validate_package(package: ChannelPackage) -> None:
    if not isinstance(package, ChannelPackage):
        raise TypeError("A complete channel package is required")
    definition, lifecycle, serving = (
        package.definition,
        package.lifecycle,
        package.serving,
    )
    if (
        not isinstance(definition, ChannelDefinition)
        or not _text(definition.slug)
        or not _SLUG.fullmatch(definition.slug)
        or not all(
            _text(value)
            for value in (
                definition.provider,
                definition.display_name,
                definition.description,
            )
        )
        or type(definition.version) is not int
        or definition.version != 1
        or definition.setup_kind != "managed-text-v1"
    ):
        raise ValueError("Invalid managed channel catalogue definition")
    if not isinstance(lifecycle, ChannelLifecycle) or not isinstance(
        serving, ChannelServing
    ):
        raise TypeError("A channel requires lifecycle and serving adapters")
    callbacks = (
        lifecycle.observe,
        lifecycle.install,
        lifecycle.set_enabled,
        lifecycle.remove,
        serving.probe,
        serving.receive,
    )
    if not all(iscoroutinefunction(fn) for fn in callbacks) or (
        serving.full_page_link is not None
        and not iscoroutinefunction(serving.full_page_link)
    ):
        raise TypeError("Channel adapter operations must be async callables")


def _valid_installation(
    installation: ChannelInstallation, scope: ChannelScope, slug: str
) -> bool:
    return (
        isinstance(installation, ChannelInstallation)
        and installation.workspace_id == scope.workspace_id
        and installation.slug == slug
        and _text(installation.provision_id)
        and _text(installation.route_epoch)
        and type(installation.enabled) is bool
    )


def _valid_evidence(
    evidence: ChannelServingStatus,
    scope: ChannelScope,
    slug: str,
    installation: ChannelInstallation | None,
) -> bool:
    return (
        isinstance(evidence, ChannelServingStatus)
        and evidence.workspace_id == scope.workspace_id
        and evidence.slug == slug
        and evidence.provision_id
        == (installation.provision_id if installation else None)
        and evidence.route_epoch == (installation.route_epoch if installation else None)
        and evidence.state in ("ready", "unavailable", "accepting")
    )


def _safe_public_link(value: str | None) -> str | None:
    """Allow only a server-issued public HTTPS URL, not credentials or embed code."""
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or any(not char.isascii() or char.isspace() or ord(char) < 32 for char in value)
        or any(char in value for char in "\\?#")
    ):
        return None
    try:
        parts = urlsplit(value)
        if (
            parts.scheme == "https"
            and parts.hostname
            and parts.username is None
            and parts.password is None
            and parts.port != 0
        ):
            return value
    except ValueError:
        pass
    return None


class ChannelPackageRegistry:
    """Atomic in-process catalogue; all persistent state belongs to adapters."""

    def __init__(self) -> None:
        self._packages: dict[str, ChannelPackage] = {}
        self._lock = Lock()

    def register(self, package: ChannelPackage) -> None:
        """Validate the whole package before publishing its unique identity."""
        _validate_package(package)
        slug = package.definition.slug
        with self._lock:
            if slug in self._packages:
                raise ValueError("Channel package already registered")
            self._packages[slug] = package

    def resolve(self, slug: str) -> ChannelPackage | None:
        """Return the complete registered adapter to trusted composition code."""
        with self._lock:
            return self._packages.get(slug)

    async def discover(
        self, scope: ChannelScope, *, include_sharing: bool = False
    ) -> list[ChannelListing]:
        """Project state for an already-authorized management scope.

        A future endpoint must authorize discovery before calling it and request
        sharing only after its separate permission check. A stored
        provision never follows from enablement, assignment, or registry
        presence. A readiness, sharing, or adapter fault cannot grant access.
        """
        if not isinstance(scope, ChannelScope) or not (
            _text(scope.workspace_id) and _text(scope.actor_id)
        ):
            return []
        with self._lock:
            packages = tuple(sorted(self._packages.items()))
        listings = []
        for _, package in packages:
            listing = await _project(scope, package, include_sharing=include_sharing)
            if listing is not None:
                listings.append(listing)
        return listings


async def _project(
    scope: ChannelScope, package: ChannelPackage, *, include_sharing: bool
) -> ChannelListing | None:
    definition = package.definition
    try:
        installation = await package.lifecycle.observe(scope)
    except Exception:
        # Unknown installation is not permission to advertise Add or share.
        return None
    if installation is not None and not _valid_installation(
        installation, scope, definition.slug
    ):
        return None
    unavailable = (
        ChannelListing(definition, "unavailable", True, "configure", False)
        if installation is not None
        else None
    )
    try:
        evidence = await package.serving.probe(scope, installation)
        if not _valid_evidence(evidence, scope, definition.slug, installation):
            return unavailable
        if await package.lifecycle.observe(scope) != installation:
            # Don't pair one version's health with another's provision.
            # A subsequent discovery will read the updated installation.
            return None
        if installation is None:
            if evidence.state == "ready":
                return ChannelListing(definition, "registered", False, "add", False)
            return None
        if evidence.state == "ready" and not installation.enabled:
            return ChannelListing(definition, "inactive", True, "configure", True)
        if evidence.state != "accepting" or not installation.enabled:
            return unavailable

        link = None
        if include_sharing and package.serving.full_page_link:
            try:
                link = _safe_public_link(
                    await package.serving.full_page_link(scope, installation)
                )
            except Exception:
                # Optional sharing can fail independently of an active channel.
                link = None
            current = await package.serving.probe(scope, installation)
            if await package.lifecycle.observe(scope) != installation:
                return None
            if current != evidence:
                return unavailable
        return ChannelListing(definition, "active", True, "configure", False, link)
    except Exception:
        return unavailable
