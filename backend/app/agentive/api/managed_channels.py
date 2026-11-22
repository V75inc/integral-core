"""Authenticated, explicitly composed managed channel catalogue and lifecycle."""

from dataclasses import asdict
from typing import Awaitable, TypeVar

from fastapi import FastAPI, Request
from jvspatial.api import endpoint

from app.agentive.services.profile_management import require_profile_manager
from app.api.errors import (
    BadRequestError,
    MissingAuthenticationError,
    ResourceConflictError,
    ResourceNotFoundError,
    ServiceUnavailableError,
)
from app.api.utils import resolve_principal_id
from app.connectors.channel_packages import (
    PROFILE_CONFIGURATION,
    ChannelInstallation,
    ChannelPackage,
    ChannelPackageRegistry,
    ChannelProfileConfiguration,
    ChannelProfileOption,
    ChannelProfileSetup,
    ChannelScope,
)
from app.schemas.agentive.managed_channels import (
    ManageChannelEnablement,
    ManageChannelProfileAssignment,
    ManageChannelProvision,
)
from app.services.permissions import get_user_node
from app.services.scope_header import parse_scope_header

_PATH = "/agentive/managed-channels"
_STATE_KEY = "integral_managed_channel_registry"


def install_managed_channel_registry(
    app: FastAPI, registry: ChannelPackageRegistry
) -> None:
    """Explicit trusted bootstrap injection; never import or install packages here."""
    if not isinstance(registry, ChannelPackageRegistry):
        raise TypeError("A managed channel package registry is required")
    if hasattr(app.state, _STATE_KEY):
        raise ValueError("Managed channel registry already configured")
    setattr(app.state, _STATE_KEY, registry)


def _registry(request: Request) -> ChannelPackageRegistry:
    return getattr(request.app.state, _STATE_KEY, None) or ChannelPackageRegistry()


async def _scope(request: Request) -> ChannelScope:
    actor = await get_user_node(resolve_principal_id(request))
    if actor is None:
        raise MissingAuthenticationError("Authentication required")
    selected = parse_scope_header(request.headers.get("X-Integral-Scope"))
    workspace = request.query_params.get("workspace_id")
    if not workspace or selected is None or selected.get("workspace_id") != workspace:
        raise BadRequestError("An exact workspace scope is required")
    await require_profile_manager(actor.id, workspace)
    return ChannelScope(actor.id, workspace)


async def _projection(registry: ChannelPackageRegistry, scope: ChannelScope) -> dict:
    rows = []
    for row in await registry.discover(scope):
        item = asdict(row)
        item.pop("full_page_url", None)
        package = registry.resolve(row.definition.slug)
        if row.connected and package and package.profile_setup is not None:
            item["configuration_kind"] = PROFILE_CONFIGURATION
        rows.append(item)
    return {"channels": rows}


def _package(registry: ChannelPackageRegistry, slug: str) -> ChannelPackage:
    package = registry.resolve(slug)
    if package is None:
        raise ResourceNotFoundError("Unknown managed channel")
    return package


_Result = TypeVar("_Result")


async def _lifecycle(operation: Awaitable[_Result]) -> _Result:
    """Do not expose arbitrary external adapter diagnostics over management APIs."""
    try:
        return await operation
    except ResourceConflictError:
        raise ResourceConflictError(
            "Managed channel changed. Refresh and try again."
        ) from None
    except Exception:
        raise ServiceUnavailableError(
            "Managed channel operation is unavailable"
        ) from None


async def _installation(
    package: ChannelPackage, scope: ChannelScope, match: ManageChannelProvision
) -> ChannelInstallation:
    current = await _lifecycle(package.lifecycle.observe(scope))
    if (
        not isinstance(current, ChannelInstallation)
        or current.workspace_id != scope.workspace_id
        or current.slug != package.definition.slug
        or current.provision_id != match.provision_id
        or current.route_epoch != match.route_epoch
        or type(current.enabled) is not bool
    ):
        raise ResourceConflictError("Managed channel changed. Refresh and try again.")
    return current


@endpoint(_PATH, methods=["GET"], auth=True, tags=["Agentive"])
async def list_managed_channels(request: Request) -> dict:
    """Show management-safe channel catalogue data for a workspace manager."""
    return await _projection(_registry(request), await _scope(request))


@endpoint(f"{_PATH}/{{slug}}/install", methods=["POST"], auth=True, tags=["Agentive"])
async def install_managed_channel(request: Request, slug: str) -> dict:
    """Install an authorized package in its disabled state without publishing it."""
    scope = await _scope(request)
    registry = _registry(request)
    package = _package(registry, slug)
    row = await registry.describe_one(scope, slug)
    if row is None or not row.can_install:
        raise ResourceConflictError("Managed channel is not available to install")
    await require_profile_manager(scope.actor_id, scope.workspace_id)
    installed = await _lifecycle(package.lifecycle.install(scope))
    if (
        not isinstance(installed, ChannelInstallation)
        or installed.workspace_id != scope.workspace_id
        or installed.slug != slug
        or installed.enabled is not False
        or not isinstance(installed.provision_id, str)
        or not installed.provision_id.strip()
        or not isinstance(installed.route_epoch, str)
        or not installed.route_epoch.strip()
    ):
        raise ServiceUnavailableError(
            "Managed channel installation could not be verified"
        )
    return await _projection(registry, scope)


