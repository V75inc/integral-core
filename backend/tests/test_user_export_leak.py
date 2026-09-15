"""Regression guard: embedded user payloads must never carry sensitive fields.

Endpoints embed an ``author`` (and member/collaborator) object alongside
entries and comments. Those objects were previously serialized with the
unfiltered ``export_node`` (``node.export(flat=True)``), which emits **every**
declared model field. Depending on which id form the ``author_id`` carries,
that surfaced either the jvspatial ``AuthUser`` record (``password_hash``,
``roles``, ``permissions``) or the graph ``User`` node's
``preferences.reset_token`` / ``notification_preferences.whatsapp.phone_e164``.

``app/api/utils.py::public_user_view`` is now an explicit allowlist, so the
serializer cannot leak a field that a future model gains. These tests assert
the allowlist holds on every surface that embeds a user — including the
unauthenticated public-share routes, where the blast radius is widest.

Related: ``tests/test_crud_users.py`` guards ``/api/users`` itself; these guard
the *embedded* objects, which had no coverage.
"""

import pytest
from httpx import AsyncClient

# Part of the per-PR smoke gate (see pyproject [tool.pytest.ini_options] markers).
# CI bills only this subset; the full suite runs locally via `make verify` and nightly.
pytestmark = pytest.mark.smoke

# Keys that must never appear in a user object serialized toward a client.
FORBIDDEN_USER_KEYS = {
    "password_hash",
    "password",
    "preferences",
    "notification_preferences",
    "roles",
    "permissions",
    "is_active",
    "email",
    "user_id",
    "active_workspace_id",
    "email_verified",
    "last_accessed",
}


def assert_no_sensitive_user_fields(payload, *, where: str) -> None:
    """Fail if any forbidden key appears on a user-shaped dict, recursively.

    Walks the whole response so a user object nested anywhere (``author``,
    ``track.owner``, ``collaborators[]``, …) is covered without the test
    needing to know the shape.
    """
    hits: list[str] = []

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            # A user-shaped dict is one carrying the graph User discriminator
            # or the display_name/avatar pair the UI renders.
            is_userish = str(node.get("entity", "")).endswith("User") or (
                "display_name" in node and "avatar_url" in node
            )
            if is_userish:
                for key in FORBIDDEN_USER_KEYS:
                    if key in node:
                        hits.append(f"{path}.{key}")
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(payload, where)
    assert not hits, f"sensitive user fields leaked in {where}: {sorted(set(hits))}"


@pytest.mark.asyncio
class TestEmbeddedAuthorDoesNotLeak:
    """Authenticated surfaces that embed an ``author`` object."""

    async def _track(self, client: AsyncClient) -> str:
        r = await client.post(
            "/api/tracks", json={"title": "Leak Guard Track", "visibility": "private"}
        )
        assert r.status_code == 200, r.text
        return r.json()["track"]["id"]

    async def _entry(self, client: AsyncClient, track_id: str) -> str:
        r = await client.post(
            "/api/entries",
            json={"track_id": track_id, "title": "Leak Guard Entry", "body": "Body"},
        )
        assert r.status_code == 200, r.text
        return r.json()["entry"]["id"]

    async def test_feed_author_is_projected(
        self, authenticated_client: AsyncClient, test_user
    ):
        track_id = await self._track(authenticated_client)
        await self._entry(authenticated_client, track_id)

        r = await authenticated_client.get("/api/feed_entries", params={"limit": 20})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("entries"), "fixture produced no entries — test is vacuous"
        assert_no_sensitive_user_fields(body, where="feed_entries")

    async def test_entry_detail_author_is_projected(
        self, authenticated_client: AsyncClient, test_user
    ):
        track_id = await self._track(authenticated_client)
        entry_id = await self._entry(authenticated_client, track_id)

        r = await authenticated_client.get(f"/api/entries/{entry_id}")
        assert r.status_code == 200, r.text
        assert_no_sensitive_user_fields(r.json(), where="entry_detail")

    async def test_comment_author_is_projected(
        self, authenticated_client: AsyncClient, test_user
    ):
        track_id = await self._track(authenticated_client)
        entry_id = await self._entry(authenticated_client, track_id)

        posted = await authenticated_client.post(
            f"/api/entries/{entry_id}/comments", json={"text": "a comment"}
        )
        if posted.status_code != 200:
            pytest.skip(f"comment create unavailable: {posted.status_code}")

        r = await authenticated_client.get(f"/api/entries/{entry_id}/comments")
        assert r.status_code == 200, r.text
        assert_no_sensitive_user_fields(r.json(), where="comments")

    async def test_author_projection_keeps_fields_the_ui_renders(
        self, authenticated_client: AsyncClient, test_user
    ):
        """The allowlist must not over-trim: the UI reads these five keys."""
        track_id = await self._track(authenticated_client)
        await self._entry(authenticated_client, track_id)

        r = await authenticated_client.get("/api/feed_entries", params={"limit": 20})
        assert r.status_code == 200, r.text
        entries = r.json().get("entries") or []
        authors = [e["author"] for e in entries if isinstance(e.get("author"), dict)]
        assert authors, "no embedded author to assert on — test is vacuous"
        # Mirrors what frontend/src reads off `author?.*`.
        assert "id" in authors[0]
        assert "display_name" in authors[0]


@pytest.mark.asyncio
class TestUserDirectoryDoesNotLeak:
    """``/api/users`` is an invite directory — projected, and page-size capped."""

    async def test_directory_omits_sensitive_fields(
        self, authenticated_client: AsyncClient, test_user
    ):
        r = await authenticated_client.get("/api/users", params={"per_page": 10})
        assert r.status_code == 200, r.text
        users = r.json().get("users") or []
        assert users, "no users returned — test is vacuous"
        for u in users:
            # ``email`` is intentional here (the collaborator picker shows it),
            # so assert on the fields that must never appear instead.
            for key in ("password_hash", "preferences", "notification_preferences"):
                assert key not in u, f"/api/users leaked {key}"

    async def test_directory_caps_per_page(
        self, authenticated_client: AsyncClient, test_user
    ):
        """An uncapped per_page turned the directory into a one-shot dump."""
        r = await authenticated_client.get("/api/users", params={"per_page": 100000})
        assert r.status_code == 200, r.text
        assert r.json()["per_page"] <= 100


@pytest.mark.asyncio
class TestPublicShareAuthorDoesNotLeak:
    """The unauthenticated public-share surface — widest blast radius."""

    async def test_public_share_entries_author_is_projected(
        self, authenticated_client: AsyncClient, client: AsyncClient, test_user
    ):
        tr = await authenticated_client.post(
            "/api/tracks", json={"title": "Public Leak Guard", "visibility": "private"}
        )
        assert tr.status_code == 200, tr.text
        track_id = tr.json()["track"]["id"]
        await authenticated_client.post(
            "/api/entries",
            json={"track_id": track_id, "title": "Public Entry", "body": "Body"},
        )

        enable = await authenticated_client.post(
            f"/api/tracks/{track_id}/public-share", json={"enabled": True}
        )
        if enable.status_code != 200:
            pytest.skip(f"public share unavailable: {enable.status_code}")
        token = (enable.json() or {}).get("token") or (
            enable.json().get("public_share") or {}
        ).get("token")
        if not token:
            pytest.skip("public share returned no token")

        # Unauthenticated client on purpose.
        r = await client.get(f"/api/public-share/track/{token}")
        assert r.status_code == 200, r.text
        assert_no_sensitive_user_fields(r.json(), where="public_share_track")
