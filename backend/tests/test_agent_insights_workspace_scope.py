"""B-AGENT-03 — workspace scope boundary regression on agent insight helpers.

Verifies the two-layer access model:
    layer 1: active workspace (workspace_id arg) gates the track universe
    layer 2: existing per-user cascade (resolve_role + EXCLUDED_FROM)
             runs on top, unchanged

The fixtures seed two workspaces both visible to a single user; each
test asserts that supplying workspace_id=W produces results disjoint
from workspace_id=W', and that workspace_id=None preserves the union
behaviour for legacy callers.
"""

from __future__ import annotations

import pytest

from app.services.agent_insights import (
    activity_digest,
    count_entries_grouped,
    query_entries,
)


class _T:
    """Lightweight Track stub. Helpers only read .id, .title, .title_fold,
    .workspace_id via getattr."""

    def __init__(self, id_: str, title: str, workspace_id: str):
        self.id = id_
        self.title = title
        self.title_fold = title.casefold()
        self.workspace_id = workspace_id


class _E:
    """Lightweight Entry stub."""

    def __init__(
        self,
        id_: str,
        title: str,
        track_id: str,
        updated_at: str = "2026-05-20T00:00:00+00:00",
    ):
        self.id = id_
        self.title = title
        self.track_id = track_id
        self.updated_at = updated_at
        self.created_at = updated_at
        self.body = ""
        self.tags = []
        self.type_id = ""
        self.status = "active"
        self.author_id = "u1"
        self.custom_fields = {}


W1 = "n.Workspace.alpha"
W2 = "n.Workspace.beta"

ALL_TRACKS = [
    _T("n.Track.a1", "Alpha-One", W1),
    _T("n.Track.a2", "Alpha-Two", W1),
    _T("n.Track.b1", "Beta-One", W2),
    _T("n.Track.b2", "Beta-Two", W2),
]

ENTRIES_BY_TRACK = {
    "n.Track.a1": [_E("n.Entry.1", "A1-e1", "n.Track.a1")],
    "n.Track.a2": [
        _E("n.Entry.2", "A2-e1", "n.Track.a2"),
        _E("n.Entry.3", "A2-e2", "n.Track.a2"),
    ],
    "n.Track.b1": [_E("n.Entry.4", "B1-e1", "n.Track.b1")],
    "n.Track.b2": [_E("n.Entry.5", "B2-e1", "n.Track.b2")],
}


@pytest.fixture
def patched_permissions(monkeypatch):
    async def fake_tracks(user_id: str):
        return list(ALL_TRACKS)

    async def fake_entries(user_id: str, track_id: str, **_kwargs):
        return list(ENTRIES_BY_TRACK.get(track_id, []))

    monkeypatch.setattr(
        "app.services.permissions.get_user_accessible_tracks", fake_tracks
    )
    monkeypatch.setattr(
        "app.services.permissions.get_user_accessible_entries", fake_entries
    )
    yield


# ---------------------------------------------------------------------------
# activity_digest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activity_digest_workspace_w1_returns_only_w1_tracks(patched_permissions):
    result = await activity_digest(user_id="u1", workspace_id=W1, period="today")
    track_ids = {row["track_id"] for row in result["track_summaries"]}
    assert track_ids == {"n.Track.a1", "n.Track.a2"}
    assert result["workspace_id"] == W1


@pytest.mark.asyncio
async def test_activity_digest_workspace_w2_returns_only_w2_tracks(patched_permissions):
    result = await activity_digest(user_id="u1", workspace_id=W2, period="today")
    track_ids = {row["track_id"] for row in result["track_summaries"]}
    assert track_ids == {"n.Track.b1", "n.Track.b2"}


@pytest.mark.asyncio
async def test_activity_digest_workspace_none_returns_union(patched_permissions):
    """Back-compat: legacy callers passing no workspace_id see the full union."""
    result = await activity_digest(user_id="u1", workspace_id=None, period="today")
    track_ids = {row["track_id"] for row in result["track_summaries"]}
    assert track_ids == {
        "n.Track.a1",
        "n.Track.a2",
        "n.Track.b1",
        "n.Track.b2",
    }
    assert result["workspace_id"] is None


@pytest.mark.asyncio
async def test_activity_digest_scope_track_cross_workspace_returns_empty(
    patched_permissions,
):
    """scope=track + scope_id pointing at a foreign workspace's track →
    after the workspace gate filters it out, the scope_id resolution
    finds no matching track and the summaries are empty."""
    result = await activity_digest(
        user_id="u1",
        workspace_id=W1,
        scope="track",
        scope_id="n.Track.b1",  # belongs to W2
    )
    assert result["track_summaries"] == []


# ---------------------------------------------------------------------------
# query_entries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_entries_workspace_w1_returns_only_w1_entries(patched_permissions):
    result = await query_entries(user_id="u1", workspace_id=W1, limit=100)
    entry_ids = {e["id"] for e in result["entries"]}
    assert entry_ids == {"n.Entry.1", "n.Entry.2", "n.Entry.3"}


@pytest.mark.asyncio
async def test_query_entries_workspace_w2_returns_only_w2_entries(patched_permissions):
    result = await query_entries(user_id="u1", workspace_id=W2, limit=100)
    entry_ids = {e["id"] for e in result["entries"]}
    assert entry_ids == {"n.Entry.4", "n.Entry.5"}


@pytest.mark.asyncio
async def test_query_entries_explicit_cross_workspace_track_id_returns_empty(
    patched_permissions,
):
    """Active workspace = W1, but caller requests an entry in a W2 track."""
    result = await query_entries(
        user_id="u1",
        workspace_id=W1,
        track_id="n.Track.b1",
        limit=100,
    )
    assert result["entries"] == []
    assert result["total"] == 0
    assert result["filters_applied"]["workspace_id"] == W1


