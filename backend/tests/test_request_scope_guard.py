"""SM3 — an inaccessible ``X-Integral-Scope`` must fail, not fall back.

Before this fix, ``X-Integral-Scope: ws:<unknown-or-forbidden-id>`` silently
resolved to the caller's Personal Workspace and returned its contents with a
200. Not a cross-tenant leak (it is the caller's own data), but it contradicted
the documented contract — "backend validates caller has access to that
workspace and refuses cross-workspace reads" — and made a client pinned to a
stale workspace id look like it was working.

Contract now:
  * no header            → Personal Workspace (unchanged; deliberate)
  * ``ws:<accessible>``  → that workspace
  * ``ws:<unknown>``     → 403
  * ``ws:<other user's>``→ 403 (indistinguishable from unknown, by design)
  * ``<bare id>``        → 400 (missing the mandatory ``ws:`` prefix)
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_no_scope_header_falls_back_to_personal_workspace(
    authenticated_client: AsyncClient, test_user
):
    """Absent header stays a valid request resolving to Personal Workspace."""
    created = await authenticated_client.post(
        "/api/tracks", json={"title": "Guard Personal Track", "visibility": "private"}
    )
    assert created.status_code == 200, created.text

    listing = await authenticated_client.get("/api/tracks")
    assert listing.status_code == 200, listing.text
    titles = [t["title"] for t in listing.json()["tracks"]]
    assert "Guard Personal Track" in titles


@pytest.mark.asyncio
async def test_accessible_scope_header_is_honored(
    authenticated_client: AsyncClient, test_user
):
    """A workspace the caller belongs to still resolves normally."""
    ws = await authenticated_client.post(
        "/api/workspaces", json={"name": "Guard Accessible Org"}
    )
    assert ws.status_code == 200, ws.text
    workspace_id = ws.json()["workspace"]["id"]

    listing = await authenticated_client.get(
        "/api/tracks", headers={"X-Integral-Scope": f"ws:{workspace_id}"}
    )
    assert listing.status_code == 200, listing.text
    assert listing.json().get("scope_workspace_id") == workspace_id


@pytest.mark.asyncio
async def test_unknown_workspace_scope_is_rejected(
    authenticated_client: AsyncClient, test_user
):
    """A workspace id that does not exist must 403, not serve Personal."""
    listing = await authenticated_client.get(
        "/api/tracks", headers={"X-Integral-Scope": "ws:n.Workspace.deadbeef"}
    )
    assert listing.status_code == 403, listing.text
    assert "tracks" not in listing.json()


@pytest.mark.asyncio
async def test_other_users_workspace_scope_is_rejected(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
    test_user,
    second_user,
):
    """A real workspace the caller has no membership in must 403."""
    ws = await second_user_client.post(
        "/api/workspaces", json={"name": "Guard Foreign Org"}
    )
    assert ws.status_code == 200, ws.text
    foreign_id = ws.json()["workspace"]["id"]

    listing = await authenticated_client.get(
        "/api/tracks", headers={"X-Integral-Scope": f"ws:{foreign_id}"}
    )
    assert listing.status_code == 403, listing.text
    assert "tracks" not in listing.json()


@pytest.mark.asyncio
async def test_bare_workspace_id_without_ws_prefix_is_rejected(
    authenticated_client: AsyncClient, test_user
):
    """A header missing the ``ws:`` prefix parses to None — that must 400."""
    ws = await authenticated_client.post(
        "/api/workspaces", json={"name": "Guard Bare Id Org"}
    )
    assert ws.status_code == 200, ws.text
    workspace_id = ws.json()["workspace"]["id"]

    listing = await authenticated_client.get(
        "/api/tracks", headers={"X-Integral-Scope": workspace_id}
    )
    assert listing.status_code == 400, listing.text
    assert "tracks" not in listing.json()


@pytest.mark.asyncio
async def test_resolver_raises_for_inaccessible_scope(test_user):
    """Unit-level: the resolver itself raises rather than returning a fallback."""
    from jvspatial.api.exceptions import InsufficientPermissionsError

    from app.services.request_scope import resolve_workspace_id_from_request

    class _Req:
        headers = {"x-integral-scope": "ws:n.Workspace.does-not-exist"}
        state = None

    with pytest.raises(InsufficientPermissionsError):
        await resolve_workspace_id_from_request(_Req(), test_user.id)


@pytest.mark.asyncio
async def test_foreign_scope_rejects_create_effects(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
    test_user,
    second_user,
):
    """A create must not succeed in a fallback workspace after a bad header."""
    foreign = await second_user_client.post(
        "/api/workspaces", json={"name": "Create Scope Foreign Org"}
    )
    assert foreign.status_code == 200, foreign.text
    foreign_id = foreign.json()["workspace"]["id"]
    headers = {"X-Integral-Scope": f"ws:{foreign_id}"}

    for path, body in (
        ("/api/tracks", {"title": "Forbidden Scoped Track"}),
        ("/api/apps", {"name": "Forbidden Scoped App"}),
    ):
        response = await authenticated_client.post(path, headers=headers, json=body)
        assert response.status_code == 403, response.text

    tracks = await authenticated_client.get("/api/tracks")
    apps = await authenticated_client.get("/api/apps")
    assert "Forbidden Scoped Track" not in [
        track["title"] for track in tracks.json()["tracks"]
    ]
    assert "Forbidden Scoped App" not in [app["name"] for app in apps.json()["apps"]]


@pytest.mark.asyncio
async def test_create_header_selects_workspace_and_rejects_body_conflict(
    authenticated_client: AsyncClient, test_user
):
    """A valid create header binds the effect and cannot disagree with body."""
    first = await authenticated_client.post(
        "/api/workspaces", json={"name": "Create Scope First Org"}
    )
    second = await authenticated_client.post(
        "/api/workspaces", json={"name": "Create Scope Second Org"}
    )
    assert first.status_code == second.status_code == 200
    first_id = first.json()["workspace"]["id"]
    second_id = second.json()["workspace"]["id"]
    headers = {"X-Integral-Scope": f"ws:{first_id}"}

    track = await authenticated_client.post(
        "/api/tracks", headers=headers, json={"title": "Bound Track"}
    )
    assert track.status_code == 200, track.text
    assert track.json()["track"]["workspace_id"] == first_id

    app = await authenticated_client.post(
        "/api/apps", headers=headers, json={"name": "Bound App"}
    )
    assert app.status_code == 200, app.text
    assert app.json()["app"]["workspace_id"] == first_id

    for path, body in (
        ("/api/tracks", {"title": "Conflicted Track", "workspace_id": second_id}),
        ("/api/apps", {"name": "Conflicted App", "workspace_id": second_id}),
    ):
        response = await authenticated_client.post(path, headers=headers, json=body)
        assert response.status_code == 400, response.text

    other_app = await authenticated_client.post(
        "/api/apps", json={"name": "Other Workspace App", "workspace_id": second_id}
    )
    assert other_app.status_code == 200, other_app.text
    nested = await authenticated_client.post(
        "/api/tracks",
        headers=headers,
        json={"title": "Conflicted App Track", "app_id": other_app.json()["app"]["id"]},
    )
    assert nested.status_code == 400, nested.text


@pytest.mark.asyncio
async def test_parent_resource_creates_reject_foreign_or_mismatched_scope(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
    test_user,
    second_user,
):
    """Tag and view writes must agree with the authenticated header scope."""
    own = await authenticated_client.post(
        "/api/workspaces", json={"name": "Parent Scope Own Org"}
    )
    other = await authenticated_client.post(
        "/api/workspaces", json={"name": "Parent Scope Other Org"}
    )
    foreign = await second_user_client.post(
        "/api/workspaces", json={"name": "Parent Scope Foreign Org"}
    )
    assert own.status_code == other.status_code == foreign.status_code == 200
    own_id = own.json()["workspace"]["id"]
    other_id = other.json()["workspace"]["id"]
    foreign_id = foreign.json()["workspace"]["id"]
    track = await authenticated_client.post(
        "/api/tracks", json={"title": "Parent Scope Track", "workspace_id": own_id}
    )
    app = await authenticated_client.post(
        "/api/apps", json={"name": "Parent Scope App", "workspace_id": own_id}
    )
    assert track.status_code == app.status_code == 200
    track_id = track.json()["track"]["id"]
    app_id = app.json()["app"]["id"]

    cases = (
        ("/api/tags", {"name": "Guard Track Tag", "track_id": track_id}),
        ("/api/tags", {"name": "Guard App Tag", "app_id": app_id}),
        (f"/api/tracks/{track_id}/views", {"name": "Guard View"}),
    )
    for workspace_id, expected_status in ((foreign_id, 403), (other_id, 400)):
        headers = {"X-Integral-Scope": f"ws:{workspace_id}"}
        for path, body in cases:
            response = await authenticated_client.post(path, headers=headers, json=body)
            assert response.status_code == expected_status, response.text

    own_headers = {"X-Integral-Scope": f"ws:{own_id}"}
    tags = await authenticated_client.get(
        "/api/tags", headers=own_headers, params={"track_id": track_id}
    )
    views = await authenticated_client.get(
        f"/api/tracks/{track_id}/views", headers=own_headers
    )
    assert tags.status_code == views.status_code == 200
    assert "Guard Track Tag" not in [tag["name"] for tag in tags.json()["tags"]]
    assert "Guard View" not in [view["name"] for view in views.json()["views"]]


@pytest.mark.asyncio
async def test_entry_and_comment_effects_match_header_scope(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
    test_user,
    second_user,
):
    own = await authenticated_client.post(
        "/api/workspaces", json={"name": "Entry Scope Own Org"}
    )
    other = await authenticated_client.post(
        "/api/workspaces", json={"name": "Entry Scope Other Org"}
    )
    foreign = await second_user_client.post(
        "/api/workspaces", json={"name": "Entry Scope Foreign Org"}
    )
    assert own.status_code == other.status_code == foreign.status_code == 200
    own_id = own.json()["workspace"]["id"]
    track = await authenticated_client.post(
        "/api/tracks", json={"title": "Entry Scope Track", "workspace_id": own_id}
    )
    assert track.status_code == 200, track.text
    track_id = track.json()["track"]["id"]
    entry = await authenticated_client.post(
        "/api/entries",
        headers={"X-Integral-Scope": f"ws:{own_id}"},
        json={"track_id": track_id, "title": "Entry Scope Existing"},
    )
    assert entry.status_code == 200, entry.text
    entry_id = entry.json()["entry"]["id"]

    for workspace_id, expected_status in (
        (foreign.json()["workspace"]["id"], 403),
        (other.json()["workspace"]["id"], 400),
    ):
        headers = {"X-Integral-Scope": f"ws:{workspace_id}"}
        create_entry = await authenticated_client.post(
            "/api/entries",
            headers=headers,
            json={"track_id": track_id, "title": "Entry Scope Rejected"},
        )
        create_comment = await authenticated_client.post(
            f"/api/entries/{entry_id}/comments",
            headers=headers,
            json={"text": "Comment Scope Rejected"},
        )
        assert create_entry.status_code == expected_status, create_entry.text
        assert create_comment.status_code == expected_status, create_comment.text


@pytest.mark.asyncio
async def test_existing_resource_effects_reject_other_accessible_workspace_scope(
    authenticated_client: AsyncClient, test_user
):
    """Resource permission must not override the selected workspace boundary."""
    first = await authenticated_client.post(
        "/api/workspaces", json={"name": "Effect Scope First"}
    )
    second = await authenticated_client.post(
        "/api/workspaces", json={"name": "Effect Scope Second"}
    )
    assert first.status_code == second.status_code == 200
    first_id = first.json()["workspace"]["id"]
    wrong_scope = {"X-Integral-Scope": f"ws:{second.json()['workspace']['id']}"}
    app = await authenticated_client.post(
        "/api/apps", json={"name": "Effect Scope App", "workspace_id": first_id}
    )
    track = await authenticated_client.post(
        "/api/tracks", json={"title": "Effect Scope Track", "workspace_id": first_id}
    )
    assert app.status_code == track.status_code == 200
    app_id = app.json()["app"]["id"]
    track_id = track.json()["track"]["id"]
    entry = await authenticated_client.post(
        "/api/entries", json={"title": "Effect Scope Entry", "track_id": track_id}
    )
    tag = await authenticated_client.post(
        "/api/tags", json={"name": "Effect Scope Tag", "track_id": track_id}
    )
    view = await authenticated_client.post(
        f"/api/tracks/{track_id}/views", json={"name": "Effect Scope View"}
    )
    assert entry.status_code == tag.status_code == view.status_code == 200
    cases = (
        (f"/api/apps/{app_id}", {"description": "wrong scope"}),
        (f"/api/tracks/{track_id}", {"purpose": "wrong scope"}),
        (f"/api/entries/{entry.json()['entry']['id']}", {"title": "wrong scope"}),
        (f"/api/tags/{tag.json()['tag']['id']}", {"name": "wrong scope"}),
        (f"/api/views/{view.json()['view']['id']}", {"name": "wrong scope"}),
    )
    for headers in (wrong_scope, {"X-Integral-Scope": "ws:n.Workspace.deadbeef"}):
        for path, body in cases:
            updated = await authenticated_client.put(path, headers=headers, json=body)
            assert updated.status_code == 403, (path, updated.text)
            deleted = await authenticated_client.delete(path, headers=headers)
            assert deleted.status_code == 403, (path, deleted.text)

    for path, _ in cases:
        present = await authenticated_client.get(path)
        assert present.status_code == 200, (path, present.text)


@pytest.mark.asyncio
async def test_comment_and_sharing_effects_bind_explicit_workspace_scope(
    authenticated_client: AsyncClient, test_user, second_user
):
    first = await authenticated_client.post(
        "/api/workspaces", json={"name": "Sharing Scope First"}
    )
    second = await authenticated_client.post(
        "/api/workspaces", json={"name": "Sharing Scope Second"}
    )
    assert first.status_code == second.status_code == 200
    first_id = first.json()["workspace"]["id"]
    wrong_scope = {"X-Integral-Scope": f"ws:{second.json()['workspace']['id']}"}
    app = await authenticated_client.post(
        "/api/apps", json={"name": "Sharing Scope App", "workspace_id": first_id}
    )
    track = await authenticated_client.post(
        "/api/tracks", json={"title": "Sharing Scope Track", "workspace_id": first_id}
    )
    assert app.status_code == track.status_code == 200
    app_id = app.json()["app"]["id"]
    track_id = track.json()["track"]["id"]
    entry = await authenticated_client.post(
        "/api/entries", json={"title": "Sharing Scope Entry", "track_id": track_id}
    )
    assert entry.status_code == 200
    entry_id = entry.json()["entry"]["id"]
    comment = await authenticated_client.post(
        f"/api/entries/{entry_id}/comments", json={"text": "Original comment"}
    )
    assert comment.status_code == 200, comment.text
    comment_id = comment.json()["comment"]["id"]

    for resource, resource_id in (
        ("apps", app_id),
        ("tracks", track_id),
        ("entries", entry_id),
    ):
        grant = await authenticated_client.post(
            f"/api/{resource}/{resource_id}/collaborators",
            headers=wrong_scope,
            json={"collaborator_user_id": second_user.id, "role": "viewer"},
        )
        assert grant.status_code == 403, (resource, grant.text)
    for resource, resource_id in (
        ("apps", app_id),
        ("tracks", track_id),
        ("entries", entry_id),
    ):
        exclusion = await authenticated_client.post(
            f"/api/{resource}/{resource_id}/exclusions",
            headers=wrong_scope,
            json={"user_id_to_exclude": second_user.id},
        )
        assert exclusion.status_code == 403, (resource, exclusion.text)

    updated = await authenticated_client.put(
        f"/api/comments/{comment_id}",
        headers=wrong_scope,
        json={"text": "Wrong-scope edit"},
    )
    deleted = await authenticated_client.delete(
        f"/api/comments/{comment_id}", headers=wrong_scope
    )
    assert updated.status_code == deleted.status_code == 403


@pytest.mark.asyncio
async def test_share_link_effects_validate_scope_with_cross_workspace_redeem(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
    test_user,
    second_user,
):
    first = await authenticated_client.post(
        "/api/workspaces", json={"name": "Link Scope First"}
    )
    second = await authenticated_client.post(
        "/api/workspaces", json={"name": "Link Scope Second"}
    )
    assert first.status_code == second.status_code == 200
    first_id = first.json()["workspace"]["id"]
    wrong_header = {"X-Integral-Scope": f"ws:{second.json()['workspace']['id']}"}
    correct_header = {"X-Integral-Scope": f"ws:{first_id}"}
    track = await authenticated_client.post(
        "/api/tracks", json={"title": "Link Scope Track", "workspace_id": first_id}
    )
    assert track.status_code == 200
    track_id = track.json()["track"]["id"]

    denied_mint = await authenticated_client.post(
        f"/api/tracks/{track_id}/shares",
        headers=wrong_header,
        json={"role": "viewer"},
    )
    assert denied_mint.status_code == 403, denied_mint.text
    minted = await authenticated_client.post(
        f"/api/tracks/{track_id}/shares",
        headers=correct_header,
        json={"role": "viewer"},
    )
    assert minted.status_code == 200, minted.text
    token = minted.json()["token"]
    link_id = minted.json()["share_link"]["id"]

    denied_revoke = await authenticated_client.delete(
        f"/api/shares/{link_id}", headers=wrong_header
    )
    assert denied_revoke.status_code == 403, denied_revoke.text

    denied_redeem = await second_user_client.post(
        "/api/shares/redeem",
        headers={"X-Integral-Scope": "ws:n.Workspace.deadbeef"},
        json={"token": token},
    )
    assert denied_redeem.status_code == 403, denied_redeem.text
    workspaces = await second_user_client.get("/api/workspaces")
    assert workspaces.status_code == 200
    personal_id = next(
        item["id"]
        for item in workspaces.json()["workspaces"]
        if item.get("kind") == "personal"
    )
    redeemed = await second_user_client.post(
        "/api/shares/redeem",
        headers={"X-Integral-Scope": f"ws:{personal_id}"},
        json={"token": token},
    )
    assert redeemed.status_code == 200, redeemed.text
    assert redeemed.json()["resource_id"] == track_id

    revoked = await authenticated_client.delete(
        f"/api/shares/{link_id}", headers=correct_header
    )
    assert revoked.status_code == 200, revoked.text


@pytest.mark.asyncio
async def test_invitation_issue_and_revoke_bind_explicit_target_scope(
    authenticated_client: AsyncClient, test_user
):
    first = await authenticated_client.post(
        "/api/workspaces", json={"name": "Invitation Scope First"}
    )
    second = await authenticated_client.post(
        "/api/workspaces", json={"name": "Invitation Scope Second"}
    )
    assert first.status_code == second.status_code == 200
    first_id = first.json()["workspace"]["id"]
    wrong = {"X-Integral-Scope": f"ws:{second.json()['workspace']['id']}"}
    correct = {"X-Integral-Scope": f"ws:{first_id}"}
    track = await authenticated_client.post(
        "/api/tracks",
        json={"title": "Invitation Scope Track", "workspace_id": first_id},
    )
    assert track.status_code == 200
    track_id = track.json()["track"]["id"]

    denied_workspace_issue = await authenticated_client.post(
        f"/api/workspaces/{first_id}/invitations",
        headers=wrong,
        json={"email": "scope-workspace-invite@example.com", "role": "member"},
    )
    denied_resource_issue = await authenticated_client.post(
        f"/api/tracks/{track_id}/invitations",
        headers=wrong,
        json={"email": "scope-track-invite@example.com", "role": "viewer"},
    )
    assert denied_workspace_issue.status_code == 403
    assert denied_resource_issue.status_code == 403

    workspace_issue = await authenticated_client.post(
        f"/api/workspaces/{first_id}/invitations",
        headers=correct,
        json={"email": "scope-workspace-invite@example.com", "role": "member"},
    )
    resource_issue = await authenticated_client.post(
        f"/api/tracks/{track_id}/invitations",
        headers=correct,
        json={"email": "scope-track-invite@example.com", "role": "viewer"},
    )
    assert workspace_issue.status_code == resource_issue.status_code == 200
    workspace_invitation_id = workspace_issue.json()["invitation"]["id"]
    resource_invitation_id = resource_issue.json()["invitation"]["id"]

    for path in (
        f"/api/workspaces/{first_id}/invitations/{workspace_invitation_id}",
        f"/api/invitations/{resource_invitation_id}",
    ):
        denied = await authenticated_client.delete(path, headers=wrong)
        assert denied.status_code == 403, (path, denied.text)
        revoked = await authenticated_client.delete(path, headers=correct)
        assert revoked.status_code == 200, (path, revoked.text)


@pytest.mark.asyncio
async def test_attachment_reads_and_effects_bind_explicit_workspace_scope(
    authenticated_client: AsyncClient, monkeypatch
):
    """Attachment transports must not bypass the selected Workspace boundary."""
    from app.config import settings

    monkeypatch.setattr(settings, "CHUNKED_UPLOAD_ENABLED", True)
    target = await authenticated_client.post(
        "/api/workspaces", json={"name": "Attachment Scope Target"}
    )
    other = await authenticated_client.post(
        "/api/workspaces", json={"name": "Attachment Scope Other"}
    )
    assert target.status_code == other.status_code == 200
    target_id = target.json()["workspace"]["id"]
    target_headers = {"X-Integral-Scope": f"ws:{target_id}"}
    wrong_headers = {"X-Integral-Scope": f"ws:{other.json()['workspace']['id']}"}
    track = await authenticated_client.post(
        "/api/tracks",
        headers=target_headers,
        json={"title": "Attachment Scope Track", "workspace_id": target_id},
    )
    assert track.status_code == 200, track.text
    entry = await authenticated_client.post(
        "/api/entries",
        headers=target_headers,
        json={
            "title": "Attachment Scope Entry",
            "track_id": track.json()["track"]["id"],
        },
    )
    assert entry.status_code == 200, entry.text
    entry_id = entry.json()["entry"]["id"]
    entry_url = f"/api/entries/{entry_id}/attachments"

    denied_upload = await authenticated_client.post(
        entry_url,
        headers=wrong_headers,
        files={"file": ("scope.txt", b"must not persist", "text/plain")},
    )
    denied_batch = await authenticated_client.post(
        f"{entry_url}/batch",
        headers=wrong_headers,
        files={"files": ("scope-batch.txt", b"must not persist", "text/plain")},
    )
    denied_url = await authenticated_client.post(
        f"{entry_url}/url",
        headers=wrong_headers,
        json={"url": "https://example.com/scope"},
    )
    denied_session = await authenticated_client.post(
        f"/api/entries/{entry_id}/uploads",
        headers=wrong_headers,
        json={"filename": "scope-chunk.txt", "total_bytes": 4},
    )
    denied_creates = (denied_upload, denied_batch, denied_url, denied_session)
    assert all(response.status_code == 403 for response in denied_creates), [
        (response.status_code, response.text) for response in denied_creates
    ]

    uploaded = await authenticated_client.post(
        entry_url,
        headers=target_headers,
        files={"file": ("allowed.txt", b"allowed content", "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text
    attachment_id = uploaded.json()["attachment"]["id"]
    denied_reads = (
        await authenticated_client.get(entry_url, headers=wrong_headers),
        await authenticated_client.get(
            f"/api/attachments/{attachment_id}", headers=wrong_headers
        ),
        await authenticated_client.get(
            f"/api/attachments/{attachment_id}/download", headers=wrong_headers
        ),
    )
    denied_effects = (
        await authenticated_client.post(
            f"/api/attachments/{attachment_id}/reprocess", headers=wrong_headers
        ),
        await authenticated_client.delete(
            f"/api/attachments/{attachment_id}", headers=wrong_headers
        ),
    )
    assert all(
        response.status_code == 403 for response in denied_reads + denied_effects
    )
    still_listed = await authenticated_client.get(entry_url, headers=target_headers)
    assert still_listed.status_code == 200, still_listed.text
    assert attachment_id in {item["id"] for item in still_listed.json()["attachments"]}
    removed = await authenticated_client.delete(
        f"/api/attachments/{attachment_id}", headers=target_headers
    )
    assert removed.status_code == 200, removed.text

    started = await authenticated_client.post(
        f"/api/entries/{entry_id}/uploads",
        headers=target_headers,
        json={"filename": "chunk.txt", "total_bytes": 4},
    )
    assert started.status_code == 200, started.text
    session_id = started.json()["session"]["id"]
    denied_session_read = await authenticated_client.get(
        f"/api/uploads/{session_id}", headers=wrong_headers
    )
    denied_chunk = await authenticated_client.put(
        f"/api/uploads/{session_id}/chunks/0",
        headers=wrong_headers,
        content=b"data",
    )
    assert denied_session_read.status_code == denied_chunk.status_code == 403
    appended = await authenticated_client.put(
        f"/api/uploads/{session_id}/chunks/0",
        headers=target_headers,
        content=b"data",
    )
    assert appended.status_code == 200, appended.text
    denied_complete = await authenticated_client.post(
        f"/api/uploads/{session_id}/complete", headers=wrong_headers
    )
    denied_cancel = await authenticated_client.delete(
        f"/api/uploads/{session_id}", headers=wrong_headers
    )
    assert denied_complete.status_code == denied_cancel.status_code == 403
    cancelled = await authenticated_client.delete(
        f"/api/uploads/{session_id}", headers=target_headers
    )
    assert cancelled.status_code == 200, cancelled.text


@pytest.mark.asyncio
async def test_operational_model_effects_bind_explicit_workspace_scope(
    authenticated_client: AsyncClient,
):
    target = await authenticated_client.post(
        "/api/workspaces", json={"name": "Model Scope Target"}
    )
    other = await authenticated_client.post(
        "/api/workspaces", json={"name": "Model Scope Other"}
    )
    assert target.status_code == other.status_code == 200
    target_id = target.json()["workspace"]["id"]
    target_headers = {"X-Integral-Scope": f"ws:{target_id}"}
    wrong_headers = {"X-Integral-Scope": f"ws:{other.json()['workspace']['id']}"}
    track = await authenticated_client.post(
        "/api/tracks",
        headers=target_headers,
        json={"title": "Model Scope Track", "workspace_id": target_id},
    )
    app = await authenticated_client.post(
        "/api/apps",
        headers=target_headers,
        json={"name": "Model Scope App", "workspace_id": target_id},
    )
    assert track.status_code == app.status_code == 200
    track_id = track.json()["track"]["id"]
    app_id = app.json()["app"]["id"]

    denied_type = await authenticated_client.post(
        f"/api/tracks/{track_id}/operational-model/entry-types",
        headers=wrong_headers,
        json={"name": "Must Not Be Added"},
    )
    assert denied_type.status_code == 403, denied_type.text
    allowed_type = await authenticated_client.post(
        f"/api/tracks/{track_id}/operational-model/entry-types",
        headers=target_headers,
        json={"name": "Scoped Entry Type"},
    )
    assert allowed_type.status_code == 200, allowed_type.text

    denied_template = await authenticated_client.post(
        f"/api/operational-models/from-app/{app_id}",
        headers=wrong_headers,
        json={"name": "Must Not Be Derived", "workspace_id": target_id},
    )
    assert denied_template.status_code == 403, denied_template.text

    denied_publish = await authenticated_client.post(
        "/api/operational-models",
        headers=wrong_headers,
        json={
            "name": "Must Not Be Published",
            "workspace_id": target_id,
            "manifest": {
                "operational_model_schema_version": 2,
                "scope": "track",
                "package": {"name": "scope-test", "version": "1.0.0"},
                "track": {"entry_types": [], "views": [], "taxonomy": {}},
            },
        },
    )
    assert denied_publish.status_code == 400, denied_publish.text
