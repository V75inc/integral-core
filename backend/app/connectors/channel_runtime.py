"""Explicit trusted connector contributions; Core does not mount routes here."""

import re
from collections.abc import Mapping
from copy import copy
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.routing import APIRoute, APIRouter
from starlette.routing import Mount, Route, WebSocketRoute

from app.connectors.channel_packages import ChannelPackage, ChannelPackageRegistry


@dataclass(frozen=True)
class ChannelOriginRoutes:
    """An additional route group that must live on its own reviewed origin."""

    origin: str
    routers: tuple[APIRouter, ...]


@dataclass(frozen=True)
class ChannelRuntimeContribution:
    """One connector plus its separately reviewable authenticated and visitor routes."""

    package: ChannelPackage
    management_routes: tuple[APIRouter, ...] = ()
    visitor_routes: tuple[APIRouter, ...] = ()
    isolated_routes: tuple[ChannelOriginRoutes, ...] = ()
    visitor_origin: str | None = None


@dataclass(frozen=True)
class RegisteredChannelRoutes:
    """Mount candidates returned only after catalogue registration succeeds."""

    management: tuple[APIRouter, ...]
    visitor: tuple[APIRouter, ...]
    isolated: tuple[ChannelOriginRoutes, ...]


def register_channel_runtime(
    registry: ChannelPackageRegistry, contribution: ChannelRuntimeContribution
) -> RegisteredChannelRoutes:
    """Register catalogue metadata before returning the opt-in mount groups."""
    if not isinstance(registry, ChannelPackageRegistry) or not isinstance(
        contribution, ChannelRuntimeContribution
    ):
        raise TypeError("A trusted channel runtime contribution is required")
    groups = _route_groups(contribution)
    registry.register(contribution.package)
    return groups


def _route_groups(contribution: ChannelRuntimeContribution) -> RegisteredChannelRoutes:
    if not all(
        isinstance(group, tuple)
        and all(isinstance(router, APIRouter) for router in group)
        for group in (contribution.management_routes, contribution.visitor_routes)
    ):
        raise TypeError("Connector route contributions must be explicit routers")
    if (
        not isinstance(contribution.isolated_routes, tuple)
        or not all(
            isinstance(group, ChannelOriginRoutes)
            and isinstance(group.origin, str)
            and bool(group.origin)
            and isinstance(group.routers, tuple)
            and bool(group.routers)
            and all(isinstance(router, APIRouter) for router in group.routers)
            for group in contribution.isolated_routes
        )
        or len({group.origin for group in contribution.isolated_routes})
        != len(contribution.isolated_routes)
    ):
        raise TypeError("Connector isolated route groups must be explicit and disjoint")
    return RegisteredChannelRoutes(
        contribution.management_routes,
        contribution.visitor_routes,
        contribution.isolated_routes,
    )


def _canonical_origin(value: str) -> bool:
    if not isinstance(value, str) or not re.fullmatch(
        r"https://[a-z0-9.-]{1,253}(?::[0-9]{1,5})?", value
    ):
        return False
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        return False
    labels = (parsed.hostname or "").split(".")
    return (
        all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in labels
        )
        and port not in (0, 443)
        and parsed.netloc
        == (parsed.hostname if port is None else f"{parsed.hostname}:{port}")
    )


def _segments(path: str) -> tuple[str, ...]:
    return tuple(path.split("/")[1:])


def _overlaps(left: str, right: str) -> bool:
    """Conservatively reject intersecting HTTP route shapes, including catch-all."""
    a, b = _segments(left), _segments(right)
    for one, two in zip(a, b):
        if ":path}" in one or ":path}" in two:
            return True
        if "{" not in one and "{" not in two and one != two:
            return False
    return len(a) == len(b) or any(
        ":path}" in part for part in a[len(b) :] + b[len(a) :]
    )


