"""WP-04 contract tests for durable App definition revisions."""

import pytest

from app.services.application_definitions import (
    build_requirement_ledger,
    definition_fingerprint,
    derive_local_overrides,
    preview_application_definition,
)
from app.services.content_profile_runtime import compile_canonical_manifest


def test_requirement_ledger_names_supported_materialization_obligations():
    canonical = {
        "package": {"slug": "rental-ops"},
        "app": {
            "requires_apps": [
                {
                    "key": "identity",
                    "min_version": "2.0.0",
                    "optional": False,
                    "reason": "Renters are shared identities",
                }
            ],
            "tracks": [
                {
                    "key": "vehicles",
                    "name": "Vehicles",
                    "entry_types": [{"key": "car", "name": "Car"}],
                    "views": [{"key": "availability", "name": "Availability"}],
                }
            ],
            "track_templates": [{"key": "service", "name": "Service"}],
            "operations": [{"key": "mark_rented", "name": "Mark rented"}],
            "queries": [{"key": "due_service", "name": "Due service"}],
            "skills": [{"key": "rental_assistant", "name": "Rental assistant"}],
            "agents": [{"key": "ops", "name": "Operations"}],
        },
    }

    ledger = build_requirement_ledger(canonical)
    assert {item["id"] for item in ledger} == {
        "package:rental-ops",
        "dependency:identity",
        "track:vehicles",
        "entry_type:vehicles:car",
        "view:vehicles:availability",
        "track_template:service",
        "command:mark_rented",
        "query:due_service",
        "skill:rental_assistant",
        "agent:ops",
    }
    dependency = next(item for item in ledger if item["id"] == "dependency:identity")
    assert dependency["minimum_version"] == "2.0.0"
    assert dependency["required"] is True


def test_definition_fingerprint_is_order_insensitive_for_object_keys():
    left = {"package": {"slug": "rental"}, "app": {"tracks": []}}
    right = {"app": {"tracks": []}, "package": {"slug": "rental"}}
    assert definition_fingerprint(left) == definition_fingerprint(right)


def test_local_overrides_preserve_base_and_effective_structural_divergence():
    base = {
        "content_profile_schema_version": 2,
        "scope": "app",
        "package": {"slug": "rental"},
        "app": {"tracks": [], "relations": [], "defaults": {}},
    }
    effective = {
        **base,
        "app": {
            "tracks": [{"key": "tenant-notes", "name": "Tenant notes"}],
            "relations": [],
            "defaults": {},
        },
    }

    overrides = derive_local_overrides(
        base_package_manifest=base,
        effective_manifest=effective,
    )

    assert overrides["base_manifest_fingerprint"] == definition_fingerprint(
        compile_canonical_manifest(manifest=base)
    )
    assert overrides["effective_manifest_fingerprint"] == definition_fingerprint(
        compile_canonical_manifest(manifest=effective)
    )
    added_track = overrides["structural_diff"]["tracks"]["added"][0]
    assert added_track["key"] == "tenant-notes"
    assert added_track["name"] == "Tenant notes"
    assert (
        derive_local_overrides(
            base_package_manifest=base,
            effective_manifest=base,
        )
        == {}
    )


def test_definition_preview_uses_business_labels_and_does_not_claim_zero_impact():
    before = {
        "content_profile_schema_version": 2,
        "scope": "app",
        "package": {"slug": "rental"},
        "app": {"tracks": [], "relations": [], "defaults": {}},
        "migrations": [],
    }
    candidate = {
        **before,
        "app": {
            "tracks": [{"key": "vehicles", "name": "Vehicles"}],
            "relations": [],
            "defaults": {},
            "operations": [{"key": "mark_rented", "name": "Mark rented"}],
        },
    }

    preview = preview_application_definition(
        before_manifest=before,
        candidate_manifest=candidate,
    )

    assert {change["label"] for change in preview["changes"]} == {
        "Vehicles",
        "Mark rented",
        "Feed",
    }
    assert {effect["subject_id"] for effect in preview["effects"]} == {
        "track:vehicles",
        "command:mark_rented",
        "view:vehicles:feed",
    }
    assert preview["affected_records"]["status"] == "not_evaluated"
    assert preview["affected_records"]["count"] is None


