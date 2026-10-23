"""Native Home composition, shared authoring and guarded persistence."""

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.schemas.app_home import AppHomeComposition
from app.services.app_home import read_app_home
from app.services.app_home_contract import normalize_app_home
from app.services.home_native import validate_native_home
from app.services.operational_model_compile import compile_canonical_manifest


def custom():
    return {
        "title": "Home",
        "renderer": "native",
        "content": {"id": "intro", "type": "heading", "text": "Your work"},
    }


def manifest():
    return {
        "operational_model_schema_version": 2,
        "scope": "app",
        "app": {"home": custom()},
    }


def test_full_and_mixed_native_home_compile_idempotently():
    compiled = compile_canonical_manifest(manifest=manifest())
    assert compiled["app"]["home"]["content"]["text"] == "Your work"
    assert compile_canonical_manifest(manifest=compiled) == compiled
    mixed = {
        "title": "Mixed",
        "widgets": [
            {
                "id": "custom",
                "type": "native_component",
                "content": custom()["content"],
                "grid": {"x": 0, "y": 0, "w": 12, "h": 20},
            }
        ],
    }
    assert normalize_app_home(mixed, [])["widgets"][0]["type"] == "native_component"


@pytest.mark.parametrize(
    "content",
    [
        {"id": "one", "type": "iframe"},
        {"id": "one", "type": "text", "html": "<script>run()</script>"},
        {"id": "one", "type": "text", "style": {"position": "fixed"}},
        {
            "id": "one",
            "type": "form",
            "operation_key": "unknown",
            "fields": [{"name": "message", "label": "Message"}],
        },
        {"id": "one", "type": "records", "query_key": "unknown"},
    ],
)
def test_native_home_rejects_code_frames_and_foreign_capabilities(content):
    with pytest.raises(ValueError):
        normalize_app_home({**custom(), "content": content}, [])


def test_native_tree_identity_and_depth_are_bounded():
    home = {
        **custom(),
        "content": {
            "id": "root",
            "type": "stack",
            "children": [custom()["content"], custom()["content"]],
        },
    }
    with pytest.raises(ValueError, match="unique"):
        normalize_app_home(home, [])
    with pytest.raises(ValidationError):
        AppHomeComposition.model_validate(
            {"title": "Home", "renderer": "extension", "extension_view_key": "anything"}
        )


@pytest.mark.asyncio
async def test_native_preview_is_scoped_and_does_not_persist(monkeypatch):
    app = SimpleNamespace(
        id="a",
        workspace_id="ws",
        home_composition=custom(),
        home_revision=3,
        active_definition_id="d",
        lifecycle_state="active",
    )
    definition = SimpleNamespace(
        app_id="a", status="active", revision=2, canonical_manifest=manifest()
    )
    monkeypatch.setattr(
        "app.services.app_home.can_view_app", AsyncMock(return_value=True)
    )
    monkeypatch.setattr("app.services.app_home.App.get", AsyncMock(return_value=app))
    monkeypatch.setattr(
        "app.services.app_home.ApplicationDefinition.get",
        AsyncMock(return_value=definition),
    )
    monkeypatch.setattr(
        "app.services.app_home_override.validate_home_composition",
        AsyncMock(return_value=custom()),
    )
    resolve = AsyncMock()
    monkeypatch.setattr("app.services.app_home.resolve_widget_data", resolve)
    before = copy.deepcopy(app.home_composition)
    result = await read_app_home(
        user_id="u",
        app_id="a",
        workspace_id="ws",
        preview_home=custom(),
        include_data=False,
    )
    assert result["preview"] and not result["live_data"]
    assert result["home_revision"] == "3:d"
    assert result["components"][0]["key"] == "native"
    assert app.home_composition == before
    resolve.assert_not_awaited()
    with pytest.raises(PermissionError):
        await read_app_home(user_id="u", app_id="a", workspace_id="other")
    app.lifecycle_state = "paused"
    assert (await read_app_home(user_id="u", app_id="a", workspace_id="ws"))[
        "error"
    ] == "app_not_active"


@pytest.mark.asyncio
async def test_revision_token_rejects_stale_http_home_edit(
    authenticated_client, test_user
):
    created = await authenticated_client.post("/api/apps", json={"name": "Revision QA"})
    app_id = created.json()["app"]["id"]
    initial = (await authenticated_client.get(f"/api/apps/{app_id}/home")).json()
    home = {
        "title": "First",
        "widgets": [
            {"id": "metric", "type": "metric_card", "data_source": {"kind": "count"}}
        ],
    }
    first = await authenticated_client.put(
        f"/api/apps/{app_id}",
        json={"home": home, "expected_home_revision": initial["home_revision"]},
    )
    assert first.status_code == 200, first.text
    stale = await authenticated_client.put(
        f"/api/apps/{app_id}",
        json={
            "home": {**home, "title": "Stale"},
            "expected_home_revision": initial["home_revision"],
        },
    )
    assert stale.status_code == 409, stale.text
    final = (await authenticated_client.get(f"/api/apps/{app_id}/home")).json()
    assert final["home"]["title"] == "First"
    assert final["home_revision"] != initial["home_revision"]


@pytest.mark.asyncio
async def test_two_same_revision_authors_cannot_overwrite_each_other(
    authenticated_client, test_user
):
    import asyncio

    app_id = (
        await authenticated_client.post(
            "/api/apps", json={"name": "Concurrent Home QA"}
        )
    ).json()["app"]["id"]
    revision = (await authenticated_client.get(f"/api/apps/{app_id}/home")).json()[
        "home_revision"
    ]

    def payload(title):
        return {
            "home": {
                "title": title,
                "widgets": [
                    {
                        "id": "count",
                        "type": "metric_card",
                        "data_source": {"kind": "count"},
                    }
                ],
            },
            "expected_home_revision": revision,
        }

    results = await asyncio.gather(
        *[
            authenticated_client.put(f"/api/apps/{app_id}", json=payload(title))
            for title in ["Author A", "Author B"]
        ]
    )
    assert sorted(result.status_code for result in results) == [200, 409]


