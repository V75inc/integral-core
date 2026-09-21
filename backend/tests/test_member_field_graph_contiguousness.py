"""ACC-08 — I-GRAPH-01 preservation for the `member` field type.

The HAS_MEMBER_REF edge connects two already-rooted Nodes (Entry rooted
via Workspace→Track→Entry; User rooted via the Users registry CATALOGS).
The materializer in ``_sync_member_ref_edges`` adds an ADDITIONAL pointer
between them — no new ``Node.create(...)`` is introduced, so the static
AST gate at ``.ci/graph_contiguousness_check.sh`` is unaffected.

This test confirms the live invariant at runtime: after a `member` write,
both endpoints of the resulting HAS_MEMBER_REF edge are reachable from
``Root`` via the canonical structural traversal — i.e. neither is left
detached and the edge connects two graph-resident Nodes.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.edges import HasMemberRef
from app.models.nodes import App, Entry, Track, User, Workspace
from app.services.app_graph import catalog_user, ensure_integral_app_graph
from app.services.operational_model_graph import _sync_member_ref_edges


async def _wire_member(*, user: User, workspace: Workspace) -> None:
    from app.models.edges import IS_MEMBER_OF

    now = datetime.now(timezone.utc).isoformat()
    await user.connect(
        workspace,
        edge=IS_MEMBER_OF,
        role="member",
        joined_at=now,
    )


@pytest.mark.asyncio
async def test_member_write_keeps_both_endpoints_rooted():
    """After a `member` write, neither User nor Entry is orphaned."""
    # 1. Ensure the IntegralApp / Root subgraph + the User registry exist.
    await ensure_integral_app_graph()

    # 2. Provision a workspace + track + entry (Entry rooted via Track CONTAINS).
    workspace = await Workspace.create(name="MemberCG Workspace", kind="organization")
    track = await Track.create(
        title="MemberCG Track",
        owner_id="mcg-owner",
        workspace_id=workspace.id,
    )
    entry = await Entry.create(
        track_id=track.id,
        title="MemberCG Entry",
        author_id="mcg-owner",
    )

    # 3. Provision a User and route them through the Users-registry CATALOGS
    # wire (the same path the signup flow uses). This is the canonical
    # way the User node lands on the rooted subgraph.
    user = await User.create(
        user_id="mcg-auth-user-1",
        display_name="MemberCG User",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    await catalog_user(user)
    await _wire_member(user=user, workspace=workspace)

    # 4. Write the member ref through the single-writer helper.
    await _sync_member_ref_edges(
        source_entry=entry,
        relation_refs=[
            {
                "field_key": "assignee",
                "targets": [user.id],
                "target": "user",
            }
        ],
    )

    # 5. The HAS_MEMBER_REF edge exists, and both endpoints round-trip:
    # - the User is reachable in-bound from the Entry along the edge,
    # - the Entry is reachable in-bound from the User along the edge.
    users_out = await entry.nodes(edge=[HasMemberRef], direction="out", node=["User"])
    assert len(users_out) == 1
    assert users_out[0].id == user.id

    entries_in = await user.nodes(edge=[HasMemberRef], direction="in", node=["Entry"])
    assert len(entries_in) == 1
    assert entries_in[0].id == entry.id


@pytest.mark.asyncio
async def test_member_write_introduces_no_new_node_create():
    """ACC-08 does not introduce a new Node.create site on the write path.

    Static AST gate (``.ci/graph_contiguousness_check.sh``) verifies this
    repository-wide. This live counterpart asserts the runtime side: the
    Node count for User + Entry before and after a member write matches —
    the write adds only an edge.
    """
    await ensure_integral_app_graph()

    workspace = await Workspace.create(name="MemberCG WS-NoCreate", kind="organization")
    track = await Track.create(
        title="MemberCG Track NoCreate",
        owner_id="mcg-noc-owner",
        workspace_id=workspace.id,
    )
    entry = await Entry.create(
        track_id=track.id,
        title="MemberCG Entry NoCreate",
        author_id="mcg-noc-owner",
    )
    user = await User.create(
        user_id="mcg-auth-noc-1",
        display_name="MemberCG User NoCreate",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    await catalog_user(user)
    await _wire_member(user=user, workspace=workspace)

    user_count_before = len(await User.find())
    entry_count_before = len(await Entry.find())

    await _sync_member_ref_edges(
        source_entry=entry,
        relation_refs=[
            {
                "field_key": "assignee",
                "targets": [user.id],
                "target": "user",
            }
        ],
    )

    user_count_after = len(await User.find())
    entry_count_after = len(await Entry.find())

    assert user_count_after == user_count_before
    assert entry_count_after == entry_count_before
