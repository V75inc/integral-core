"""Tests for watcher notifications on Entry updates and Comments."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.models.edges import WATCHES
from app.models.nodes import Entry, User


@pytest.mark.asyncio
class TestWatcherNotifications:
    """Test suite for entry watcher endpoints and notifications flow."""

    async def _create_track(self, client: AsyncClient) -> str:
        """Helper to create a track."""
        track_data = {"title": "Watchers Track", "visibility": "private"}
        response = await client.post("/api/tracks", json=track_data)
        assert response.status_code == 200
        return response.json()["track"]["id"]

    async def _create_entry(self, client: AsyncClient, track_id: str) -> str:
        """Helper to create an entry."""
        entry_data = {
            "track_id": track_id,
            "title": "Watchable Entry",
            "body": "Watch this!",
        }
        response = await client.post("/api/entries", json=entry_data)
        assert response.status_code == 200
        return response.json()["entry"]["id"]

    async def test_watch_unwatch_and_get_watchers(
        self, authenticated_client: AsyncClient, test_user
    ) -> None:
        """Test endpoint flows for watching, unwatching, and listing watchers."""
        track_id = await self._create_track(authenticated_client)
        entry_id = await self._create_entry(authenticated_client, track_id)

        # 1. Initially there should be no watchers
        resp = await authenticated_client.get(f"/api/entries/{entry_id}/watchers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["is_watching"] is False
        assert len(data["watchers"]) == 0

        # 2. Watch the entry
        resp = await authenticated_client.post(f"/api/entries/{entry_id}/watch")
        assert resp.status_code == 200
        assert resp.json()["is_watching"] is True

        # 3. Listing watchers should now show the user
        resp = await authenticated_client.get(f"/api/entries/{entry_id}/watchers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["is_watching"] is True
        assert len(data["watchers"]) == 1
        assert data["watchers"][0]["id"] == test_user.id

        # 4. Unwatch the entry
        resp = await authenticated_client.post(f"/api/entries/{entry_id}/unwatch")
        assert resp.status_code == 200
        assert resp.json()["is_watching"] is False

        # 5. List watchers should be empty again
        resp = await authenticated_client.get(f"/api/entries/{entry_id}/watchers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["is_watching"] is False

    async def test_notifications_sent_to_watchers_on_update(
        self, authenticated_client: AsyncClient, test_user
    ) -> None:
        """Verify that updating an entry sends a notification to other watchers."""
        track_id = await self._create_track(authenticated_client)
        entry_id = await self._create_entry(authenticated_client, track_id)

        # Create another user that will watch
        other_user = await User.create(
            user_id="auth-other-user",
            display_name="Other Watcher",
            preferences={"email": "other@example.com"},
        )
        entry = await Entry.get(entry_id)
        assert entry is not None
        # Connect other_user as a watcher
        await other_user.connect(entry, edge=WATCHES)

        # Mock the dispatch of the notification router to verify it is called for other_user
        # but not the editor (test_user)
        with patch(
            "app.services.notification_router.dispatch",
            new=AsyncMock(return_value={"notification_id": "notif-1", "results": []}),
        ) as dispatch_mock:
            # Update the entry via the API
            update_data = {
                "title": "Updated Entry Title",
                "body": "Updated body content",
            }
            resp = await authenticated_client.put(
                f"/api/entries/{entry_id}", json=update_data
            )
            assert resp.status_code == 200

        # Dispatch should have been called exactly once (for other_user)
        assert dispatch_mock.await_count == 1
        call_kwargs = dispatch_mock.await_args.kwargs
        assert call_kwargs.get("user_id") == "auth-other-user"
        assert call_kwargs.get("kind") == "entry_update"
        assert "updated the entry" in call_kwargs.get("payload", {}).get("snippet", "")

    async def test_notifications_sent_to_watchers_on_comment(
        self, authenticated_client: AsyncClient, test_user
    ) -> None:
        """Verify that commenting on an entry sends a notification to other watchers."""
        track_id = await self._create_track(authenticated_client)
        entry_id = await self._create_entry(authenticated_client, track_id)

        # Create another user that will watch
        other_user = await User.create(
            user_id="auth-other-comment-user",
            display_name="Other Comment Watcher",
            preferences={"email": "othercomment@example.com"},
        )
        entry = await Entry.get(entry_id)
        assert entry is not None
        await other_user.connect(entry, edge=WATCHES)

        # Mock the dispatch of the notification router
        with patch(
            "app.services.notification_router.dispatch",
            new=AsyncMock(return_value={"notification_id": "notif-2", "results": []}),
        ) as dispatch_mock:
            # Post a comment
            resp = await authenticated_client.post(
                f"/api/entries/{entry_id}/comments",
                json={"text": "This is a great update!"},
            )
            assert resp.status_code == 200

        # Dispatch should have fanned out to other_user
        assert dispatch_mock.await_count == 1
        call_kwargs = dispatch_mock.await_args.kwargs
        assert call_kwargs.get("user_id") == "auth-other-comment-user"
        assert call_kwargs.get("kind") == "entry_update"
        assert "commented on" in call_kwargs.get("payload", {}).get("snippet", "")
        assert "This is a great update!" in call_kwargs.get("payload", {}).get(
            "snippet", ""
        )

    async def test_notifications_have_actor_name_and_track_context(
        self, authenticated_client: AsyncClient, test_user
    ) -> None:
        """Verify notifications use the actor's first name and show the track title on creation."""
        # Update test_user display_name to "Jane Doe"
        test_user.display_name = "Jane Doe"
        await test_user.save()

        track_id = await self._create_track(authenticated_client)

        # Create another user who watches the track
        from app.models.edges import WATCHES
        from app.models.nodes import Track

        other_user = await User.create(
            user_id="auth-watcher-user-1",
            display_name="Watcher One",
            preferences={"email": "watcher1@example.com"},
        )
        track = await Track.get(track_id)
        assert track is not None
        await other_user.connect(track, edge=WATCHES)

        # Create entry and intercept notification
        with patch(
            "app.services.notification_router.dispatch",
            new=AsyncMock(return_value={"notification_id": "notif-3", "results": []}),
        ) as dispatch_mock:
            entry_id = await self._create_entry(authenticated_client, track_id)

        # Assert correct dispatch fields
        assert dispatch_mock.await_count == 1
        call_kwargs = dispatch_mock.await_args.kwargs
        payload = call_kwargs.get("payload", {})
        assert payload.get("actor_name") == "Jane"
        assert "created the entry: Watchable Entry in Watchers Track" in payload.get(
            "snippet", ""
        )
