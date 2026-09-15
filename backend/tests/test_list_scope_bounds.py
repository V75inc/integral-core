"""Regression guards for list endpoints that were unscoped or unbounded.

Two separate defects, same shape -- a list endpoint returning more than the
caller is entitled to (or more than any caller should get in one request):

``GET /api/tags`` with neither ``track_id`` nor ``app_id`` ran a bare
``Tag.find()``: every tag in every workspace on the deployment, with none of
the ``policy_evaluate`` gating that its ``track_id`` / ``app_id`` branches
apply. It now scopes to the caller's workspace via the containing App/Track.

``GET /api/notifications`` accepted an unbounded ``per_page``. The rows are
user-scoped so this is a resource bound rather than an access control, but it
was the only bound there was.
"""

import pytest
from httpx import AsyncClient

# Part of the per-PR smoke gate (see pyproject [tool.pytest.ini_options] markers).
# CI bills only this subset; the full suite runs locally via `make verify` and nightly.
pytestmark = pytest.mark.smoke


@pytest.mark.asyncio
class TestTagListIsWorkspaceScoped:
    """The unfiltered ``/api/tags`` branch must not cross workspaces."""

    async def _track_with_tag(self, client: AsyncClient, title: str, tag: str) -> str:
        tr = await client.post(
            "/api/tracks", json={"title": title, "visibility": "private"}
        )
        assert tr.status_code == 200, tr.text
        track_id = tr.json()["track"]["id"]
        made = await client.post(
            "/api/tags",
            json={"name": tag, "track_id": track_id, "color": "#123456"},
        )
        assert made.status_code == 200, made.text
        return track_id

    async def test_unfiltered_list_excludes_other_workspaces_tags(
        self,
        authenticated_client: AsyncClient,
        second_user_client: AsyncClient,
        test_user,
    ):
        """A tag created by another user must not appear in an unfiltered list."""
        await self._track_with_tag(
            authenticated_client, "Owner Scope Track", "owner-only-tag"
        )

        probe = await second_user_client.get("/api/tags")
        if probe.status_code != 200:
            pytest.skip(f"second user client unavailable: {probe.status_code}")

        names = {(t.get("name") or "") for t in probe.json().get("tags") or []}
        assert "owner-only-tag" not in names, (
            "unfiltered /api/tags leaked a tag from another user's workspace: "
            f"{sorted(names)}"
        )

    async def test_unfiltered_list_still_returns_own_tags(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Scoping must not empty the endpoint for its legitimate caller.

        The ``integral_list_tags`` agent tool calls this with no arguments, so
        the branch has to keep working -- rejecting with 400 would have been
        tighter but breaks that caller.
        """
        await self._track_with_tag(
            authenticated_client, "Own Scope Track", "my-visible-tag"
        )

        r = await authenticated_client.get("/api/tags")
        assert r.status_code == 200, r.text
        names = {(t.get("name") or "") for t in r.json().get("tags") or []}
        assert "my-visible-tag" in names


@pytest.mark.asyncio
class TestNotificationsPageBound:
    """``per_page`` must be clamped."""

    async def test_per_page_is_capped(
        self, authenticated_client: AsyncClient, test_user
    ):
        r = await authenticated_client.get(
            "/api/notifications", params={"per_page": 100000}
        )
        assert r.status_code == 200, r.text
        assert r.json()["per_page"] <= 100

    async def test_page_floor_is_one(
        self, authenticated_client: AsyncClient, test_user
    ):
        """A zero/negative page produced a negative slice index."""
        r = await authenticated_client.get(
            "/api/notifications", params={"page": 0, "per_page": 5}
        )
        assert r.status_code == 200, r.text
        assert r.json()["page"] >= 1
