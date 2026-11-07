"""A library update brings each track's own view copies in line with the template's entry types."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import operational_model_runtime as rt


def _view(key, etk, default="", **extra):
    return SimpleNamespace(
        id=f"n.View.{key}",
        config={"_manifest_view_key": key, **extra},
        entry_type_keys=list(etk),
        default_entry_type_key=default,
        updated_at="",
    )


def _tier(*specs):
    return {"views": list(specs)}


async def _run(views, tier):
    track = SimpleNamespace(id="n.Track.t")
    saved = AsyncMock()
    with (
        patch.object(rt, "resolve_track_runtime_profile", new=AsyncMock(return_value=(None, tier, None))),
        patch.object(rt, "_catalog_views_for_track", new=AsyncMock(return_value=views)),
        patch("app.services.graph_hydration.bulk_save_nodes", new=saved),
    ):
        n = await rt.resync_track_view_constraints_from_tier(track)
    return n, saved


@pytest.mark.asyncio
async def test_stale_copy_is_rewritten_to_the_template_entry_type():
    stale = _view("lines_table", ["employee_line"], default="nis_schedule_line")
    n, saved = await _run(
        [stale],
        _tier({"key": "lines_table", "entry_type_keys": ["nis_schedule_line"], "default_entry_type": "nis_schedule_line"}),
    )
    assert n == 1
    assert stale.entry_type_keys == ["nis_schedule_line"]
    saved.assert_awaited_once()
    assert saved.await_args.args[0] == [stale]


@pytest.mark.asyncio
async def test_matching_copy_is_left_alone_and_nothing_is_saved():
    ok = _view("lines_table", ["nis_schedule_line"], default="nis_schedule_line")
    n, saved = await _run(
        [ok],
        _tier({"key": "lines_table", "entry_type_keys": ["nis_schedule_line"], "default_entry_type": "nis_schedule_line"}),
    )
    assert n == 0
    saved.assert_not_awaited()


@pytest.mark.asyncio
async def test_copy_without_a_template_counterpart_is_untouched():
    mine = _view("my_custom_board", ["employee_line"])
    n, saved = await _run(
        [mine],
        _tier({"key": "lines_table", "entry_type_keys": ["nis_schedule_line"]}),
    )
    assert n == 0
    assert mine.entry_type_keys == ["employee_line"]
    saved.assert_not_awaited()


@pytest.mark.asyncio
async def test_template_with_no_entry_types_never_clears_a_copy():
    keep = _view("lines_table", ["employee_line"], default="employee_line")
    n, saved = await _run([keep], _tier({"key": "lines_table", "entry_types": []}))
    assert n == 0
    assert keep.entry_type_keys == ["employee_line"]
    saved.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_the_changed_views_are_saved_and_other_fields_survive():
    a = _view("lines_table", ["employee_line"], default="x", hidden_note="keep")
    b = _view("lines_scope", ["paye_filing_line"], default="paye_filing_line")
    n, saved = await _run(
        [a, b],
        _tier(
            {"key": "lines_table", "entry_types": ["Nis Schedule Line", "nis_schedule_line"], "default_entry_type": "nis_schedule_line"},
            {"key": "lines_scope", "entry_type_keys": ["paye_filing_line"], "default_entry_type": "paye_filing_line"},
        ),
    )
    assert n == 1
    assert a.entry_type_keys == ["nis_schedule_line"]  # slugged and de-duplicated
    assert a.default_entry_type_key == "nis_schedule_line"
    assert a.config["hidden_note"] == "keep"
    assert saved.await_args.args[0] == [a]


@pytest.mark.asyncio
async def test_a_tier_that_is_not_a_dict_changes_nothing():
    v = _view("lines_table", ["employee_line"])
    n, saved = await _run([v], None)
    assert n == 0
    saved.assert_not_awaited()
