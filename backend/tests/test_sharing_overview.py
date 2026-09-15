"""Phase 8 Plan 08-05 — coverage for GET /api/me/sharing-overview.

Caller-scoped outbound sharing aggregator (SET-08). Verifies the strict
caller-scoped invariant from the threat register (T-08-05-I01) — no other
user's resources/links/exclusions/invitations leak through.

Negative cases (the cross-user-leak tests) are the load-bearing security
gate; the happy-path cases assert the three buckets light up correctly.

Service-level fixture builders (mirrors backend/tests/test_sharing.py
idiom) plus HTTP cases against the authenticated client.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.models.edges import EXCLUDED_FROM, OWNS
from app.models.nodes import (
    App,
    Entry,
    Invitation,
    ShareLink,
    Track,
    User,
    Workspace,
)
from app.services.invitations import (
    create_invitation,
    create_resource_invitation,
)
from app.services.share_links import mint_share_link, revoke_share_link
from app.services.sharing import add_exclusion

# ---------------------------------------------------------------------------
# Helpers (mirrors test_sharing.py idiom; isolated namespace per test).
# ---------------------------------------------------------------------------


async def _user(suffix: str) -> User:
    return await User.create(
        user_id=f"overview_user_{suffix}",
        display_name=f"Overview User {suffix}",
    )


async def _workspace(name: str, kind: str = "organization") -> Workspace:
    return await Workspace.create(kind=kind, name=name, name_fold=name.casefold())


async def _track(title: str, workspace_id: str = "") -> Track:
    return await Track.create(
        title=title, title_fold=title.casefold(), workspace_id=workspace_id
    )


async def _space(name: str, workspace_id: str = "") -> App:
    return await App.create(
        name=name, name_fold=name.casefold(), workspace_id=workspace_id
    )


# ---------------------------------------------------------------------------
# 1. Unauthenticated.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unauthenticated_returns_401(client: AsyncClient):
    """No auth header → 401 (MissingAuthenticationError envelope)."""
    resp = await client.get("/api/me/sharing-overview")
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# 2. Empty buckets.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_overview_for_fresh_user(
    authenticated_client: AsyncClient, test_user
):
    """A user with no resources gets three empty arrays — never null."""
    resp = await authenticated_client.get("/api/me/sharing-overview")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"share_links": [], "exclusions": [], "invitations": []}


# ---------------------------------------------------------------------------
# 3. Share link appears.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_share_link_appears_after_mint(
    authenticated_client: AsyncClient, test_user
):
    """Mint a share link on an owned Track → appears in share_links bucket.

    NOTE: The HTTP authenticated principal is the AuthUser id (the JWT
    ``sub`` claim — what ``resolve_principal_id(request)`` returns). The
    ``test_user`` fixture's ``.user_id`` field holds that AuthUser id; the
    ``.id`` field is the User Node id. When seeding via service-level
    helpers, the service writes ``ShareLink.created_by`` from whatever id
    we pass; we use the AuthUser id so the aggregator's
    ``{created_by: principal_id}`` filter matches.
    """
    track = await _track("OverviewT", workspace_id="")
    await test_user.connect(track, edge=OWNS)
    principal_id = test_user.user_id
    minted = await mint_share_link(principal_id, "track", track.id, role="viewer")
    link_id = minted["share_link"]["id"]

    resp = await authenticated_client.get("/api/me/sharing-overview")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = [row["id"] for row in body["share_links"]]
    assert link_id in ids
    row = next(r for r in body["share_links"] if r["id"] == link_id)
    assert row["resource_type"] == "track"
    assert row["resource_id"] == track.id
    assert row["resource_label"] == "OverviewT"
    assert row["role"] == "viewer"


# ---------------------------------------------------------------------------
# 4. Revoked share link excluded.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoked_share_link_excluded(
    authenticated_client: AsyncClient, test_user
):
    track = await _track("RevokeT")
    await test_user.connect(track, edge=OWNS)
    principal_id = test_user.user_id
    minted = await mint_share_link(principal_id, "track", track.id, role="viewer")
    link_id = minted["share_link"]["id"]
    await revoke_share_link(principal_id, link_id)

    resp = await authenticated_client.get("/api/me/sharing-overview")
    assert resp.status_code == 200
    body = resp.json()
    ids = [row["id"] for row in body["share_links"]]
    assert link_id not in ids


# ---------------------------------------------------------------------------
# 5. Exclusion on owned resource appears.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exclusion_appears_for_owned_resource(
    authenticated_client: AsyncClient, test_user
):
    other = await _user("excl_other")
    app_node = await _space("ExclS")
    track = await _track("ExclT")
    await test_user.connect(app_node, edge=OWNS)
    await test_user.connect(track, edge=OWNS)
    # ``add_exclusion`` requires the target to have inherited access — give
    # `other` an App-level COLLABORATES_ON so the track-level exclusion is
    # a meaningful operation. Then assert the resulting EXCLUDED_FROM row
    # appears in the aggregator.
    from app.models.edges import COLLABORATES_ON, CONTAINS

    await other.connect(app_node, edge=COLLABORATES_ON, role="editor")
    await app_node.connect(track, edge=CONTAINS)
    await add_exclusion(test_user.id, "track", track.id, other.id, reason="testing")

    resp = await authenticated_client.get("/api/me/sharing-overview")
    assert resp.status_code == 200
    body = resp.json()
    rows = [
        r
        for r in body["exclusions"]
        if r["resource_id"] == track.id and r["excluded_user_id"] == other.id
    ]
    assert (
        rows
    ), f"expected exclusion for track {track.id} -> user {other.id}; got {body['exclusions']}"
    row = rows[0]
    assert row["resource_type"] == "track"
    assert row["resource_label"] == "ExclT"


# ---------------------------------------------------------------------------
# 6. Resource-level invitation appears.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resource_level_invitation_appears(
    authenticated_client: AsyncClient, test_user
):
    track = await _track("InviteT")
    await test_user.connect(track, edge=OWNS)
    principal_id = test_user.user_id
    invitation, _, _ = await create_resource_invitation(
        resource_type="track",
        resource_id=track.id,
        workspace_id="",
        inviter_user_id=principal_id,
        email="ext@example.com",
        role="viewer",
        send_email_notification=False,
        resource_label="InviteT",
    )

    resp = await authenticated_client.get("/api/me/sharing-overview")
    assert resp.status_code == 200
    body = resp.json()
    ids = [r["id"] for r in body["invitations"]]
    assert invitation.id in ids
    row = next(r for r in body["invitations"] if r["id"] == invitation.id)
    assert row["resource_type"] == "track"
    assert row["resource_id"] == track.id
    assert row["resource_label"] == "InviteT"
    assert row["status"] == "pending"
    assert row["role"] == "viewer"


# ---------------------------------------------------------------------------
# 7. Workspace-targeted invitation EXCLUDED from this surface.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workspace_invitation_excluded_from_overview(
    authenticated_client: AsyncClient, test_user
):
    ws = await _workspace("OverviewOrg")
    # workspace_id must be a real organization workspace for create_invitation.
    inv, _, _ = await create_invitation(
        workspace=ws,
        inviter_user_id=test_user.user_id,
        email="member@example.com",
        role="member",
        send_email_notification=False,
    )

    resp = await authenticated_client.get("/api/me/sharing-overview")
    assert resp.status_code == 200
    body = resp.json()
    ids = [r["id"] for r in body["invitations"]]
    assert inv.id not in ids, (
        "workspace-targeted invitation MUST NOT appear in /me/sharing-overview "
        "(this surface is resource-level only; workspace invites live on the "
        "Workspace Members surface)."
    )


# ---------------------------------------------------------------------------
# 8. Cross-user share-link leakage negative case (T-08-05-I01).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_user_share_link_not_leaked(
    authenticated_client: AsyncClient, test_user, second_user_client, second_user
):
    """User B mints a link on B's resource; User A's overview MUST NOT include it."""
    if second_user is None:
        pytest.skip("second_user fixture unavailable")
    track = await _track("LeakT")
    await second_user.connect(track, edge=OWNS)
    minted = await mint_share_link(second_user.id, "track", track.id, role="viewer")
    other_link_id = minted["share_link"]["id"]

    # User A (authenticated_client) queries — must NOT see User B's link.
    resp = await authenticated_client.get("/api/me/sharing-overview")
    assert resp.status_code == 200
    body = resp.json()
    ids = [r["id"] for r in body["share_links"]]
    assert other_link_id not in ids, (
        f"caller-scoped invariant breach: link {other_link_id} (minted by "
        f"user {second_user.id}) appeared in user {test_user.id}'s overview"
    )


# ---------------------------------------------------------------------------
# 9. Cross-user exclusion leakage negative case (T-08-05-I01).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_user_exclusion_not_leaked(
    authenticated_client: AsyncClient, test_user, second_user
):
    if second_user is None:
        pytest.skip("second_user fixture unavailable")
    third = await _user("third_party")
    app_node = await _space("LeakS")
    track = await _track("LeakXT")
    # second_user owns these resources.
    await second_user.connect(app_node, edge=OWNS)
    await second_user.connect(track, edge=OWNS)
    from app.models.edges import COLLABORATES_ON, CONTAINS

    await third.connect(app_node, edge=COLLABORATES_ON, role="editor")
    await app_node.connect(track, edge=CONTAINS)
    await add_exclusion(second_user.id, "track", track.id, third.id)

    # test_user (the auth'd caller) MUST NOT see second_user's exclusion.
    resp = await authenticated_client.get("/api/me/sharing-overview")
    assert resp.status_code == 200
    body = resp.json()
    leaked = [
        r
        for r in body["exclusions"]
        if r["resource_id"] == track.id and r["excluded_user_id"] == third.id
    ]
    assert not leaked, (
        f"caller-scoped invariant breach: exclusion on {track.id} owned by "
        f"{second_user.id} appeared in {test_user.id}'s overview"
    )


# ---------------------------------------------------------------------------
# 10. extra:forbid Pydantic schema.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_response_schema_extra_field_rejected():
    """SharingOverviewResponse + its row models use extra='forbid'."""
    from pydantic import ValidationError

    from app.schemas.sharing_overview import (
        OutboundShareLink,
        SharingOverviewResponse,
    )

    with pytest.raises(ValidationError):
        OutboundShareLink(  # type: ignore[call-arg]
            id="x",
            resource_type="track",
            resource_id="t",
            resource_label="T",
            role="viewer",
            unknown_field="boom",
        )

    with pytest.raises(ValidationError):
        SharingOverviewResponse(  # type: ignore[call-arg]
            share_links=[],
            exclusions=[],
            invitations=[],
            unknown_top_level="boom",
        )


# ---------------------------------------------------------------------------
# 11. No new PolicyAction member.
# ---------------------------------------------------------------------------


def test_no_new_policy_action_member():
    """The aggregator is a caller-scoped READ — it does NOT require a new
    PolicyAction Literal member. Mirrors the /me/shared + /me/invitations
    precedent. This test guards against accidental D-12 drift.
    """
    from typing import get_args

    from app.schemas.policy import PolicyAction

    members = set(get_args(PolicyAction))
    # If any of these slipped in by mistake, the strict-superset invariant
    # is muddied and we'd have to extend both Literals in lockstep.
    for offender in (
        "sharing.overview.read",
        "sharing_overview.read",
        "me.sharing_overview.read",
    ):
        assert offender not in members, (
            f"PolicyAction must NOT carry {offender!r} — the aggregator is a "
            "caller-scoped read, no policy gate required."
        )
