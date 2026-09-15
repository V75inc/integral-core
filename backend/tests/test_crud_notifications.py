"""CRUD tests for Notifications API."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestNotificationsCRUD:
    """Test suite for Notifications CRUD operations."""

    async def test_get_notifications(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test listing notifications for current user."""
        response = await authenticated_client.get("/api/notifications")

        assert response.status_code == 200
        data = response.json()
        assert "notifications" in data
        assert "total" in data
        assert "page" in data
        assert "per_page" in data
        assert "unread_count" in data
        assert isinstance(data["notifications"], list)

    async def test_get_notifications_with_read_filter(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test filtering notifications by read status."""
        response = await authenticated_client.get(
            "/api/notifications", params={"read": False}
        )

        assert response.status_code == 200
        data = response.json()
        assert "notifications" in data
        assert "unread_count" in data

    async def test_get_notifications_pagination(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test notifications pagination."""
        response = await authenticated_client.get(
            "/api/notifications", params={"page": 1, "per_page": 5}
        )

        assert response.status_code == 200
        data = response.json()
        assert "notifications" in data
        assert "page" in data
        assert data["page"] == 1
        assert data["per_page"] == 5
        assert len(data["notifications"]) <= 5

    async def test_create_notification(
        self,
        authenticated_admin_client: AsyncClient,
        test_user,
    ):
        """Creating a notification is an admin/internal-only operation."""
        target_id = getattr(test_user, "user_id", None) or test_user.id
        response = await authenticated_admin_client.post(
            "/api/notifications",
            json={
                "target_user_id": target_id,
                "type": "update",
                "content": "Test notification content",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "notification" in data
        assert data["notification"]["content"] == "Test notification content"
        assert data["notification"]["type"] == "update"
        assert "id" in data["notification"]

    async def test_mark_notification_as_read(
        self,
        authenticated_client: AsyncClient,
        authenticated_admin_client: AsyncClient,
        test_user,
    ):
        """Test marking a notification as read (seeded by an admin)."""
        target_id = getattr(test_user, "user_id", None) or test_user.id
        create_resp = await authenticated_admin_client.post(
            "/api/notifications",
            json={
                "target_user_id": target_id,
                "type": "system",
                "content": "Unread notification",
            },
        )
        notification_id = create_resp.json()["notification"]["id"]

        response = await authenticated_client.put(
            f"/api/notifications/{notification_id}/read",
            json={},
        )

        assert response.status_code == 200
        data = response.json()
        assert "notification" in data
        assert data["notification"]["read"] is True

    async def test_mark_all_notifications_read(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test marking all notifications as read."""
        response = await authenticated_client.put(
            "/api/notifications/mark-all-read",
            json={},
        )

        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "count" in data

    async def test_delete_notification(
        self,
        authenticated_client: AsyncClient,
        authenticated_admin_client: AsyncClient,
        test_user,
    ):
        """Test deleting a notification (seeded by an admin)."""
        target_id = getattr(test_user, "user_id", None) or test_user.id
        create_resp = await authenticated_admin_client.post(
            "/api/notifications",
            json={
                "target_user_id": target_id,
                "type": "system",
                "content": "Notification to delete",
            },
        )
        notification_id = create_resp.json()["notification"]["id"]

        response = await authenticated_client.delete(
            f"/api/notifications/{notification_id}"
        )

        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert data["deleted_notification_id"] == notification_id

        get_resp = await authenticated_client.get("/api/notifications")
        ids = [n["id"] for n in get_resp.json()["notifications"]]
        assert notification_id not in ids

    async def test_mark_nonexistent_notification_read(
        self, authenticated_client: AsyncClient
    ):
        """Test marking non-existent notification returns 404."""
        response = await authenticated_client.put(
            "/api/notifications/nonexistent-id/read",
            json={},
        )
        assert response.status_code in (404, 422)

    async def test_delete_nonexistent_notification(
        self, authenticated_client: AsyncClient
    ):
        """Test deleting non-existent notification returns 404."""
        response = await authenticated_client.delete(
            "/api/notifications/nonexistent-id"
        )
        assert response.status_code == 404

    async def test_create_notification_rejects_non_admin(
        self, authenticated_client: AsyncClient, test_user
    ):
        """A normal user must not be able to push notifications to anyone.

        ``POST /notifications`` is admin/internal-only (``require_platform_admin``);
        without the gate any user could spam another user's feed.
        """
        target_id = getattr(test_user, "user_id", None) or test_user.id
        response = await authenticated_client.post(
            "/api/notifications",
            json={
                "target_user_id": target_id,
                "type": "system",
                "content": "should be refused",
            },
        )

        assert response.status_code == 403
