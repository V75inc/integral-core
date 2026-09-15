"""ACC-07 — Privileged-track access verification.

The exit gate for Phase 16: an engineer test account with full
``COLLABORATES_ON`` access to a Project must resolve to **no access**
on the Project's anchored ``Project Financials`` / ``Contracts & Legal``
privileged tracks, while their Project access stays intact.

Enforcement runs through the canonical `resolve_role` resolver in
``backend/app/services/permissions.py`` (composes
``COLLABORATES_ON`` + ``EXCLUDED_FROM`` + parent cascade with the
inherited-role cap). No new resolver code is introduced; ACC-07
verifies the deny semantics behave as the ACC-04 role matrix
intends.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.edges import (
    COLLABORATES_ON,
    CONTAINS,
    EXCLUDED_FROM,
    IS_MEMBER_OF,
    OWNS,
)
from app.models.nodes import App, Entry, Track, User, Workspace
from app.services.app_graph import catalog_user, ensure_integral_app_graph
from app.services.permissions import resolve_role


async def _ws_member(user: User, workspace: Workspace, role: str = "member") -> None:
    """Wire user → workspace IS_MEMBER_OF."""
    now = datetime.now(timezone.utc).isoformat()
    await user.connect(workspace, edge=IS_MEMBER_OF, role=role, joined_at=now)


async def _provision_fixture() -> dict:
    """Provision the canonical ACC-07 fixture.

    Layout:
        IntegralApp (rooted)
        Users registry —CATALOGS→ engineer, founder
        Workspace (org)
          founder —OWNS→ Workspace
          founder —IS_MEMBER_OF{owner}→ Workspace
          engineer —IS_MEMBER_OF{member}→ Workspace
          Workspace —CONTAINS→ App (CRM)
            founder —OWNS→ App
            App —CONTAINS→ Track Projects
              engineer —COLLABORATES_ON{editor}→ Track Projects
              Track Projects —CONTAINS→ Entry Project A
            App —CONTAINS→ Track ProjectFinancials
              engineer —EXCLUDED_FROM→ Track ProjectFinancials
              Track ProjectFinancials —CONTAINS→ Entry Financials A
            App —CONTAINS→ Track Contracts
              engineer —EXCLUDED_FROM→ Track Contracts
    """
    await ensure_integral_app_graph()

    workspace = await Workspace.create(name="Acme Inc.", kind="organization")
    founder = await User.create(
        user_id="auth-founder",
        display_name="Founder",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    engineer = await User.create(
        user_id="auth-engineer",
        display_name="Engineer",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    await catalog_user(founder)
    await catalog_user(engineer)
    await founder.connect(
        workspace, edge=OWNS, created_at=datetime.now(timezone.utc).isoformat()
    )
    await _ws_member(founder, workspace, role="owner")
    await _ws_member(engineer, workspace, role="member")

    app = await App.create(
        name="CRM",
        owner_id=founder.id,
        workspace_id=workspace.id,
    )
    now = datetime.now(timezone.utc).isoformat()
    await workspace.connect(app, edge=CONTAINS, added_at=now)
    await founder.connect(app, edge=OWNS, created_at=now)

    projects_track = await Track.create(
        title="Projects",
        owner_id=founder.id,
        workspace_id=workspace.id,
    )
    await app.connect(projects_track, edge=CONTAINS, added_at=now)
    await founder.connect(projects_track, edge=OWNS, created_at=now)
    await engineer.connect(
        projects_track,
        edge=COLLABORATES_ON,
        role="editor",
        granted_at=now,
        granted_by=founder.id,
    )

    project_a = await Entry.create(
        track_id=projects_track.id,
        title="Project A",
        author_id=founder.id,
    )
    await projects_track.connect(project_a, edge=CONTAINS, added_at=now)

    fin_track = await Track.create(
        title="Project Financials — A",
        owner_id=founder.id,
        workspace_id=workspace.id,
        track_template_key="project-financials",
    )
    await app.connect(fin_track, edge=CONTAINS, added_at=now)
    await founder.connect(fin_track, edge=OWNS, created_at=now)
    await engineer.connect(
        fin_track,
        edge=EXCLUDED_FROM,
        excluded_at=now,
        excluded_by=founder.id,
        reason="ACC-04 — engineer default-deny on project-financials",
    )

    contracts_track = await Track.create(
        title="Contracts & Legal — A",
        owner_id=founder.id,
        workspace_id=workspace.id,
        track_template_key="contracts-legal",
    )
    await app.connect(contracts_track, edge=CONTAINS, added_at=now)
    await founder.connect(contracts_track, edge=OWNS, created_at=now)
    await engineer.connect(
        contracts_track,
        edge=EXCLUDED_FROM,
        excluded_at=now,
        excluded_by=founder.id,
        reason="ACC-04 — engineer default-deny on contracts-legal",
    )

    financials_entry = await Entry.create(
        track_id=fin_track.id,
        title="Q3 Cost",
        author_id=founder.id,
        custom_fields={"cost": 50000, "margin": 0.35},
    )
    await fin_track.connect(financials_entry, edge=CONTAINS, added_at=now)

    return {
        "workspace": workspace,
        "founder": founder,
        "engineer": engineer,
        "app": app,
        "projects_track": projects_track,
        "project_a": project_a,
        "fin_track": fin_track,
        "contracts_track": contracts_track,
        "financials_entry": financials_entry,
    }


# --- Core access assertions ---------------------------------------------------


@pytest.mark.asyncio
async def test_engineer_keeps_editor_access_to_projects_track():
    """ACC-07 — direct COLLABORATES_ON gives the engineer editor on Projects."""
    fx = await _provision_fixture()
    role = await resolve_role(fx["engineer"].id, "track", fx["projects_track"].id)
    assert role == "editor", role


@pytest.mark.asyncio
async def test_engineer_keeps_editor_access_to_project_a_entry():
    """ACC-07 — inherited editor cascades from Track to its Entries."""
    fx = await _provision_fixture()
    role = await resolve_role(fx["engineer"].id, "entry", fx["project_a"].id)
    assert role == "editor", role


@pytest.mark.asyncio
async def test_engineer_excluded_from_privileged_track_resolves_to_none():
    """ACC-07 — EXCLUDED_FROM on Project Financials yields no access."""
    fx = await _provision_fixture()
    role = await resolve_role(fx["engineer"].id, "track", fx["fin_track"].id)
    assert role is None, role


@pytest.mark.asyncio
async def test_engineer_excluded_from_contracts_track_resolves_to_none():
    """ACC-07 — EXCLUDED_FROM on Contracts & Legal yields no access."""
    fx = await _provision_fixture()
    role = await resolve_role(fx["engineer"].id, "track", fx["contracts_track"].id)
    assert role is None, role


@pytest.mark.asyncio
async def test_engineer_cannot_read_financials_entry_under_excluded_track():
    """ACC-07 — the privileged track's Entry inherits the deny (no access)."""
    fx = await _provision_fixture()
    role = await resolve_role(fx["engineer"].id, "entry", fx["financials_entry"].id)
    assert role is None, role


