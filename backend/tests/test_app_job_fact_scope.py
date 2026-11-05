"""App jobs may reuse an App's unchanged facts for a few seconds; nothing else does."""

import pytest

from app.middleware import permissions_cache as pc
from app.services import app_invariant_guards as guards

KEY = ("protected_state", "ws", "track")


def test_nothing_is_reused_outside_a_job_scope():
    pc.app_job_fact_put(KEY, {"x": 1})
    assert pc.app_job_fact_get(KEY) is None


def test_a_fact_is_reused_inside_the_scope_and_forgotten_after_it():
    with pc.app_job_fact_scope():
        pc.app_job_fact_put(KEY, {"x": 1})
        assert pc.app_job_fact_get(KEY) == {"x": 1}
    assert pc.app_job_fact_get(KEY) is None
    with pc.app_job_fact_scope():
        assert pc.app_job_fact_get(KEY) is None  # a new job starts empty


def test_a_definition_change_makes_remembered_facts_unusable():
    with pc.app_job_fact_scope():
        pc.app_job_fact_put(KEY, {"x": 1})
        pc.note_definition_changed()
        assert pc.app_job_fact_get(KEY) is None


def test_an_old_fact_expires(monkeypatch):
    import time

    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    with pc.app_job_fact_scope():
        pc.app_job_fact_put(KEY, {"x": 1})
        clock[0] += pc._APP_JOB_FACT_TTL - 0.1
        assert pc.app_job_fact_get(KEY) == {"x": 1}
        clock[0] += 0.2
        assert pc.app_job_fact_get(KEY) is None


@pytest.mark.asyncio
async def test_protected_state_is_read_once_per_track_inside_a_job_and_always_for_updates(monkeypatch):
    reads = []

    async def fresh(workspace_id, track_id, entry_id):
        reads.append((workspace_id, track_id, entry_id))
        return {"payslip": {"fields": ["gross"]}}

    monkeypatch.setattr(guards, "_canonical_protected_state_uncached", fresh)
    # An ordinary request reads every time.
    await guards._canonical_protected_state("ws", "track", "")
    await guards._canonical_protected_state("ws", "track", "")
    assert len(reads) == 2
    reads.clear()
    with pc.app_job_fact_scope():
        for _ in range(5):
            await guards._canonical_protected_state("ws", "track", "")
        await guards._canonical_protected_state("ws", "other-track", "")
        assert len(reads) == 2                      # one per track
        await guards._canonical_protected_state("ws", "track", "entry-1")
        await guards._canonical_protected_state("ws", "track", "entry-1")
        assert len(reads) == 4                      # updates name an entry and are never reused


@pytest.mark.asyncio
async def test_functions_that_move_or_delete_things_drop_remembered_facts():
    @pc.invalidates_job_facts
    async def delete_something():
        # A fact remembered while the change is running must not survive it.
        pc.app_job_fact_put(KEY, {"stale": True})
        return "deleted"

    with pc.app_job_fact_scope():
        pc.app_job_fact_put(KEY, {"x": 1})
        assert pc.app_job_fact_get(KEY) == {"x": 1}
        assert await delete_something() == "deleted"
        assert pc.app_job_fact_get(KEY) is None


@pytest.mark.asyncio
async def test_facts_are_dropped_even_when_the_change_fails():
    @pc.invalidates_job_facts
    async def failing_delete():
        raise RuntimeError("boom")

    with pc.app_job_fact_scope():
        pc.app_job_fact_put(KEY, {"x": 1})
        with pytest.raises(RuntimeError):
            await failing_delete()
        assert pc.app_job_fact_get(KEY) is None


def test_every_known_move_delete_and_lifecycle_path_is_marked():
    import inspect

    from app.services import (
        app_deletion,
        app_install,
        app_lifecycle,
        bulk_move_entries,
        entry_deletion,
        track_restructuring,
        workspace_rehome,
    )

    marked = [
        (entry_deletion, "delete_entry_fast"),
        (entry_deletion, "_delete_admitted_entry"),
        (app_deletion, "delete_track_and_nested_content"),
        (app_deletion, "delete_app_cascade"),
        (bulk_move_entries, "move_entries"),
        (track_restructuring, "merge_tracks"),
        (track_restructuring, "split_track"),
        (workspace_rehome, "rehome_workspace_resources"),
        (app_lifecycle, "install_app"),
        (app_lifecycle, "pause_app"),
        (app_lifecycle, "resume_app"),
        (app_install, "delete_tracks"),
    ]
    for module, name in marked:
        function = getattr(module, name)
        # functools.wraps leaves the original reachable; the wrapper is ours.
        assert "invalidates_job_facts" in inspect.getsource(function) or hasattr(function, "__wrapped__"), name
        assert getattr(function, "__wrapped__", None) is not None, f"{module.__name__}.{name} is not marked"


@pytest.mark.asyncio
async def test_a_record_is_located_once_inside_a_job_and_fresh_everywhere_else(monkeypatch):
    from app.services import resource_location as rl

    walks = []

    class FakeWalker:
        valid = True
        workspace_id = "ws"
        app_id = "app"
        track_id = "trk"
        _records = {}

        def __init__(self, allowed_app_states=None):
            pass

        async def read_node(self, model, resource_id, context):
            return object()

        async def spawn(self, node):
            walks.append(1)

        async def get_report(self):
            return []

    monkeypatch.setattr(rl, "ResourceLocationWalker", FakeWalker)
    monkeypatch.setattr(rl, "get_default_context", lambda: object())

    await rl.locate_resource("entry", "e1")
    await rl.locate_resource("entry", "e1")
    assert len(walks) == 2                                   # outside a job: always fresh
    walks.clear()
    with pc.app_job_fact_scope():
        for _ in range(4):
            await rl.locate_resource("entry", "e1")
        await rl.locate_resource("entry", "e2")
        assert len(walks) == 2                               # once per record
        await rl.locate_resource("entry", "e1", records_out={})
        assert len(walks) == 3                               # callers wanting the walked records walk fresh
        pc.note_definition_changed()
        await rl.locate_resource("entry", "e1")
        assert len(walks) == 4                               # a structural change forgets everything

