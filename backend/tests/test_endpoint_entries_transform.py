"""Generic entry.transform endpoint (DR-30-02)."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_transform_resolves_hook_and_creates_target_entry(
    authenticated_client: AsyncClient, test_user, monkeypatch
):
    from app.models.edges import COLLABORATES_ON, CONTAINS, IS_MEMBER_OF
    from app.models.nodes import Entry, EntryType, Track, Workspace
    from app.services.hooks.registry import (
        clear_workspace_registrations,
        register_workspace_hooks,
    )
    from app.utils.time import utc_now_iso

    owner = test_user
    now = utc_now_iso()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="WS T1",
        name_fold="ws t1",
        created_at=now,
        updated_at=now,
    )
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)
    src_track = await Track.create(
        title="Project Proposals", owner_id=owner.id, workspace_id=ws.id
    )
    tgt_track = await Track.create(
        title="Opportunities", owner_id=owner.id, workspace_id=ws.id
    )
    await owner.connect(src_track, edge=COLLABORATES_ON, role="owner", added_at=now)
    await owner.connect(tgt_track, edge=COLLABORATES_ON, role="owner", added_at=now)
    src_et = await EntryType.create(
        name="Project Proposal", name_fold="project proposal", track_id=src_track.id
    )
    tgt_et = await EntryType.create(
        name="Bid",
        name_fold="bid",
        track_id=tgt_track.id,
        form_schema={
            "fields": [
                {"key": "value", "name": "Deal value", "type": "number"},
            ]
        },
    )
    proposal = await Entry.create(
        title="Contoso v2 — proposal",
        body="Cover",
        track_id=src_track.id,
        type_id=src_et.id,
        author_id=owner.id,
        custom_fields={
            "total_price": 50000.0,
            "total_cost": 30000.0,
            "projected_margin_pct": 40,
            "under_margin": False,
            "line_items": [{"x": 1}],
        },
    )
    await src_track.connect(proposal, edge=CONTAINS, added_at=now)
    await owner.connect(proposal, edge=COLLABORATES_ON, role="owner", added_at=now)

    # Register a binding for this workspace.
    clear_workspace_registrations(ws.id)
    register_workspace_hooks(
        ws.id,
        "test-bundle",
        [
            {
                "point": "entry.transform",
                "key": "proposal_to_opp",
                "match": {
                    "source_entry_type": "project_proposal",
                    "target_track_type": "opportunities",
                    "target_entry_type": "bid",
                },
                "mode": "declarative",
                "declarative": {
                    "copy_fields": [
                        {"from": "title", "to": "title"},
                        {"from": "body", "to": "body"},
                        {
                            "from": "custom_fields.total_price",
                            "to": "custom_fields.value",
                        },
                    ],
                    "strip_fields": [
                        "custom_fields.total_cost",
                        "custom_fields.projected_margin_pct",
                        "custom_fields.line_items",
                        "custom_fields.under_margin",
                    ],
                    "provenance_edge": {
                        "edge": "REFERENCES",
                        "direction": "out",
                        "from": "target",
                        "to": "source",
                    },
                    "override_flag": "under_margin",
                },
            }
        ],
    )

    resp = await authenticated_client.post(
        f"/api/entries/{proposal.id}/transform",
        json={"to_track": tgt_track.id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    new_entry_id = body["new_entry_id"]
    new_entry = await Entry.get(new_entry_id)
    assert new_entry.custom_fields.get("value") == 50000.0
    assert "total_cost" not in new_entry.custom_fields
    assert "line_items" not in new_entry.custom_fields
    # REFERENCES edge wired.
    targets = await new_entry.nodes(
        edge=["REFERENCES"], direction="out", node=["Entry"]
    )
    assert any(t.id == proposal.id for t in targets)


@pytest.mark.asyncio
async def test_transform_under_margin_blocks_without_override(
    authenticated_client, test_user
):
    # Same setup but proposal carries under_margin=True; expect 400.
    pytest.skip("similar shape — covered by E2E test in Wave E")


@pytest.mark.asyncio
async def test_transform_unknown_hook_returns_404(authenticated_client, test_user):
    from app.models.edges import COLLABORATES_ON, CONTAINS, IS_MEMBER_OF
    from app.models.nodes import Entry, EntryType, Track, Workspace
    from app.services.hooks.registry import clear_workspace_registrations
    from app.utils.time import utc_now_iso

    owner = test_user
    now = utc_now_iso()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="WS T3",
        name_fold="ws t3",
        created_at=now,
        updated_at=now,
    )
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)
    src = await Track.create(title="X", owner_id=owner.id, workspace_id=ws.id)
    tgt = await Track.create(title="Y", owner_id=owner.id, workspace_id=ws.id)
    await owner.connect(src, edge=COLLABORATES_ON, role="owner", added_at=now)
    await owner.connect(tgt, edge=COLLABORATES_ON, role="owner", added_at=now)
    et = await EntryType.create(name="X", name_fold="x", track_id=src.id)
    e = await Entry.create(
        title="t",
        body="",
        track_id=src.id,
        type_id=et.id,
        author_id=owner.id,
        custom_fields={},
    )
    await src.connect(e, edge=CONTAINS, added_at=now)
    await owner.connect(e, edge=COLLABORATES_ON, role="owner", added_at=now)
    clear_workspace_registrations(ws.id)
    resp = await authenticated_client.post(
        f"/api/entries/{e.id}/transform", json={"to_track": tgt.id}
    )
    assert resp.status_code == 404, resp.text
