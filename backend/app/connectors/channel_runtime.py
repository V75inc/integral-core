"""Explicit trusted connector contributions; Core does not mount routes here."""

from dataclasses import dataclass

from fastapi.routing import APIRouter

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
    registry.register(contribution.package)
    return RegisteredChannelRoutes(
        contribution.management_routes,
        contribution.visitor_routes,
        contribution.isolated_routes,
    )
