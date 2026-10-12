"""Two independent external Apps compile bounded role and transition contracts."""

from pathlib import Path

import pytest
import yaml

from app.services.operational_model_runtime import compile_canonical_manifest

pytestmark = pytest.mark.smoke


@pytest.mark.unit
@pytest.mark.parametrize(
    "slug, role",
    [
        ("reference-review-queue", "reviewer"),
        ("reference-change-control", "release-manager"),
    ],
)
def test_external_action_contract_compiles(slug, role):
    path = (
        Path(__file__).resolve().parents[2]
        / "examples"
        / slug
        / "operational-model.yaml"
    )
    manifest = yaml.safe_load(path.read_text())
    from app.services.operational_model_loader import _assemble_manifest

    canonical = compile_canonical_manifest(manifest=_assemble_manifest(manifest))
    action = canonical["app"]["operations"][0]
    assert action["authorization"]["roles"] == [role]
    assert action["authorization"]["conditions"][0]["principal"] is True
    protected = next(iter(canonical["app"]["protected_state"].values()))
    assert protected["top_level"] == ["status"] and protected["deny_delete"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", ["reference-review-queue", "reference-change-control"])
async def test_external_roles_separate_editor_approver_configuration(slug):
    from app.services.access_delegation import action_decision, create_binding
    from app.services.app_graph import ensure_integral_app_graph
    from app.services.application_definitions import definition_fingerprint
    from app.services.operational_model_loader import _assemble_manifest
    from tests.test_app_action_delegation import fixture

    await ensure_integral_app_graph(include_library=False)
    owner, reader, workspace, app, track, entry, definition = await fixture()
    manifest = yaml.safe_load(
        (
            Path(__file__).resolve().parents[2]
            / "examples"
            / slug
            / "operational-model.yaml"
        ).read_text()
    )
    canonical = compile_canonical_manifest(manifest=_assemble_manifest(manifest))
    definition.canonical_manifest = canonical
    definition.manifest_fingerprint = definition_fingerprint(canonical)
    await definition.save()
    role = canonical["app"]["operations"][0]["authorization"]["roles"][0]
    await create_binding(
        actor_id=owner.id,
        app_id=app.id,
        subject_kind="human",
        subject_id=reader.id,
        role=role,
        conditions=[],
    )
    args = dict(
        principal_id=reader.id,
        app_id=app.id,
        payload={"entry_id": entry.id, "value": "configured"},
    )
    assert not (await action_decision(**args, operation_key="configure")).allowed
    assert not (await action_decision(**args, operation_key="edit")).allowed
    await create_binding(
        actor_id=owner.id,
        app_id=app.id,
        subject_kind="human",
        subject_id=reader.id,
        role="configuration-admin",
        conditions=[],
    )
    assert (await action_decision(**args, operation_key="configure")).allowed
    assert not (await action_decision(**args, operation_key="edit")).allowed
    await create_binding(
        actor_id=owner.id,
        app_id=app.id,
        subject_kind="human",
        subject_id=reader.id,
        role="editor",
        conditions=[],
    )
    assert (await action_decision(**args, operation_key="edit")).allowed
