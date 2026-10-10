"""Declared-query summaries preserve authorization, schema and unknown values."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import dashboard_service as ds
from app.services.dashboard_widget_validation import validate_widget_specs


def summary_widget(field="custom_fields.next_step"):
    """Build a domain-neutral declared-query summary fixture."""
    return {
        "type": "record_summary",
        "config": {
            "fields": [{"field": field, "label": "Next step"}],
            "max_records": 1,
        },
        "data_source": {
            "kind": "declared_query",
            "query_key": "summary",
            "rows_path": "items",
            "total_path": "total",
            "query_params": {},
        },
    }


@pytest.fixture
def query_fixture(monkeypatch):
    """Install a scoped query and an authorized-result stub."""
    app = SimpleNamespace(
        id="a1", lifecycle_state="active", installed_package_slug="sample"
    )
    query = {
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
                            "custom_fields": {
                                "type": "object",
                                "properties": {
                                    "next_step": {"type": ["string", "null"]},
                                },
                            },
                        },
                    },
                },
                "total": {"type": "integer"},
            },
        },
        "dashboard": {"rows_path": "items", "total_path": "total", "params": {}},
    }
    invoke = AsyncMock(
        return_value={
            "output": {
                "items": [
                    {
                        "id": "n.Entry.1",
                        "title": "First",
                        "custom_fields": {
                            "next_step": None,
                            "secret_extra": "excluded",
                        },
                    },
                    {
                        "id": "n.Entry.2",
                        "title": "Second",
                        "custom_fields": {"next_step": "Review"},
                    },
                ],
                "total": 2,
            }
        }
    )
    monkeypatch.setattr(ds, "_get_app_or_none", AsyncMock(return_value=app))
    monkeypatch.setattr(
        "app.services.app_queries.registry.get_app_query",
        lambda workspace_id, app_id, query_key: query,
    )
    monkeypatch.setattr("app.services.app_queries.dispatch.invoke_app_query", invoke)
    return query, invoke


@pytest.mark.asyncio
async def test_summary_projects_only_declared_fields_and_keeps_null(query_fixture):
    _, invoke = query_fixture
    result = await ds.resolve_widget_data(
        user_id="u1", app_id="a1", workspace_id="ws1", widget=summary_widget()
    )
    assert result["records"] == [
        {
            "id": "n.Entry.1",
            "title": "First",
            "fields": {"custom_fields.next_step": None},
        }
    ]
    assert result["total_shown"] == 1
    assert result["total_matched"] == 2
    invoke.assert_awaited_once_with(
        user_id="u1", workspace_id="ws1", app_id="a1", query_key="summary", params={}
    )


@pytest.mark.asyncio
async def test_summary_rejects_undeclared_field_before_dispatch(query_fixture):
    _, invoke = query_fixture
    result = await ds.resolve_widget_data(
        user_id="u1",
        app_id="a1",
        workspace_id="ws1",
        widget=summary_widget("custom_fields.secret_extra"),
    )
    assert result == {"error": "declared_query_summary_field_not_declared"}
    invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_summary_does_not_present_incomplete_query_as_current(query_fixture):
    _, invoke = query_fixture
    invoke.return_value["output"]["total"] = 3
    result = await ds.resolve_widget_data(
        user_id="u1", app_id="a1", workspace_id="ws1", widget=summary_widget()
    )
    assert result == {"value": None, "error": "incomplete_declared_query"}


@pytest.mark.asyncio
async def test_summary_rejects_invalid_value_and_duplicate_identity(query_fixture):
    _, invoke = query_fixture
    rows = invoke.return_value["output"]["items"]
    rows[0]["custom_fields"]["next_step"] = {"nested": "not a scalar"}
    result = await ds.resolve_widget_data(
        user_id="u1", app_id="a1", workspace_id="ws1", widget=summary_widget()
    )
    assert result == {"error": "declared_query_summary_value_invalid"}
    rows[0]["custom_fields"]["next_step"] = None
    rows[1]["id"] = rows[0]["id"]
    result = await ds.resolve_widget_data(
        user_id="u1", app_id="a1", workspace_id="ws1", widget=summary_widget()
    )
    assert result.get("error") == "duplicate_declared_query_rows"


@pytest.mark.parametrize(
    "change",
    [
        {"max_records": 0},
        {"max_records": 11},
        {"max_records": True},
        {"fields": []},
        {"fields": [{"field": "__proto__", "label": "Bad"}]},
        {"fields": [{"field": "title", "label": "Title", "detail": "yes"}]},
    ],
)
def test_summary_bounds_are_enforced(change):
    widget = summary_widget()
    widget["config"].update(change)
    assert validate_widget_specs([widget])
