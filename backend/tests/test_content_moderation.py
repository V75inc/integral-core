"""Tests for the profanity filter (content_moderation service + write-path wiring).

June 23 QA feature request — block profane language in comments, entry
creation, and entry editing on public-facing paths (authenticated + public
share-link).
"""

import pytest

from app.api.errors import BadRequestError
from app.services.content_moderation import contains_profanity, validate_no_profanity

PROFANE = "this is fucking terrible"
CLEAN = "this is a perfectly reasonable sentence"


# ---------------------------------------------------------------------------
# Unit tests — content_moderation service
# ---------------------------------------------------------------------------


def test_contains_profanity_detects_flagged_language():
    """Flagged words in text are detected."""
    assert contains_profanity(PROFANE) is True


def test_contains_profanity_allows_clean_text():
    """Ordinary text is not flagged."""
    assert contains_profanity(CLEAN) is False


def test_contains_profanity_empty_text_is_clean():
    """Empty/None text is treated as clean, not an error."""
    assert contains_profanity("") is False
    assert contains_profanity(None) is False  # type: ignore[arg-type]


def test_validate_no_profanity_raises_on_match():
    """validate_no_profanity raises BadRequestError on flagged text."""
    with pytest.raises(BadRequestError):
        validate_no_profanity(PROFANE, "title")


def test_validate_no_profanity_noop_on_clean_text():
    """validate_no_profanity is a no-op on clean text."""
    validate_no_profanity(CLEAN, "title")  # must not raise


def test_fails_open_when_library_missing(monkeypatch):
    """When ``_filter`` is explicitly disabled, moderation fails OPEN — it
    must never block writes. Regression guard for the smoke-test blocker where
    a missing dependency 500'd every entry/comment create."""
    from app.services import content_moderation as cm

    cm._filter.cache_clear()
    monkeypatch.setattr(cm, "_filter", lambda: None)

    # Even flagged text passes when the filter is unavailable.
    assert cm.contains_profanity(PROFANE) is False
    cm.validate_no_profanity(PROFANE, "title")  # must NOT raise
    # monkeypatch restores the real _filter (lru_cache already cleared above).


def test_fallback_filter_detects_common_conjugations():
    """Built-in fallback catches conjugations the divergent 1.x package misses."""
    from app.services.content_moderation import _FallbackFilter

    flt = _FallbackFilter()
    assert flt.contains_profanity(PROFANE) is True
    assert flt.contains_profanity(CLEAN) is False


