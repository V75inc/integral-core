"""CRUD tests for Comments API."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestCommentsCRUD:
    """Test suite for Comments CRUD operations."""

    async def _create_track_and_entry(self, client: AsyncClient):
        """Helper to create a track and entry."""
        # Create track
        track_data = {"title": "Comment Test Track", "visibility": "private"}
        track_response = await client.post("/api/tracks", json=track_data)
        assert (
            track_response.status_code == 200
        ), f"Track creation failed: {track_response.status_code} - {track_response.text}"
        track_data_json = track_response.json()
        assert (
            "track" in track_data_json
        ), f"Response missing 'track': {track_data_json}"
        track_id = track_data_json["track"]["id"]

        # Create entry
        entry_data = {
            "track_id": track_id,
            "title": "Entry for comments",
            "body": "Entry for comments",
        }
        entry_response = await client.post("/api/entries", json=entry_data)
        assert (
            entry_response.status_code == 200
        ), f"Entry creation failed: {entry_response.status_code} - {entry_response.text}"
        entry_data_json = entry_response.json()
        assert (
            "entry" in entry_data_json
        ), f"Response missing 'entry': {entry_data_json}"
        entry_id = entry_data_json["entry"]["id"]

        return track_id, entry_id

    async def test_create_comment(self, authenticated_client: AsyncClient, test_user):
        """Test creating a comment on an entry."""
        _, entry_id = await self._create_track_and_entry(authenticated_client)

        comment_data = {"text": "This is a test comment"}

        response = await authenticated_client.post(
            f"/api/entries/{entry_id}/comments", json=comment_data
        )

        assert response.status_code == 200
        data = response.json()
        assert "comment" in data
        assert "id" in data["comment"]

        return data["comment"]["id"]

    async def test_create_threaded_comment(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test creating a reply to a comment."""
        _, entry_id = await self._create_track_and_entry(authenticated_client)

        # Create parent comment
        parent_comment_data = {"text": "Parent comment"}
        parent_response = await authenticated_client.post(
            f"/api/entries/{entry_id}/comments", json=parent_comment_data
        )
        parent_id = parent_response.json()["comment"]["id"]

        # Create reply
        reply_data = {"text": "Reply comment", "parent_id": parent_id}

        response = await authenticated_client.post(
            f"/api/entries/{entry_id}/comments", json=reply_data
        )

        assert response.status_code == 200
        data = response.json()
        assert "comment" in data
        assert data["comment"]["parent_id"] == parent_id

    async def test_get_entry_comments(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test getting all comments for an entry."""
        _, entry_id = await self._create_track_and_entry(authenticated_client)

        # Create multiple comments
        for i in range(3):
            comment_data = {"text": f"Comment {i}"}
            await authenticated_client.post(
                f"/api/entries/{entry_id}/comments", json=comment_data
            )

        response = await authenticated_client.get(f"/api/entries/{entry_id}/comments")

        assert response.status_code == 200
        data = response.json()
        assert "comments" in data
        assert "total" in data
        assert isinstance(data["comments"], list)
        assert len(data["comments"]) >= 3

    async def test_get_comments_nonexistent_entry(
        self, authenticated_client: AsyncClient
    ):
        """Test getting comments for a non-existent entry."""
        response = await authenticated_client.get(
            "/api/entries/nonexistent-id/comments"
        )

        assert response.status_code == 404

    async def test_update_comment(self, authenticated_client: AsyncClient, test_user):
        """Test updating a comment."""
        _, entry_id = await self._create_track_and_entry(authenticated_client)

        # Create a comment
        comment_data = {"text": "Original comment body"}
        create_response = await authenticated_client.post(
            f"/api/entries/{entry_id}/comments", json=comment_data
        )
        comment_id = create_response.json()["comment"]["id"]

        # Update the comment
        update_data = {"text": "Updated comment body"}

        response = await authenticated_client.put(
            f"/api/comments/{comment_id}", json=update_data
        )

        assert response.status_code == 200
        data = response.json()
        assert "comment" in data
        assert "updated_at" in data["comment"]
        assert data["comment"]["text"] == "Updated comment body"

    async def test_update_other_user_comment_forbidden(
        self,
        authenticated_client: AsyncClient,
        second_user_client: AsyncClient,
        test_user,
        test_user2,
    ):
        """Test that users cannot update other users' comments."""
        _, entry_id = await self._create_track_and_entry(authenticated_client)

        # Create a comment (would be created by test_user via authenticated_client)
        comment_data = {"text": "Other user's comment"}
        create_response = await authenticated_client.post(
            f"/api/entries/{entry_id}/comments", json=comment_data
        )
        comment_id = create_response.json()["comment"]["id"]

        # Try to update as another authenticated user.
        update_data = {"text": "Hacked comment"}
        response = await second_user_client.put(
            f"/api/comments/{comment_id}", json=update_data
        )
        assert response.status_code == 403

    async def test_delete_comment(self, authenticated_client: AsyncClient, test_user):
        """Test deleting a comment."""
        _, entry_id = await self._create_track_and_entry(authenticated_client)

        # Create a comment
        comment_data = {"text": "Comment to delete"}
        create_response = await authenticated_client.post(
            f"/api/entries/{entry_id}/comments", json=comment_data
        )
        comment_id = create_response.json()["comment"]["id"]

        # Delete the comment
        response = await authenticated_client.delete(f"/api/comments/{comment_id}")

        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "deleted_comment_id" in data

    async def test_delete_other_user_comment_forbidden(
        self,
        authenticated_client: AsyncClient,
        second_user_client: AsyncClient,
        test_user,
        test_user2,
    ):
        """Test that users cannot delete other users' comments."""
        _, entry_id = await self._create_track_and_entry(authenticated_client)

        # Create a comment
        comment_data = {"text": "Protected comment"}
        create_response = await authenticated_client.post(
            f"/api/entries/{entry_id}/comments", json=comment_data
        )
        comment_id = create_response.json()["comment"]["id"]

        # Try to delete as another authenticated user.
        response = await second_user_client.delete(f"/api/comments/{comment_id}")
        assert response.status_code == 403

    async def test_get_nonexistent_comment(self, authenticated_client: AsyncClient):
        """Test getting a comment that doesn't exist."""
        response = await authenticated_client.get("/api/comments/nonexistent-id")

        # Comments don't have a GET endpoint, so this should 404 or 405
        assert response.status_code in [404, 405]

    async def test_create_comment_with_invalid_parent(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test creating a comment with invalid parent ID."""
        _, entry_id = await self._create_track_and_entry(authenticated_client)

        comment_data = {
            "text": "Reply to non-existent comment",
            "parent_id": "nonexistent-parent-id",
        }

        response = await authenticated_client.post(
            f"/api/entries/{entry_id}/comments", json=comment_data
        )

        assert response.status_code == 404
