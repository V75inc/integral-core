"""Tests for ``attach_backlinks`` — surfaces inbound graph context on entry GET.

Covers the two backlink buckets exposed on ``GET /entries/{id}``:

  * ``anchor_source`` — the Entry whose ANCHORS edge points at this entry's
    parent Track (e.g. a Task inside "Project: Foo" links back to the source
    Project entry).
  * ``referenced_by`` — every Entry whose REFERENCES relation field points at
    this Entry (e.g. the Contact lists every Project that references it).

These act as a stable, standard contract for the EntryDetail "Linked from" /
"Referenced by" header — always-present keys (``None`` / ``[]`` when absent)
so the frontend has no shape-drift to defend against.
"""

from __future__ import annotations

import pytest

from app.models.edges import ANCHORS, CONTAINS, REFERENCES
from app.models.nodes import App, Entry, Track
from app.services.entry_context import (
    BACKLINKS_REFERENCED_BY_LIMIT,
    attach_backlinks,
)


async def _entry_in(track: Track, *, title: str, type_id: str = "") -> Entry:
    e = await Entry.create(
        title=title,
        track_id=track.id,
        type_id=type_id,
        author_id="user-bl",
        body="",
    )
    await track.connect(e, edge=CONTAINS)
    return e


@pytest.mark.asyncio
async def test_attach_backlinks_default_keys_present_when_empty():
    """Always sets both keys so the frontend has a stable contract."""
    ws = "ws-bl-empty"
    app_node = await App.create(name="S", workspace_id=ws, owner_user_id="u")
    track = await Track.create(title="T", owner_id="u", workspace_id=ws)
    await app_node.connect(track, edge=CONTAINS)
    entry = await _entry_in(track, title="lone entry")

    data: dict = {}
    await attach_backlinks(data, entry)

    assert data["anchor_source"] is None
    assert data["referenced_by"] == []
    assert "referenced_by_total" not in data


@pytest.mark.asyncio
async def test_attach_backlinks_resolves_anchor_source():
    """When this entry's Track has an inbound ANCHORS, surface the source Entry."""
    ws = "ws-bl-anchor"
    app_node = await App.create(name="S", workspace_id=ws, owner_user_id="u")
    source_track = await Track.create(title="Projects", owner_id="u", workspace_id=ws)
    anchor_track = await Track.create(
        title="Project: Onboarding", owner_id="u", workspace_id=ws
    )
    await app_node.connect(source_track, edge=CONTAINS)
    await app_node.connect(anchor_track, edge=CONTAINS)

    source_entry = await _entry_in(source_track, title="Onboarding: Contoso")
    # Wire ANCHORS source_entry → anchor_track (the relation that
    # ``materialize_anchor_track`` writes on auto-provision).
    await source_entry.connect(anchor_track, edge=ANCHORS, field_key="details_track")

    child = await _entry_in(anchor_track, title="Kickoff prep")

    data: dict = {}
    await attach_backlinks(data, child)

    src = data["anchor_source"]
    assert src is not None
    assert src["id"] == source_entry.id
    assert src["title"] == "Onboarding: Contoso"
    assert src["track_id"] == source_track.id
    assert src["track_title"] == "Projects"
    assert src["field_key"] == "details_track"


@pytest.mark.asyncio
async def test_attach_backlinks_lists_incoming_references():
    """Every Entry with an outbound REFERENCES edge to this entry is surfaced."""
    ws = "ws-bl-refs"
    app_node = await App.create(name="S", workspace_id=ws, owner_user_id="u")
    contacts = await Track.create(title="Contacts", owner_id="u", workspace_id=ws)
    projects = await Track.create(title="Projects", owner_id="u", workspace_id=ws)
    await app_node.connect(contacts, edge=CONTAINS)
    await app_node.connect(projects, edge=CONTAINS)

    target = await _entry_in(contacts, title="Jordan Lee")
    p1 = await _entry_in(projects, title="Onboarding")
    p2 = await _entry_in(projects, title="Renewal")
    await p1.connect(target, edge=REFERENCES, field_key="contact")
    await p2.connect(target, edge=REFERENCES, field_key="contact")

    data: dict = {}
    await attach_backlinks(data, target)

    refs = data["referenced_by"]
    assert len(refs) == 2
    titles = {r["title"] for r in refs}
    assert titles == {"Onboarding", "Renewal"}
    for r in refs:
        assert r["track_id"] == projects.id
        assert r["track_title"] == "Projects"
        assert r["field_key"] == "contact"


@pytest.mark.asyncio
async def test_attach_backlinks_caps_referenced_by():
    """Long fan-in lists are capped to keep the response bounded."""
    ws = "ws-bl-cap"
    app_node = await App.create(name="S", workspace_id=ws, owner_user_id="u")
    contacts = await Track.create(title="Contacts", owner_id="u", workspace_id=ws)
    projects = await Track.create(title="Projects", owner_id="u", workspace_id=ws)
    await app_node.connect(contacts, edge=CONTAINS)
    await app_node.connect(projects, edge=CONTAINS)

    target = await _entry_in(contacts, title="Popular Contact")
    over = BACKLINKS_REFERENCED_BY_LIMIT + 3
    for i in range(over):
        p = await _entry_in(projects, title=f"Project {i}")
        await p.connect(target, edge=REFERENCES, field_key="contact")

    data: dict = {}
    await attach_backlinks(data, target)

    assert len(data["referenced_by"]) == BACKLINKS_REFERENCED_BY_LIMIT
    assert data["referenced_by_total"] == over
