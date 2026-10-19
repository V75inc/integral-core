"""App Home composition is distinct, bounded and permission validated."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import app_home_override as service
from app.services.app_home import read_app_home


def home():
    """Build one generic Home widget without an analytics Dashboard node."""
    return {
        "title": "Home",
        "description": "Start here",
        "widgets": [
            {
                "id": "one",
                "type": "metric_card",
                "grid": {"x": 0, "y": 0, "w": 4, "h": 2},
                "data_source": {"kind": "count"},
            }
        ],
    }


@pytest.mark.asyncio
async def test_home_composition_checks_current_source_authority(monkeypatch):
    """App updates cannot bypass canonical dashboard source validation."""
    validate = AsyncMock()
    monkeypatch.setattr(service, "validate_dashboard_field_bindings", validate)
    result = await service.validate_home_composition(
        user_id="u", app_id="a", home=home()
    )
    assert result["title"] == "Home"
    validate.assert_awaited_once()
    validate.side_effect = PermissionError("denied")
    with pytest.raises(PermissionError):
        await service.validate_home_composition(user_id="u", app_id="a", home=home())


@pytest.mark.asyncio
async def test_home_rejects_overlapping_widgets(monkeypatch):
    """Bad Home layouts fail before any persistence or consent."""
    payload = home()
    payload["widgets"].append({**payload["widgets"][0], "id": "two"})
    with pytest.raises(ValueError, match="overlap"):
        await service.validate_home_composition(user_id="u", app_id="a", home=payload)


@pytest.mark.asyncio
async def test_workspace_home_is_read_without_a_package_definition(monkeypatch):
    """A workspace Home is rendered separately from named dashboards."""
    app = SimpleNamespace(id="a", workspace_id="ws", home_composition=home())
    monkeypatch.setattr(
        "app.services.app_home.can_view_app", AsyncMock(return_value=True)
    )
    monkeypatch.setattr("app.services.app_home.App.get", AsyncMock(return_value=app))
    result = await read_app_home(
        user_id="u", app_id="a", workspace_id="ws", include_data=False
    )
    assert result["home"]["description"] == "Start here"
    assert result["home"]["actions"] == []


@pytest.mark.asyncio
async def test_home_update_persists_separately_from_dashboards(
    authenticated_client, test_user
):
    """Exercise the actual App update and Home read endpoints in test storage."""
    created = await authenticated_client.post(
        "/api/apps", json={"name": "Home qualification"}
    )
    assert created.status_code == 200
    app_id = created.json()["app"]["id"]
    updated = await authenticated_client.put(
        f"/api/apps/{app_id}", json={"home": home()}
    )
    assert updated.status_code == 200, updated.text
    read = await authenticated_client.get(f"/api/apps/{app_id}/home")
    assert read.status_code == 200, read.text
    assert read.json()["home"]["title"] == "Home"
    boards = await authenticated_client.get(f"/api/apps/{app_id}/dashboards")
    assert boards.json()["dashboards"] == []
    cleared = await authenticated_client.put(f"/api/apps/{app_id}", json={"home": {}})
    assert cleared.status_code == 200
    assert (await authenticated_client.get(f"/api/apps/{app_id}/home")).json()[
        "home"
    ] is None


@pytest.mark.asyncio
async def test_empty_widget_update_clears_board(authenticated_client, test_user):
    """An explicitly empty array is a real update, not an omitted field."""
    app = (
        await authenticated_client.post(
            "/api/apps", json={"name": "Clear board qualification"}
        )
    ).json()["app"]
    path = f"/api/apps/{app['id']}/dashboards"
    created = await authenticated_client.post(
        path, json={"name": "Board", "widgets": home()["widgets"]}
    )
    assert created.status_code == 200, created.text
    board = created.json().get("dashboard") or created.json()
    updated = await authenticated_client.patch(
        f"{path}/{board['id']}", json={"widgets": []}
    )
    assert updated.status_code == 200, updated.text
    assert (await authenticated_client.get(path)).json()["dashboards"][0][
        "widgets"
    ] == []


@pytest.mark.asyncio
async def test_resident_home_proposal_approval_and_readback(
    authenticated_client, test_user, monkeypatch
):
    """The real proposal must leave Home untouched until consent, then read back."""
    # Transactional staging receipts require an explicit test-only storage key.
    # Do not depend on a developer .env or weaken encrypted persistence.
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "ab" * 32)
    from app.agentive.services.staging_apply import bless_and_execute
    from app.agentive.staging import StagingError, get_token
    from app.agentive.tooling.dispatch import dispatch_tool

    created = await authenticated_client.post(
        "/api/apps", json={"name": "Resident Home qualification"}
    )
    assert created.status_code == 200, created.text
    app = created.json()["app"]
    app_id = app["id"]
    me = await authenticated_client.get("/api/auth/me")
    principal = me.json()["user"]["id"]
    result = await dispatch_tool(
        "integral_update_app",
        {"app_id": app_id, "home": home()},
        principal_id=principal,
        scope=app.get("workspace_id"),
    )
    assert not result.is_error, result
    token = result.data["token"]
    assert (await get_token(token)).state == "pending"
    before = await authenticated_client.get(f"/api/apps/{app_id}/home")
    assert before.json()["home"] is None
    applied = await bless_and_execute(user_id=principal, token=token)
    assert applied["consumed"] is True, applied
    read = await authenticated_client.get(f"/api/apps/{app_id}/home")
    assert read.status_code == 200, read.text
    assert read.json()["home"]["title"] == "Home"
    assert read.json()["home"]["widgets"][0]["id"] == "one"
    assert (await authenticated_client.get(f"/api/apps/{app_id}/dashboards")).json()[
        "dashboards"
    ] == []
    with pytest.raises(StagingError):
        await bless_and_execute(user_id=principal, token=token)
