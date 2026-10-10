"""Package homes are explicit, scoped, read-only views of active definitions."""

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.app_home import read_app_home
from app.services.app_home_contract import normalize_app_home
from app.services.operational_model_compile import compile_canonical_manifest


def home_fixture():
    """Return a domain-neutral prescribed home and its query declaration."""
    query = {
        "key": "records",
        "handler_key": "records",
        "input_schema": {"type": "object", "properties": {}},
        "query_template": {
            "resource": "entry",
            "select": ["id", "title", "custom_fields"],
            "limit": 10,
            "cost_ceiling": 1000,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "title": {"type": "string"},
                            "step": {"type": ["string", "null"]},
                        },
                    },
                },
                "total": {"type": "integer"},
            },
        },
        "dashboard": {"rows_path": "items", "total_path": "total", "params": {}},
    }
    home = {
        "title": "Current work",
        "widgets": [
            {
                "id": "current",
                "type": "record_summary",
                "title": "Next step",
                "config": {"fields": [{"field": "step", "label": "Step"}]},
                "data_source": {
                    "kind": "declared_query",
                    "query_key": "records",
                    "rows_path": "items",
                    "total_path": "total",
                    "query_params": {},
                },
            }
        ],
        "actions": [
            {
                "label": "Start",
                "draft": "Help me get started.",
                "when": {"widget": "current", "state": "empty"},
            }
        ],
    }
    return home, query


def test_home_compiles_and_remains_idempotent():
    home, query = home_fixture()
    manifest = {
        "operational_model_schema_version": 2,
        "scope": "app",
        "app": {"home": home, "queries": [query]},
    }
    canonical = compile_canonical_manifest(manifest=manifest)
    assert canonical["app"]["home"]["title"] == "Current work"
    assert (
        compile_canonical_manifest(manifest=canonical)["app"]["home"]
        == canonical["app"]["home"]
    )


@pytest.mark.parametrize(
    "case",
    [
        "foreign_query",
        "changed_paths",
        "undeclared_field",
        "unknown_action_widget",
        "too_many_widgets",
        "overlapping_widgets",
        "duplicate_actions",
    ],
)
def test_home_rejects_invalid_contracts(case):
    home, query = home_fixture()
    widget = home["widgets"][0]
    if case == "foreign_query":
        widget["data_source"]["query_key"] = "other_app_records"
    elif case == "changed_paths":
        widget["data_source"]["rows_path"] = "preview"
    elif case == "undeclared_field":
        widget["config"]["fields"][0]["field"] = "secret"
    elif case == "unknown_action_widget":
        home["actions"][0]["when"]["widget"] = "missing"
    elif case == "overlapping_widgets":
        other = copy.deepcopy(widget)
        other["id"] = "other"
        home["widgets"].append(other)
    elif case == "duplicate_actions":
        home["actions"] *= 2
    else:
        home["widgets"] *= 7
    with pytest.raises(ValueError):
        normalize_app_home(home, [query])


@pytest.fixture
def home_runtime(monkeypatch):
    home, query = home_fixture()
    normalized = normalize_app_home(home, [query])
    app = SimpleNamespace(
        id="a1", workspace_id="ws1", active_definition_id="d1", lifecycle_state="active"
    )
    definition = SimpleNamespace(
        app_id="a1",
        status="active",
        revision=2,
        canonical_manifest={"app": {"home": normalized}},
    )
    permission = AsyncMock(return_value=True)
    resolve = AsyncMock(return_value={"records": [], "total_matched": 0})
    monkeypatch.setattr("app.services.app_home.can_view_app", permission)
    monkeypatch.setattr("app.services.app_home.App.get", AsyncMock(return_value=app))
    monkeypatch.setattr(
        "app.services.app_home.ApplicationDefinition.get",
        AsyncMock(return_value=definition),
    )
    monkeypatch.setattr("app.services.app_home.resolve_widget_data", resolve)
    return app, definition, permission, resolve


@pytest.mark.asyncio
async def test_home_reads_owned_definition_and_scoped_query_without_mutating_it(
    home_runtime,
):
    _, definition, _, resolve = home_runtime
    before = copy.deepcopy(definition.canonical_manifest)
    result = await read_app_home(user_id="u1", app_id="a1", workspace_id="ws1")
    assert result["home"]["widgets"][0]["data"]["records"] == []
    assert result["definition_revision"] == 2
    assert definition.canonical_manifest == before
    assert resolve.await_args.kwargs["workspace_id"] == "ws1"
    assert resolve.await_args.kwargs["user_id"] == "u1"


@pytest.mark.asyncio
async def test_home_metadata_does_not_execute_queries(home_runtime):
    _, _, _, resolve = home_runtime
    result = await read_app_home(
        user_id="u1", app_id="a1", workspace_id=None, include_data=False
    )
    assert "data" not in result["home"]["widgets"][0]
    resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_home_rejects_wrong_scope_and_revoked_access(home_runtime):
    _, _, permission, resolve = home_runtime
    with pytest.raises(PermissionError):
        await read_app_home(user_id="u1", app_id="a1", workspace_id="other")
    permission.return_value = False
    with pytest.raises(PermissionError):
        await read_app_home(user_id="u1", app_id="a1", workspace_id="ws1")
    resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_home_rejects_foreign_definition_and_paused_execution(home_runtime):
    app, definition, _, resolve = home_runtime
    definition.app_id = "other"
    assert await read_app_home(user_id="u1", app_id="a1", workspace_id="ws1") == {
        "home": None
    }
    definition.app_id = "a1"
    app.lifecycle_state = "paused"
    result = await read_app_home(user_id="u1", app_id="a1", workspace_id="ws1")
    assert result["home"]["widgets"][0]["data"] == {"error": "app_not_active"}
    resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_home_query_failure_is_unknown_and_redacted(home_runtime):
    _, _, _, resolve = home_runtime
    resolve.side_effect = ValueError("private diagnostic")
    result = await read_app_home(user_id="u1", app_id="a1", workspace_id="ws1")
    assert result["home"]["widgets"][0]["data"] == {"error": "home_query_unavailable"}
    assert "private diagnostic" not in str(result)
