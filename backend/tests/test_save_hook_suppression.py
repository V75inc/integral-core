"""An App can skip its own entry-save hooks for a batch, and nothing else.

Payroll's finalize fills its filing lines itself; the per-line save hooks
would only step aside, yet each dispatch still cost a staging bracket and an
audit record (about 1,400 audit records for a 110-person finalize). Skipped
hooks are not dispatched at all. Only the calling App's own tools can be
named, other Apps' hooks keep firing, and the skip ends when released.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.models.nodes import EntryType
from app.services.hooks import entry_save_runtime, registry, resolver, tool_dispatch
from app.services.hooks.registry import ToolContext

TOOLS = {
    "a_mirror": {"key": "a_mirror", "_bundle_slug": "bundle-a"},
    "a_guard": {"key": "a_guard", "_bundle_slug": "bundle-a"},
    "b_mirror": {"key": "b_mirror", "_bundle_slug": "bundle-b"},
}


@pytest.fixture
def wired(monkeypatch):
    calls = []
    bindings = [
        {"key": k, "mode": "tool", "tool": k, "_bundle_slug": s["_bundle_slug"]}
        for k, s in TOOLS.items()
    ]
    monkeypatch.setattr(resolver, "find_matching_bindings", lambda *args: bindings)
    monkeypatch.setattr(registry, "get_workspace_hooks", lambda *args: bindings)
    monkeypatch.setattr(registry, "get_workspace_tools", lambda ws: TOOLS)

    async def get_type(_id):
        return SimpleNamespace(name="Line", form_schema={})

    async def owner(_workspace, track_id):
        return {"a-track": "bundle-a", "b-track": "bundle-b"}.get(track_id, "")

    async def run(spec, payload, ctx):
        calls.append(spec["key"])
        return {"custom_fields": {spec["key"]: True}}

    monkeypatch.setattr(EntryType, "get", get_type)
    monkeypatch.setattr(entry_save_runtime, "_owning_bundle_slug", owner)
    monkeypatch.setattr(tool_dispatch, "run_tool", run)
    return calls


async def _after_write(track_id):
    entry = SimpleNamespace(id="entry", type_id="type", track_id=track_id)
    await entry_save_runtime.run_entry_save_hooks(
        entry=entry, workspace_id="ws", actor_id="u", hook_point="entry.create"
    )


async def _before_write(track_id):
    return await entry_save_runtime.run_entry_before_write_hooks(
        workspace_id="ws",
        track_id=track_id,
        actor_id="u",
        hook_point="entry.create",
        entry_type_slug="line",
        entry_type_id="type",
        operation="create",
        custom_fields={},
    )


def _ctx(bundle="bundle-a", **kw):
    return ToolContext(
        user_id="u", workspace_id="ws", scope="s", bundle_slug=bundle, **kw
    )


@pytest.mark.asyncio
async def test_suppressed_own_hook_is_not_dispatched_others_still_run(wired):
    ctx = _ctx()
    token = ctx.suppress_own_save_hooks(["a_mirror"])
    try:
        await _after_write("a-track")
        assert wired == ["a_guard"]
        wired.clear()
        # before_write: the skipped tool contributes no fields, the rest still merge.
        assert await _before_write("a-track") == {"a_guard": True}
        assert wired == ["a_guard"]
        wired.clear()
        # Another App's hooks on its own records are untouched.
        await _after_write("b-track")
        assert wired == ["b_mirror"]
    finally:
        ctx.release_save_hooks(token)
    wired.clear()
    await _after_write("a-track")
    assert wired == ["a_mirror", "a_guard"]


@pytest.mark.asyncio
async def test_cannot_suppress_another_apps_tool_or_unknown_tool(wired):
    with pytest.raises(PermissionError, match="not a tool of this App"):
        _ctx().suppress_own_save_hooks(["b_mirror"])
    with pytest.raises(PermissionError, match="not a tool of this App"):
        _ctx().suppress_own_save_hooks(["no_such_tool"])
    with pytest.raises(PermissionError, match="only a running app tool"):
        _ctx(bundle="").suppress_own_save_hooks(["a_mirror"])
    with pytest.raises(PermissionError, match="read-only"):
        _ctx(read_only=True).suppress_own_save_hooks(["a_mirror"])
    # Nothing was left switched off by the refused calls.
    await _after_write("a-track")
    assert wired == ["a_mirror", "a_guard"]


@pytest.mark.asyncio
async def test_suppression_follows_spawned_tasks_but_not_other_requests(wired):
    ctx = _ctx()
    started = asyncio.Event()
    release = asyncio.Event()
    seen = {}

    async def unrelated_request():
        await started.wait()
        await _after_write("a-track")
        seen["other"] = list(wired)
        release.set()

    other = asyncio.create_task(unrelated_request())
    token = ctx.suppress_own_save_hooks(["a_mirror"])
    try:
        # Child tasks (the batch's parallel writes) inherit the skip.
        await asyncio.gather(_after_write("a-track"))
        assert wired == ["a_guard"]
        wired.clear()
        started.set()
        await release.wait()
    finally:
        ctx.release_save_hooks(token)
    await other
    assert seen["other"] == ["a_mirror", "a_guard"]


def test_check_is_scoped_to_workspace():
    ctx = _ctx()
    token = registry._SUPPRESSED_SAVE_HOOKS.set(
        frozenset({("ws", "bundle-a", "a_mirror")})
    )
    try:
        assert registry.save_hook_suppressed("ws", TOOLS["a_mirror"])
        assert not registry.save_hook_suppressed("ws-2", TOOLS["a_mirror"])
        assert not registry.save_hook_suppressed("ws", TOOLS["a_guard"])
    finally:
        ctx.release_save_hooks(token)