@pytest.mark.asyncio
async def test_blank_app_creation_binds_initial_local_definition():
    """Greenfield Apps use the same effective-definition seam as packages."""
    from app.models.edges import HAS_APPLICATION_DEFINITION, IS_MEMBER_OF
    from app.models.nodes import ApplicationDefinition, User
    from app.services.app_service import create_app_for_user
    from tests.fixtures.workspaces import make_org_workspace

    workspace = await make_org_workspace("definition-greenfield")
    owners = await workspace.nodes(
        edge=[IS_MEMBER_OF], direction="in", node=["User"], limit=1
    )
    owner = owners[0]
    assert isinstance(owner, User)

    app = await create_app_for_user(
        owner.id,
        "Greenfield Rentals",
        workspace_id=workspace.id,
    )

    definition = await ApplicationDefinition.get(app.active_definition_id)
    assert definition is not None
    assert definition.source_kind == "local"
    assert definition.source_profile_id
    attached = await app.nodes(
        edge=[HAS_APPLICATION_DEFINITION],
        direction="out",
        node=["ApplicationDefinition"],
    )
    assert [item.id for item in attached] == [definition.id]


@pytest.mark.asyncio
async def test_extension_view_runtime_uses_active_definition_not_unactivated_profile():
    """A mutable attached profile cannot alter a live extension surface by itself."""
    from app.models.edges import IS_MEMBER_OF
    from app.models.nodes import App, User
    from app.services.app_extension_views import _compiled_app_manifest
    from app.services.app_graph import get_app_attached_content_profile
    from app.services.app_service import create_app_for_user
    from tests.fixtures.workspaces import make_org_workspace

    workspace = await make_org_workspace("definition-extension-authority")
    owners = await workspace.nodes(
        edge=[IS_MEMBER_OF], direction="in", node=["User"], limit=1
    )
    owner = owners[0]
    assert isinstance(owner, User)
    app = await create_app_for_user(
        owner.id,
        "Definition Extension Authority",
        workspace_id=workspace.id,
    )
    attached = await get_app_attached_content_profile(app)
    assert attached is not None
    attached.manifest = {
        **(attached.manifest or {}),
        "app": {
            **((attached.manifest or {}).get("app") or {}),
            "extension_views": [
                {
                    "key": "unactivated-panel",
                    "name": "Unactivated panel",
                    "entry": "views/unactivated/index.html",
                }
            ],
        },
    }
    await attached.save()

    reloaded = await App.get(app.id)
    assert reloaded is not None
    canonical = await _compiled_app_manifest(reloaded)
    assert (canonical.get("app") or {}).get("extension_views") == []


@pytest.mark.asyncio
async def test_bundle_rehydration_uses_active_definition_not_unactivated_profile(
    monkeypatch,
):
    """A restart cannot register hooks or operations from a draft profile edit."""
    from app.models.edges import IS_MEMBER_OF
    from app.models.nodes import App, User
    from app.services.app_graph import get_app_attached_content_profile
    from app.services.app_service import create_app_for_user
    from app.services.hooks import install_hook
    from tests.fixtures.workspaces import make_org_workspace

    workspace = await make_org_workspace("definition-rehydration-authority")
    owners = await workspace.nodes(
        edge=[IS_MEMBER_OF], direction="in", node=["User"], limit=1
    )
    owner = owners[0]
    assert isinstance(owner, User)
    app = await create_app_for_user(
        owner.id,
        "Definition Rehydration Authority",
        workspace_id=workspace.id,
    )
    attached = await get_app_attached_content_profile(app)
    assert attached is not None
    attached.manifest = {
        **(attached.manifest or {}),
        "app": {
            **((attached.manifest or {}).get("app") or {}),
            "operations": [
                {"key": "unactivated_operation", "name": "Unactivated operation"}
            ],
        },
    }
    await attached.save()

    captured = []

    async def capture_registration(**kwargs):
        captured.append(kwargs["canonical"])

    async def only_this_app(_query):
        return [app]

    monkeypatch.setattr(App, "find", only_this_app)
    monkeypatch.setattr(
        install_hook, "register_bundle_on_install", capture_registration
    )

    await install_hook.rehydrate_all_installed_bundles()

    assert len(captured) == 1
    assert (captured[0].get("app") or {}).get("operations") == []


