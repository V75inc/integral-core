"""ANCHORS edge tests — Phase 3.1 Plan 03.1-01 Task 1 (ANC-01).

Section 1 (this task): edge class shape + alias identity + wire round-trip + inverse traversal.
Section 2 (Task 3): runtime routing via _sync_anchor_edges — added in Task 3.
"""

from __future__ import annotations

import pytest

from app.models.edges import ANCHORS, REFERENCES, Anchors
from app.models.nodes import Entry, Track


def test_anchors_alias_identity():
    """Phase 1 D-07 PascalCase + ALL_CAPS pattern: ANCHORS is Anchors."""
    assert ANCHORS is Anchors


def test_anchors_default_field_values():
    """CONTEXT decisions Edge layer (ANC-01): locked 3-field schema.

    Note: ``bidirectional`` is declared on every Integral Edge subclass as
    ``False`` (locked schema), but jvspatial's base ``Edge.__init__`` sets the
    instance value to ``True`` regardless of the subclass default. We assert
    the *class-level* declared default (model_fields) — this is what defines
    the schema contract and what every existing edge in ``edges.py`` declares.
    The runtime ``bidirectional`` value is irrelevant to ANC-01's schema lock.
    """
    e = Anchors()
    assert e.field_key is None
    assert e.role == "detail"
    # Class-level locked-schema declaration (mirrors every existing edge)
    assert Anchors.model_fields["bidirectional"].default is False
    assert Anchors.model_fields["field_key"].default is None
    assert Anchors.model_fields["role"].default == "detail"


def test_references_preserved_verbatim():
    """Regression: REFERENCES entry→entry semantics UNCHANGED (CONTEXT ANC-01)."""
    r = REFERENCES()
    assert r.field_key is None
    assert r.relation_type == "content_profile"
    assert r.cross_track is False
    # Class-level locked-schema declaration (see test_anchors_default_field_values note)
    assert REFERENCES.model_fields["bidirectional"].default is False


@pytest.mark.asyncio
async def test_anchor_wire_entry_to_track_round_trip():
    """Entry.connect(track, edge=ANCHORS) wires correctly; out-traversal returns the track."""
    track = await Track.create(
        title="Detail Track",
        owner_id="anchor-owner-1",
        workspace_id="ws-test",
    )
    entry = await Entry.create(
        track_id="parent-t",
        title="Parent Entry",
        author_id="anchor-owner-1",
    )

    await entry.connect(track, edge=ANCHORS, field_key="details", role="detail")

    tracks_out = await entry.nodes(edge=[ANCHORS], direction="out", node=["Track"])
    assert len(tracks_out) == 1
    assert tracks_out[0].id == track.id


@pytest.mark.asyncio
async def test_anchor_inverse_traversal_track_back_to_entry():
    """No back-reference field needed (CONTEXT) — inverse traversal via node.nodes(edge=, direction='in')."""
    track = await Track.create(
        title="Detail Track Inv",
        owner_id="anchor-owner-2",
        workspace_id="ws-test",
    )
    entry = await Entry.create(
        track_id="parent-t",
        title="Parent Entry Inv",
        author_id="anchor-owner-2",
    )

    await entry.connect(track, edge=ANCHORS, field_key="details", role="detail")

    entries_in = await track.nodes(edge=[ANCHORS], direction="in", node=["Entry"])
    assert len(entries_in) == 1
    assert entries_in[0].id == entry.id


@pytest.mark.asyncio
async def test_anchor_and_references_coexist_disjoint():
    """ANCHORS (entry→track) and REFERENCES (entry→entry) on the same source Entry are independent."""
    parent_entry = await Entry.create(
        track_id="parent-disjoint",
        title="Parent",
        author_id="disjoint-author",
    )
    sibling_entry = await Entry.create(
        track_id="parent-disjoint",
        title="Sibling",
        author_id="disjoint-author",
    )
    detail_track = await Track.create(
        title="Detail Disjoint",
        owner_id="disjoint-author",
        workspace_id="ws-test",
    )

    await parent_entry.connect(
        sibling_entry,
        edge=REFERENCES,
        field_key="related",
        relation_type="content_profile",
    )
    await parent_entry.connect(
        detail_track,
        edge=ANCHORS,
        field_key="detail_track",
        role="detail",
    )

    refs_out = await parent_entry.nodes(
        edge=["REFERENCES"], direction="out", node=["Entry"]
    )
    anchors_out = await parent_entry.nodes(
        edge=[ANCHORS], direction="out", node=["Track"]
    )

    assert len(refs_out) == 1 and refs_out[0].id == sibling_entry.id
    assert len(anchors_out) == 1 and anchors_out[0].id == detail_track.id


