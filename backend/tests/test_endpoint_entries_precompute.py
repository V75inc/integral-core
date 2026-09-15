"""Generic entry.precompute endpoint — runs a tool against an entry; returns patch."""

import pytest


@pytest.mark.asyncio
async def test_precompute_runs_bound_tool_and_returns_patch(
    authenticated_client, test_user, monkeypatch
):
    from app.models.edges import COLLABORATES_ON, CONTAINS, IS_MEMBER_OF
    from app.models.nodes import Entry, EntryType, Track, Workspace
    from app.services.hooks.registry import (
        clear_workspace_registrations,
        register_workspace_hooks,
        register_workspace_tools,
    )
    from app.utils.time import utc_now_iso

    owner = test_user
    now = utc_now_iso()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="WS Pre",
        name_fold="ws pre",
        created_at=now,
        updated_at=now,
    )
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)
    track = await Track.create(
        title="Project Proposals", owner_id=owner.id, workspace_id=ws.id
    )
    et = await EntryType.create(
        name="Project Proposal", name_fold="project proposal", track_id=track.id
    )
    e = await Entry.create(
        title="P",
        body="",
        track_id=track.id,
        type_id=et.id,
        author_id=owner.id,
        custom_fields={},
    )
    await track.connect(e, edge=CONTAINS, added_at=now)
    await owner.connect(track, edge=COLLABORATES_ON, role="owner", added_at=now)
    await owner.connect(e, edge=COLLABORATES_ON, role="owner", added_at=now)

    clear_workspace_registrations(ws.id)
    register_workspace_tools(
        ws.id,
        "test-bundle",
        [
            {
                "key": "stub_pricing",
                "handler_ref": "tests.fixtures.tool_echo:echo",
                "parameters_schema": {"type": "object"},
                "output_schema": {"type": "object"},
            }
        ],
    )
    register_workspace_hooks(
        ws.id,
        "test-bundle",
        [
            {
                "point": "entry.precompute",
                "key": "pricing_precompute",
                "match": {
                    "entry_type": "project_proposal",
                    "hook": "pricing_precompute",
                },
                "mode": "tool",
                "tool": "stub_pricing",
                "tool_input": {"msg": "hello"},
            }
        ],
    )

    resp = await authenticated_client.post(
        f"/api/entries/{e.id}/precompute",
        json={"hook_key": "pricing_precompute"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["patch"]["echoed"] == "hello"
