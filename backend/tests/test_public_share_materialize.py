"""Public-share GET materializes entry types only when write perms allow."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.smoke


@pytest.mark.asyncio
async def test_get_public_track_materializes_when_create_allowed(
    authenticated_client, test_user, monkeypatch
):
    """Unauthenticated public-track GET materializes when create_entries is on."""
    from app.api import tracks_public_share as public_mod
    from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
    from app.models.nodes import Track, Workspace
    from app.utils.time import utc_now_iso

    now = utc_now_iso()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Materialize Share WS",
        name_fold="materialize share ws",
        created_at=now,
        updated_at=now,
    )
    await test_user.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)
    track = await Track.create(
        title="Anchored Public",
        owner_id=test_user.id,
        workspace_id=ws.id,
        visibility="private",
    )
    await test_user.connect(track, edge=COLLABORATES_ON, role="owner", added_at=now)

    body = {
        "enabled": True,
        "public_permissions": {
            "read_entries": True,
            "create_entries": True,
            "update_entries": False,
            "read_comments": False,
            "create_comments": False,
        },
    }
    res = await authenticated_client.post(
        f"/api/tracks/{track.id}/public-share", json=body
    )
    assert res.status_code == 200
    token = res.json()["token"]

    calls = {"n": 0}

    async def spy_materialize(t):
        calls["n"] += 1
        return []

    monkeypatch.setattr(
        public_mod, "materialize_entry_types_from_tier", spy_materialize
    )

    res = await authenticated_client.get(f"/api/public-share/track/{token}")
    assert res.status_code == 200
    assert calls["n"] == 1
    assert "entry_types" in res.json()


@pytest.mark.asyncio
async def test_get_public_track_skips_materialize_when_read_only(
    authenticated_client, test_user, monkeypatch
):
    """Read-only public GET must not write EntryTypes."""
    from app.api import tracks_public_share as public_mod
    from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
    from app.models.nodes import Track, Workspace
    from app.utils.time import utc_now_iso

    now = utc_now_iso()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Readonly Share WS",
        name_fold="readonly share ws",
        created_at=now,
        updated_at=now,
    )
    await test_user.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)
    track = await Track.create(
        title="Readonly Public",
        owner_id=test_user.id,
        workspace_id=ws.id,
        visibility="private",
    )
    await test_user.connect(track, edge=COLLABORATES_ON, role="owner", added_at=now)

    body = {
        "enabled": True,
        "public_permissions": {
            "read_entries": True,
            "create_entries": False,
            "update_entries": False,
            "read_comments": False,
            "create_comments": False,
        },
    }
    res = await authenticated_client.post(
        f"/api/tracks/{track.id}/public-share", json=body
    )
    assert res.status_code == 200
    token = res.json()["token"]

    calls = {"n": 0}

    async def spy_materialize(t):
        calls["n"] += 1
        return []

    monkeypatch.setattr(
        public_mod, "materialize_entry_types_from_tier", spy_materialize
    )

    res = await authenticated_client.get(f"/api/public-share/track/{token}")
    assert res.status_code == 200
    assert calls["n"] == 0
    assert "entry_types" in res.json()
