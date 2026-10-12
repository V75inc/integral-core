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

import uuid
from typing import Any, Dict, List

import bcrypt
import pytest
from jvspatial.api.auth.models import User as AuthUser

from app.models.edges import ANCHORS, IS_MEMBER_OF
from app.models.nodes import App, Entry, Track, User, Workspace
from app.schemas.policy import Decision

# ===== Fixtures =====


async def _make_space_with_track_template(name: str):
    """Create a currently authenticated owner and fully rooted canonical App."""
    from app.services.app_graph import (
        catalog_user,
        catalog_workspace,
        ensure_integral_app_graph,
    )
    from app.services.app_service import create_app_for_user

    await ensure_integral_app_graph(include_library=False)
    email = f"anchor-cascade-{uuid.uuid4().hex}@example.com"
    identity = await AuthUser.create(
        email=email,
        password_hash=bcrypt.hashpw(
            b"anchor-cascade-test-password", bcrypt.gensalt()
        ).decode(),
    )
    owner = await User.create(
        user_id=identity.id,
        display_name="Cascade owner",
        email_verified=True,
        verified_email=email,
    )
    await catalog_user(owner)
    workspace = await Workspace.create(name=name)
    await catalog_workspace(workspace)
    await owner.connect(workspace, edge=IS_MEMBER_OF, role="owner")
    app = await create_app_for_user(
        user_id=owner.id, name=name, workspace_id=workspace.id, include_seed_data=False
    )
    return owner, workspace.id, app


async def _make_source_track_and_entry(app_node: App, workspace_id: str):
    """Create a canonical Track and typed Entry admitted by the current owner."""
    from app.services.entry_create import create_entry_in_track
    from app.services.track_service import create_track_in_space

    track = await create_track_in_space(
        user_id=app_node.owner_user_id,
        title="Projects",
        workspace_id=workspace_id,
        app_id=app_node.id,
    )
    entry = await create_entry_in_track(
        track=track,
        user_id=app_node.owner_user_id,
        workspace_id=workspace_id,
        title="Project A",
    )
    assert entry.type_id
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
    from app.services.entry_create import create_entry_in_track
    from app.services.track_service import create_track_in_space

    track = await create_track_in_space(
        user_id=app_node.owner_user_id,
        title=title,
        workspace_id=workspace_id,
        app_id=app_node.id,
    )
    entries: List[Entry] = []
    for i in range(entry_count):
        entry = await create_entry_in_track(
            track=track,
            user_id=app_node.owner_user_id,
            workspace_id=workspace_id,
            title=f"{title} child {i}",
        )
        assert entry.type_id
        entries.append(entry)
    return track, entries


# ===== Tests =====


@pytest.mark.asyncio
async def test_entry_delete_walks_anchors_and_cascades(monkeypatch):
    """Deleting an entry hard-deletes the anchored Track AND its contained entries."""
    from app.services import entry_deletion

    owner, ws, app_node = await _make_space_with_track_template("Cascade Walk App")
    src_track, src_entry = await _make_source_track_and_entry(app_node, ws)
    anchored, anchored_entries = await _make_anchored_track_with_entries(
        app_node, workspace_id=ws, title="Anchored Walk", entry_count=2
    )
    await _attach_anchor(src_entry, anchored)

    await entry_deletion.delete_entry_fast(src_entry, actor_user_id=owner.id)

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

    original_evaluate = policy_engine.evaluate

    async def mock_evaluate(*, subject, action, resource, **kwargs):
        eval_calls.append({"action": action, "resource": resource, "subject": subject})
        return await original_evaluate(
            subject=subject, action=action, resource=resource, **kwargs
        )

    monkeypatch.setattr(policy_engine, "evaluate", mock_evaluate)

    owner, ws, app_node = await _make_space_with_track_template("EvalCascadeSpace")
    src_track, src_entry = await _make_source_track_and_entry(app_node, ws)
    anchored, _ = await _make_anchored_track_with_entries(
        app_node, workspace_id=ws, title="A1", entry_count=1
    )
    await _attach_anchor(src_entry, anchored)

    await entry_deletion.delete_entry_fast(src_entry, actor_user_id=owner.id)

    cascade_calls = [c for c in eval_calls if c["action"] == "anchor.cascade"]
    assert (
        len(cascade_calls) >= 1
    ), f"Expected at least one anchor.cascade evaluate call, got: {eval_calls}"


