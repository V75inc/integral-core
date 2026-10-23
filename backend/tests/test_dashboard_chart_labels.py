"""Chart labels disclose only admitted reference names and preserve group keys."""

from unittest.mock import AsyncMock

import pytest

from app.models.edges import EXCLUDED_FROM
from app.services import dashboard_service as service
from tests.test_dashboard_scope_authority import dashboard_graph

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


async def test_entry_aggregate_uses_readable_label_and_keeps_raw_drill_key(monkeypatch):
    owner, workspace, app, _ = await dashboard_graph()
    from app.models.edges import CONTAINS
    from app.models.nodes import Track

    track = (await app.nodes(edge=[CONTAINS], node=[Track], limit=1))[0]
    entries = await track.nodes(edge=[CONTAINS], node=["Entry"], limit=1)
    entry = entries[0]
    entry.title = "Daily reading"
    await entry.save()
    # The aggregate consumes governed query rows; actual graph admission is
    # still exercised by the reference-label lookup, including revocation below.
    monkeypatch.setattr(
        service,
        "query_all_entries",
        AsyncMock(
            return_value={
                "entries": [
                    {
                        "id": entry.id,
                        "track_id": track.id,
                        "title": entry.title,
                        "custom_fields": {"habit": entry.id},
                    }
                ],
                "complete": True,
            }
        ),
    )
    monkeypatch.setattr(
        service,
        "_track_dashboard_fields",
        AsyncMock(return_value=[{"key": "habit", "type": "relation"}]),
    )
    result = await service.resolve_widget_data(
        user_id=owner.id,
        workspace_id=workspace.id,
        app_id=app.id,
        widget={
            "type": "chart_pie",
            "data_source": {
                "kind": "aggregate",
                "op": "count",
                "group_by": "habit",
            },
        },
    )
    assert result["series"] == [{"key": entry.id, "label": "Daily reading", "value": 1}]
    assert result["groups"][0]["key"] == entry.id


async def test_chart_label_rechecks_current_permission_and_workspace():
    owner, workspace, app, _ = await dashboard_graph()
    from app.models.edges import CONTAINS

    track = (await app.nodes(edge=[CONTAINS], node=["Track"], limit=1))[0]
    entry = (await track.nodes(edge=[CONTAINS], node=["Entry"], limit=1))[0]
    entry.title = "Private reading goal"
    await entry.save()
    data = {"series": [{"key": entry.id, "label": entry.id, "value": 7}]}
    source = {"kind": "aggregate", "group_by": "id"}
    readable = await service._chart_display_data(
        data, owner.id, workspace.id, app, source, "chart_pie"
    )
    assert readable["series"][0]["label"] == entry.title
    outside = await service._chart_display_data(
        data, owner.id, "other-workspace", app, source, "chart_pie"
    )
    assert outside["series"][0] == {
        "key": entry.id,
        "label": "Entry label unavailable",
        "value": 7,
    }
    await owner.connect(entry, edge=EXCLUDED_FROM)
    denied = await service._chart_display_data(
        data, owner.id, workspace.id, app, source, "chart_pie"
    )
    assert denied["series"][0]["label"] == "Entry label unavailable"


async def test_typed_relation_labels_resolve_but_ordinary_text_is_unchanged(
    monkeypatch,
):
    owner, workspace, app, _ = await dashboard_graph()
    from app.models.edges import CONTAINS

    track = (await app.nodes(edge=[CONTAINS], node=["Track"], limit=1))[0]
    entry = (await track.nodes(edge=[CONTAINS], node=["Entry"], limit=1))[0]
    data = {"series": [{"key": entry.id, "label": entry.id, "value": 2}]}
    source = {"group_by": "custom_fields.habit"}
    monkeypatch.setattr(
        service,
        "_track_dashboard_fields",
        AsyncMock(return_value=[{"key": "habit", "type": "relation"}]),
    )
    result = await service._chart_display_data(
        data, owner.id, workspace.id, app, source, "chart_pie"
    )
    assert result["series"][0]["label"] == entry.title
    monkeypatch.setattr(
        service,
        "_track_dashboard_fields",
        AsyncMock(return_value=[{"key": "habit", "type": "text"}]),
    )
    assert (
        await service._chart_display_data(
            data, owner.id, workspace.id, app, source, "chart_pie"
        )
        == data
    )


async def test_label_lookup_failure_keeps_numeric_data_and_unavailable_text(
    monkeypatch,
):
    identity = "n.Entry." + "a" * 24
    monkeypatch.setattr(
        "app.services.id_resolver.resolve_id_labels",
        AsyncMock(side_effect=RuntimeError("unavailable")),
    )
    result = await service._chart_display_data(
        {"series": [{"label": identity, "value": 9}]},
        "reader",
        "workspace",
        None,
        {"group_by": "id"},
        "chart_pie",
    )
    assert result["series"] == [
        {"key": identity, "label": "Entry label unavailable", "value": 9}
    ]
