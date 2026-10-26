"""App record pages do bounded work; counts retain live ordinary authority."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.errors import BadRequestError
from app.models.edges import COLLABORATES_ON, CONTAINS, EXCLUDED_FROM
from app.models.nodes import Entry, EntryType
from app.services.app_operations.context import OperationContext
from tests.test_resource_location import graph, sibling

pytestmark = [pytest.mark.asyncio]


async def setup():
    user, ws, app, track, entry = await graph()
    ctx = OperationContext(
        user_id=user.id, workspace_id=ws.id, scope="tool", app_id=app.id, read_only=True
    )
    return user, ws, app, track, entry, ctx


async def test_page_does_not_hydrate_or_count_the_whole_track(monkeypatch):
    _, _, _, track, entry, ctx = await setup()
    for n in range(35):
        row = await Entry.create(
            title=f"Row {n}", track_id=track.id, custom_fields={"stage": "open"}
        )
        await track.connect(row, edge=CONTAINS)
    from app.services import entry_access_projection as projection

    hydrate = projection.hydrate_records
    sizes = []

    async def measured(records):
        sizes.append(len(records))
        return await hydrate(records)

    monkeypatch.setattr(projection, "hydrate_records", measured)
    page = await ctx.read_entry_records({"track": track.title, "limit": 5})
    assert page["count"] is None and not page["count_is_exact"]
    assert page["returned_count"] == 5 and page["truncated"] and page["next_cursor"]
    assert sizes == [5]
    second = await ctx.read_entry_records(
        {"track": track.title, "limit": 5, "cursor": page["next_cursor"]}
    )
    assert not {r["id"] for r in page["rows"]} & {r["id"] for r in second["rows"]}
    assert sizes == [5, 5]
    with pytest.raises(BadRequestError):
        await ctx.read_entry_records(
            {"track": track.title, "limit": 6, "cursor": page["next_cursor"]}
        )


async def test_count_streams_raw_windows_without_entry_hydration(monkeypatch):
    _, _, _, track, _, ctx = await setup()
    for n in range(52):
        row = await Entry.create(
            title=f"Row {n}",
            track_id=track.id,
            custom_fields={"stage": "open" if n % 2 else "closed"},
        )
        await track.connect(row, edge=CONTAINS)
    from app.services import entry_access_projection as projection

    monkeypatch.setattr(
        projection,
        "hydrate_records",
        AsyncMock(side_effect=AssertionError("no node hydration")),
    )
    result = await ctx.read_entry_records(
        {"track": track.title, "count_only": True, "group_by": "stage"}
    )
    assert result["count"] == 53 and result["count_is_exact"]
    assert result["groups"] == {"closed": 26, "open": 26, "(missing)": 1}
    assert result["source_urls"] == [f"/tracks/{track.id}"]


async def test_page_and_count_keep_current_denials_and_app_state():
    user, ws, app, track, entry, ctx = await setup()
    await sibling(user, ws)
    await user.connect(entry, edge=EXCLUDED_FROM)
    assert (await ctx.read_entry_records({"track": track.title, "count_only": True}))[
        "count"
    ] == 0
    await user.connect(entry, edge=COLLABORATES_ON, role="viewer")
    assert (await ctx.read_entry_records({"track": track.title, "count_only": True}))[
        "count"
    ] == 1
    page = await ctx.read_entry_records({"track": track.title})
    assert [r["id"] for r in page["rows"]] == [entry.id]
    app.lifecycle_state = "paused"
    await app.save()
    assert not (await ctx.read_entry_records({"track": track.title}))["ok"]


async def test_type_predicate_precedes_record_selection_and_no_type_fallback(
    monkeypatch,
):
    _, _, _, track, _, ctx = await setup()
    et = await EntryType.create(
        name="Wanted",
        track_id=track.id,
        form_schema={"_manifest_entry_type_key": "wanted"},
    )
    await track.connect(et, edge=CONTAINS)
    row = await Entry.create(
        title="Selected",
        track_id=track.id,
        type_id=et.id,
        custom_fields={"status": "Open"},
    )
    await track.connect(row, edge=CONTAINS)
    result = await ctx.read_entry_records(
        {
            "track": track.title,
            "entry_type": "wanted",
            "count_only": True,
            "filters": {"status": "open"},
        }
    )
    assert result["count"] == 1
    empty = await ctx.read_entry_records(
        {"track": track.title, "entry_type": "other", "count_only": True}
    )
    assert empty["count"] == 0


async def test_requires_bound_app():
    from app.services.hooks.registry import ToolContext

    _, ws, _, track, _, ctx = await setup()
    with pytest.raises(BadRequestError):
        await ToolContext(
            user_id=ctx.user_id, workspace_id=ws.id, scope="tool"
        ).read_entry_records({"track": track.title})


async def test_scalar_sort_is_numeric_before_paging_and_keeps_missing_last():
    _, _, _, track, _, ctx = await setup()
    ids = []
    for v in ["9", "100", None, "20"]:
        row = await Entry.create(
            title=str(v), track_id=track.id, custom_fields={"value": v}
        )
        await track.connect(row, edge=CONTAINS)
        ids.append(row.id)
    first = await ctx.read_entry_records(
        {"track": track.title, "sort_by": "value", "sort_direction": "desc", "limit": 2}
    )
    assert [r["id"] for r in first["rows"]] == [ids[1], ids[3]]
    second = await ctx.read_entry_records(
        {
            "track": track.title,
            "sort_by": "value",
            "sort_direction": "desc",
            "limit": 2,
            "cursor": first["next_cursor"],
        }
    )
    assert second["rows"][0]["id"] == ids[0]
