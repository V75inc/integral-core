"""Exercise trusted bootstrap with a harmless independent connector contribution."""

from dataclasses import replace

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.routing import Host

from app.connectors.channel_packages import ChannelPackageRegistry
from app.connectors.channel_runtime import (
    ChannelOriginRoutes,
    ChannelRuntimeContribution,
    mount_trusted_channel_runtime,
)
from tests.test_channel_packages import _fixture

STAFF = "https://staff.example.invalid"
VISITOR = "https://visitor.example.invalid"
PARTNER = "https://embed.example.invalid"


def _router(path):
    router = APIRouter()

    @router.get(path)
    async def read():
        return {"fixture": path}

    return router


def _contribution():
    return ChannelRuntimeContribution(
        _fixture(),
        management_routes=(_router("/connector/management"),),
        visitor_routes=(_router("/connector/visitor"),),
        isolated_routes=(ChannelOriginRoutes(PARTNER, (_router("/connector/embed"),)),),
        visitor_origin=VISITOR,
    )


def _app():
    return FastAPI(openapi_url=None, docs_url=None, redoc_url=None)


@pytest.mark.asyncio
async def test_independent_connector_mounts_only_at_chosen_boundaries():
    """No Core route changes are needed to admit the custom connector paths."""
    registry = ChannelPackageRegistry()
    staff, visitor, partner = (_app() for _ in range(3))
    contribution = _contribution()
    mount_trusted_channel_runtime(
        registry,
        contribution,
        staff_app=staff,
        staff_origin=STAFF,
        visitor_apps={VISITOR: visitor, PARTNER: partner},
    )
    assert (
        registry.resolve(contribution.package.definition.slug) is contribution.package
    )
    cases = (
        (STAFF, staff, "/connector/management"),
        (VISITOR, visitor, "/connector/visitor"),
        (PARTNER, partner, "/connector/embed"),
    )
    for origin, app, wanted in cases:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=origin
        ) as browser:
            for _, _, path in cases:
                response = await browser.get(path)
                assert response.status_code == (200 if path == wanted else 404)


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "aliased",
        "origin",
        "duplicate",
        "static",
        "dynamic",
        "catchall",
        "mount",
        "foreign",
        "unknown",
        "openapi",
    ],
)
def test_mount_precheck_failure_mutates_neither_registry_nor_routes(failure):
    """Wrong boundaries or shadowing are terminal before committing any routes."""
    registry = ChannelPackageRegistry()
    staff, visitor, partner = (_app() for _ in range(3))
    contribution = _contribution()
    targets = {VISITOR: visitor, PARTNER: partner}
    if failure == "missing":
        del targets[PARTNER]
    elif failure == "aliased":
        targets[VISITOR] = staff
    elif failure == "origin":
        contribution = replace(contribution, visitor_origin=STAFF)
        targets[STAFF] = targets.pop(VISITOR)
    elif failure == "duplicate":
        registry.register(contribution.package)
    elif failure == "static":
        visitor.include_router(_router("/connector/visitor"))
    elif failure == "dynamic":
        visitor.include_router(_router("/connector/{any_name}"))
    elif failure == "catchall":
        visitor.include_router(_router("/{asset:path}"))
    elif failure == "mount":
        visitor.mount("/connector", _app())
    elif failure == "unknown":
        visitor.router.routes.append(Host("outside.example.invalid", _app()))
    elif failure == "openapi":
        staff.openapi()
    else:
        contribution = replace(
            contribution, visitor_routes=(_router("/{anything:path}"),)
        )
    route_snapshots = [list(app.router.routes) for app in (staff, visitor, partner)]
    before = registry.resolve(contribution.package.definition.slug)
    with pytest.raises((TypeError, ValueError)):
        mount_trusted_channel_runtime(
            registry,
            contribution,
            staff_app=staff,
            staff_origin=STAFF,
            visitor_apps=targets,
        )
    assert registry.resolve(contribution.package.definition.slug) is before
    assert [app.router.routes for app in (staff, visitor, partner)] == route_snapshots