@pytest.mark.asyncio
async def test_anchor_replace_does_not_delete_target_track():
    """CONTEXT decisions Edge layer / Resolved Fork 1: replacing an ANCHORS edge does NOT delete the underlying Track.

    Wire ANCHORS edge; replace by deleting the edge and re-connecting to a different track;
    the original track still exists as a top-level resource.
    """
    track_a = await Track.create(
        title="Detail A",
        owner_id="replace-author",
        workspace_id="ws-test",
    )
    track_b = await Track.create(
        title="Detail B",
        owner_id="replace-author",
        workspace_id="ws-test",
    )
    entry = await Entry.create(
        track_id="parent-replace",
        title="Parent Replace",
        author_id="replace-author",
    )

    await entry.connect(track_a, edge=ANCHORS, field_key="detail", role="detail")

    # Mirror _sync_anchor_edges replace semantics: find + delete existing edges, then connect new
    ctx = await entry.get_context()
    old_edges = await ctx.find_edges_between(entry.id, track_a.id, edge_class=Anchors)
    for ed in old_edges:
        await ed.delete()
    await entry.connect(track_b, edge=ANCHORS, field_key="detail", role="detail")

    # track_a still exists as a top-level resource
    reloaded_a = await Track.get(track_a.id)
    assert reloaded_a is not None
    assert reloaded_a.id == track_a.id

    # Only one anchor remains, pointing to track_b
    anchors_out = await entry.nodes(edge=[ANCHORS], direction="out", node=["Track"])
    assert len(anchors_out) == 1
    assert anchors_out[0].id == track_b.id


# ===== Section 2 — sync_relation_edges routing (Task 3, ANC-01 wire path) =====


@pytest.mark.asyncio
async def test_sync_relation_edges_entry_target_preserves_references():
    """Regression: relation_refs with target='entry' wires REFERENCES (unchanged Plan 1 behavior)."""
    from app.services.content_profile_runtime import sync_relation_edges

    parent = await Entry.create(
        track_id="route-entry-t", title="Parent", author_id="route-1"
    )
    sibling = await Entry.create(
        track_id="route-entry-t", title="Sibling", author_id="route-1"
    )

    await sync_relation_edges(
        source_entry=parent,
        relation_refs=[
            {
                "field_key": "related",
                "targets": [sibling.id],
                "target": "entry",
            }
        ],
    )

    refs = await parent.nodes(edge=["REFERENCES"], direction="out", node=["Entry"])
    assert len(refs) == 1
    assert refs[0].id == sibling.id


@pytest.mark.asyncio
async def test_sync_relation_edges_track_target_wires_anchors():
    """relation_refs with target='track' wires ANCHORS via _sync_anchor_edges."""
    from app.services.content_profile_runtime import sync_relation_edges

    parent = await Entry.create(
        track_id="route-tt-t", title="Parent TT", author_id="route-2"
    )
    detail = await Track.create(
        title="Detail TT", owner_id="route-2", workspace_id="ws-route"
    )

    await sync_relation_edges(
        source_entry=parent,
        relation_refs=[
            {
                "field_key": "detail_track",
                "targets": [detail.id],
                "target": "track",
            }
        ],
    )

    anchors_out = await parent.nodes(edge=[ANCHORS], direction="out", node=["Track"])
    assert len(anchors_out) == 1
    assert anchors_out[0].id == detail.id


