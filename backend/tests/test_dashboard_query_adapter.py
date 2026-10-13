"""Template dashboards retain App authority and never aggregate partial pages."""

from unittest.mock import AsyncMock

import pytest

from app.services.dashboard_query_adapter import collect_template_rows, qualify_template


def declaration():
    """Declaration."""
    return {
        "input_schema": {"properties": {"cursor": {"type": "string"}}},
        "query_template": {
            "resource": "entry",
            "select": ["id", "track_id", "title", "custom_fields"],
        },
    }


def test_qualifies_selected_published_scalar_fields():
    """Test qualifies selected published scalar fields."""
    query = declaration()
    result = qualify_template(
        query, [{"key": "amount", "type": "number"}, {"key": "file", "type": "file"}]
    )
    assert result["_template_dashboard"]
    fields = result["output_schema"]["properties"]["items"]["items"]["properties"][
        "custom_fields"
    ]["properties"]
    assert fields == {"amount": {"type": ["number", "null"]}}
    assert "dashboard" not in query


@pytest.mark.parametrize(
    "change",
    [
        {"handler_ref": "tools:read"},
        {"query_template": {"resource": "track", "select": ["id"]}},
        {"input_schema": {"properties": {"cursor": {}, "arbitrary": {}}}},
        {"query_template": {"resource": "entry", "select": ["id", "title"]}},
    ],
)
def test_does_not_invent_contracts_for_ineligible_queries(change):
    """Test does not invent contracts for ineligible queries."""
    query = declaration()
    if "handler_ref" in change:
        query.pop("query_template")
    query.update(change)
    assert not qualify_template(query, []).get("_template_dashboard")


async def collect(invoke, budget=5000):
    """Collect."""
    return await collect_template_rows(
        invoke=invoke,
        user_id="user",
        workspace_id="ws",
        app_id="app",
        query_key="directory",
        track_id="target",
        budget=budget,
    )


@pytest.mark.asyncio
async def test_paginates_original_query_then_narrows_track():
    """Test paginates original query then narrows track."""
    invoke = AsyncMock(
        side_effect=[
            {
                "output": {
                    "items": [{"id": "one", "track_id": "other"}],
                    "next_cursor": "cursor1",
                }
            },
            {
                "output": {
                    "items": [{"id": "two", "track_id": "target"}],
                    "next_cursor": None,
                }
            },
        ]
    )
    result = await collect(invoke)
    assert result == {
        "items": [{"id": "two", "track_id": "target"}],
        "total_estimate": 1,
    }
    assert invoke.call_args_list[1].kwargs["params"] == {"cursor": "cursor1"}
    assert invoke.call_args_list[1].kwargs["user_id"] == "user"


@pytest.mark.asyncio
async def test_scan_budget_applies_even_to_rows_outside_selected_track():
    """Test scan budget applies even to rows outside selected track."""
    invoke = AsyncMock(
        return_value={
            "output": {
                "items": [
                    {"id": "one", "track_id": "other"},
                    {"id": "two", "track_id": "other"},
                ],
                "next_cursor": None,
            }
        }
    )
    assert (await collect(invoke, 1))["error"] == "over_budget"


@pytest.mark.asyncio
async def test_repeated_page_never_produces_a_total():
    """Test repeated page never produces a total."""
    invoke = AsyncMock(
        return_value={
            "output": {
                "items": [{"id": "one", "track_id": "target"}],
                "next_cursor": "same",
            }
        }
    )
    result = await collect(invoke)
    assert result["error"] == "duplicate_declared_query_rows"
    assert "total_estimate" not in result


@pytest.mark.asyncio
async def test_revoked_authority_on_later_page_propagates():
    """Test revoked authority on later page propagates."""
    invoke = AsyncMock(
        side_effect=[
            {
                "output": {
                    "items": [{"id": "one", "track_id": "target"}],
                    "next_cursor": "cursor1",
                }
            },
            PermissionError("revoked"),
        ]
    )
    with pytest.raises(PermissionError):
        await collect(invoke)


@pytest.mark.asyncio
async def test_template_chart_and_summary_use_scoped_nested_fields(monkeypatch):
    """Run the real resolver over an authorized portable query projection."""
    from types import SimpleNamespace

    from app.services import dashboard_service as ds

    app = SimpleNamespace(
        id="app",
        workspace_id="ws",
        lifecycle_state="active",
        installed_package_slug="sample",
    )
    monkeypatch.setattr(ds, "_get_app_or_none", AsyncMock(return_value=app))
    monkeypatch.setattr(
        ds, "_data_source_track_ids", AsyncMock(return_value=["target"])
    )
    monkeypatch.setattr(
        ds.Track, "get", AsyncMock(return_value=SimpleNamespace(id="target"))
    )
    monkeypatch.setattr(
        "app.services.permissions.can_view_track", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        ds,
        "_track_dashboard_fields",
        AsyncMock(
            return_value=[
                {"key": "phase", "type": "select"},
                {"key": "amount", "type": "number"},
            ]
        ),
    )
    monkeypatch.setattr(
        "app.services.app_queries.registry.get_app_query", lambda *args: declaration()
    )
    invoke = AsyncMock(
        return_value={
            "output": {
                "items": [
                    {
                        "id": "one",
                        "track_id": "target",
                        "title": "One",
                        "custom_fields": {"phase": "open", "amount": 10},
                    },
                    {
                        "id": "two",
                        "track_id": "target",
                        "title": "Two",
                        "custom_fields": {"phase": "closed", "amount": 20},
                    },
                    {
                        "id": "other",
                        "track_id": "different",
                        "title": "Other",
                        "custom_fields": {"phase": "open", "amount": 999},
                    },
                ],
                "next_cursor": None,
            }
        }
    )
    monkeypatch.setattr("app.services.app_queries.dispatch.invoke_app_query", invoke)
    source = {
        "kind": "declared_query",
        "query_key": "directory",
        "track_id": "target",
        "rows_path": "items",
        "total_path": "total_estimate",
        "query_params": {},
    }
    chart = await ds.resolve_widget_data(
        user_id="user",
        app_id="app",
        workspace_id="ws",
        widget={
            "type": "chart_bar",
            "data_source": {**source, "group_by": "custom_fields.phase"},
        },
    )
    assert chart["total_matched"] == 2
    assert {row["label"]: row["value"] for row in chart["series"]} == {
        "open": 1,
        "closed": 1,
    }
    total = await ds.resolve_widget_data(
        user_id="user",
        app_id="app",
        workspace_id="ws",
        widget={
            "type": "metric_card",
            "data_source": {**source, "op": "sum", "field": "custom_fields.amount"},
        },
    )
    assert total["value"] == "30"
    summary = await ds.resolve_widget_data(
        user_id="user",
        app_id="app",
        workspace_id="ws",
        widget={
            "type": "record_summary",
            "config": {
                "fields": [{"field": "custom_fields.amount", "label": "Amount"}],
                "max_records": 2,
            },
            "data_source": source,
        },
    )
    assert summary["total_matched"] == 2
    assert summary["records"][0]["fields"] == {"custom_fields.amount": 10}
    query = await ds._qualified_dashboard_query(
        user_id="user", app_id="app", workspace_id="ws", source=source
    )
    with pytest.raises(ValueError, match="numeric"):
        ds._validate_declared_dashboard_source(
            query, {**source, "op": "sum", "field": "custom_fields.phase"}, {}
        )
    with pytest.raises(ValueError, match="explicit track_id"):
        await ds._qualified_dashboard_query(
            user_id="user",
            app_id="app",
            workspace_id="ws",
            source={**source, "track_id": None},
        )
