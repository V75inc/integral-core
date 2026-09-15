"""Synchronous API tests using TestClient instead of AsyncClient."""

import os

import pytest
from fastapi.testclient import TestClient

# Set test database BEFORE importing app
os.environ.setdefault("JVSPATIAL_DB_PATH", "test_api_sync_db")
os.environ.setdefault("JVSPATIAL_DB_TYPE", "json")
os.environ.setdefault("TESTING", "1")


def test_root_endpoint_sync():
    """Test the root health check endpoint using synchronous TestClient."""
    # Lazy import - only import app when test actually runs
    from app.main import app

    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    assert "service" in data or "status" in data or "version" in data


def test_health_endpoint_sync():
    """Test the health check endpoint using synchronous TestClient."""
    # Lazy import - only import app when test actually runs
    from app.main import app

    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
