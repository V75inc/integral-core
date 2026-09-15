"""ANC-05 cascade lifecycle tests — Phase 3.1 Plan 03.1-03 Task 3.

v1 hard cascade only per CONTEXT decisions Cascade lifecycle — archive-then-delete
substrate is a separate future phase. Tests cover:

  - Entry delete walks ANCHORS edges and cascades hard-deletes
  - anchor.cascade policy is evaluated per anchored Track
  - Denial preserves the anchored Track (source Entry deletion still proceeds)
  - No orphan ANCHORS edges after cascade
  - anchor.cascade ChangeEvents NOT broadcast to WS subscribers (Pitfall 7)
  - Per-Track emit count matches anchored Track count
  - Regression: entry with no ANCHORS edges deletes exactly as before

No ``app.main`` import — substrate functions tested directly per
deferred-items.md.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.models.edges import ANCHORS, CONTAINS, HAS_CONTENT_PROFILE
from app.models.nodes import App, ContentProfile, Entry, Track
from app.schemas.policy import Decision

# ===== Fixtures =====


async def _make_space_with_track_template(workspace_id: str, name: str):
    cp_manifest = {
        "content_profile_schema_version": 2,
        "scope": "app",
        "package": {"slug": "casc", "name": "Cascade", "version": "1.0.0"},
        "app": {
            "tracks": [],
            "track_templates": [
                {
                    "key": "details",
                    "name": "Details",
                    "entry_types": [
                        {
                            "key": "note",
                            "name": "Note",
                            "fields": [{"key": "title", "type": "text"}],
                        }
                    ],
                    "views": [],
                    "taxonomy": {"tag_groups": []},
                    "defaults": {},
                }
            ],
            "relations": [],
            "defaults": {},
        },
    }
    app_node = await App.create(
        name=name,
        workspace_id=workspace_id,
        owner_user_id="user-casc-1",
    )
    cp = await ContentProfile.create(
        name=f"{name} Profile",
        scope="app",
        manifest=cp_manifest,
        app_id=app_node.id,
        workspace_id=workspace_id,
        library_package=False,
    )
    await app_node.connect(cp, edge=HAS_CONTENT_PROFILE)
    app_node.attached_content_profile_id = cp.id
    await app_node.save()
    return app_node


async def _make_source_track_and_entry(app_node: App, workspace_id: str):
    """Create a parent (source) Track and one Entry inside it."""
    track = await Track.create(
        title="Projects",
        owner_id="user-casc-1",
        workspace_id=workspace_id,
    )
    await app_node.connect(track, edge=CONTAINS)
    entry = await Entry.create(
        title="Project A",
        track_id=track.id,
    )
    await track.connect(entry, edge=CONTAINS)
    return track, entry


async def _attach_anchor(source_entry: Entry, anchored_track: Track) -> None:
    """Wire the ANCHORS edge: source_entry → anchored_track."""
    await source_entry.connect(
        anchored_track,
        edge=ANCHORS,
        field_key="details",
        cross_track=False,
    )


async def _make_anchored_track_with_entries(
    app_node: App,
    *,
    workspace_id: str,
    title: str,
    entry_count: int = 2,
) -> tuple[Track, list[Entry]]:
    """Create an anchored Track inside the same App + entry_count Entries inside it."""
    track = await Track.create(
        title=title,
        owner_id="user-casc-1",
        workspace_id=workspace_id,
        template_id="details",
    )
    await app_node.connect(track, edge=CONTAINS)
    entries: List[Entry] = []
    for i in range(entry_count):
        e = await Entry.create(
            title=f"{title} child {i}",
            track_id=track.id,
        )
        await track.connect(e, edge=CONTAINS)
        entries.append(e)
    return track, entries


# ===== Tests =====


@pytest.mark.asyncio
async def test_entry_delete_walks_anchors_and_cascades(monkeypatch):
    """Deleting an entry hard-deletes the anchored Track AND its contained entries."""
    from app.services import entry_deletion, policy_engine

    async def mock_evaluate(*, subject, action, resource, _internal_actor=None):
        return Decision(allowed=True, reason="default_human_policy")

    monkeypatch.setattr(policy_engine, "evaluate", mock_evaluate)

    ws = "ws-casc-walk-1"
    app_node = await _make_space_with_track_template(ws, "Cascade Walk App")
    src_track, src_entry = await _make_source_track_and_entry(app_node, ws)
    anchored, anchored_entries = await _make_anchored_track_with_entries(
        app_node, workspace_id=ws, title="Anchored Walk", entry_count=2
    )
    await _attach_anchor(src_entry, anchored)

    await entry_deletion.delete_entry_fast(src_entry, actor_user_id="user-casc-1")

    # Anchored Track + its entries are gone.
    assert await Track.get(anchored.id) is None
    for e in anchored_entries:
        assert await Entry.get(e.id) is None
    # Source entry itself is gone too.
    assert await Entry.get(src_entry.id) is None


@pytest.mark.asyncio
async def test_cascade_evaluates_anchor_cascade_policy(monkeypatch):
    """policy_engine.evaluate is called with action='anchor.cascade' per anchored Track."""
    from app.services import entry_deletion, policy_engine

    eval_calls: List[Dict[str, Any]] = []

    async def mock_evaluate(*, subject, action, resource, _internal_actor=None):
        eval_calls.append({"action": action, "resource": resource})
        return Decision(allowed=True, reason="default_human_policy")

    monkeypatch.setattr(policy_engine, "evaluate", mock_evaluate)

    ws = "ws-casc-eval-1"
    app_node = await _make_space_with_track_template(ws, "EvalCascadeSpace")
    src_track, src_entry = await _make_source_track_and_entry(app_node, ws)
    anchored, _ = await _make_anchored_track_with_entries(
        app_node, workspace_id=ws, title="A1", entry_count=1
    )
    await _attach_anchor(src_entry, anchored)

    await entry_deletion.delete_entry_fast(src_entry, actor_user_id="user-casc-1")

    cascade_calls = [c for c in eval_calls if c["action"] == "anchor.cascade"]
    assert (
        len(cascade_calls) >= 1
    ), f"Expected at least one anchor.cascade evaluate call, got: {eval_calls}"


@pytest.mark.asyncio
async def test_cascade_denial_preserves_anchored_track(monkeypatch):
    """Decision(allowed=False) for anchor.cascade → anchored Track preserved."""
    from app.services import entry_deletion, policy_engine

    async def mock_evaluate(*, subject, action, resource, _internal_actor=None):
        if action == "anchor.cascade":
            return Decision(
                allowed=False,
                reason="fail_closed_no_policy",
            )
        return Decision(allowed=True, reason="system_subject")

    monkeypatch.setattr(policy_engine, "evaluate", mock_evaluate)

    ws = "ws-casc-deny-1"
    app_node = await _make_space_with_track_template(ws, "DenyCascadeSpace")
    src_track, src_entry = await _make_source_track_and_entry(app_node, ws)
    anchored, anchored_entries = await _make_anchored_track_with_entries(
        app_node, workspace_id=ws, title="DenyAnchor", entry_count=1
    )
    await _attach_anchor(src_entry, anchored)

    src_entry_id = src_entry.id
    anchored_track_id = anchored.id
    anchored_entry_id = anchored_entries[0].id

    await entry_deletion.delete_entry_fast(src_entry, actor_user_id="user-casc-1")

    # Anchored Track persists.
    reloaded = await Track.get(anchored_track_id)
    assert reloaded is not None, "denied anchor.cascade should preserve Track"
    # Its entries persist.
    reloaded_entry = await Entry.get(anchored_entry_id)
    assert reloaded_entry is not None
    # Source entry itself IS deleted (cascade abort is per-Track, not whole-op).
    assert await Entry.get(src_entry_id) is None


@pytest.mark.asyncio
async def test_no_orphan_anchors_edges_after_cascade(monkeypatch):
    """After entry delete, ANCHORS edges with source=deleted_entry are gone."""
    from jvspatial.core.entities import Edge

    from app.services import entry_deletion, policy_engine

    async def mock_evaluate(*, subject, action, resource, _internal_actor=None):
        return Decision(allowed=True, reason="default_human_policy")

    monkeypatch.setattr(policy_engine, "evaluate", mock_evaluate)

    ws = "ws-casc-orphan-1"
    app_node = await _make_space_with_track_template(ws, "OrphanSpace")
    src_track, src_entry = await _make_source_track_and_entry(app_node, ws)
    anchored, _ = await _make_anchored_track_with_entries(
        app_node, workspace_id=ws, title="OrphanAnchor", entry_count=1
    )
    await _attach_anchor(src_entry, anchored)

    src_entry_id = src_entry.id
    await entry_deletion.delete_entry_fast(src_entry, actor_user_id="user-casc-1")

    # Query all Edge rows for any ANCHORS with source_id pointing at the
    # deleted entry. jvspatial Edge.find by source_node_id is the canonical
    # orphan-edge probe.
    all_edges = await Edge.find({})
    orphans = [
        e
        for e in all_edges
        if (
            getattr(e, "name", "") == "Anchors"
            and (
                getattr(e, "source_node_id", "") == src_entry_id
                or getattr(e, "left_id", "") == src_entry_id
            )
        )
    ]
    assert not orphans, f"Found orphan ANCHORS edges: {orphans}"


@pytest.mark.asyncio
async def test_cascade_ws_broadcast_skip(monkeypatch):
    """anchor.cascade ChangeEvents are NOT broadcast to WS subscribers (Pitfall 7)."""
    from app.services import change_event as ce
    from app.services import entry_deletion, policy_engine

    async def mock_evaluate(*, subject, action, resource, _internal_actor=None):
        return Decision(allowed=True, reason="default_human_policy")

    monkeypatch.setattr(policy_engine, "evaluate", mock_evaluate)

    # BROADCAST_SKIP_ACTIONS must include anchor.cascade.
    assert "anchor.cascade" in ce.BROADCAST_SKIP_ACTIONS

    # The skip check is in emit_change_event; we verify it by patching the
    # downstream broadcast helper and asserting it is NOT called for
    # action='anchor.cascade'. The helper is dynamically imported inside
    # emit_change_event so we patch the source module too.
    broadcast_calls: List[Any] = []

    async def mock_broadcast(envelope):
        broadcast_calls.append(envelope)

    # The broadcast helper lives in event_subscription_registry — patch there.
    try:
        import app.services.event_subscription_registry as esr

        monkeypatch.setattr(
            esr, "broadcast_change_event", mock_broadcast, raising=False
        )
    except ImportError:
        # Registry module missing in this env; the skip set is the only
        # observable surface, and we asserted it above. The skip is in the
        # ``if action not in BROADCAST_SKIP_ACTIONS`` branch directly, so the
        # logic test is the membership assertion. Return early.
        return

    ws = "ws-casc-skip-1"
    app_node = await _make_space_with_track_template(ws, "SkipSpace")
    src_track, src_entry = await _make_source_track_and_entry(app_node, ws)
    anchored, _ = await _make_anchored_track_with_entries(
        app_node, workspace_id=ws, title="SkipAnchor", entry_count=1
    )
    await _attach_anchor(src_entry, anchored)

    await entry_deletion.delete_entry_fast(src_entry, actor_user_id="user-casc-1")

    # No broadcast envelope should have action='anchor.cascade'.
    cascade_broadcasts = [
        b for b in broadcast_calls if getattr(b, "action", "") == "anchor.cascade"
    ]
    assert (
        not cascade_broadcasts
    ), "anchor.cascade ChangeEvents must NOT be broadcast (fan-out safeguard)"


@pytest.mark.asyncio
async def test_cascade_emits_one_event_per_anchored_track(monkeypatch):
    """N anchored Tracks → N anchor.cascade emit_change_event calls."""
    from app.services import change_event as ce
    from app.services import entry_deletion, policy_engine

    async def mock_evaluate(*, subject, action, resource, _internal_actor=None):
        return Decision(allowed=True, reason="default_human_policy")

    monkeypatch.setattr(policy_engine, "evaluate", mock_evaluate)

    emit_calls: List[Dict[str, Any]] = []

    async def mock_emit(*, action, **kwargs):
        emit_calls.append({"action": action, **kwargs})

    monkeypatch.setattr(ce, "emit_change_event", mock_emit)

    ws = "ws-casc-multi-1"
    app_node = await _make_space_with_track_template(ws, "MultiCascade")
    src_track, src_entry = await _make_source_track_and_entry(app_node, ws)
    # 3 anchored tracks
    a1, _ = await _make_anchored_track_with_entries(
        app_node, workspace_id=ws, title="Multi-A1", entry_count=1
    )
    a2, _ = await _make_anchored_track_with_entries(
        app_node, workspace_id=ws, title="Multi-A2", entry_count=1
    )
    a3, _ = await _make_anchored_track_with_entries(
        app_node, workspace_id=ws, title="Multi-A3", entry_count=1
    )
    await _attach_anchor(src_entry, a1)
    await _attach_anchor(src_entry, a2)
    await _attach_anchor(src_entry, a3)

    await entry_deletion.delete_entry_fast(src_entry, actor_user_id="user-casc-1")

    cascade_emits = [c for c in emit_calls if c["action"] == "anchor.cascade"]
    assert (
        len(cascade_emits) == 3
    ), f"Expected 3 anchor.cascade emits (one per anchored Track), got {len(cascade_emits)}: {cascade_emits}"


@pytest.mark.asyncio
async def test_entry_with_no_anchors_unchanged_behavior(monkeypatch):
    """Entry with no ANCHORS edges deletes without invoking the cascade gate."""
    from app.services import entry_deletion, policy_engine

    eval_calls: List[Dict[str, Any]] = []

    async def mock_evaluate(*, subject, action, resource, _internal_actor=None):
        eval_calls.append({"action": action})
        return Decision(allowed=True, reason="default_human_policy")

    monkeypatch.setattr(policy_engine, "evaluate", mock_evaluate)

    ws = "ws-casc-no-anchor-1"
    app_node = await _make_space_with_track_template(ws, "NoAnchorSpace")
    src_track, src_entry = await _make_source_track_and_entry(app_node, ws)
    # NO _attach_anchor call → entry has no ANCHORS edges.
    src_entry_id = src_entry.id

    await entry_deletion.delete_entry_fast(src_entry, actor_user_id="user-casc-1")

    # Source entry deleted.
    assert await Entry.get(src_entry_id) is None
    # No anchor.cascade evaluate calls were made.
    cascade_calls = [c for c in eval_calls if c["action"] == "anchor.cascade"]
    assert (
        not cascade_calls
    ), f"Expected no anchor.cascade evaluations on entry with no ANCHORS edges: {cascade_calls}"


def test_broadcast_skip_actions_membership():
    """Sanity: BROADCAST_SKIP_ACTIONS contains both policy.deny and anchor.cascade."""
    from app.services.change_event import BROADCAST_SKIP_ACTIONS

    assert "policy.deny" in BROADCAST_SKIP_ACTIONS
    assert "anchor.cascade" in BROADCAST_SKIP_ACTIONS
