"""Standalone Track imports use the normal validated YAML preview pipeline."""

import pytest

_MINIMAL_YAML = """operational_model_schema_version: 2
scope: track
package:
  name: Track import QA
  slug: track-import-qa
  version: 1.0.0
track:
  entry_types:
    - key: item
      name: Item
      fields: []
  views: []
"""


@pytest.mark.smoke
@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["track.yml", "track.yaml", "track.YML"])
async def test_import_preview_accepts_yaml_extensions(authenticated_client, filename):
    created = await authenticated_client.post(
        "/api/workspaces", json={"name": "YAML upload"}
    )
    assert created.status_code == 200, created.text
    ws_id = created.json()["workspace"]["id"]
    response = await authenticated_client.post(
        "/api/operational-models/import",
        params={"workspace_id": ws_id, "preview": "true"},
        files={"file": (filename, _MINIMAL_YAML.encode(), "application/yaml")},
    )
    assert response.status_code == 200, response.text
    package = response.json()["packages"][0]
    assert package["scope_kind"] == "track"
    assert package["validation_errors"] == []


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_yaml_publish_creates_standalone_track(authenticated_client):
    created = await authenticated_client.post(
        "/api/workspaces", json={"name": "Standalone import"}
    )
    ws_id = created.json()["workspace"]["id"]
    published = await authenticated_client.post(
        "/api/operational-models/import",
        params={"workspace_id": ws_id},
        files={"file": ("standalone.yml", _MINIMAL_YAML.encode(), "application/yaml")},
    )
    assert published.status_code == 200, published.text
    model_id = published.json()["operational_model"]["id"]
    track = await authenticated_client.post(
        "/api/tracks",
        json={
            "title": "Standalone YAML track",
            "workspace_id": ws_id,
            "library_operational_model_id": model_id,
        },
    )
    assert track.status_code == 200, track.text
    assert not track.json()["track"].get("app_id")


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_json_import_options_remain_supported(authenticated_client):
    import yaml

    created = await authenticated_client.post(
        "/api/workspaces", json={"name": "JSON import"}
    )
    ws_id = created.json()["workspace"]["id"]
    response = await authenticated_client.post(
        "/api/operational-models/import",
        json={
            "workspace_id": ws_id,
            "preview": True,
            "manifest": yaml.safe_load(_MINIMAL_YAML),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["packages"][0]["scope_kind"] == "track"
    invalid = await authenticated_client.post(
        "/api/operational-models/import",
        params={
            "workspace_id": ws_id,
            "preview": "not-a-boolean",
        },
        files={"file": ("track.yml", _MINIMAL_YAML.encode(), "application/yaml")},
    )
    assert invalid.status_code == 400, invalid.text
