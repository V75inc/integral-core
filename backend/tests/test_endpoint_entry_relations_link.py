"""Phase 30 (DR-30-02) — generic /entries/{id}/related link/unlink/list tests.

Replaces the legacy Phase 19 ``related-communications`` URL suite;
the substrate now dispatches manual thread-linking through the generic
``/api/entries/{id}/related`` family. The semantics are unchanged — a
REFERENCES edge with ``field_key="related_communications"`` is still
written / read / deleted — but the URL is no longer domain-coupled.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.models.edges import COLLABORATES_ON, CONTAINS, IS_MEMBER_OF, OWNS, REFERENCES
from app.models.nodes import Entry, Track, User, Workspace
from app.utils.time import utc_now_iso


async def _make_workspace_and_entries(owner: User):
    now = utc_now_iso()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="WS Rel",
        name_fold="ws rel",
        created_at=now,
        updated_at=now,
    )
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)
    project_track = await Track.create(
        title="Projects",
        owner_id=owner.id,
        workspace_id=ws.id,
    )
    project = await Entry.create(
        title="Project P",
        body="",
        track_id=project_track.id,
        author_id=owner.id,
    )
    await owner.connect(project_track, edge=COLLABORATES_ON, role="owner", added_at=now)
    await project_track.connect(project, edge=CONTAINS, added_at=now)
    await owner.connect(project, edge=COLLABORATES_ON, role="owner", added_at=now)
    comm_track = await Track.create(
        title="Communications",
        owner_id=owner.id,
        workspace_id=ws.id,
    )
    thread = await Entry.create(
        title="Thread A",
        body="",
        track_id=comm_track.id,
        author_id=owner.id,
        custom_fields={
            "subject": "Renewal pricing",
            "message_count": 3,
            "last_message_at": "2026-05-23T12:00:00+00:00",
        },
    )
    await owner.connect(comm_track, edge=COLLABORATES_ON, role="owner", added_at=now)
    await comm_track.connect(thread, edge=CONTAINS, added_at=now)
    await owner.connect(thread, edge=COLLABORATES_ON, role="owner", added_at=now)
    return {"workspace": ws, "project": project, "thread": thread}


@pytest.mark.asyncio
async def test_post_link_creates_related_communications_edge(
    authenticated_client: AsyncClient, test_user
):
    fx = await _make_workspace_and_entries(test_user)
    resp = await authenticated_client.post(
        f"/api/entries/{fx['project'].id}/related/link",
        json={
            "source_id": fx["thread"].id,
            "relation": "related_communications",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "linked"
    # REFERENCES edge exists thread → project with field_key.
    thread = await Entry.get(fx["thread"].id)
    ctx = await thread.get_context()
    edges = await ctx.find_edges_between(
        thread.id, fx["project"].id, edge_class=REFERENCES
    )
    assert any(
        (getattr(e, "field_key", "") or "") == "related_communications" for e in edges
    )


@pytest.mark.asyncio
async def test_get_list_returns_linked_threads_newest_first(
    authenticated_client: AsyncClient, test_user
):
    fx = await _make_workspace_and_entries(test_user)
    await authenticated_client.post(
        f"/api/entries/{fx['project'].id}/related/link",
        json={
            "source_id": fx["thread"].id,
            "relation": "related_communications",
        },
    )
    resp = await authenticated_client.get(
        f"/api/entries/{fx['project'].id}/related",
        params={"relation": "related_communications"},
    )
    assert resp.status_code == 200, resp.text
    threads = resp.json()["threads"]
    assert len(threads) == 1
    assert threads[0]["id"] == fx["thread"].id
    assert threads[0]["subject"] == "Renewal pricing"


@pytest.mark.asyncio
async def test_delete_link_removes_edge(authenticated_client: AsyncClient, test_user):
    fx = await _make_workspace_and_entries(test_user)
    await authenticated_client.post(
        f"/api/entries/{fx['project'].id}/related/link",
        json={
            "source_id": fx["thread"].id,
            "relation": "related_communications",
        },
    )
    resp = await authenticated_client.delete(
        f"/api/entries/{fx['project'].id}/related/{fx['thread'].id}",
        params={"relation": "related_communications"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["removed"] >= 1
    # Subsequent list is empty.
    list_resp = await authenticated_client.get(
        f"/api/entries/{fx['project'].id}/related",
        params={"relation": "related_communications"},
    )
    assert list_resp.json()["threads"] == []


@pytest.mark.asyncio
async def test_get_list_without_relation_returns_every_inbound_link(
    authenticated_client: AsyncClient, test_user
):
    """Omitting ``relation`` answers "what points at this?" instead of 400ing.

    ``relation`` used to be required, which made the tool unusable for the only
    question an agent can ask before it has discovered any field_key. Measured
    on the Personal Context walk: 7 of 13 ``integral_get_related`` calls came
    back ``bad_request``, and since the orchestrator retries an errored call
    with identical arguments, the third attempt tripped the repeat guard and
    killed the turn.
    """
    fx = await _make_workspace_and_entries(test_user)
    link = await authenticated_client.post(
        f"/api/entries/{fx['project'].id}/related/link",
        json={"source_id": fx["thread"].id, "relation": "related_communications"},
    )
    assert link.status_code == 200, link.text

    resp = await authenticated_client.get(f"/api/entries/{fx['project'].id}/related")
    assert resp.status_code == 200, resp.text
    threads = resp.json()["threads"]
    assert [t["id"] for t in threads] == [fx["thread"].id]
    # The row names the key it arrived on, so a follow-up can narrow without a
    # separate schema round-trip.
    assert threads[0]["relation"] == "related_communications"


@pytest.mark.asyncio
async def test_get_list_still_filters_when_relation_is_passed(
    authenticated_client: AsyncClient, test_user
):
    """Making the param optional must not weaken filtering when it IS given."""
    fx = await _make_workspace_and_entries(test_user)
    link = await authenticated_client.post(
        f"/api/entries/{fx['project'].id}/related/link",
        json={"source_id": fx["thread"].id, "relation": "related_communications"},
    )
    assert link.status_code == 200, link.text

    match = await authenticated_client.get(
        f"/api/entries/{fx['project'].id}/related?relation=related_communications"
    )
    assert [t["id"] for t in match.json()["threads"]] == [fx["thread"].id]

    other = await authenticated_client.get(
        f"/api/entries/{fx['project'].id}/related?relation=some_other_field"
    )
    assert other.status_code == 200, other.text
    assert other.json()["threads"] == []
