"""Collection fences recheck scope and never turn ordering into authority."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.errors import InsufficientPermissionsError
from app.services import track_write_scope as service

pytestmark = pytest.mark.asyncio


@pytest.fixture
def fence(monkeypatch):
    database = SimpleNamespace(
        find_one_and_update=AsyncMock(return_value={"id": "track"})
    )
    failures = []

    @asynccontextmanager
    async def scope(identity):
        assert identity == "track:track"
        try:
            yield SimpleNamespace(database=database)
        except BaseException as error:
            failures.append(error)
            raise

    monkeypatch.setattr(service, "entry_write_scope", scope)
    monkeypatch.setattr(
        service, "is_postgres_entry_transaction", lambda db: db is database
    )
    location = AsyncMock(return_value=SimpleNamespace(workspace_id="ws", app_id="app"))
    role = AsyncMock(return_value="editor")
    monkeypatch.setattr(service, "locate_resource", location)
    monkeypatch.setattr(service, "resolve_role", role)
    monkeypatch.setattr(
        service.App,
        "get",
        AsyncMock(return_value=SimpleNamespace(installed_package_slug="example")),
    )
    ctx = SimpleNamespace(
        workspace_id="ws",
        app_id="app",
        user_id="user",
        bundle_slug="example",
        read_only=False,
    )
    return ctx, database, location, role, failures


async def test_holds_existing_transaction_and_rechecks_after_lock(fence):
    ctx, db, location, role, _ = fence
    async with service.app_track_write_scope(ctx, "track"):
        db.find_one_and_update.assert_awaited_once_with(
            "node",
            {"id": "track", "entity": service.Track._entity_name()},
            {"$set": {}},
        )
        assert location.await_count == role.await_count == 2


@pytest.mark.parametrize("change", ["workspace", "app", "role", "readonly", "bundle"])
async def test_refuses_foreign_scope_revoked_read_and_readonly(fence, change):
    ctx, db, _, role, _ = fence
    if change == "workspace":
        ctx.workspace_id = "foreign"
    elif change == "app":
        ctx.app_id = "foreign"
    elif change == "role":
        role.return_value = None
    elif change == "readonly":
        ctx.read_only = True
    else:
        ctx.app_id = ""
        ctx.bundle_slug = "foreign"
    with pytest.raises(InsufficientPermissionsError):
        async with service.app_track_write_scope(ctx, "track"):
            pytest.fail("Refused context must never enter the rule")
    db.find_one_and_update.assert_not_awaited()


async def test_permission_revoked_while_waiting_never_enters_rule(fence):
    ctx, db, _, role, failures = fence
    role.side_effect = ["editor", None]
    with pytest.raises(InsufficientPermissionsError):
        async with service.app_track_write_scope(ctx, "track"):
            pytest.fail("Stale authority must never enter the rule")
    db.find_one_and_update.assert_awaited_once()
    assert len(failures) == 1


async def test_rule_failure_reaches_the_enclosing_transaction(fence):
    ctx, _, _, _, failures = fence
    error = ValueError("Synthetic uniqueness conflict")
    with pytest.raises(ValueError, match="Synthetic uniqueness"):
        async with service.app_track_write_scope(ctx, "track"):
            raise error
    assert failures == [error]
