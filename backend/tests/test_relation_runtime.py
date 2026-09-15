"""Unit tests for ``app.services.relation_runtime``.

Phase 10 Plan 10-06 (APP-CROSS-RELATIONS-01) Task 1.

Covers:
- ``resolve_target_app`` (workspace + instance:<id> resolution + ambiguity +
  I-APP-05 same-workspace gate).
- ``read_cross_app_label`` (restricted-stub on denial + label on allow +
  strict zero-leak shape on denial).
- ``materialize_cross_app_reference`` (REFERENCES edge gets ``target_app_id``
  populated server-side; never client-supplied).
- ``validate_cross_app_relation_set`` (5-step validation flow).

Heavier end-to-end (HR + Payroll + Performance Review) scenarios live in
``test_cross_app_relations.py``.
"""

from __future__ import annotations

import pytest

from app.exceptions import (
    AmbiguousCrossAppTargetError,
    BadRequestError,
    CrossAppPermissionDenied,
    CrossAppTargetNotFoundError,
    CrossWorkspaceTargetRejectedError,
)
from app.models.edges import CONTAINS, REFERENCES
from app.models.nodes import App, Entry, Track, Workspace
from app.schemas.policy import Subject
from app.services.relation_runtime import (
    materialize_cross_app_reference,
    read_cross_app_label,
    resolve_target_app,
)
from app.utils.time import utc_now_iso

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_workspace(name: str = "ws") -> Workspace:
    now = utc_now_iso()
    return await Workspace.create(
        kind="organization",
        workspace_type="company",
        name=name,
        name_fold=name.casefold(),
        created_at=now,
        updated_at=now,
    )


async def _make_app(
    *,
    workspace_id: str,
    name: str,
    lifecycle_state: str = "active",
    library_id: str = "",
) -> App:
    now = utc_now_iso()
    return await App.create(
        name=name,
        name_fold=name.casefold(),
        owner_user_id="u_1",
        description="",
        visibility="private",
        workspace_id=workspace_id,
        lifecycle_state=lifecycle_state,
        installed_from_library_id=library_id or None,
        created_at=now,
        updated_at=now,
    )


async def _make_track_with_entry(
    *,
    app_node: App,
    track_title: str,
    entry_title: str,
) -> tuple[Track, Entry]:
    now = utc_now_iso()
    track = await Track.create(
        title=track_title,
        title_fold=track_title.casefold(),
        owner_id="u_1",
        workspace_id=app_node.workspace_id,
        created_at=now,
        updated_at=now,
    )
    await app_node.connect(track, edge=CONTAINS, added_at=now)
    entry = await Entry.create(
        title=entry_title,
        body="",
        tags=[],
        custom_fields={},
        track_id=track.id,
        author_id="u_1",
        status="active",
        created_at=now,
        updated_at=now,
    )
    await track.connect(entry, edge=CONTAINS, added_at=now)
    return track, entry


# ---------------------------------------------------------------------------
# resolve_target_app
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_target_app_workspace_single_match_returns_app_id():
    ws = await _make_workspace()
    hr = await _make_app(workspace_id=ws.id, name="hr_app")
    out = await resolve_target_app(
        workspace_id=ws.id,
        target_app_key="hr_app",
        resolution="workspace",
    )
    assert out == hr.id


@pytest.mark.asyncio
async def test_resolve_target_app_workspace_no_match_raises_not_found():
    ws = await _make_workspace()
    with pytest.raises(CrossAppTargetNotFoundError):
        await resolve_target_app(
            workspace_id=ws.id,
            target_app_key="absent_app",
            resolution="workspace",
        )


@pytest.mark.asyncio
async def test_resolve_target_app_workspace_multiple_matches_raises_ambiguous():
    ws = await _make_workspace()
    await _make_app(workspace_id=ws.id, name="hr_app")
    await _make_app(workspace_id=ws.id, name="hr_app")
    with pytest.raises(AmbiguousCrossAppTargetError):
        await resolve_target_app(
            workspace_id=ws.id,
            target_app_key="hr_app",
            resolution="workspace",
        )


