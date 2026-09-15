"""Tests for the @-mention candidate endpoint + resolver scope gate.

Verifies that mention surfacing is constrained to users with effective
visibility on the requested track:

  * picker endpoint surfaces owner + direct + app-inherited collaborators
    on private/workspace tracks, minus EXCLUDED_FROM (deny overrides
    inherited paths only — direct/owner survive);
  * the requester is never returned (no point @-mentioning yourself);
  * public-vis tracks fall through to global user search;
  * resolver-side gate in services.mentions.resolve_mentions drops
    out-of-scope users silently so a raw user-id token in comment text
    cannot bypass the picker.
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestMentionCandidatesEndpoint:
    """``GET /api/tracks/{track_id}/mention-candidates``."""

    async def test_returns_owner_excluded_when_requester_is_owner(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Owner calling the endpoint never sees themselves in the candidate list."""
        tr = await authenticated_client.post(
            "/api/tracks",
            json={"title": "Solo Track", "visibility": "private"},
        )
        track_id = tr.json()["track"]["id"]

        r = await authenticated_client.get(f"/api/tracks/{track_id}/mention-candidates")
        assert r.status_code == 200, r.text
        body = r.json()
        user_ids = {u["id"] for u in body["users"]}
        assert test_user.id not in user_ids
        assert body["visibility_grant"] is None

    async def test_surfaces_direct_collaborator(
        self, authenticated_client: AsyncClient, test_user, test_user2
    ):
        """A directly-added collaborator appears in the picker."""
        tr = await authenticated_client.post(
            "/api/tracks",
            json={"title": "Direct Track", "visibility": "private"},
        )
        track_id = tr.json()["track"]["id"]
        await authenticated_client.post(
            f"/api/tracks/{track_id}/collaborators",
            json={"collaborator_user_id": test_user2.id, "role": "editor"},
        )

        r = await authenticated_client.get(f"/api/tracks/{track_id}/mention-candidates")
        assert r.status_code == 200, r.text
        user_ids = {u["id"] for u in r.json()["users"]}
        assert test_user2.id in user_ids

    async def test_surfaces_app_inherited_collaborator(
        self, authenticated_client: AsyncClient, test_user, test_user2
    ):
        """App-inherited collaborators appear in the picker via cascade."""
        sp = await authenticated_client.post(
            "/api/apps", json={"name": "Cascade App", "visibility": "private"}
        )
        app_id = sp.json()["app"]["id"]
        tr = await authenticated_client.post(
            "/api/tracks",
            json={"title": "Cascade Track", "app_id": app_id},
        )
        track_id = tr.json()["track"]["id"]
        await authenticated_client.post(
            f"/api/apps/{app_id}/collaborators",
            json={"collaborator_user_id": test_user2.id, "role": "viewer"},
        )

        r = await authenticated_client.get(f"/api/tracks/{track_id}/mention-candidates")
        assert r.status_code == 200, r.text
        user_ids = {u["id"] for u in r.json()["users"]}
        assert test_user2.id in user_ids

    async def test_exclusion_filters_inherited(
        self, authenticated_client: AsyncClient, test_user, test_user2
    ):
        """EXCLUDED_FROM removes the user from the picker when they are inherited only."""
        sp = await authenticated_client.post(
            "/api/apps", json={"name": "Excl App", "visibility": "private"}
        )
        app_id = sp.json()["app"]["id"]
        tr = await authenticated_client.post(
            "/api/tracks",
            json={"title": "Excl Track", "app_id": app_id},
        )
        track_id = tr.json()["track"]["id"]
        await authenticated_client.post(
            f"/api/apps/{app_id}/collaborators",
            json={"collaborator_user_id": test_user2.id, "role": "viewer"},
        )
        # Pre-condition: inherited member is visible.
        r1 = await authenticated_client.get(
            f"/api/tracks/{track_id}/mention-candidates"
        )
        assert test_user2.id in {u["id"] for u in r1.json()["users"]}

        # Exclude → user disappears from candidates.
        await authenticated_client.post(
            f"/api/tracks/{track_id}/exclusions",
            json={"user_id_to_exclude": test_user2.id},
        )
        r2 = await authenticated_client.get(
            f"/api/tracks/{track_id}/mention-candidates"
        )
        assert test_user2.id not in {u["id"] for u in r2.json()["users"]}

    async def test_exclusion_does_not_filter_direct(
        self, authenticated_client: AsyncClient, test_user, test_user2
    ):
        """Direct COLLABORATES_ON beats any EXCLUDED_FROM edge in the picker."""
        tr = await authenticated_client.post(
            "/api/tracks",
            json={"title": "Direct Beats Excl", "visibility": "private"},
        )
        track_id = tr.json()["track"]["id"]
        await authenticated_client.post(
            f"/api/tracks/{track_id}/collaborators",
            json={"collaborator_user_id": test_user2.id, "role": "editor"},
        )
        await authenticated_client.post(
            f"/api/tracks/{track_id}/exclusions",
            json={"user_id_to_exclude": test_user2.id},
        )
        r = await authenticated_client.get(f"/api/tracks/{track_id}/mention-candidates")
        assert test_user2.id in {u["id"] for u in r.json()["users"]}

    async def test_q_filter_matches_display_name(
        self, authenticated_client: AsyncClient, test_user, test_user2
    ):
        """``q`` filters case-insensitively against display_name."""
        tr = await authenticated_client.post(
            "/api/tracks",
            json={"title": "Q Track", "visibility": "private"},
        )
        track_id = tr.json()["track"]["id"]
        await authenticated_client.post(
            f"/api/tracks/{track_id}/collaborators",
            json={"collaborator_user_id": test_user2.id, "role": "editor"},
        )
        # test_user2.display_name = "Test User 2" — query "User 2" matches,
        # query "Nobody" does not.
        r_match = await authenticated_client.get(
            f"/api/tracks/{track_id}/mention-candidates",
            params={"q": "user 2"},
        )
        user_ids = {u["id"] for u in r_match.json()["users"]}
        assert test_user2.id in user_ids

        r_miss = await authenticated_client.get(
            f"/api/tracks/{track_id}/mention-candidates",
            params={"q": "nobody-matches-this-zzzzz"},
        )
        assert test_user2.id not in {u["id"] for u in r_miss.json()["users"]}

    async def test_limit_clamps_results(
        self, authenticated_client: AsyncClient, test_user, test_user2
    ):
        """``limit`` caps the returned candidate count."""
        tr = await authenticated_client.post(
            "/api/tracks",
            json={"title": "Limit Track", "visibility": "private"},
        )
        track_id = tr.json()["track"]["id"]
        await authenticated_client.post(
            f"/api/tracks/{track_id}/collaborators",
            json={"collaborator_user_id": test_user2.id, "role": "editor"},
        )
        r = await authenticated_client.get(
            f"/api/tracks/{track_id}/mention-candidates",
            params={"limit": 1},
        )
        assert len(r.json()["users"]) <= 1

    async def test_public_visibility_falls_through_to_global_search(
        self, authenticated_client: AsyncClient, test_user, test_user2
    ):
        """``visibility=public`` flips the picker into global-search mode.

        On a public track even non-collaborators can be @-mentioned because
        anyone authenticated can read the track. The response carries
        ``visibility_grant = "public"`` so the frontend can render an
        appropriate hint.
        """
        tr = await authenticated_client.post(
            "/api/tracks",
            json={"title": "Public Track", "visibility": "public"},
        )
        track_id = tr.json()["track"]["id"]
        # test_user2 has NO collaborator edge on this track — yet should be
        # reachable via global search on the public-vis branch.
        r = await authenticated_client.get(
            f"/api/tracks/{track_id}/mention-candidates",
            params={"q": "user 2"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["visibility_grant"] == "public"
        # On public-vis the global search reaches test_user2 even without
        # an explicit edge.
        assert test_user2.id in {u["id"] for u in body["users"]}

    async def test_unknown_track_returns_404(
        self, authenticated_client: AsyncClient, test_user
    ):
        r = await authenticated_client.get(
            "/api/tracks/n.Track.does-not-exist/mention-candidates"
        )
        assert r.status_code in (403, 404), r.text


@pytest.mark.asyncio
class TestResolveMentionsScopeGate:
    """``services.mentions.resolve_mentions(track_id=...)`` gate."""

    async def test_track_id_drops_out_of_scope_user(
        self, authenticated_client: AsyncClient, test_user, test_user2
    ):
        """A user with no track role is silently dropped from the resolution."""
        from app.services.mentions import resolve_mentions

        tr = await authenticated_client.post(
            "/api/tracks",
            json={"title": "Gate Track", "visibility": "private"},
        )
        track_id = tr.json()["track"]["id"]

        token = (test_user2.display_name or "").strip().replace(" ", "_")
        assert token, "test_user2 needs a display_name"
        text = f"hello @{token}"

        # No collaborator edge — out-of-scope mention drops.
        resolved = await resolve_mentions(text, track_id=track_id)
        assert test_user2.id not in {u.id for u in resolved}

        # Add collaborator — now the same text resolves.
        await authenticated_client.post(
            f"/api/tracks/{track_id}/collaborators",
            json={"collaborator_user_id": test_user2.id, "role": "editor"},
        )
        resolved2 = await resolve_mentions(text, track_id=track_id)
        assert test_user2.id in {u.id for u in resolved2}

    async def test_no_track_id_resolves_globally(
        self, authenticated_client: AsyncClient, test_user, test_user2
    ):
        """Without ``track_id`` the resolver matches the pre-scoping behaviour."""
        from app.services.mentions import resolve_mentions

        token = (test_user2.display_name or "").strip().replace(" ", "_")
        text = f"hi @{token}"
        resolved = await resolve_mentions(text)
        assert test_user2.id in {u.id for u in resolved}

    async def test_public_track_bypasses_gate(
        self, authenticated_client: AsyncClient, test_user, test_user2
    ):
        """Public-vis tracks bypass the role gate (anyone can read → anyone is mentionable)."""
        from app.services.mentions import resolve_mentions

        tr = await authenticated_client.post(
            "/api/tracks",
            json={"title": "Public Gate Track", "visibility": "public"},
        )
        track_id = tr.json()["track"]["id"]

        token = (test_user2.display_name or "").strip().replace(" ", "_")
        text = f"yo @{token}"
        resolved = await resolve_mentions(text, track_id=track_id)
        assert test_user2.id in {u.id for u in resolved}
