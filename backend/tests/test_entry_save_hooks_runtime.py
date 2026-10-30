"""run_entry_save_hooks entry_type resolution — found via live browser testing.

hooks[].match.entry_type in operational-model.yaml is always the manifest ``key:``
(e.g. ``nis_schedule_line``). The dispatch payload used to build ``entry_type``
by slugifying the EntryType's display ``name`` instead (e.g. "Employee Line"
-> "employee_line") — silently never matching hook bindings for any entry
type whose display name doesn't happen to slugify identically to its
manifest key. hr_app's hooks "worked" only because "Time Off Request" ->
"time_off_request" happens to coincide with its key. payroll_filings' NIS/PAYE
line entry types are both named "Employee Line" (key: nis_schedule_line /
paye_filing_line) — a real, differently-named case that exposed the bug:
nis_calc_row never fired on row edits, no error, no log line, computed
columns stayed permanently null.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.models.edges import CONTAINS
from app.models.nodes import App, Entry, EntryType, Track, Workspace
from app.services.hooks import entry_save_runtime, resolver


@pytest.mark.asyncio
async def test_hook_dispatch_prefers_manifest_key_over_slugified_name(monkeypatch):
    captured: List[Dict[str, Any]] = []

    def fake_find_matching_bindings(
        workspace_id, point, payload, explicit_hook_key=None
    ):
        captured.append(payload)
        return []

    monkeypatch.setattr(resolver, "find_matching_bindings", fake_find_matching_bindings)

    track = await Track.create(
        title="Lines", owner_id="user-hooktest", workspace_id="ws-hooktest"
    )
    et = await EntryType.create(
        name="Employee Line",
        icon="user",
        form_schema={
            "_manifest_entry_type_key": "nis_schedule_line",
            "fields": [],
        },
        track_id=track.id,
        is_template=False,
    )
    entry = await Entry.create(
        type_id=et.id,
        title="",
        author_id="user-hooktest",
        track_id=track.id,
        custom_fields={},
    )

    await entry_save_runtime.run_entry_save_hooks(
        entry=entry,
        workspace_id="ws-hooktest",
        actor_id="user-hooktest",
        hook_point="entry.update",
    )

    assert len(captured) == 1
    # The bug: this used to be "employee_line" (slugified display name).
    assert captured[0]["entry_type"] == "nis_schedule_line"
    assert captured[0]["phase"] == "after_write"


@pytest.mark.asyncio
async def test_hook_dispatch_falls_back_to_slugified_name_when_no_manifest_key(
    monkeypatch,
):
    """Back-compat: entry types with no manifest key (ad-hoc, user-created via
    the UI, not from a operational-model.yaml) keep today's slugified-name behavior."""
    captured: List[Dict[str, Any]] = []

    def fake_find_matching_bindings(
        workspace_id, point, payload, explicit_hook_key=None
    ):
        captured.append(payload)
        return []

    monkeypatch.setattr(resolver, "find_matching_bindings", fake_find_matching_bindings)

    track = await Track.create(
        title="Ad Hoc", owner_id="user-hooktest2", workspace_id="ws-hooktest2"
    )
    et = await EntryType.create(
        name="Time Off Request",
        icon="calendar",
        form_schema={"fields": []},
        track_id=track.id,
        is_template=False,
    )
    entry = await Entry.create(
        type_id=et.id,
        title="",
        author_id="user-hooktest2",
        track_id=track.id,
        custom_fields={},
    )

    await entry_save_runtime.run_entry_save_hooks(
        entry=entry,
        workspace_id="ws-hooktest2",
        actor_id="user-hooktest2",
        hook_point="entry.update",
    )

    assert len(captured) == 1
    assert captured[0]["entry_type"] == "time_off_request"
    assert captured[0]["phase"] == "after_write"


