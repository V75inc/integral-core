"""Package creation metadata is rejected before an upgrade mutates the App."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.exceptions import BadRequestError
from app.services.app_lifecycle import validate_package_creation_descriptions

pytestmark = pytest.mark.smoke


def _manifest(*, description=None, template=False):
    item = {"key": "records", "name": "Records"}
    if description is not None:
        item["description"] = description
    return {"app": {"track_templates" if template else "tracks": [item]}}


@pytest.mark.asyncio
@pytest.mark.parametrize("template", [False, True])
async def test_new_package_containers_require_descriptions(template):
    with pytest.raises(BadRequestError, match="meaningful description"):
        await validate_package_creation_descriptions(_manifest(template=template))


@pytest.mark.asyncio
async def test_legacy_track_and_template_metadata_can_still_be_updated():
    app = SimpleNamespace(
        nodes=AsyncMock(return_value=[SimpleNamespace(template_id="records")])
    )
    prior = {"app": {"track_templates": [{"key": "details", "name": "Details"}]}}
    target = _manifest()
    target["app"]["track_templates"] = deepcopy(prior["app"]["track_templates"])
    await validate_package_creation_descriptions(
        target, app_node=app, prior_manifest=prior
    )
    target["app"]["track_templates"].append({"key": "new", "name": "New Details"})
    with pytest.raises(BadRequestError, match="New Details"):
        await validate_package_creation_descriptions(
            target, app_node=app, prior_manifest=prior
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint_name",
    ["merge_library_into_app_operational_model", "apply_app_operational_model_library"],
)
async def test_upgrade_endpoint_rejects_before_any_manifest_or_app_write(
    monkeypatch, endpoint_name
):
    from app.api import apps
    from app.services import (
        application_definitions,
        application_upgrade_safety,
        package_trust,
    )

    prior_manifest = {"app": {"tracks": []}}
    profile = SimpleNamespace(manifest=deepcopy(prior_manifest))
    app = SimpleNamespace(
        id="app-1",
        library_merge_source_id="old",
        nodes=AsyncMock(return_value=[]),
        save=AsyncMock(),
    )
    lib = SimpleNamespace(id="lib-2", library_package=True, manifest=_manifest())
    monkeypatch.setattr(apps, "resolve_principal_id", lambda request: "owner")
    monkeypatch.setattr(
        apps, "policy_evaluate", AsyncMock(return_value=SimpleNamespace(allowed=True))
    )
    monkeypatch.setattr(
        apps, "_require_app_attached_cp", AsyncMock(return_value=(app, profile))
    )
    monkeypatch.setattr(apps.OperationalModel, "get", AsyncMock(return_value=lib))
    monkeypatch.setattr(
        package_trust, "assert_library_artifact_trusted", lambda artifact: None
    )
    monkeypatch.setattr(
        application_definitions,
        "get_active_application_definition",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(apps, "_preview_app_library_apply", AsyncMock(return_value={}))
    monkeypatch.setattr(
        application_upgrade_safety,
        "assert_package_upgrade_migration_safe",
        AsyncMock(return_value={"candidate_manifest": _manifest()}),
    )
    merge = AsyncMock()
    monkeypatch.setattr(apps, "merge_library_manifest_into_operational_model", merge)

    with pytest.raises(BadRequestError, match="meaningful description"):
        await getattr(apps, endpoint_name)(SimpleNamespace(), "app-1", "lib-2")
    merge.assert_not_awaited()
    app.save.assert_not_awaited()
    assert profile.manifest == prior_manifest
    assert app.library_merge_source_id == "old"
