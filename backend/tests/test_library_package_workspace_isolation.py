"""Private library packages must not leak across workspaces."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


def _app_manifest(name: str = "iso-pack") -> dict:
    return {
        "operational_model_schema_version": 2,
        "scope": "app",
        "package": {"name": name},
        "app": {
            "tracks": [
                {
                    "key": "items",
                    "name": "Items",
                    "provision_on_create": False,
                    "entry_types": [
                        {"key": "item", "name": "Item", "fields": []}
                    ],
                    "views": [{"key": "all", "name": "All", "view_type": "feed"}],
                    "taxonomy": {"tag_groups": []},
                }
            ],
            "relations": [],
        },
    }


@pytest.mark.asyncio
async def test_private_library_hidden_and_not_installable_in_other_workspace(
    authenticated_client: AsyncClient,
):
    ws_a = await authenticated_client.post(
        "/api/workspaces", json={"name": "Template Isolation A"}
    )
    assert ws_a.status_code == 200, ws_a.text
    ws_a_id = ws_a.json()["workspace"]["id"]

    ws_b = await authenticated_client.post(
        "/api/workspaces", json={"name": "Template Isolation B"}
    )
    assert ws_b.status_code == 200, ws_b.text
    ws_b_id = ws_b.json()["workspace"]["id"]

    headers_a = {"X-Integral-Scope": f"ws:{ws_a_id}"}
    headers_b = {"X-Integral-Scope": f"ws:{ws_b_id}"}

    pub = await authenticated_client.post(
        "/api/operational-models",
        json={
            "name": "Private A Template",
            "workspace_id": ws_a_id,
            "manifest": _app_manifest("private-a-template"),
        },
        headers=headers_a,
    )
    assert pub.status_code == 200, pub.text
    private_id = pub.json()["operational_model"]["id"]

    listed_a = await authenticated_client.get(
        "/api/operational-models", headers=headers_a
    )
    assert listed_a.status_code == 200, listed_a.text
    ids_a = {row["id"] for row in listed_a.json()["operational_models"]}
    assert private_id in ids_a

    listed_b = await authenticated_client.get(
        "/api/operational-models", headers=headers_b
    )
    assert listed_b.status_code == 200, listed_b.text
    ids_b = {row["id"] for row in listed_b.json()["operational_models"]}
    assert private_id not in ids_b

    get_b = await authenticated_client.get(
        f"/api/operational-models/{private_id}", headers=headers_b
    )
    assert get_b.status_code == 404, get_b.text

    install_b = await authenticated_client.post(
        f"/api/workspaces/{ws_b_id}/apps/install",
        json={"library_operational_model_id": private_id},
        headers=headers_b,
    )
    assert install_b.status_code in (404, 400, 403), install_b.text