@pytest.mark.asyncio
async def test_query_entries_workspace_none_returns_union(patched_permissions):
    result = await query_entries(user_id="u1", workspace_id=None, limit=100)
    entry_ids = {e["id"] for e in result["entries"]}
    assert entry_ids == {
        "n.Entry.1",
        "n.Entry.2",
        "n.Entry.3",
        "n.Entry.4",
        "n.Entry.5",
    }


# ---------------------------------------------------------------------------
# count_entries_grouped (passes workspace_id through to query_entries)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_entries_track_name_resolution_under_workspace_gate(
    patched_permissions,
):
    """Name-resolution path must respect the workspace gate.

    Track NAMES can legitimately collide across workspaces (e.g. an
    "Records" track in two workspaces). Without
    the gate, query_entries would resolve the name to whichever track
    happened to be first in the accessible list, leaking foreign-
    workspace entries when the caller's active scope is the other one.
    With the gate, the foreign-workspace track is filtered out BEFORE
    name resolution runs.
    """
    # Re-seed: add a same-named track to W2 so the name "Alpha-One"
    # is ambiguous across workspaces.
    ambiguous_t = _T("n.Track.b3", "Alpha-One", W2)
    ENTRIES_BY_TRACK["n.Track.b3"] = [_E("n.Entry.6", "B3-e1", "n.Track.b3")]
    ALL_TRACKS.append(ambiguous_t)
    try:
        # Active scope = W1 → name match resolves to the W1 track.
        r_w1 = await query_entries(
            user_id="u1",
            workspace_id=W1,
            track_id="Alpha-One",
            limit=100,
        )
        ids_w1 = {e["id"] for e in r_w1["entries"]}
        assert ids_w1 == {"n.Entry.1"}, f"expected W1 alpha-one, got {ids_w1}"

        # Active scope = W2 → name match resolves to the W2 track.
        r_w2 = await query_entries(
            user_id="u1",
            workspace_id=W2,
            track_id="Alpha-One",
            limit=100,
        )
        ids_w2 = {e["id"] for e in r_w2["entries"]}
        assert ids_w2 == {"n.Entry.6"}, f"expected W2 alpha-one, got {ids_w2}"
    finally:
        ALL_TRACKS.pop()
        ENTRIES_BY_TRACK.pop("n.Track.b3", None)


@pytest.mark.asyncio
async def test_count_entries_grouped_workspace_propagates(patched_permissions):
    result = await count_entries_grouped(
        user_id="u1",
        group_by="track",
        workspace_id=W1,
    )
    # count_entries_grouped returns {"group_by", "total_matched", "groups",
    # "filters_applied"} where groups is a list of {"key", "label", "count"}.
    # Three entries across two W1 tracks (a1=1, a2=2).
    assert result["total_matched"] == 3
    group_keys = {g["key"] for g in result["groups"]}
    assert group_keys == {"n.Track.a1", "n.Track.a2"}
    total = sum(g["count"] for g in result["groups"])
    assert total == 3


# ---------------------------------------------------------------------------
# query_entries — keyword-term text match (mode-adaptive retrieval)
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_permissions_kw(monkeypatch):
    """Single-track fixture whose entries have distinct title/body text."""
    tracks = [_T("n.Track.kw", "Wiki", W1)]
    e_acme = _E("n.Entry.acme", "Acme Inc company page", "n.Track.kw")
    e_acme.body = "Reference notes about the Acme customer relationship"
    e_globex = _E("n.Entry.globex", "Globex roadmap", "n.Track.kw")
    e_globex.body = "Quarterly planning"
    entries = {"n.Track.kw": [e_acme, e_globex]}

    async def fake_tracks(user_id):
        return list(tracks)

    async def fake_entries(user_id, track_id, **_kwargs):
        return list(entries.get(track_id, []))

    monkeypatch.setattr(
        "app.services.permissions.get_user_accessible_tracks", fake_tracks
    )
    monkeypatch.setattr(
        "app.services.permissions.get_user_accessible_entries", fake_entries
    )
    yield


@pytest.mark.asyncio
async def test_query_entries_multiword_keyword_match(patched_permissions_kw):
    """A multi-word query keyword-matches the Acme wiki page by term overlap.

    Pre-tokenizer the literal full string "Acme Inc company" never appeared
    contiguously and this returned EMPTY; now it matches on any term.
    """
    result = await query_entries(
        user_id="u1", workspace_id=W1, query="Acme Inc company", limit=100
    )
    ids = {e["id"] for e in result["entries"]}
    assert ids == {"n.Entry.acme"}


@pytest.mark.asyncio
async def test_query_entries_boolean_or_blob_match(patched_permissions_kw):
    """An 'A OR B' blob splits into terms and matches entries hitting ANY."""
    result = await query_entries(
        user_id="u1", workspace_id=W1, query="Acme OR Globex", limit=100
    )
    ids = {e["id"] for e in result["entries"]}
    assert ids == {"n.Entry.acme", "n.Entry.globex"}


@pytest.mark.asyncio
async def test_query_entries_empty_query_returns_all(patched_permissions_kw):
    """No query / all-stopword query → no text filter → every entry."""
    result = await query_entries(user_id="u1", workspace_id=W1, query="   ", limit=100)
    ids = {e["id"] for e in result["entries"]}
    assert ids == {"n.Entry.acme", "n.Entry.globex"}


@pytest.mark.asyncio
async def test_query_entries_keyword_no_match_returns_empty(patched_permissions_kw):
    """A query with no matching terms still filters everything out."""
    result = await query_entries(
        user_id="u1", workspace_id=W1, query="initech partnership", limit=100
    )
    assert result["entries"] == []
