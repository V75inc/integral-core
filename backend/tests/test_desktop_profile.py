"""Desktop capability denials apply before database reads or policy bypasses."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from app.api.auth import local_owner_session, register_user
from app.api.errors import InsufficientPermissionsError, MissingAuthenticationError
from app.config import settings
from app.middleware.desktop_perimeter import DesktopPerimeterMiddleware
from app.schemas.policy import Resource, Subject
from app.services.deployment import capabilities, owner_ids
from app.services.policy_engine import evaluate

pytestmark = pytest.mark.unit


@pytest.fixture
def desktop_profile(monkeypatch):
    """Use a synthetic installation, without loading any local credentials."""
    monkeypatch.setattr(settings, "INTEGRAL_DEPLOYMENT_PROFILE", "desktop_single_user")
    monkeypatch.setattr(settings, "INTEGRAL_DESKTOP_INSTALLATION_ID", "d" * 32)
    monkeypatch.setattr(settings, "INTEGRAL_DESKTOP_CONTROL_TOKEN", "c" * 64)
    monkeypatch.setattr(
        settings, "INTEGRAL_DESKTOP_WEB_ORIGIN", "http://127.0.0.1:19006"
    )


@pytest.mark.parametrize("kind", ["human", "agent", "system"])
@pytest.mark.parametrize(
    "action",
    [
        "user.create",
        "workspace.member_add",
        "workspace.invitation_accept",
        "track.collaborator_add",
        "entry.share_link.redeem",
        "app.collaborator_role_update",
    ],
)
async def test_desktop_denies_collaboration_before_any_policy_bypass(
    desktop_profile, kind, action
):
    """Even a system subject cannot enable desktop collaboration."""
    decision = await evaluate(
        subject=Subject(kind=kind, id=owner_ids()[1]),
        action=action,
        resource=Resource(kind="workspace", id="missing", scope="ws:missing"),
    )
    assert not decision.allowed
    assert decision.reason == "deployment_profile_denied"


async def test_server_keeps_system_policy_and_capabilities(monkeypatch):
    """The existing server profile retains its capabilities and system policy."""
    monkeypatch.setattr(settings, "INTEGRAL_DEPLOYMENT_PROFILE", "server")
    assert capabilities()["features"]["collaboration"] is True
    decision = await evaluate(
        subject=Subject(kind="system", id="job"),
        action="workspace.member_add",
        resource=Resource(kind="workspace", id="missing", scope="ws:missing"),
    )
    assert decision.allowed


async def test_signup_is_closed_before_parsing_request(desktop_profile):
    """No first HTTP requester can claim an empty desktop installation."""
    with pytest.raises(InsufficientPermissionsError):
        await register_user(Request({"type": "http", "headers": []}))


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"authorization", b"Bearer wrong")],
        [
            (b"authorization", b"Bearer " + b"c" * 64),
            (b"origin", b"http://127.0.0.1:19006"),
        ],
        [
            (b"authorization", b"Bearer " + b"c" * 64),
            (b"sec-fetch-site", b"same-origin"),
        ],
    ],
)
async def test_native_session_rejects_browser_and_missing_secret(
    desktop_profile, headers
):
    """Browser requests cannot use the controller endpoint, even with a secret."""
    with pytest.raises(MissingAuthenticationError):
        await local_owner_session(Request({"type": "http", "headers": headers}))


@pytest.mark.parametrize(
    "path,method",
    [
        ("/api/auth/signup", "POST"),
        ("/api/auth/register", "POST"),
        ("/api/auth/users", "POST"),
        ("/api/workspaces/w/members", "POST"),
        ("/api/tracks/t/collaborators", "PATCH"),
        ("/api/tracks/t/public-share", "GET"),
        ("/api/public/tracks/token", "GET"),
        ("/api/ownership-proposals/x/respond", "POST"),
        ("/api/users/me", "DELETE"),
        ("/api/users", "POST"),
        ("/api/users/other/pinned", "POST"),
        ("/api/users/me/pinned", "PUT"),
        ("/api/users/me/pinned/extra", "POST"),
        ("/api/users/me/model-credentials/extra", "POST"),
        ("/api/users/me/model-credentials/validate/extra", "POST"),
        ("/api/users/me/unknown-preference", "PATCH"),
        ("/api/users/me/role", "PUT"),
    ],
)
async def test_perimeter_closes_legacy_routes(desktop_profile, path, method):
    """The perimeter also covers public routes and legacy aliases."""

    async def forbidden(scope, receive, send):
        raise AssertionError("A disabled request reached the application")

    async with AsyncClient(
        transport=ASGITransport(app=DesktopPerimeterMiddleware(forbidden)),
        base_url="http://127.0.0.1:19006",
    ) as client:
        assert (await client.request(method, path)).status_code == 403


@pytest.mark.parametrize(
    "path,method",
    [
        ("/api/users/me/pinned", "POST"),
        ("/api/users/me/scope", "PUT"),
        ("/api/users/me/timezone", "PATCH"),
        ("/api/users/me/notification-preferences", "PATCH"),
        ("/api/users/me/speech-preferences", "PATCH"),
        ("/api/users/me/model-credentials", "POST"),
        ("/api/users/me/model-credentials", "DELETE"),
        ("/api/users/me/model-credentials/validate", "POST"),
    ],
)
async def test_local_settings_reach_route_authentication(desktop_profile, path, method):
    """Transport admits local self-settings without granting authentication."""
    from starlette.responses import JSONResponse

    async def authentication_required(scope, receive, send):
        assert scope["path"] == path
        assert not scope.get("state", {}).get("user")
        await JSONResponse({"detail": "Authentication required"}, status_code=401)(
            scope, receive, send
        )

    async with AsyncClient(
        transport=ASGITransport(
            app=DesktopPerimeterMiddleware(authentication_required)
        ),
        base_url="http://127.0.0.1:19006",
    ) as client:
        assert (await client.request(method, path)).status_code == 401


@pytest.mark.parametrize(
    "path,method",
    [
        ("/api/meta/deployment", "GET"),
        ("/api/users/me/pinned", "POST"),
        ("/api/users/me/model-credentials", "POST"),
    ],
)
@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "attacker.test:19006"},
        {"Origin": "http://attacker.test"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
async def test_perimeter_rejects_rebinding_and_hostile_origins(
    desktop_profile, headers, path, method
):
    """Loopback transport alone is never browser authority."""

    async def forbidden(scope, receive, send):
        raise AssertionError("Hostile request reached the application")

    async with AsyncClient(
        transport=ASGITransport(app=DesktopPerimeterMiddleware(forbidden)),
        base_url="http://127.0.0.1:19006",
    ) as client:
        assert (await client.request(method, path, headers=headers)).status_code == 403


@pytest.mark.parametrize(
    "module_name,function_name",
    [
        ("sharing", "add_collaborator"),
        ("sharing", "_materialize_guest_membership_unlocked"),
        ("invitations", "create_invitation"),
        ("invitations", "create_resource_invitation"),
        ("invitation_grants", "apply_invitation_grant"),
        ("share_links", "mint_share_link"),
        ("share_link_lifecycle", "redeem_source_link"),
        ("ownership_consent", "request_ownership_transfer"),
        ("ownership_consent", "respond_ownership_transfer"),
        ("workspace_membership", "add_member"),
        ("member_provision", "provision_workspace_member"),
    ],
)
async def test_direct_service_invocation_cannot_bypass_the_desktop_profile(
    desktop_profile, module_name, function_name
):
    """Workers and tools receive denial before resolving a proposed grant."""
    import importlib
    import inspect

    function = getattr(
        importlib.import_module(f"app.services.{module_name}"), function_name
    )
    proposed = {
        name: None
        for name, parameter in inspect.signature(function).parameters.items()
        if parameter.default is inspect.Parameter.empty
    }
    with pytest.raises(InsufficientPermissionsError):
        await function(**proposed)


async def test_deployment_denials_use_the_existing_audit_path(
    desktop_profile, monkeypatch
):
    """Deployment rejection retains policy observability and pure dry runs."""
    from unittest.mock import AsyncMock

    from app.services import policy_engine

    emit = AsyncMock()
    monkeypatch.setattr(policy_engine, "emit_change_event", emit)
    arguments = dict(
        subject=Subject(kind="system", id="worker"),
        action="track.share_link.mint",
        resource=Resource(kind="track", id="target", scope="track:target"),
    )
    assert not (await evaluate(**arguments, _dry_run=True)).allowed
    emit.assert_not_awaited()
    assert not (await evaluate(**arguments)).allowed
    assert emit.await_args.kwargs["action"] == "policy.deny"
    assert (
        emit.await_args.kwargs["details"]["decision_reason"]
        == "deployment_profile_denied"
    )


async def test_desktop_readiness_refuses_swallowed_startup_failure(desktop_profile):
    """A healthy HTTP listener is insufficient when bootstrap did not finish."""
    from types import SimpleNamespace

    from app.api.errors import ServiceUnavailableError
    from app.api.meta import desktop_readiness

    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(state=SimpleNamespace(desktop_ready=False)),
        }
    )
    with pytest.raises(ServiceUnavailableError, match="startup is incomplete"):
        await desktop_readiness(request)