def _check_target_route(existing: object, incoming: APIRoute) -> None:
    if isinstance(existing, WebSocketRoute):
        return
    if isinstance(existing, Mount):
        if _overlaps(f"{existing.path.rstrip('/')}/{{path:path}}", incoming.path):
            raise ValueError("Connector route overlaps a mounted path")
        return
    if (
        not isinstance(existing, Route)
        or not isinstance(existing.path, str)
        or not existing.path.startswith("/")
        or existing.methods is None
    ):
        raise ValueError("Cannot verify existing host route ownership")
    if incoming.methods & existing.methods and _overlaps(existing.path, incoming.path):
        raise ValueError("Connector route overlaps an existing HTTP route")


def _stage(app: FastAPI, routers: tuple[APIRouter, ...]) -> APIRouter:
    staged = copy(app.router)
    staged.routes = list(app.router.routes)
    for router in routers:
        if router.on_startup or router.on_shutdown:
            raise ValueError("Connector routes cannot register lifecycle handlers")
        for candidate in router.routes:
            if (
                not isinstance(candidate, APIRoute)
                or not candidate.path.startswith("/")
                or not candidate.methods
                or ":path}" in candidate.path
            ):
                raise ValueError("Connector contribution requires bounded HTTP routes")
            for existing in staged.routes:
                _check_target_route(existing, candidate)
        # Validation below covers collisions within one router too. Copying the
        # host router preserves its dependency overrides and route defaults.
        before = len(staged.routes)
        staged.include_router(router)
        for index, item in enumerate(staged.routes[before:], start=before):
            for previous in staged.routes[before:index]:
                _check_target_route(previous, item)
    return staged


def mount_trusted_channel_runtime(
    registry: ChannelPackageRegistry,
    contribution: ChannelRuntimeContribution,
    *,
    staff_app: FastAPI,
    staff_origin: str,
    visitor_apps: Mapping[str, FastAPI],
) -> None:
    """Admit routes and package on explicit apps during synchronous host bootstrap.

    The caller selects vetted authenticating staff and scheme/host-enforcing
    public boundaries before requests or OpenAPI generation. No listeners,
    router lifecycle callbacks or distribution discovery are run here.
    """
    if (
        not isinstance(registry, ChannelPackageRegistry)
        or not isinstance(contribution, ChannelRuntimeContribution)
        or not isinstance(staff_app, FastAPI)
        or not isinstance(visitor_apps, Mapping)
    ):
        raise TypeError("An explicit trusted connector bootstrap is required")
    groups = _route_groups(contribution)
    supplied = dict(visitor_apps)
    public: dict[str, tuple[APIRouter, ...]] = {
        group.origin: group.routers for group in groups.isolated
    }
    if groups.visitor:
        origin = contribution.visitor_origin
        if origin is None or origin in public:
            raise ValueError("An exact distinct visitor origin is required")
        public[origin] = groups.visitor
    elif contribution.visitor_origin is not None:
        raise ValueError("Unexpected visitor origin without routes")
    if (
        not _canonical_origin(staff_origin)
        or staff_origin in public
        or set(public) != set(supplied)
        or any(not _canonical_origin(origin) for origin in public)
        or any(not isinstance(target, FastAPI) for target in supplied.values())
        or len({id(target) for target in supplied.values()} | {id(staff_app)})
        != len(supplied) + 1
    ):
        raise ValueError("Connector requires explicit distinct approved origin apps")
    plans = []
    destinations = [(staff_app, groups.management)] + [
        (supplied[origin], routers) for origin, routers in public.items()
    ]
    for target, routers in destinations:
        if routers:
            if target.openapi_schema is not None:
                raise ValueError("Connector routes must be admitted before OpenAPI")
            plans.append((target, _stage(target, routers).routes))
    # All route copying and collision checks completed before a duplicate or
    # invalid package can affect a real host. No await or dynamic HTTP here.
    registry.register(contribution.package)
    for target, routes in plans:
        target.router.routes = routes