@pytest.mark.asyncio
async def test_before_write_dispatch_includes_phase_and_manifest_key(monkeypatch):
    captured: List[Dict[str, Any]] = []

    def fake_find_matching_bindings(
        workspace_id, point, payload, explicit_hook_key=None
    ):
        captured.append(payload)
        return []

    monkeypatch.setattr(resolver, "find_matching_bindings", fake_find_matching_bindings)

    from app.services.hooks.entry_save_runtime import run_entry_before_write_hooks

    await run_entry_before_write_hooks(
        workspace_id="ws-hooktest3",
        actor_id="user-hooktest3",
        hook_point="entry.update",
        entry_type_slug="onboarding_form",
        entry_type_id="type-onboarding",
        operation="update",
        custom_fields={"status": "approved"},
        entry_id="form-1",
    )

    assert len(captured) == 1
    assert captured[0]["phase"] == "before_write"
    assert captured[0]["entry_type"] == "onboarding_form"


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["entry.create", "entry.update"])
async def test_save_hooks_only_dispatch_from_owning_bundle(monkeypatch, point):
    """Common type names must not trigger hooks from another installed App."""
    from app.services.hooks import registry, tool_dispatch

    calls = []
    bindings = [
        {"key": slug, "mode": "tool", "tool": slug, "_bundle_slug": slug}
        for slug in ("bundle-a", "bundle-b")
    ]
    monkeypatch.setattr(resolver, "find_matching_bindings", lambda *args: bindings)
    monkeypatch.setattr(
        registry, "get_workspace_tools", lambda ws: {b["tool"]: b for b in bindings}
    )

    async def get_type(_id):
        return SimpleNamespace(name="Shared", form_schema={})

    async def owner(_workspace, track_id):
        return {"own": "bundle-a", "other": "bundle-b"}.get(track_id, "")

    async def run(spec, payload, ctx):
        calls.append(spec["tool"])
        return {"custom_fields": {spec["tool"]: True}}

    monkeypatch.setattr(EntryType, "get", get_type)
    monkeypatch.setattr(entry_save_runtime, "_owning_bundle_slug", owner)
    monkeypatch.setattr(tool_dispatch, "run_tool", run)
    for track_id, expected in [
        ("own", ["bundle-a"]),
        ("other", ["bundle-b"]),
        ("custom", []),
    ]:
        calls.clear()
        entry = SimpleNamespace(id="entry", type_id="type", track_id=track_id)
        await entry_save_runtime.run_entry_save_hooks(
            entry=entry, workspace_id="ws", actor_id="u", hook_point=point
        )
        assert calls == expected
        calls.clear()
        result = await entry_save_runtime.run_entry_before_write_hooks(
            workspace_id="ws",
            track_id=track_id,
            actor_id="u",
            hook_point=point,
            entry_type_slug="shared",
            entry_type_id="type",
            operation="create",
            custom_fields={},
        )
        assert calls == expected
        assert list(result) == expected


@pytest.mark.asyncio
async def test_bundle_owner_requires_unique_same_workspace_app(monkeypatch):
    owners = []

    class FakeTrack:
        workspace_id = "ws"

        async def nodes(self, **kwargs):
            return owners

    async def get_track(_id):
        return FakeTrack()

    monkeypatch.setattr(Track, "get", get_track)
    assert await entry_save_runtime._owning_bundle_slug("ws", "track") == ""
    owners.append(SimpleNamespace(workspace_id="ws", installed_package_slug="bundle-a"))
    assert await entry_save_runtime._owning_bundle_slug("ws", "track") == "bundle-a"
    owners[0].lifecycle_state = "installing"
    assert await entry_save_runtime._owning_bundle_slug("ws", "track") == "bundle-a"
    owners[0].lifecycle_state = "paused"
    assert await entry_save_runtime._owning_bundle_slug("ws", "track") == ""
    owners[0].lifecycle_state = "active"
    owners[0].installed_package_slug = None
    owners[0].source_operational_model_slug = "bundle-a"
    assert await entry_save_runtime._owning_bundle_slug("ws", "track") == "bundle-a"
    assert await entry_save_runtime._owning_bundle_slug("other", "track") == ""
    owners.append(SimpleNamespace(workspace_id="ws", installed_package_slug="bundle-b"))
    from app.api.errors import ResourceConflictError

    with pytest.raises(ResourceConflictError, match="more than one owning App"):
        await entry_save_runtime._owning_bundle_slug("ws", "track")
    owners[:] = [
        SimpleNamespace(workspace_id="other", installed_package_slug="bundle-a")
    ]
    with pytest.raises(ResourceConflictError, match="different workspaces"):
        await entry_save_runtime._owning_bundle_slug("ws", "track")


@pytest.mark.asyncio
async def test_owner_resolution_uses_real_track_to_app_edge():
    from app.services.app_graph import catalog_app, catalog_track, catalog_workspace

    workspace = await Workspace.create(name="Hook test", kind="organization")
    await catalog_workspace(workspace)
    app = await App.create(
        name="Packaged", workspace_id=workspace.id, installed_package_slug="pkg"
    )
    await catalog_app(app)
    track = await Track.create(title="Items", workspace_id=workspace.id)
    await app.connect(track, edge=CONTAINS)
    await catalog_track(track)
    assert await entry_save_runtime._owning_bundle_slug(workspace.id, track.id) == "pkg"
    assert await entry_save_runtime._owning_bundle_slug("different-ws", track.id) == ""

    # A corrupt second owning edge must not silently disable save hooks.
    other = await App.create(
        name="Other package", workspace_id=workspace.id, installed_package_slug="other"
    )
    await catalog_app(other)
    await other.connect(track, edge=CONTAINS)
    from app.api.errors import ResourceConflictError

    with pytest.raises(ResourceConflictError, match="more than one owning App"):
        await entry_save_runtime._owning_bundle_slug(workspace.id, track.id)
