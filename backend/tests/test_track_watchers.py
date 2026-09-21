"""Tests for track-level watchers and assignee notifications."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.models.edges import WATCHES
from app.models.nodes import Entry, EntryType, Track, User, Workspace
from app.services.notification_router import get_entry_assignees_user_ids


@pytest.mark.asyncio
class TestTrackWatchersAndAssignees:
    """Test suite for track watchers endpoints and notifications including assignees."""

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

    async def test_track_watch_unwatch_lifecycle(
        self, authenticated_client: AsyncClient, test_user
    ) -> None:
        """Test endpoint flows for watching, unwatching, and listing track watchers."""
        track_id = await self._create_track(authenticated_client)

        # 1. Initially there should be no watchers
        resp = await authenticated_client.get(f"/api/tracks/{track_id}/watchers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["is_watching"] is False
        assert len(data["watchers"]) == 0

        # 2. Watch the track
        resp = await authenticated_client.post(f"/api/tracks/{track_id}/watch")
        assert resp.status_code == 200
        assert resp.json()["is_watching"] is True

        # 3. Listing watchers should now show the user
        resp = await authenticated_client.get(f"/api/tracks/{track_id}/watchers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["is_watching"] is True
        assert len(data["watchers"]) == 1
        assert data["watchers"][0]["id"] == test_user.id

        # 4. Unwatch the track
        resp = await authenticated_client.post(f"/api/tracks/{track_id}/unwatch")
        assert resp.status_code == 200
        assert resp.json()["is_watching"] is False

        # 5. List watchers should be empty again
        resp = await authenticated_client.get(f"/api/tracks/{track_id}/watchers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["is_watching"] is False

    async def test_notifications_sent_to_track_watchers(
        self, authenticated_client: AsyncClient, test_user
    ) -> None:
        """Verify that entry events send notifications to track watchers."""
        track_id = await self._create_track(authenticated_client)

        # Create another user that will watch the track
        track_watcher = await User.create(
            user_id="auth-track-watcher",
            display_name="Track Watcher",
            preferences={"email": "trackwatcher@example.com"},
        )
        track = await Track.get(track_id)
        assert track is not None
        await track_watcher.connect(track, edge=WATCHES)

        # Mock the dispatch function
        with patch(
            "app.services.notification_router.dispatch",
            new=AsyncMock(return_value={"notification_id": "notif-t1", "results": []}),
        ) as dispatch_mock:
            # 1. Create an entry (should notify track watcher)
            entry_id = await self._create_entry(authenticated_client, track_id)
            assert dispatch_mock.await_count == 1
            call_kwargs = dispatch_mock.await_args.kwargs
            assert call_kwargs.get("user_id") == "auth-track-watcher"
            assert "created the entry" in call_kwargs.get("payload", {}).get(
                "snippet", ""
            )

            # Reset mock
            dispatch_mock.reset_mock()

            # 2. Update the entry (should notify track watcher)
            update_data = {
                "title": "Updated Entry Title",
            }
            resp = await authenticated_client.put(
                f"/api/entries/{entry_id}", json=update_data
            )
            assert resp.status_code == 200
            assert dispatch_mock.await_count == 1
            call_kwargs = dispatch_mock.await_args.kwargs
            assert call_kwargs.get("user_id") == "auth-track-watcher"
            assert "updated the entry" in call_kwargs.get("payload", {}).get(
                "snippet", ""
            )

            # Reset mock
            dispatch_mock.reset_mock()

            # 3. Comment on the entry (should notify track watcher)
            resp = await authenticated_client.post(
                f"/api/entries/{entry_id}/comments",
                json={"text": "Interesting change!"},
            )
            assert resp.status_code == 200
            assert dispatch_mock.await_count == 1
            call_kwargs = dispatch_mock.await_args.kwargs
            assert call_kwargs.get("user_id") == "auth-track-watcher"
            assert "commented on" in call_kwargs.get("payload", {}).get("snippet", "")

    async def test_notifications_sent_to_assignees(
        self, authenticated_client: AsyncClient, test_user
    ) -> None:
        """Verify that assignees resolved from relation fields are notified on entry changes."""
        # Setup workspace and tracks
        from app.models.edges import IS_MEMBER_OF, OWNS
        from app.utils.time import utc_now_iso

        now = utc_now_iso()

        ws = await Workspace.create(
            kind="organization",
            workspace_type="company",
            name="Assignee Test WS",
            name_fold="assignee test ws",
            created_at=now,
            updated_at=now,
        )
        await test_user.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)

        # 1. Create employees track + employee entry type
        employees_track = await Track.create(
            title="Employees",
            owner_id=test_user.id,
            workspace_id=ws.id,
            visibility="private",
        )
        # Mirror the real creation path: services/track_service.py wires an OWNS
        # edge from the creator. Hand-built fixtures that set only the scalar
        # owner_id used to pass because org staff got an implicit admin role;
        # that is now capped at viewer, so a missing edge surfaces as a 403.
        await test_user.connect(employees_track, edge=OWNS, added_at=now)
        employee_et = await EntryType.create(
            name="Employee",
            name_fold="employee",
            track_id=employees_track.id,
            form_schema={
                "fields": [{"key": "member", "type": "member", "required": True}]
            },
        )

        # 2. Create the target user (assignee) and their employee entry
        assignee_user = await User.create(
            user_id="auth-assignee-user",
            display_name="Assigned Employee",
            preferences={"email": "assignee@example.com"},
        )
        await assignee_user.connect(ws, edge=IS_MEMBER_OF, role="member", added_at=now)

        employee_entry = await Entry.create(
            type_id=employee_et.id,
            title="Assigned Employee Profile",
            author_id=test_user.id,
            track_id=employees_track.id,
            custom_fields={"member": assignee_user.id},
        )

        # 3. Create requests track + request entry type with relation to employee
        requests_track = await Track.create(
            title="Requests",
            owner_id=test_user.id,
            workspace_id=ws.id,
            visibility="private",
        )
        await test_user.connect(requests_track, edge=OWNS, added_at=now)
        request_et = await EntryType.create(
            name="Request",
            name_fold="request",
            track_id=requests_track.id,
            form_schema={
                "fields": [
                    {
                        "key": "assignees",
                        "type": "relation",
                        "relation": {
                            "target_entry_types": ["employee"],
                            "target_track_types": ["employees"],
                            "allow_cross_track": True,
                            "many": True,
                        },
                    }
                ]
            },
        )

        # Attach operational models to make sure references resolve correctly
        from app.services.app_graph import ensure_track_attached_operational_model

        await ensure_track_attached_operational_model(employees_track)
        await ensure_track_attached_operational_model(requests_track)

        # 4. Assert get_entry_assignees_user_ids resolves the assignee correctly
        test_entry = await Entry.create(
            type_id=request_et.id,
            title="Vacation Request",
            author_id=test_user.id,
            track_id=requests_track.id,
            custom_fields={"assignees": [employee_entry.id]},
        )
        assignees = await get_entry_assignees_user_ids(test_entry)
        assert assignee_user.id in assignees
        assert len(assignees) == 1

        # 5. Mock dispatch to test notification trigger on update
        with patch(
            "app.services.notification_router.dispatch",
            new=AsyncMock(return_value={"notification_id": "notif-a1", "results": []}),
        ) as dispatch_mock:
            # Update request entry (this should notify assignee)
            resp = await authenticated_client.put(
                f"/api/entries/{test_entry.id}",
                json={"title": "Updated Vacation Request"},
            )
            assert resp.status_code == 200
            assert dispatch_mock.await_count == 1
            call_kwargs = dispatch_mock.await_args.kwargs
            assert call_kwargs.get("user_id") == assignee_user.user_id
            assert "updated the entry" in call_kwargs.get("payload", {}).get(
                "snippet", ""
            )
