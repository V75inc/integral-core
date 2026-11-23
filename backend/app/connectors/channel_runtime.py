"""Explicit trusted connector contributions; Core does not mount routes here."""

from dataclasses import dataclass

from fastapi.routing import APIRouter

from app.connectors.channel_packages import ChannelPackage, ChannelPackageRegistry


@dataclass(frozen=True)
class ChannelRuntimeContribution:
    """One connector plus its separately reviewable authenticated and visitor routes."""

    package: ChannelPackage
    management_routes: tuple[APIRouter, ...] = ()
    visitor_routes: tuple[APIRouter, ...] = ()


@dataclass(frozen=True)
class RegisteredChannelRoutes:
    """Mount candidates returned only after catalogue registration succeeds."""

    management: tuple[APIRouter, ...]
    visitor: tuple[APIRouter, ...]


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
    registry.register(contribution.package)
    return RegisteredChannelRoutes(
        contribution.management_routes, contribution.visitor_routes
    )