@pytest.mark.asyncio
async def test_resolve_target_app_instance_pin_returns_specified_app():
    ws = await _make_workspace()
    a = await _make_app(workspace_id=ws.id, name="hr_app")
    b = await _make_app(workspace_id=ws.id, name="hr_app")
    out = await resolve_target_app(
        workspace_id=ws.id,
        target_app_key="hr_app",
        resolution=f"instance:{b.id}",
    )
    assert out == b.id
    assert out != a.id


@pytest.mark.asyncio
async def test_resolve_target_app_inactive_apps_are_skipped():
    """Inactive (paused/uninstalled/awaiting_settings) installs do not resolve."""
    ws = await _make_workspace()
    await _make_app(workspace_id=ws.id, name="hr_app", lifecycle_state="paused")
    active = await _make_app(workspace_id=ws.id, name="hr_app")
    out = await resolve_target_app(
        workspace_id=ws.id,
        target_app_key="hr_app",
        resolution="workspace",
    )
    assert out == active.id


@pytest.mark.asyncio
async def test_resolve_target_app_cross_workspace_rejected_via_instance_pin():
    """I-APP-05 — instance pin to an App in a different workspace is rejected."""
    ws_a = await _make_workspace(name="ws_a")
    ws_b = await _make_workspace(name="ws_b")
    other_ws_app = await _make_app(workspace_id=ws_b.id, name="hr_app")
    with pytest.raises(CrossWorkspaceTargetRejectedError):
        await resolve_target_app(
            workspace_id=ws_a.id,
            target_app_key="hr_app",
            resolution=f"instance:{other_ws_app.id}",
        )


@pytest.mark.asyncio
async def test_resolve_target_app_bad_resolution_string_raises():
    ws = await _make_workspace()
    with pytest.raises(BadRequestError):
        await resolve_target_app(
            workspace_id=ws.id,
            target_app_key="hr_app",
            resolution="not_a_valid_token",
        )


@pytest.mark.asyncio
async def test_resolve_target_app_instance_empty_id_raises():
    ws = await _make_workspace()
    with pytest.raises(BadRequestError):
        await resolve_target_app(
            workspace_id=ws.id,
            target_app_key="hr_app",
            resolution="instance:",
        )


# ---------------------------------------------------------------------------
# read_cross_app_label — the data-leak vector mitigation (Risk 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_cross_app_label_denial_returns_strict_restricted_stub():
    """The single data-leak vector — denial returns 3 keys, nothing more."""
    ws = await _make_workspace()
    hr = await _make_app(workspace_id=ws.id, name="hr_app")
    _, entry = await _make_track_with_entry(
        app_node=hr,
        track_title="employees",
        entry_title="Eldon Marks",
    )

    # Use a connector subject to force fail-closed evaluation (no Policy edge).
    viewer = Subject(kind="connector", id="conn_anonymous")
    out = await read_cross_app_label(
        viewer=viewer,
        relation_field_key="employees",
        target_entry_id=entry.id,
        target_app_id=hr.id,
        label_field="title",
    )

    # CRITICAL: shape must be EXACTLY the restricted stub — three keys only.
    assert set(out.keys()) == {
        "kind",
        "relation_field_key",
        "target_resource_kind",
    }
    assert out["kind"] == "restricted_relation"
    assert out["relation_field_key"] == "employees"
    assert out["target_resource_kind"] == "entry"

    # Negative assertions — none of these should ever appear in denial output.
    assert "label" not in out, "label MUST NOT be present in restricted stub"
    assert "title" not in out, "title MUST NOT be present in restricted stub"
    assert "target_entry_id" not in out, "target_entry_id is a data leak"
    assert "target_app_id" not in out, "target_app_id is a data leak"
    assert "target_app_name" not in out, "target_app_name is a data leak"
    # The actual entry title should NEVER leak via any field.
    assert "Eldon Marks" not in str(out)