@endpoint(f"{_PATH}/{{slug}}/enabled", methods=["PUT"], auth=True, tags=["Agentive"])
async def set_managed_channel_enabled(request: Request, slug: str) -> dict:
    """Change exact installed enablement; disabling does not require readiness."""
    scope = await _scope(request)
    registry = _registry(request)
    package = _package(registry, slug)
    try:
        change = ManageChannelEnablement.model_validate(await request.json())
    except ValueError:
        raise BadRequestError("Invalid managed channel enablement") from None
    current = await _installation(package, scope, change)
    if change.enabled:
        row = await registry.describe_one(scope, slug)
        if (
            row is None
            or (row.provision_id, row.route_epoch)
            != (current.provision_id, current.route_epoch)
            or (not row.can_enable and row.state != "active")
        ):
            raise ResourceConflictError("Managed channel is not ready to enable")
    await require_profile_manager(scope.actor_id, scope.workspace_id)
    # Adapter must atomically compare the same provision/epoch at commit.
    if current.enabled != change.enabled:
        await _lifecycle(package.lifecycle.set_enabled(scope, current, change.enabled))
    return await _projection(registry, scope)


@endpoint(f"{_PATH}/{{slug}}", methods=["DELETE"], auth=True, tags=["Agentive"])
async def remove_managed_channel(request: Request, slug: str) -> dict:
    """Remove only the exact authorized provision, including during outages."""
    scope = await _scope(request)
    registry = _registry(request)
    package = _package(registry, slug)
    match = _provision_query(request)
    current = await _installation(package, scope, match)
    await require_profile_manager(scope.actor_id, scope.workspace_id)
    await _lifecycle(package.lifecycle.remove(scope, current))
    return await _projection(registry, scope)


def _provision_query(request: Request) -> ManageChannelProvision:
    try:
        return ManageChannelProvision.model_validate(
            {
                "provision_id": request.query_params.get("provision_id"),
                "route_epoch": request.query_params.get("route_epoch"),
            }
        )
    except ValueError:
        raise BadRequestError("Invalid managed channel provision") from None


def _configuration_setup(package: ChannelPackage) -> ChannelProfileSetup:
    setup = package.profile_setup
    if setup is None:
        raise ResourceNotFoundError("Managed channel configuration is unavailable")
    return setup


async def _configuration_snapshot(
    setup: ChannelProfileSetup,
    scope: ChannelScope,
    current: ChannelInstallation,
) -> ChannelProfileConfiguration:
    data = await _lifecycle(setup.describe(scope, current))
    if (
        not isinstance(data, ChannelProfileConfiguration)
        or type(data.profile_revision) is not int
        or data.profile_revision < 0
        or (data.profile_key is not None and not _profile_text(data.profile_key))
        or not isinstance(data.eligible_profiles, tuple)
        or (
            data.published_version is not None
            and (
                type(data.published_version) is not int
                or not 1 <= data.published_version <= 9_007_199_254_740_991
                or data.profile_key is None
            )
        )
        or type(data.editable) is not bool
        or (current.enabled and data.editable)
        or not all(
            isinstance(item, ChannelProfileOption)
            and _profile_text(item.key)
            and _profile_text(item.name)
            for item in data.eligible_profiles
        )
        or (
            data.published_version is not None
            and sum(item.key == data.profile_key for item in data.eligible_profiles)
            != 1
        )
    ):
        raise ServiceUnavailableError("Managed channel configuration is unavailable")
    return data


def _profile_text(value: str) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


@endpoint(
    f"{_PATH}/{{slug}}/configuration", methods=["GET"], auth=True, tags=["Agentive"]
)
async def get_managed_channel_configuration(request: Request, slug: str) -> dict:
    """Describe the one Core-supported optional setup at an exact provision."""
    scope = await _scope(request)
    package = _package(_registry(request), slug)
    setup = _configuration_setup(package)
    match = _provision_query(request)
    current = await _installation(package, scope, match)
    snapshot = await _configuration_snapshot(setup, scope, current)
    if await _installation(package, scope, match) != current:
        raise ResourceConflictError("Managed channel changed. Refresh and try again.")
    await require_profile_manager(scope.actor_id, scope.workspace_id)
    projection = asdict(snapshot)
    if snapshot.published_version is None:
        projection.pop("published_version")
    return {
        "kind": PROFILE_CONFIGURATION,
        **projection,
        "provision_id": current.provision_id,
        "route_epoch": current.route_epoch,
    }


@endpoint(
    f"{_PATH}/{{slug}}/configuration", methods=["PUT"], auth=True, tags=["Agentive"]
)
async def assign_managed_channel_profile(request: Request, slug: str) -> dict:
    """Assign a current eligible public profile only to a disabled provision."""
    scope = await _scope(request)
    registry = _registry(request)
    package = _package(registry, slug)
    setup = _configuration_setup(package)
    try:
        change = ManageChannelProfileAssignment.model_validate(await request.json())
    except ValueError:
        raise BadRequestError("Invalid managed channel profile assignment") from None
    current = await _installation(package, scope, change)
    snapshot = await _configuration_snapshot(setup, scope, current)
    if (
        current.enabled
        or not snapshot.editable
        or snapshot.profile_revision != change.expected_profile_revision
        or (
            change.profile_key is not None
            and not any(
                item.key == change.profile_key for item in snapshot.eligible_profiles
            )
        )
    ):
        raise ResourceConflictError("Managed channel profile changed or is ineligible")
    await require_profile_manager(scope.actor_id, scope.workspace_id)
    await _lifecycle(
        setup.assign(
            scope, current, change.expected_profile_revision, change.profile_key
        )
    )
    return await _projection(registry, scope)