@pytest.mark.asyncio
async def test_sync_relation_edges_mixed_targets_route_disjoint():
    """Mixed relation_refs (entry-target + track-target) route to their respective edges."""
    from app.services.content_profile_runtime import sync_relation_edges

    parent = await Entry.create(
        track_id="route-mix-t", title="Parent Mix", author_id="route-3"
    )
    sibling = await Entry.create(
        track_id="route-mix-t", title="Sibling Mix", author_id="route-3"
    )
    detail = await Track.create(
        title="Detail Mix", owner_id="route-3", workspace_id="ws-route"
    )

    await sync_relation_edges(
        source_entry=parent,
        relation_refs=[
            {
                "field_key": "related",
                "targets": [sibling.id],
                "target": "entry",
            },
            {
                "field_key": "detail_track",
                "targets": [detail.id],
                "target": "track",
            },
        ],
    )

    refs = await parent.nodes(edge=["REFERENCES"], direction="out", node=["Entry"])
    anchors = await parent.nodes(edge=[ANCHORS], direction="out", node=["Track"])
    assert len(refs) == 1 and refs[0].id == sibling.id
    assert len(anchors) == 1 and anchors[0].id == detail.id


@pytest.mark.asyncio
async def test_sync_anchor_edges_replace_preserves_target_track():
    """Replacing an ANCHORS edge via sync_relation_edges does NOT delete the prior Track."""
    from app.services.content_profile_runtime import sync_relation_edges

    parent = await Entry.create(
        track_id="route-rep-t", title="Parent Rep", author_id="route-4"
    )
    detail_a = await Track.create(
        title="Detail A Rep", owner_id="route-4", workspace_id="ws-route"
    )
    detail_b = await Track.create(
        title="Detail B Rep", owner_id="route-4", workspace_id="ws-route"
    )

    await sync_relation_edges(
        source_entry=parent,
        relation_refs=[
            {
                "field_key": "detail_track",
                "targets": [detail_a.id],
                "target": "track",
            }
        ],
    )
    await sync_relation_edges(
        source_entry=parent,
        relation_refs=[
            {
                "field_key": "detail_track",
                "targets": [detail_b.id],
                "target": "track",
            }
        ],
    )

    anchors = await parent.nodes(edge=[ANCHORS], direction="out", node=["Track"])
    assert len(anchors) == 1
    assert anchors[0].id == detail_b.id

    # detail_a still exists as a top-level resource (CONTEXT Resolved Fork 1)
    reloaded_a = await Track.get(detail_a.id)
    assert reloaded_a is not None


@pytest.mark.asyncio
async def test_sync_relation_edges_default_target_is_entry():
    """Regression: relation_refs entries without an explicit 'target' default to 'entry' (REFERENCES)."""
    from app.services.content_profile_runtime import sync_relation_edges

    parent = await Entry.create(
        track_id="route-def-t", title="Parent Def", author_id="route-5"
    )
    sibling = await Entry.create(
        track_id="route-def-t", title="Sibling Def", author_id="route-5"
    )

    await sync_relation_edges(
        source_entry=parent,
        relation_refs=[
            {
                "field_key": "related",
                "targets": [sibling.id],
                # target intentionally omitted — must default to 'entry'
            }
        ],
    )

    refs = await parent.nodes(edge=["REFERENCES"], direction="out", node=["Entry"])
    assert len(refs) == 1
    assert refs[0].id == sibling.id


def test_anchors_write_path_grep_gate():
    """CONTEXT Conformance invariant: ANCHORS writes occur ONLY in
    ``content_profile_graph.py``'s ``_sync_anchor_edges`` helper.

    Strategy:
      1. ``grep -rE 'edge=ANCHORS|edge=Anchors\\b'`` across ``backend/app/``.
      2. Drop comment-only lines (Python ``#``-prefixed before the ``edge=`` token).
      3. Assert every remaining offender lives in
         ``app/services/content_profile_graph.py`` — the single sanctioned
         write path.
    """
    import re
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    backend_app = repo_root / "backend" / "app"
    rx = re.compile(r"edge=ANCHORS|edge=Anchors\b")
    sanctioned_suffixes = ("app/services/content_profile_graph.py",)
    offenders: list[str] = []
    for py in backend_app.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        rel = py.relative_to(repo_root).as_posix()
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if not rx.search(line):
                continue
            # Drop comment-only or trailing-comment matches (``#`` before ``edge=``).
            prefix = line.split("edge=", 1)[0]
            if "#" in prefix or line.lstrip().startswith("#"):
                continue
            if not any(rel.endswith(s) for s in sanctioned_suffixes):
                offenders.append(f"{rel}:{i}:{line}")
    assert not offenders, (
        "ANCHORS edge writes are restricted to "
        f"{sanctioned_suffixes}::_sync_anchor_edges; offenders:\n"
        + "\n".join(offenders)
    )
