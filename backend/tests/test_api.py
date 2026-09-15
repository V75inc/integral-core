"""Basic API endpoint tests."""

import os

import pytest
from fastapi.testclient import TestClient

# Set test database BEFORE importing app
os.environ.setdefault("JVSPATIAL_DB_PATH", "test_api_db")
os.environ.setdefault("JVSPATIAL_DB_TYPE", "json")
os.environ.setdefault("TESTING", "1")


def test_root_endpoint():
    """Test the root health check endpoint."""
    # Lazy import - only import app when test actually runs
    from app.main import app

    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 200
    data = response.json()
    # jvspatial Server may override root endpoint - check for any expected fields
    assert isinstance(data, dict)
    # Check for either custom response or jvspatial default response
    assert "service" in data or "status" in data or "version" in data


def test_health_endpoint():
    """Test the health check endpoint."""
    # Lazy import - only import app when test actually runs
    from app.main import app

    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


# Note: Full API tests would require authentication setup
# and database fixtures. These are basic smoke tests.