@pytest.mark.asyncio
async def test_cascade_denial_preserves_anchored_track(monkeypatch):
    """Decision(allowed=False) for anchor.cascade → anchored Track preserved."""
    from app.services import entry_deletion, policy_engine

    original_evaluate = policy_engine.evaluate

    async def mock_evaluate(*, subject, action, resource, **kwargs):
        if action == "anchor.cascade":
            return Decision(
                allowed=False,
                reason="fail_closed_no_policy",
            )
        return await original_evaluate(
            subject=subject, action=action, resource=resource, **kwargs
        )

    monkeypatch.setattr(policy_engine, "evaluate", mock_evaluate)

    owner, ws, app_node = await _make_space_with_track_template("DenyCascadeSpace")
    src_track, src_entry = await _make_source_track_and_entry(app_node, ws)
    anchored, anchored_entries = await _make_anchored_track_with_entries(
        app_node, workspace_id=ws, title="DenyAnchor", entry_count=1
    )
    await _attach_anchor(src_entry, anchored)

    src_entry_id = src_entry.id
    anchored_track_id = anchored.id
    anchored_entry_id = anchored_entries[0].id

    await entry_deletion.delete_entry_fast(src_entry, actor_user_id=owner.id)

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
    from app.services import entry_deletion

    owner, ws, app_node = await _make_space_with_track_template("OrphanSpace")
    src_track, src_entry = await _make_source_track_and_entry(app_node, ws)
    anchored, _ = await _make_anchored_track_with_entries(
        app_node, workspace_id=ws, title="OrphanAnchor", entry_count=1
    )
    await _attach_anchor(src_entry, anchored)

    src_entry_id = src_entry.id
    await entry_deletion.delete_entry_fast(src_entry, actor_user_id=owner.id)

    context = await app_node.get_context()
    orphans = await context.database.find(
        "edge",
        {
            "entity": ANCHORS._entity_name(),
            "$or": [{"source": src_entry_id}, {"target": anchored.id}],
        },
        limit=100,
    )
    assert not orphans, f"Found orphan ANCHORS edges: {orphans}"
    assert await Track.get(anchored.id) is None


@pytest.mark.asyncio
async def test_cascade_ws_broadcast_skip(monkeypatch):
    """anchor.cascade ChangeEvents are NOT broadcast to WS subscribers (Pitfall 7)."""
    from app.services import change_event as ce
    from app.services import entry_deletion

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
    import app.services.event_subscription_registry as esr

    monkeypatch.setattr(esr, "broadcast_change_event", mock_broadcast)

    owner, ws, app_node = await _make_space_with_track_template("SkipSpace")
    src_track, src_entry = await _make_source_track_and_entry(app_node, ws)
    anchored, _ = await _make_anchored_track_with_entries(
        app_node, workspace_id=ws, title="SkipAnchor", entry_count=1
    )
    await _attach_anchor(src_entry, anchored)

    await entry_deletion.delete_entry_fast(src_entry, actor_user_id=owner.id)

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
    from app.services import entry_deletion

    emit_calls: List[Dict[str, Any]] = []

    async def mock_emit(*, action, **kwargs):
        emit_calls.append({"action": action, **kwargs})

    monkeypatch.setattr(ce, "emit_change_event", mock_emit)

    owner, ws, app_node = await _make_space_with_track_template("MultiCascade")
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

    await entry_deletion.delete_entry_fast(src_entry, actor_user_id=owner.id)

    cascade_emits = [c for c in emit_calls if c["action"] == "anchor.cascade"]
    assert (
        len(cascade_emits) == 3
    ), f"Expected 3 anchor.cascade emits (one per anchored Track), got {len(cascade_emits)}: {cascade_emits}"


@pytest.mark.asyncio
async def test_entry_with_no_anchors_unchanged_behavior(monkeypatch):
    """Entry with no ANCHORS edges deletes without invoking the cascade gate."""
    from app.services import entry_deletion, policy_engine

    eval_calls: List[Dict[str, Any]] = []

    original_evaluate = policy_engine.evaluate

    async def mock_evaluate(*, subject, action, resource, **kwargs):
        eval_calls.append({"action": action})
        return await original_evaluate(
            subject=subject, action=action, resource=resource, **kwargs
        )

    monkeypatch.setattr(policy_engine, "evaluate", mock_evaluate)

    owner, ws, app_node = await _make_space_with_track_template("NoAnchorSpace")
    src_track, src_entry = await _make_source_track_and_entry(app_node, ws)
    # NO _attach_anchor call → entry has no ANCHORS edges.
    src_entry_id = src_entry.id

    await entry_deletion.delete_entry_fast(src_entry, actor_user_id=owner.id)

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
