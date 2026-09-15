"""Wave 1 access-control hardening regressions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF, OWNS
from app.models.nodes import Track, User, Workspace
from app.services.password_reset import _find_user_node_by_token_hash
from app.services.permissions import resolve_role
from app.services.share_links import _is_expired, mint_share_link
from app.services.sharing import add_collaborator, list_access
from app.services.workspace_permissions import workspace_staff_implicit_resource_role
from app.utils.time import utc_now_iso


async def _user(name: str) -> User:
    return await User.create(display_name=name)


async def _org_workspace(name: str) -> Workspace:
    return await Workspace.create(
        kind="organization",
        workspace_type="company",
        name=name,
        name_fold=name.casefold(),
    )


@pytest.mark.asyncio
async def test_admin_cannot_grant_owner_role():
    from app.api.errors import InsufficientPermissionsError

    owner = await _user("w1_owner")
    admin = await _user("w1_admin")
    track = await Track.create(title="GrantOwner", title_fold="grantowner")
    await owner.connect(track, edge=OWNS)
    await admin.connect(track, edge=COLLABORATES_ON, role="admin")

    with pytest.raises(InsufficientPermissionsError, match="Only the resource owner"):
        await add_collaborator(admin.id, "track", track.id, owner.id, role="owner")


@pytest.mark.asyncio
async def test_true_owner_can_grant_owner_role():
    owner = await _user("w1_true_owner")
    target = await _user("w1_target")
    track = await Track.create(title="TrueOwnerGrant", title_fold="trueownergrant")
    await owner.connect(track, edge=OWNS)

    result = await add_collaborator(
        owner.id, "track", track.id, target.id, role="owner"
    )
    assert result["role"] == "owner"
    assert await resolve_role(target.id, "track", track.id) == "owner"


@pytest.mark.asyncio
async def test_org_staff_implicit_role_capped_to_commenter():
    ws = await _org_workspace("Org Staff Cap")
    staff = await _user("w1_staff")
    now = utc_now_iso()
    await staff.connect(ws, edge=IS_MEMBER_OF, role="admin", joined_at=now)

    implicit = await workspace_staff_implicit_resource_role(staff.id, ws.id)
    # Read + participate. Was "viewer" until org admins turned out to be unable
    # to answer a comment on content they administer (QA "users with full
    # permissions cannot add comments", filed July 1 and again August 5).
    # `commenter` is still one rank below `editor`, so the authority gates
    # below — minting shares, managing collaborators — stay closed.
    assert implicit == "commenter"


@pytest.mark.asyncio
async def test_org_admin_cannot_mint_share_without_direct_grant():
    from app.api.errors import InsufficientPermissionsError

    ws = await _org_workspace("Org Mint Gate")
    owner = await _user("w1_ws_owner")
    admin = await _user("w1_ws_admin")
    now = utc_now_iso()
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner", joined_at=now)
    await admin.connect(ws, edge=IS_MEMBER_OF, role="admin", joined_at=now)
    track = await Track.create(title="PrivateTrack", workspace_id=ws.id)
    await owner.connect(track, edge=OWNS)

    with pytest.raises(InsufficientPermissionsError):
        await mint_share_link(admin.id, "track", track.id, role="viewer")


@pytest.mark.asyncio
async def test_list_access_viewer_gets_effective_role_only():
    owner = await _user("w1_la_owner")
    viewer = await _user("w1_la_viewer")
    track = await Track.create(title="AccessList")
    await owner.connect(track, edge=OWNS)
    await viewer.connect(track, edge=COLLABORATES_ON, role="viewer")

    snapshot = await list_access(viewer.id, "track", track.id)
    assert snapshot["effective_role"] == "viewer"
    assert "direct" not in snapshot
    assert snapshot["links_visible"] is False


@pytest.mark.asyncio
async def test_share_link_expiry_uses_utc():
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)
    link = SimpleNamespace(expires_at=future.isoformat())
    assert _is_expired(link) is False

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    expired = SimpleNamespace(expires_at=past)
    assert _is_expired(expired) is True


@pytest.mark.asyncio
async def test_password_reset_hash_scan_uses_constant_time_compare(monkeypatch):
    import hashlib
    import secrets as secrets_mod

    token_hash = hashlib.sha256(b"wave1-token").hexdigest()
    user = await User.create(
        display_name="Reset Scan",
        preferences={"reset_token": {"hash": token_hash, "attempts": 0}},
    )

    calls: list[tuple[str, str]] = []
    original = secrets_mod.compare_digest

    def _spy(a, b):
        calls.append((a, b))
        return original(a, b)

    async def _selective_find(query, *args, **kwargs):
        if isinstance(query, dict) and query.get(
            "context.preferences.reset_token.hash"
        ):
            return []
        if query == {}:
            return [user]
        return []

    monkeypatch.setattr("app.services.password_reset.secrets.compare_digest", _spy)
    monkeypatch.setattr("app.services.password_reset.User.find", _selective_find)

    found = await _find_user_node_by_token_hash(token_hash)
    assert found is not None
    assert found.id == user.id
    assert calls


@pytest.mark.asyncio
async def test_auth_me_scrubs_sensitive_preferences():
    from app.api.auth import _scrub_sensitive_preferences

    prefs = {
        "theme": "dark",
        "reset_token": {"hash": "secret"},
        "email_verification": {"hash": "otp"},
    }
    scrubbed = _scrub_sensitive_preferences(prefs)
    assert scrubbed == {"theme": "dark"}
