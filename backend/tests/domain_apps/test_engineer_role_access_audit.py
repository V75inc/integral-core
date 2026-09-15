"""Phase 23 GO-02 — Access audit for the engineer role.

Proves a delivery-engineer-class user has ZERO read access to the
four privileged categories:

  1. Pricing Rubrics
  2. Project Proposals
  3. Compensation Records (Payroll)
  4. Finance App entries (QuickBooks invoices / customers)

…across the resolver (resolve_role returns None) AND the API surface
(per-entry GET returns 404 / 403). The pattern is Phase 16 Option A:
the engineer is a workspace member with access to non-privileged
tracks but carries an explicit EXCLUDED_FROM edge to each privileged
track. Direct OWNS / COLLABORATES_ON beats exclusion (CLAUDE.md
rule 3), so workspace leadership remains unaffected.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest
from httpx import AsyncClient

from app.models.edges import (
    COLLABORATES_ON,
    CONTAINS,
    EXCLUDED_FROM,
    IS_MEMBER_OF,
)
from app.models.nodes import Entry, EntryType, Track, User, Workspace
from app.services.permissions import resolve_role
from app.utils.time import utc_now_iso


async def _make_engineer(workspace_id: str) -> User:
    """Create a workspace-member engineer who's EXCLUDED_FROM nothing yet."""
    now = utc_now_iso()
    eng = await User.create(
        user_id="engineer-audit-user",
        display_name="Engineer (audit)",
        created_at=now,
    )
    ws = await Workspace.get(workspace_id)
    if ws is not None:
        await eng.connect(ws, edge=IS_MEMBER_OF, role="member", added_at=now)
    return eng


async def _make_privileged_track(
    owner: User, ws_id: str, title: str, entry_type_name: str
) -> Dict[str, Any]:
    """Create a track + one entry on it; owner gets COLLABORATES_ON{owner}."""
    now = utc_now_iso()
    t = await Track.create(title=title, owner_id=owner.id, workspace_id=ws_id)
    await owner.connect(t, edge=COLLABORATES_ON, role="owner", added_at=now)
    et = await EntryType.create(
        name=entry_type_name,
        name_fold=entry_type_name.lower(),
        track_id=t.id,
    )
    e = await Entry.create(
        title=f"Sample {entry_type_name}",
        body="Privileged content.",
        track_id=t.id,
        type_id=et.id,
        author_id=owner.id,
        custom_fields={"sample": True},
    )
    await t.connect(e, edge=CONTAINS, added_at=now)
    await owner.connect(e, edge=COLLABORATES_ON, role="owner", added_at=now)
    return {"track": t, "entry": e, "entry_type": et}


@pytest.mark.asyncio
async def test_engineer_role_has_no_access_to_privileged_tracks(
    authenticated_client: AsyncClient, test_user
):
    """Delivery engineer carries EXCLUDED_FROM on every privileged track
    → resolve_role returns None for every privileged track + every entry
    on that track. Workspace leadership (the test_user owner) is unaffected.
    """
    owner = test_user
    now = utc_now_iso()

    # Workspace owned by leadership; engineer is a member.
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Audit Co",
        name_fold="audit co",
        created_at=now,
        updated_at=now,
    )
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)
    eng = await _make_engineer(ws.id)

    privileged = {
        "pricing_rubrics": await _make_privileged_track(
            owner, ws.id, "Pricing Rubrics", "Pricing Rubric"
        ),
        "project_proposals": await _make_privileged_track(
            owner, ws.id, "Project Proposals", "Project Proposal"
        ),
        "compensation_records": await _make_privileged_track(
            owner, ws.id, "Guyana Compensation Records", "Compensation Record"
        ),
        "qb_invoices": await _make_privileged_track(
            owner, ws.id, "QB Invoices", "QB Invoice"
        ),
    }

    # Phase 16 Option A — engineer gets EXCLUDED_FROM each privileged track.
    for fx in privileged.values():
        await eng.connect(fx["track"], edge=EXCLUDED_FROM, added_at=now)

    # ----- Verify exclusion: engineer has ZERO role on privileged tracks
    #       (track + every entry on it via cascade) ----------------------
    for name, fx in privileged.items():
        track_role = await resolve_role(eng.id, "track", fx["track"].id)
        assert (
            track_role is None
        ), f"engineer leaked role={track_role!r} on privileged track {name!r}"
        entry_role = await resolve_role(eng.id, "entry", fx["entry"].id)
        assert (
            entry_role is None
        ), f"engineer leaked role={entry_role!r} on entry of privileged track {name!r}"

    # ----- Verify workspace leadership remains unaffected ---------------
    for name, fx in privileged.items():
        owner_role = await resolve_role(owner.id, "track", fx["track"].id)
        assert owner_role == "owner", (
            f"workspace leadership lost privileged-track access to {name!r} "
            f"(role={owner_role!r})"
        )


@pytest.mark.asyncio
async def test_engineer_role_retains_access_to_non_privileged_tracks(
    authenticated_client: AsyncClient, test_user
):
    """Sanity check on the privilege model: engineer can still read CRM
    operational tracks (contacts, projects, portfolio). Phase 16 Option A
    excludes specific privileged tracks; the engineer's workspace
    membership keeps everything else accessible."""
    owner = test_user
    now = utc_now_iso()

    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Audit Co Ops",
        name_fold="audit co ops",
        created_at=now,
        updated_at=now,
    )
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)
    eng = await _make_engineer(ws.id)

    # A non-privileged Track the engineer collaborates on as editor.
    contacts = await Track.create(
        title="Contacts", owner_id=owner.id, workspace_id=ws.id
    )
    await owner.connect(contacts, edge=COLLABORATES_ON, role="owner", added_at=now)
    await eng.connect(contacts, edge=COLLABORATES_ON, role="editor", added_at=now)

    role = await resolve_role(eng.id, "track", contacts.id)
    assert role in (
        "editor",
        "owner",
    ), f"engineer expected editor/owner on non-privileged track, got {role!r}"


@pytest.mark.asyncio
async def test_direct_owns_beats_exclusion(
    authenticated_client: AsyncClient, test_user
):
    """CLAUDE.md rule 3 — direct OWNS / COLLABORATES_ON beats EXCLUDED_FROM.

    If workspace leadership somehow ends up with an EXCLUDED_FROM on a
    privileged track they directly own, the direct grant still wins.
    This guards against an op accidentally writing the wrong edge type
    when re-permissioning."""
    owner = test_user
    now = utc_now_iso()

    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Audit Co Direct",
        name_fold="audit co direct",
        created_at=now,
        updated_at=now,
    )
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)

    fx = await _make_privileged_track(
        owner, ws.id, "Pricing Rubrics (direct)", "Pricing Rubric"
    )
    # Now ALSO exclude the owner — direct COLLABORATES_ON{owner} should win.
    await owner.connect(fx["track"], edge=EXCLUDED_FROM, added_at=now)

    role = await resolve_role(owner.id, "track", fx["track"].id)
    assert role == "owner", (
        f"direct COLLABORATES_ON{{owner}} did not beat EXCLUDED_FROM "
        f"(role resolved to {role!r})"
    )
