"""Track icon identity uses the existing authorized metadata contract."""

import pytest

pytestmark = pytest.mark.smoke


@pytest.mark.asyncio
async def test_track_icon_persists_and_viewer_cannot_replace_it(
    authenticated_client, test_user2, second_user_client
):
    created = await authenticated_client.post(
        "/api/tracks", json={"title": "Track identity", "icon": "briefcase"}
    )
    assert created.status_code == 200, created.text
    track = created.json()["track"]
    assert track["icon"] == "briefcase"
    tid = track["id"]
    grant = await authenticated_client.post(
        f"/api/tracks/{tid}/collaborators",
        json={"collaborator_user_id": test_user2.id, "role": "viewer"},
    )
    assert grant.status_code == 200, grant.text
    headers = {"X-Integral-Scope": f"ws:{track['workspace_id']}"}
    visible = await second_user_client.get(f"/api/tracks/{tid}", headers=headers)
    assert visible.status_code == 200, visible.text
    denied = await second_user_client.put(
        f"/api/tracks/{tid}", json={"icon": "banknote"}, headers=headers
    )
    assert denied.status_code == 403, denied.text
    unchanged = await authenticated_client.get(f"/api/tracks/{tid}")
    assert unchanged.json()["track"]["icon"] == "briefcase"
    cleared = await authenticated_client.put(f"/api/tracks/{tid}", json={"icon": ""})
    assert cleared.status_code == 200, cleared.text
    reread = await authenticated_client.get(f"/api/tracks/{tid}")
    assert reread.json()["track"]["icon"] == ""
    assert reread.json()["track"]["icon_overridden"] is True
    forged = await authenticated_client.put(
        f"/api/tracks/{tid}",
        json={"title": "Track identity renamed", "icon_overridden": False},
    )
    assert forged.status_code == 422, forged.text
    assert forged.json()["error_code"] == "VALIDATION_ERROR"
    preserved = await authenticated_client.get(f"/api/tracks/{tid}")
    assert preserved.json()["track"]["icon_overridden"] is True
    assert preserved.json()["track"]["title"] == "Track identity"


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_dot", [False, True])
async def test_create_uses_app_template_icon(
    authenticated_client, test_user, explicit_dot
):
    from app.models.nodes import OperationalModel

    response = await authenticated_client.post(
        "/api/apps", json={"name": "Icon source"}
    )
    assert response.status_code == 200, response.text
    app_id = response.json()["app"]["id"]
    response = await authenticated_client.post(
        f"/api/apps/{app_id}/operational-model/track-templates",
        json={"name": "Requests", "description": "Requests to review."},
    )
    assert response.status_code == 200, response.text
    template = await OperationalModel.get(response.json()["track_template"]["id"])
    template.manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "track": {
            "name": "Requests",
            "description": "Requests to review.",
            "icon": "briefcase",
        },
    }
    await template.save()
    payload = {
        "title": "Template icon",
        "app_id": app_id,
        "app_track_template_operational_model_id": template.id,
    }
    if explicit_dot:
        payload["icon"] = ""
    created = await authenticated_client.post("/api/tracks", json=payload)
    assert created.status_code == 200, created.text
    assert created.json()["track"]["icon"] == ("" if explicit_dot else "briefcase")
    assert created.json()["track"]["icon_overridden"] is explicit_dot


def test_standalone_track_manifest_retains_validated_icon():
    from app.services.operational_model_compile import compile_canonical_manifest

    result = compile_canonical_manifest(
        manifest={
            "operational_model_schema_version": 2,
            "scope": "track",
            "track": {
                "name": "Requests",
                "description": "Requests to review.",
                "icon": "briefcase",
            },
        }
    )
    assert result["track"]["icon"] == "briefcase"
