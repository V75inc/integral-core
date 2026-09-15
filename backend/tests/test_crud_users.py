"""CRUD tests for Users API."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestUsersCRUD:
    """Test suite for Users CRUD operations."""

    async def test_create_user_via_auth(self, client: AsyncClient):
        """Test creating a user via the supported registration endpoint.

        ``/api/auth/register`` (jvspatial's built-in) is no longer exposed —
        it created an AuthUser with no User node and no Personal Workspace.
        ``/api/auth/signup`` is the only registration path; see
        ``tests/test_auth_signup_surface.py``.
        """
        user_data = {
            "email": "newuser@example.com",
            "password": "securepassword123",
            "name": "New User",
        }

        response = await client.post("/api/auth/signup", json=user_data)

        # Should succeed or return 400 if the user already exists
        assert response.status_code in [200, 201, 400, 409]
        if response.status_code in (200, 201):
            data = response.json()
            assert "access_token" in data
            assert data["user"]["id"]

    async def test_list_users(self, authenticated_client: AsyncClient):
        """Test listing all users."""
        response = await authenticated_client.get("/api/users")

        assert response.status_code == 200
        data = response.json()
        assert "users" in data
        assert "total" in data
        assert isinstance(data["users"], list)

    async def test_list_users_with_pagination(self, authenticated_client: AsyncClient):
        """Test listing users with pagination."""
        response = await authenticated_client.get("/api/users?page=1&per_page=5")

        assert response.status_code == 200
        data = response.json()
        assert "users" in data
        assert "page" in data
        assert "per_page" in data
        assert "total_pages" in data
        assert len(data["users"]) <= 5

    async def test_list_users_with_search(self, authenticated_client: AsyncClient):
        """Test searching users."""
        response = await authenticated_client.get("/api/users?search=test")

        assert response.status_code == 200
        data = response.json()
        assert "users" in data
        assert isinstance(data["users"], list)

    async def test_get_user_by_id(self, authenticated_client: AsyncClient, test_user):
        """Test getting a specific user by ID."""
        response = await authenticated_client.get(f"/api/users/{test_user.id}")

        assert response.status_code == 200
        data = response.json()
        assert "user" in data
        assert data["user"]["id"] == test_user.id
        assert "password_hash" not in data["user"]

    async def test_get_nonexistent_user(self, authenticated_client: AsyncClient):
        """Test getting a user that doesn't exist."""
        response = await authenticated_client.get("/api/users/nonexistent-id")

        assert response.status_code == 404

    async def test_update_user(self, authenticated_client: AsyncClient, test_user):
        """Test updating user profile."""
        update_data = {
            "display_name": "Updated Name",
            "avatar_url": "https://example.com/avatar.jpg",
        }

        response = await authenticated_client.put(
            f"/api/users/{test_user.id}", json=update_data
        )

        assert response.status_code == 200
        data = response.json()
        assert "user" in data
        assert data["user"]["display_name"] == "Updated Name"
        assert data["user"]["avatar_url"] == "https://example.com/avatar.jpg"

    async def test_update_user_preferences(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test updating user preferences."""
        preferences = {"theme": "dark", "notifications": True}
        update_data = {"preferences": preferences}

        response = await authenticated_client.put(
            f"/api/users/{test_user.id}", json=update_data
        )

        assert response.status_code == 200
        data = response.json()
        assert "user" in data
        assert data["user"]["preferences"] == preferences

    async def test_update_other_user_forbidden(
        self, authenticated_client: AsyncClient, test_user, test_user2
    ):
        """Test that users cannot update other users' profiles."""
        update_data = {"display_name": "Hacked Name"}

        response = await authenticated_client.put(
            f"/api/users/{test_user2.id}", json=update_data
        )

        assert response.status_code == 403

    async def test_delete_user(self, authenticated_client: AsyncClient, test_user):
        """Test deleting a user."""
        response = await authenticated_client.request(
            "DELETE",
            f"/api/users/{test_user.id}",
            json={"confirm_email": "test@example.com"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "deleted_user_id" in data

        # Verify user is deleted
        get_response = await authenticated_client.get(f"/api/users/{test_user.id}")
        assert get_response.status_code == 404

    async def test_account_deletion_preview(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Preview returns impact summary for the caller."""
        response = await authenticated_client.get(
            "/api/users/me/account-deletion-preview"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "test@example.com"
        assert "can_delete" in data
        assert "impact" in data
        assert isinstance(data["impact"], list)

    async def test_delete_user_wrong_confirm_email(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Wrong confirmation email is rejected."""
        response = await authenticated_client.request(
            "DELETE",
            f"/api/users/{test_user.id}",
            json={"confirm_email": "wrong@example.com"},
        )
        assert response.status_code == 400

    async def test_delete_other_user_forbidden(
        self, authenticated_client: AsyncClient, test_user, test_user2
    ):
        """Test that users cannot delete other users."""
        response = await authenticated_client.delete(f"/api/users/{test_user2.id}")

        assert response.status_code == 403

    async def test_get_current_user(self, authenticated_client: AsyncClient, test_user):
        """Test getting current authenticated user."""
        response = await authenticated_client.get("/api/auth/me")

        assert response.status_code == 200
        data = response.json()
        assert "user" in data
        assert "password_hash" not in data["user"]

    async def test_update_profile(self, authenticated_client: AsyncClient, test_user):
        """Test updating current user profile."""
        update_data = {
            "display_name": "My New Name",
            "avatar_url": "https://example.com/new-avatar.jpg",
        }

        response = await authenticated_client.put(
            "/api/auth/update-profile", json=update_data
        )

        assert response.status_code == 200
        data = response.json()
        assert "user" in data
        assert data["user"]["display_name"] == "My New Name"