# ---------------------------------------------------------------------------
# Integration — authenticated write paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_entry_blocks_profane_title(authenticated_client, test_user):
    """POST /entries rejects a profane title with 400."""
    track_resp = await authenticated_client.post(
        "/api/tracks", json={"title": "Moderation Track", "visibility": "private"}
    )
    track_id = track_resp.json()["track"]["id"]

    resp = await authenticated_client.post(
        "/api/entries",
        json={"track_id": track_id, "title": PROFANE, "body": "fine"},
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_create_entry_blocks_profane_body(authenticated_client, test_user):
    """POST /entries rejects a profane body with 400."""
    track_resp = await authenticated_client.post(
        "/api/tracks", json={"title": "Moderation Track 2", "visibility": "private"}
    )
    track_id = track_resp.json()["track"]["id"]

    resp = await authenticated_client.post(
        "/api/entries",
        json={"track_id": track_id, "title": "fine title", "body": PROFANE},
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_update_entry_blocks_profane_title(authenticated_client, test_user):
    """PUT /entries/{id} rejects a profane title edit with 400."""
    track_resp = await authenticated_client.post(
        "/api/tracks", json={"title": "Moderation Track 3", "visibility": "private"}
    )
    track_id = track_resp.json()["track"]["id"]
    entry_resp = await authenticated_client.post(
        "/api/entries",
        json={"track_id": track_id, "title": "clean title", "body": "clean body"},
    )
    entry_id = entry_resp.json()["entry"]["id"]

    resp = await authenticated_client.put(
        f"/api/entries/{entry_id}", json={"title": PROFANE}
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_create_comment_blocks_profanity(authenticated_client, test_user):
    """POST comments rejects profanity with 400; clean text still works."""
    track_resp = await authenticated_client.post(
        "/api/tracks", json={"title": "Moderation Track 4", "visibility": "private"}
    )
    track_id = track_resp.json()["track"]["id"]
    entry_resp = await authenticated_client.post(
        "/api/entries",
        json={"track_id": track_id, "title": "clean title", "body": "clean body"},
    )
    entry_id = entry_resp.json()["entry"]["id"]

    resp = await authenticated_client.post(
        f"/api/entries/{entry_id}/comments", json={"text": PROFANE}
    )
    assert resp.status_code == 400, resp.text

    # Clean text still works.
    resp = await authenticated_client.post(
        f"/api/entries/{entry_id}/comments", json={"text": CLEAN}
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_update_comment_blocks_profanity(authenticated_client, test_user):
    """PUT /comments/{id} rejects a profane edit with 400."""
    track_resp = await authenticated_client.post(
        "/api/tracks", json={"title": "Moderation Track 5", "visibility": "private"}
    )
    track_id = track_resp.json()["track"]["id"]
    entry_resp = await authenticated_client.post(
        "/api/entries",
        json={"track_id": track_id, "title": "clean title", "body": "clean body"},
    )
    entry_id = entry_resp.json()["entry"]["id"]
    comment_resp = await authenticated_client.post(
        f"/api/entries/{entry_id}/comments", json={"text": CLEAN}
    )
    comment_id = comment_resp.json()["comment"]["id"]

    resp = await authenticated_client.put(
        f"/api/comments/{comment_id}", json={"text": PROFANE}
    )
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# Integration — public share-link write paths (auth=False)
# ---------------------------------------------------------------------------


@pytest.fixture
async def shared_track_setup(test_user):
    """Track + entry type + public-share-eligible workspace for public-path tests."""
    from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
    from app.models.nodes import EntryType, Track, Workspace
    from app.utils.time import utc_now_iso

    now = utc_now_iso()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Test Moderation WS",
        name_fold="test moderation ws",
        created_at=now,
        updated_at=now,
    )
    await test_user.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)

    track = await Track.create(
        title="Public Moderation Project",
        owner_id=test_user.id,
        workspace_id=ws.id,
        visibility="private",
    )
    await test_user.connect(track, edge=COLLABORATES_ON, role="owner", added_at=now)

    et = await EntryType.create(
        name="Task Item",
        name_fold="task item",
        track_id=track.id,
        form_schema={"fields": []},
    )

    from app.services.app_graph import ensure_track_attached_operational_model

    await ensure_track_attached_operational_model(track)

    return track, et, ws


async def _enable_public_share(authenticated_client, track_id):
    body = {
        "enabled": True,
        "public_permissions": {
            "read_entries": True,
            "create_entries": True,
            "update_entries": True,
            "read_comments": True,
            "create_comments": True,
        },
    }
    res = await authenticated_client.post(
        f"/api/tracks/{track_id}/public-share", json=body
    )
    assert res.status_code == 200, res.text
    return res.json()["token"]


@pytest.mark.asyncio
async def test_public_create_entry_blocks_profanity(
    authenticated_client, test_user, shared_track_setup
):
    """Unauthenticated public-share create/update entry + create comment all
    reject profanity with 400."""
    track, et, ws = shared_track_setup
    token = await _enable_public_share(authenticated_client, track.id)

    auth_header = authenticated_client.headers.pop("Authorization", None)
    try:
        resp = await authenticated_client.post(
            f"/api/public-share/track/{token}/entries",
            json={"title": PROFANE, "type_id": et.id, "body": "fine"},
        )
        assert resp.status_code == 400, resp.text

        resp = await authenticated_client.post(
            f"/api/public-share/track/{token}/entries",
            json={"title": CLEAN, "type_id": et.id, "body": "fine"},
        )
        assert resp.status_code == 200, resp.text
        entry_id = resp.json()["entry"]["id"]
    finally:
        if auth_header:
            authenticated_client.headers["Authorization"] = auth_header

    # ---- update, still unauthenticated ----
    auth_header = authenticated_client.headers.pop("Authorization", None)
    try:
        resp = await authenticated_client.patch(
            f"/api/public-share/track/{token}/entries/{entry_id}",
            json={"title": PROFANE},
        )
        assert resp.status_code == 400, resp.text

        resp = await authenticated_client.post(
            f"/api/public-share/track/{token}/entries/{entry_id}/comments",
            json={"text": PROFANE},
        )
        assert resp.status_code == 400, resp.text
    finally:
        if auth_header:
            authenticated_client.headers["Authorization"] = auth_header
