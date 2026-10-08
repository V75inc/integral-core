"""Generic workspace partial-success contracts; no domain packages required."""

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_create_workspace_preserves_safe_partial_install_details(
    authenticated_client, monkeypatch
):
    batch = AsyncMock(
        return_value={
            "installed": [{"status": "active", "install_token": "private-token"}],
            "failed": [
                {
                    "library_cp_id": "library-1",
                    "name": "Example App",
                    "error_code": "entitlement.required",
                    "error": "private exception",
                }
            ],
            "skipped": [],
            "auto_dependencies": 0,
        }
    )
    monkeypatch.setattr("app.services.app_batch_install.batch_install", batch)
    response = await authenticated_client.post(
        "/api/workspaces",
        json={
            "name": "Partial Provisioning Smoke",
            "library_operational_model_ids": ["library-1"],
        },
    )
    assert response.status_code == 200, response.text
    summary = response.json()["provisioning"]
    assert summary["installed"] == 1
    assert summary["failed"] == 1
    assert summary["failures"] == [
        {
            "library_cp_id": "library-1",
            "name": "Example App",
            "error_code": "entitlement.required",
        }
    ]
    assert "private" not in response.text
    workspace = response.json()["workspace"]
    assert workspace["your_role"] == "owner"
    read = await authenticated_client.get(f"/api/workspaces/{workspace['id']}")
    assert read.status_code == 200


@pytest.mark.asyncio
async def test_create_workspace_failure_has_generic_code_fallback(
    authenticated_client, monkeypatch
):
    monkeypatch.setattr(
        "app.services.app_batch_install.batch_install",
        AsyncMock(
            return_value={
                "failed": [{"library_cp_id": "library-2", "error": "internal paths"}],
            }
        ),
    )
    response = await authenticated_client.post(
        "/api/workspaces",
        json={
            "name": "Generic Provisioning Failure",
            "library_operational_model_ids": ["library-2"],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["provisioning"]["failures"] == [
        {"library_cp_id": "library-2", "name": "App", "error_code": "install_failed"}
    ]
    assert "internal paths" not in response.text
