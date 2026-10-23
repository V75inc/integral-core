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
    """Preview accepts supported YAML filename extensions."""
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
    """Publish and create a Track without assigning an App."""
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
    """JSON preview remains valid and rejects invalid option values."""
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


@pytest.mark.smoke
@pytest.mark.asyncio
@pytest.mark.parametrize("use_import", [False, True])
async def test_custom_and_imported_tracks_keep_app_destination(
    authenticated_client, use_import
):
    """Custom and imported Tracks retain their structural App parent."""
    workspace = await authenticated_client.post(
        "/api/workspaces", json={"name": "App Track creation"}
    )
    assert workspace.status_code == 200, workspace.text
    ws_id = workspace.json()["workspace"]["id"]
    app = await authenticated_client.post(
        "/api/apps", json={"name": "Track destination", "workspace_id": ws_id}
    )
    assert app.status_code == 200, app.text
    app_id = app.json()["app"]["id"]
    body = {
        "title": "Imported track" if use_import else "Custom track",
        "workspace_id": ws_id,
        "app_id": app_id,
    }
    if use_import:
        published = await authenticated_client.post(
            "/api/operational-models/import",
            params={"workspace_id": ws_id},
            files={
                "file": ("app-track.yml", _MINIMAL_YAML.encode(), "application/yaml")
            },
        )
        assert published.status_code == 200, published.text
        body["library_operational_model_id"] = published.json()["operational_model"][
            "id"
        ]
    response = await authenticated_client.post("/api/tracks", json=body)
    assert response.status_code == 200, response.text
    track = response.json()["track"]
    assert track["app"]["id"] == app_id
    readback = await authenticated_client.get(f"/api/tracks/{track['id']}")
    assert readback.status_code == 200, readback.text
    assert readback.json()["track"]["app"]["id"] == app_id