@pytest.mark.asyncio
async def test_founder_keeps_access_across_the_fixture():
    """Regression: the owner sees Projects, Financials, Contracts, all Entries.

    Per I-ROLE-02, inherited owner caps to editor on child Entries (track
    config rights require a direct OWNS / COLLABORATES_ON{admin} on the
    Entry, not parent cascade). The founder is `owner` on every Track
    (direct OWNS edges) and `editor` on the inherited Entries — both are
    sufficient for read + entry CRUD on the privileged tracks.
    """
    fx = await _provision_fixture()
    # Direct OWNS on every Track resolves to "owner".
    for rid in (
        fx["projects_track"].id,
        fx["fin_track"].id,
        fx["contracts_track"].id,
    ):
        role = await resolve_role(fx["founder"].id, "track", rid)
        assert role == "owner", ("track", rid, role)
    # Entries inherit from Track; inherited owner caps to editor (I-ROLE-02).
    for rid in (fx["project_a"].id, fx["financials_entry"].id):
        role = await resolve_role(fx["founder"].id, "entry", rid)
        assert role in ("owner", "editor"), ("entry", rid, role)


# --- Direct-grant override (I-ROLE-02) regression ----------------------------


@pytest.mark.asyncio
async def test_direct_grant_on_privileged_track_beats_exclusion():
    """ACC-07 — explicit COLLABORATES_ON the privileged track wins over EXCLUDED_FROM.

    Per I-ROLE-02: EXCLUDED_FROM overrides INHERITED paths only — direct
    grants on the resource itself always beat it. If later promotes
    an engineer to also see Financials for one project (e.g. a tech-lead
    case), a direct grant is sufficient.
    """
    fx = await _provision_fixture()
    now = datetime.now(timezone.utc).isoformat()
    await fx["engineer"].connect(
        fx["fin_track"],
        edge=COLLABORATES_ON,
        role="viewer",
        granted_at=now,
        granted_by=fx["founder"].id,
    )
    role = await resolve_role(fx["engineer"].id, "track", fx["fin_track"].id)
    # Direct viewer grant beats the EXCLUDED_FROM (I-ROLE-02 direct-grant
    # exception). Acceptable values: "viewer" or higher; depends on cache
    # behavior — the engine takes the strongest direct grant.
    assert role == "viewer", role
