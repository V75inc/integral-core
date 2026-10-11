"""Actual auth admission must not restore stale accounts or token authority."""

from datetime import datetime, timezone

import pytest

from app.services.authentication import (
    AuthenticationStateConflict,
    IntegralAuthenticationService,
    get_authentication_service,
)

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


@pytest.fixture
async def identity_service(test_user):
    service = get_authentication_service()
    identity = await service._get_user_by_id(test_user.user_id)
    assert identity is not None and identity.is_active
    token, _ = service._generate_jwt_token(identity.id, identity.email)
    return service, identity, token


async def test_warm_validation_cannot_reactivate_disabled_identity(identity_service):
    service, identity, token = identity_service
    assert await service.validate_token(token) is not None
    await service.context.database.find_one_and_update(
        "object", {"id": identity.id}, {"$set": {"context.is_active": False}}
    )
    assert await service.validate_token(token) is None
    stored = await service.context.database.find_one("object", {"id": identity.id})
    assert stored["context"]["is_active"] is False


async def test_disable_between_lookup_and_auth_touch_denies(
    identity_service, monkeypatch
):
    service, identity, token = identity_service
    save = service.context.save
    calls = []

    async def disable_before_touch(entity):
        calls.append(entity.id)
        await service.context.database.find_one_and_update(
            "object", {"id": identity.id}, {"$set": {"context.is_active": False}}
        )
        return await save(entity)

    monkeypatch.setattr(service.context, "save", disable_before_touch)
    assert await service.validate_token(token) is None
    assert calls == [identity.id]
    stored = await service.context.database.find_one("object", {"id": identity.id})
    assert stored["context"]["is_active"] is False


async def test_auth_touch_preserves_concurrent_profile_change(
    identity_service, monkeypatch
):
    service, identity, token = identity_service
    save = service.context.save

    async def rename_before_touch(entity):
        await service.context.database.find_one_and_update(
            "object", {"id": identity.id}, {"$set": {"context.name": "Current name"}}
        )
        return await save(entity)

    monkeypatch.setattr(service.context, "save", rename_before_touch)
    response = await service.validate_token(token)
    assert response is not None and response.name == "Current name"
    stored = await service.context.database.find_one("object", {"id": identity.id})
    assert stored["context"]["name"] == "Current name"


async def test_auth_touch_does_not_restore_revoked_roles(identity_service, monkeypatch):
    service, identity, token = identity_service
    await service.context.database.find_one_and_update(
        "object", {"id": identity.id}, {"$set": {"context.roles": ["admin"]}}
    )
    save = service.context.save

    async def demote_before_touch(entity):
        await service.context.database.find_one_and_update(
            "object", {"id": identity.id}, {"$set": {"context.roles": ["user"]}}
        )
        return await save(entity)

    monkeypatch.setattr(service.context, "save", demote_before_touch)
    response = await service.validate_token(token)
    assert response is not None and response.roles == ["user"]
    stored = await service.context.database.find_one("object", {"id": identity.id})
    assert stored["context"]["roles"] == ["user"]


async def test_missing_active_fact_is_not_defaulted_true(identity_service):
    service, identity, token = identity_service
    await service.context.database.find_one_and_update(
        "object", {"id": identity.id}, {"$unset": {"context.is_active": ""}}
    )
    assert await service.validate_token(token) is None


async def test_stale_password_change_cannot_restore_account_state(identity_service):
    service, identity, _ = identity_service
    identity.password_hash = "proposed-hash"
    await service.context.database.find_one_and_update(
        "object", {"id": identity.id}, {"$set": {"context.is_active": False}}
    )
    with pytest.raises(AuthenticationStateConflict):
        await identity.save()
    current = await service._get_user_by_id(identity.id)
    assert current is not None and not current.is_active
    assert current.password_hash != "proposed-hash"


async def test_warm_blacklist_miss_does_not_survive_other_service_logout(
    identity_service,
):
    service, identity, token = identity_service
    assert await service.validate_token(token) is not None
    other = IntegralAuthenticationService(
        service.context, jwt_secret=service.jwt_secret
    )
    assert await other.logout_user(token)
    assert await service.validate_token(token) is None
    assert (await service._get_user_by_id(identity.id)).is_active


async def test_auth_context_changes_only_observed_fields(identity_service):
    service, identity, _ = identity_service
    identity.last_accessed = datetime.now(timezone.utc)
    await service.context.database.find_one_and_update(
        "object", {"id": identity.id}, {"$set": {"context.name": "Concurrent"}}
    )
    await identity.save()
    current = await service._get_user_by_id(identity.id)
    assert current.name == "Concurrent" and current.last_accessed is not None


async def test_server_auth_routes_and_middleware_use_current_identity(identity_service):
    from httpx import ASGITransport, AsyncClient
    from jvspatial.api.config_groups import AuthConfig, DatabaseConfig

    from app.config import settings
    from app.server import IntegralServer

    _, identity, _ = identity_service
    server = IntegralServer(
        title="Isolated auth admission test",
        database=DatabaseConfig(db_type=None),
        auth=AuthConfig(
            enabled=True,
            test_mode=False,
            jwt_secret=settings.SECRET_KEY,
            oauth_enabled=False,
            password_reset_enabled=False,
            exempt_paths=["/api/auth/login", "/api/auth/refresh"],
        ),
    )
    assert isinstance(server._auth_service, IntegralAuthenticationService)
    async with AsyncClient(
        transport=ASGITransport(app=server.get_app()), base_url="http://test"
    ) as client:
        login = await client.post(
            "/api/auth/login",
            json={"email": identity.email, "password": "testpassword123"},
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        assert await server._auth_service.validate_token(token) is not None
        await server._auth_service.context.database.find_one_and_update(
            "object", {"id": identity.id}, {"$set": {"context.is_active": False}}
        )
        denied = await client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert denied.status_code == 401, denied.text
        refresh = await client.post(
            "/api/auth/refresh", json={"refresh_token": login.json()["refresh_token"]}
        )
        assert refresh.status_code in (400, 401), refresh.text
        current = await server._auth_service._get_user_by_id(identity.id)
        assert current is not None and not current.is_active
