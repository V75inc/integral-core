"""HR-04 — Time-Off Requests kanban transitions ride the existing comment surface.

The workflow contract: an approver posts a comment on the time_off_request
entry, then transitions ``custom_fields.status`` (requested → approved or
denied). No new approval primitive is introduced — Phase 17 reuses
Comment + the existing Entry update path.

Approval as a Node class exists in nodes.py (line 817 — predates Phase
17, used by other surfaces); the gate is that the HR manifest and the
Phase 17 surface DO NOT depend on it. Phase 17 introduces no new
Approval-like Node class either.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.models.edges import (
    AUTHORED_BY,
    CONTAINS,
    HAS_COMMENT,
    IS_MEMBER_OF,
    OWNS,
)
from app.models.nodes import (
    Comment,
    ContentProfile,
    Entry,
    Track,
    User,
    Workspace,
)
from app.services.app_graph import (
    catalog_user,
    catalog_workspace,
    ensure_integral_app_graph,
)
from app.services.app_lifecycle import install_app
from app.services.content_profile_loader import load_library_profiles_with_issues
from app.utils.time import utc_now_iso


async def _load_library_cp(slug: str) -> ContentProfile:
    specs, _ = load_library_profiles_with_issues(profiles_root=Path("app/profiles"))
    spec = next(s for s in specs if s.slug == slug)
    manifest = dict(spec.manifest)
    pkg = dict(manifest.get("package") or {})
    pkg["version"] = spec.version or "1.0.0"
    manifest["package"] = pkg
    now = utc_now_iso()
    return await ContentProfile.create(
        name=manifest["package"]["name"],
        scope="app",
        manifest=manifest,
        library_package=True,
        version="1.0.0",
        created_at=now,
        updated_at=now,
    )


async def _provision_fixture() -> dict:
    # Shell only — library CP is loaded from disk below; skip disk catalog
    # sync to avoid JsonDB races when the prior test's teardown is in flight.
    await ensure_integral_app_graph(include_library=False)
    now = utc_now_iso()
    workspace = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name=" TimeOff",
        name_fold=" timeoff",
        created_at=now,
        updated_at=now,
    )
    await catalog_workspace(workspace)
    founder = await User.create(
        user_id="auth-founder-to", display_name="Founder", created_at=now
    )
    approver = await User.create(
        user_id="auth-approver-to", display_name="Approver", created_at=now
    )
    requester = await User.create(
        user_id="auth-requester-to", display_name="Requester", created_at=now
    )
    for u in (founder, approver, requester):
        await catalog_user(u)
    await founder.connect(workspace, edge=OWNS, created_at=now)
    for u, role in ((founder, "owner"), (approver, "member"), (requester, "member")):
        await u.connect(workspace, edge=IS_MEMBER_OF, role=role, joined_at=now)

    hr_lib = await _load_library_cp("hr_app")
    install = await install_app(
        workspace_id=workspace.id, library_cp_id=hr_lib.id, actor_id=founder.id
    )
    assert install.get("status") == "active", install
    from app.models.nodes import App

    hr_app = await App.get(install["app_id"])
    assert hr_app is not None, install
    track_nodes = [
        t
        for t in await hr_app.nodes(edge=[CONTAINS], node=["Track"])
        if isinstance(t, Track)
    ]
    tracks = {getattr(t, "title", ""): t for t in track_nodes}
    assert "Requests" in tracks, f"expected Requests track; got titles {sorted(tracks)}"
    return {
        "workspace": workspace,
        "founder": founder,
        "approver": approver,
        "requester": requester,
        "hr_app": hr_app,
        "tracks": tracks,
    }


@pytest.mark.asyncio
async def test_time_off_status_transitions_via_status_field():
    """HR-04 — time_off_request moves requested → approved by status update.

    The transition is a plain custom_fields update — Phase 17 mandates
    reuse of the existing entry-update path, not a new approval primitive.
    """
    fx = await _provision_fixture()
    time_off_track = fx["tracks"]["Requests"]
    now = utc_now_iso()

    req = await Entry.create(
        track_id=time_off_track.id,
        title="Vacation — Requester",
        author_id=fx["requester"].id,
        custom_fields={
            "leave_type": "vacation",
            "start_date": "2026-07-01",
            "end_date": "2026-07-05",
            "days": 5,
            "status": "requested",
        },
    )
    await time_off_track.connect(req, edge=CONTAINS, added_at=now)

    # Approver transitions status — direct custom_fields update, no new primitive.
    req.custom_fields = {**req.custom_fields, "status": "approved"}
    await req.save()

    reloaded = await Entry.get(req.id)
    assert reloaded.custom_fields["status"] == "approved"


@pytest.mark.asyncio
async def test_approver_comment_attaches_to_request():
    """HR-04 — approval discussion rides the existing Comment surface."""
    fx = await _provision_fixture()
    time_off_track = fx["tracks"]["Requests"]
    now = utc_now_iso()

    req = await Entry.create(
        track_id=time_off_track.id,
        title="Vacation — Requester 2",
        author_id=fx["requester"].id,
        custom_fields={"leave_type": "vacation", "status": "requested"},
    )
    await time_off_track.connect(req, edge=CONTAINS, added_at=now)

    # Approver posts a comment — existing Comment node; reused, not new.
    comment = await Comment.create(
        body="Approved — see you 2026-07-06.",
        author_user_id=fx["approver"].id,
        entry_id=req.id,
        created_at=now,
        updated_at=now,
    )
    await req.connect(comment, edge=HAS_COMMENT, created_at=now)
    await comment.connect(fx["approver"], edge=AUTHORED_BY, authored_at=now)

    # Walk the comment back from the entry.
    comments = await req.nodes(edge=["HAS_COMMENT"], direction="out", node=["Comment"])
    assert len(comments) == 1
    assert comments[0].id == comment.id


def test_no_new_approval_node_introduced_by_phase_17():
    """HR-04 invariant — Phase 17 introduces no new Approval-like Node class.

    The pre-existing ``Approval`` Node (nodes.py line 817) predates Phase 17
    and serves other surfaces; it is NOT a Phase 17 contribution. This test
    pins the class count of approval-related Node subclasses to the
    pre-Phase-17 baseline (1) so a future regression that adds a
    TimeOffApproval / LeaveApproval / WorkflowApproval Node sibling fails
    fast.
    """
    from app.models import nodes

    approval_like = [
        name
        for name in dir(nodes)
        if "approval" in name.lower() and isinstance(getattr(nodes, name), type)
    ]
    # Only the legacy `Approval` class is allowed — Phase 17 did not add a
    # new sibling (TimeOffApproval, LeaveApproval, etc.).
    assert approval_like == ["Approval"], approval_like