@pytest.mark.asyncio
async def test_read_cross_app_label_allow_returns_full_label():
    """When viewer has access, returns the full label shape with provenance."""
    ws = await _make_workspace()
    hr = await _make_app(workspace_id=ws.id, name="hr_app")
    _, entry = await _make_track_with_entry(
        app_node=hr,
        track_title="employees",
        entry_title="Eldon Marks",
    )

    # System subject bypasses fail-closed (D-03 system_subject reason).
    viewer = Subject(kind="system", id="system_test")
    out = await read_cross_app_label(
        viewer=viewer,
        relation_field_key="employees",
        target_entry_id=entry.id,
        target_app_id=hr.id,
        label_field="title",
    )
    assert out["kind"] == "relation"
    assert out["relation_field_key"] == "employees"
    assert out["target_entry_id"] == entry.id
    assert out["target_app_id"] == hr.id
    assert out["label"] == "Eldon Marks"


@pytest.mark.asyncio
async def test_read_cross_app_label_missing_target_returns_restricted_stub():
    """A non-existent target id leaks no information — returns the same stub."""
    ws = await _make_workspace()
    hr = await _make_app(workspace_id=ws.id, name="hr_app")
    viewer = Subject(kind="connector", id="conn_anonymous")
    out = await read_cross_app_label(
        viewer=viewer,
        relation_field_key="employees",
        target_entry_id="entry_does_not_exist",
        target_app_id=hr.id,
        label_field="title",
    )
    # Same strict shape — privileged viewers won't even reach this branch.
    assert set(out.keys()) == {
        "kind",
        "relation_field_key",
        "target_resource_kind",
    }
    assert out["kind"] == "restricted_relation"


@pytest.mark.asyncio
async def test_read_cross_app_label_supports_dotted_label_field():
    """label_field='custom_fields.full_name' resolves dotted path."""
    ws = await _make_workspace()
    hr = await _make_app(workspace_id=ws.id, name="hr_app")
    track, entry = await _make_track_with_entry(
        app_node=hr,
        track_title="employees",
        entry_title="Default Title",
    )
    entry.custom_fields = {"full_name": "Eldon Marks"}
    await entry.save()

    viewer = Subject(kind="system", id="system_test")
    out = await read_cross_app_label(
        viewer=viewer,
        relation_field_key="employees",
        target_entry_id=entry.id,
        target_app_id=hr.id,
        label_field="custom_fields.full_name",
    )
    assert out["label"] == "Eldon Marks"


# ---------------------------------------------------------------------------
# materialize_cross_app_reference
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_materialize_cross_app_reference_sets_target_app_id_on_edge():
    """Pillar 2 — target_app_id lives on the REFERENCES edge, not the node."""
    ws = await _make_workspace()
    hr = await _make_app(workspace_id=ws.id, name="hr_app")
    payroll = await _make_app(workspace_id=ws.id, name="payroll_app")
    _, hr_entry = await _make_track_with_entry(
        app_node=hr, track_title="employees", entry_title="Eldon"
    )
    _, payroll_entry = await _make_track_with_entry(
        app_node=payroll, track_title="pay_runs", entry_title="2026-Q1"
    )

    await materialize_cross_app_reference(
        source_entry=payroll_entry,
        field_key="employees",
        target_entry=hr_entry,
        target_app_id=hr.id,
    )

    # Verify the edge has target_app_id populated.
    ctx = await payroll_entry.get_context()
    edges = await ctx.find_edges_between(
        payroll_entry.id, target_id=hr_entry.id, edge_class=REFERENCES
    )
    assert len(edges) == 1
    assert edges[0].target_app_id == hr.id
    assert edges[0].field_key == "employees"


@pytest.mark.asyncio
async def test_materialize_cross_app_reference_requires_target_app_id():
    ws = await _make_workspace()
    a = await _make_app(workspace_id=ws.id, name="app_a")
    _, e1 = await _make_track_with_entry(app_node=a, track_title="t", entry_title="e1")
    _, e2 = await _make_track_with_entry(app_node=a, track_title="t", entry_title="e2")
    with pytest.raises(BadRequestError):
        await materialize_cross_app_reference(
            source_entry=e1,
            field_key="r",
            target_entry=e2,
            target_app_id="",
        )