@pytest.mark.asyncio
async def test_run_snapshot_uses_active_definition_not_unactivated_profile():
    """Run receipts describe the contract that could actually execute."""
    from app.agentive.services.execution_runs import build_capability_snapshot
    from app.models.edges import IS_MEMBER_OF
    from app.models.nodes import User
    from app.services.app_graph import get_app_attached_content_profile
    from app.services.app_service import create_app_for_user
    from tests.fixtures.workspaces import make_org_workspace

    workspace = await make_org_workspace("definition-snapshot-authority")
    owners = await workspace.nodes(
        edge=[IS_MEMBER_OF], direction="in", node=["User"], limit=1
    )
    owner = owners[0]
    assert isinstance(owner, User)
    app = await create_app_for_user(
        owner.id,
        "Definition Snapshot Authority",
        workspace_id=workspace.id,
    )
    attached = await get_app_attached_content_profile(app)
    assert attached is not None
    attached.manifest = {
        **(attached.manifest or {}),
        "app": {
            **((attached.manifest or {}).get("app") or {}),
            "operations": [
                {"key": "unactivated_operation", "name": "Unactivated operation"}
            ],
        },
    }
    await attached.save()

    snapshot = await build_capability_snapshot(workspace.id)
    app_snapshot = next(item for item in snapshot["apps"] if item["app_id"] == app.id)
    assert app_snapshot["operations"] == []


@pytest.mark.asyncio
async def test_staging_exemption_uses_active_definition_not_unactivated_profile():
    """A profile draft cannot silently grant an agent write a staging bypass."""
    from app.agentive.unstaged_targets import _unstaged_track_keys
    from app.models.edges import IS_MEMBER_OF
    from app.models.nodes import User
    from app.services.app_graph import get_app_attached_content_profile
    from app.services.app_service import create_app_for_user
    from tests.fixtures.workspaces import make_org_workspace

    workspace = await make_org_workspace("definition-staging-authority")
    owners = await workspace.nodes(
        edge=[IS_MEMBER_OF], direction="in", node=["User"], limit=1
    )
    owner = owners[0]
    assert isinstance(owner, User)
    app = await create_app_for_user(
        owner.id,
        "Definition Staging Authority",
        workspace_id=workspace.id,
    )
    attached = await get_app_attached_content_profile(app)
    assert attached is not None
    attached.manifest = {
        **(attached.manifest or {}),
        "app": {
            **((attached.manifest or {}).get("app") or {}),
            "unstaged_tracks": ["unactivated-track"],
        },
    }
    await attached.save()

    assert await _unstaged_track_keys(app) == frozenset()


@pytest.mark.asyncio
async def test_uninstall_dependency_check_uses_active_definition_not_profile_draft():
    """A pending dependency declaration cannot block a live App uninstall."""
    from app.models.edges import IS_MEMBER_OF
    from app.models.nodes import User
    from app.services.app_graph import get_app_attached_content_profile
    from app.services.app_lifecycle import _check_uninstall_blockers
    from app.services.app_service import create_app_for_user
    from tests.fixtures.workspaces import make_org_workspace

    workspace = await make_org_workspace("definition-uninstall-authority")
    owners = await workspace.nodes(
        edge=[IS_MEMBER_OF], direction="in", node=["User"], limit=1
    )
    owner = owners[0]
    assert isinstance(owner, User)
    target = await create_app_for_user(
        owner.id,
        "Definition Uninstall Target",
        workspace_id=workspace.id,
    )
    dependent = await create_app_for_user(
        owner.id,
        "Definition Uninstall Dependent",
        workspace_id=workspace.id,
    )
    attached = await get_app_attached_content_profile(dependent)
    assert attached is not None
    attached.manifest = {
        **(attached.manifest or {}),
        "app": {
            **((attached.manifest or {}).get("app") or {}),
            "requires_apps": [{"key": target.name, "optional": False}],
        },
    }
    await attached.save()

    assert await _check_uninstall_blockers(target) == ([], [])