@pytest.mark.asyncio
async def test_preview_http_accepts_flat_body_and_never_saves(
    authenticated_client, test_user
):
    app_id = (
        await authenticated_client.post(
            "/api/apps", json={"name": "Preview Transport QA"}
        )
    ).json()["app"]["id"]
    home = {
        "title": "Preview only",
        "widgets": [
            {"id": "count", "type": "metric_card", "data_source": {"kind": "count"}}
        ],
    }
    result = await authenticated_client.post(
        f"/api/apps/{app_id}/home/preview", json={"home": home}
    )
    assert result.status_code == 200, result.text
    assert result.json()["preview"] and not result.json()["live_data"]
    assert result.json()["home"]["title"] == "Preview only"
    assert (await authenticated_client.get(f"/api/apps/{app_id}/home")).json()[
        "home"
    ] is None
    navigation = await authenticated_client.post(
        f"/api/extensions/{app_id}/navigate", json={"kind": "app", "id": app_id}
    )
    assert navigation.status_code == 200, navigation.text
    assert navigation.json()["path"] == f"/apps/{app_id}"


@pytest.mark.asyncio
async def test_definition_activation_refreshes_home_held_by_stale_developer_object(
    authenticated_client, test_user
):
    from app.models.nodes import App
    from app.services.application_definitions import compile_application_definition

    app_id = (
        await authenticated_client.post(
            "/api/apps", json={"name": "Developer Coauthor QA"}
        )
    ).json()["app"]["id"]
    stale = await App.get(app_id)
    home = {
        "title": "Newer human edit",
        "widgets": [
            {"id": "count", "type": "metric_card", "data_source": {"kind": "count"}}
        ],
    }
    assert (
        await authenticated_client.put(f"/api/apps/{app_id}", json={"home": home})
    ).status_code == 200
    await compile_application_definition(
        app_node=stale, manifest=manifest(), source_kind="package"
    )
    saved = (await authenticated_client.get(f"/api/apps/{app_id}/home")).json()
    assert saved["source"] == "instance"
    assert saved["home"]["title"] == "Newer human edit"


@pytest.mark.asyncio
async def test_staged_home_execution_rejects_a_newer_edit(
    authenticated_client, test_user
):
    from app.agentive.staging_executors import _x_update_app
    from app.agentive.tooling.bindings import _propose_principal, _stage_update_app

    app_id = (
        await authenticated_client.post(
            "/api/apps", json={"name": "Approval Coauthor QA"}
        )
    ).json()["app"]["id"]
    home = {
        "title": "Proposed",
        "widgets": [
            {"id": "count", "type": "metric_card", "data_source": {"kind": "count"}}
        ],
    }
    principal = test_user.user_id
    token = _propose_principal.set(principal)
    try:
        staged = await _stage_update_app({"app_id": app_id, "home": home})
    finally:
        _propose_principal.reset(token)
    newer = {**home, "title": "Newer edit"}
    assert (
        await authenticated_client.put(f"/api/apps/{app_id}", json={"home": newer})
    ).status_code == 200
    result = await _x_update_app(principal, staged["payload"])
    assert result["error"] and result["status_code"] == 409
    assert (await authenticated_client.get(f"/api/apps/{app_id}/home")).json()["home"][
        "title"
    ] == "Newer edit"


@pytest.mark.asyncio
async def test_native_upgrade_preserves_instance_and_rejects_removed_binding(
    authenticated_client, test_user
):
    from app.models.nodes import App
    from app.services.application_definitions import compile_application_definition

    app_id = (
        await authenticated_client.post("/api/apps", json={"name": "Native Upgrade QA"})
    ).json()["app"]["id"]
    app = await App.get(app_id)
    package = manifest()
    package["app"]["queries"] = [
        {
            "key": "records",
            "handler_key": "records",
            "query_template": {
                "resource": "entry",
                "select": ["id", "title"],
                "limit": 10,
                "cost_ceiling": 1000,
            },
            "input_schema": {"type": "object"},
            "output_schema": {
                "type": "object",
                "properties": {"total_estimate": {"type": "integer"}},
            },
        }
    ]
    first = await compile_application_definition(app_node=app, manifest=package)
    observed = (await authenticated_client.get(f"/api/apps/{app_id}/home")).json()
    custom_home = {
        "title": "Our Home",
        "renderer": "native",
        "content": {
            "id": "count",
            "type": "metric",
            "title": "Our records",
            "query_key": "records",
        },
    }
    saved = await authenticated_client.put(
        f"/api/apps/{app_id}",
        json={"home": custom_home, "expected_home_revision": observed["home_revision"]},
    )
    assert saved.status_code == 200, saved.text
    package["app"]["home"]["content"]["text"] = "New package default"
    second = await compile_application_definition(app_node=app, manifest=package)
    assert first.id != second.id
    current = (await authenticated_client.get(f"/api/apps/{app_id}/home")).json()
    assert current["source"] == "instance" and current["home"]["title"] == "Our Home"
    incompatible = copy.deepcopy(package)
    incompatible["app"]["queries"] = []
    with pytest.raises(ValueError, match="owning App"):
        await compile_application_definition(app_node=app, manifest=incompatible)
    assert (await App.get(app_id)).active_definition_id == second.id
