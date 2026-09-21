"""App install / settings / uninstall lifecycle regression suite.

Phase 10 Plan 10-05 (APP-LIFECYCLE-01 / APP-SETTINGS-01 / APP-SEEDS-01).

Covers the lifecycle state machine end-to-end:
- Install transaction (12 steps per app_bundles_v1.md §9.1).
- Awaiting-settings pause + finalize_install resume.
- Token replay rejection (T-10-05-02).
- Pause / resume.
- Uninstall (normal + force + archive paths).
- Single-emission D-05 invariant (each lifecycle action emits exactly ONE
  ChangeEvent).
- Reaper compensates abandoned awaiting_settings Apps past TTL.
- Update-from-library re-merge.

Tests use the same hand-rolled minimal manifest pattern as other Phase 10
tests (build a ContentProfile library row directly via Node CRUD; no
seeded-package coupling).
"""

from __future__ import annotations

import os
from typing import Any, Dict

import pytest

from app.exceptions import (
    AppDependencyError,
    AppInstallTokenExpiredError,
    AppInstallTokenInvalidError,
    AppLifecycleStateError,
    AppUninstallBlockedError,
    BadRequestError,
    ContentProfileValidationError,
)
from app.models.edges import CONTAINS, HAS_APPLICATION_DEFINITION
from app.models.nodes import (
    App,
    ApplicationDefinition,
    ContentProfile,
    Entry,
    EntryType,
    Track,
    User,
    Workspace,
)
from app.services.app_install_reaper import run_reaper_pass
from app.services.app_install_token import (
    issue_install_token,
    verify_install_token,
)
from app.services.app_lifecycle import (
    InstallTransaction,
    finalize_install,
    install_app,
    pause_app,
    resume_app,
    uninstall_app,
    update_app_from_library,
    update_app_settings,
)
from app.utils.time import utc_now_iso
from tests.fixtures.workspaces import make_org_workspace

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_workspace(name: str = "WS Test") -> Workspace:
    return await make_org_workspace(name)


def _minimal_app_manifest(
    package_name: str = "test-app",
    version: str = "1.0.0",
    with_settings_schema: bool = False,
    with_seeds: bool = False,
    requires_apps: list | None = None,
) -> Dict[str, Any]:
    """Build a minimal v2 app-scope manifest for install tests.

    Avoids the full seeded-package dependency graph so test failures point
    at the lifecycle, not at incidental seeded-package shape.
    """
    manifest: Dict[str, Any] = {
        "content_profile_schema_version": 2,
        "scope": "app",
        "package": {
            "name": package_name,
            "key": package_name,
            "version": version,
            "description": f"Test app {package_name}",
            "tags": ["test"],
        },
        "app": {
            "description": f"Description for {package_name}",
            "tracks": [
                {
                    "key": "demo_track",
                    "name": "Demo Track",
                    "provision_on_create": True,
                    "entry_types": [
                        {
                            "key": "note",
                            "name": "Note",
                            "fields": [
                                {"key": "body", "name": "Body", "type": "markdown"},
                            ],
                        },
                    ],
                    "defaults": {"default_entry_type": "note"},
                    "views": [
                        {"key": "feed", "name": "Feed", "type": "feed"},
                    ],
                },
            ],
        },
    }
    if with_settings_schema:
        manifest["app"]["settings_schema"] = {
            "type": "object",
            "properties": {
                "publish_cadence": {
                    "type": "string",
                    "enum": ["weekly", "daily"],
                    "default": "weekly",
                },
            },
            "required": ["publish_cadence"],
        }
    if with_seeds:
        manifest["app"]["seeds"] = [
            {
                "track": "demo_track",
                "entries": [
                    {
                        "title": "Welcome",
                        "body": "Hello",
                        "tags": [],
                        "custom_fields": {"kind": "intro"},
                    },
                    {
                        "title": "Second",
                        "body": "World",
                        "tags": ["greeting"],
                        "custom_fields": {},
                    },
                ],
            }
        ]
    if requires_apps:
        manifest["app"]["requires_apps"] = requires_apps
    return manifest


async def _make_library_cp(manifest: Dict[str, Any]) -> ContentProfile:
    """Create a library-package ContentProfile row carrying ``manifest``."""
    now = utc_now_iso()
    return await ContentProfile.create(
        name=manifest["package"]["name"],
        scope="app",
        manifest=manifest,
        library_package=True,
        version=manifest["package"].get("version") or "1.0.0",
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# InstallTransaction primitive — unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_transaction_compensates_in_reverse_order():
    """Compensations run in reverse, and a failing compensation doesn't block later ones."""
    txn = InstallTransaction(workspace_id="ws_1", actor_id="u_1")
    log: list[str] = []

    async def step1():
        log.append("comp1")

    async def step2():
        log.append("comp2")
        raise RuntimeError("step2 compensation explodes")

    async def step3():
        log.append("comp3")

    txn.record("step1", step1)
    txn.record("step2", step2)
    txn.record("step3", step3)

    n = await txn.compensate()
    # All three ran (step2 caught its exception); reverse order.
    assert log == ["comp3", "comp2", "comp1"]
    # Count reflects successful compensations only.
    assert n == 2


@pytest.mark.asyncio
async def test_install_transaction_empty_compensate_is_noop():
    txn = InstallTransaction(workspace_id="ws", actor_id="u")
    n = await txn.compensate()
    assert n == 0


# ---------------------------------------------------------------------------
# Install — happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_with_no_settings_schema_completes_immediately():
    ws = await _make_workspace()
    manifest = _minimal_app_manifest(package_name="basic-app")
    lib = await _make_library_cp(manifest)

    result = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    assert result["status"] == "active"
    assert result["app_id"]
    assert result["installed_at"]
    assert result["version"] == "1.0.0"

    # Persisted state.
    app = await App.get(result["app_id"])
    assert app is not None
    assert app.lifecycle_state == "active"
    assert app.installed_from_library_id == lib.id
    assert app.version == "1.0.0"
    assert app.active_definition_id
    definition = await ApplicationDefinition.get(app.active_definition_id)
    assert definition is not None
    assert definition.status == "active"
    assert definition.revision == 1
    assert any(item["kind"] == "package" for item in definition.requirement_ledger)
    evidence_by_requirement = {
        item["requirement_id"]: item for item in definition.materialization_evidence
    }
    assert evidence_by_requirement["package:basic-app"]["status"] == "verified"
    assert evidence_by_requirement["track:demo_track"]["status"] == "verified"
    assert evidence_by_requirement["entry_type:demo_track:note"]["status"] == "verified"
    assert evidence_by_requirement["view:demo_track:feed"]["status"] == "verified"
    assert definition.verified_at
    attached = await app.nodes(
        edge=[HAS_APPLICATION_DEFINITION],
        direction="out",
        node=["ApplicationDefinition"],
    )
    assert [item.id for item in attached] == [definition.id]


@pytest.mark.asyncio
async def test_install_with_settings_schema_pauses_at_step_9():
    ws = await _make_workspace()
    manifest = _minimal_app_manifest(
        package_name="settings-app", with_settings_schema=True
    )
    lib = await _make_library_cp(manifest)

    result = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    assert result["status"] == "awaiting_settings"
    assert result["install_token"]
    assert "publish_cadence" in (result["settings_schema"] or {}).get("properties", {})

    app = await App.get(result["app_id"])
    assert app.lifecycle_state == "awaiting_settings"


@pytest.mark.asyncio
async def test_install_with_settings_pre_supplied_skips_pause():
    ws = await _make_workspace()
    manifest = _minimal_app_manifest(
        package_name="pre-supplied-app", with_settings_schema=True
    )
    lib = await _make_library_cp(manifest)

    result = await install_app(
        workspace_id=ws.id,
        library_cp_id=lib.id,
        actor_id="u_1",
        settings={"publish_cadence": "weekly"},
    )
    assert result["status"] == "active"
    app = await App.get(result["app_id"])
    assert app.settings == {"publish_cadence": "weekly"}


# ---------------------------------------------------------------------------
# Finalize install — resume from awaiting_settings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_install_with_valid_token_completes():
    ws = await _make_workspace()
    manifest = _minimal_app_manifest(
        package_name="resume-ok", with_settings_schema=True
    )
    lib = await _make_library_cp(manifest)

    first = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    assert first["status"] == "awaiting_settings"

    second = await finalize_install(
        app_id=first["app_id"],
        install_token=first["install_token"],
        settings={"publish_cadence": "daily"},
        actor_id="u_1",
    )
    assert second["status"] == "active"
    app = await App.get(first["app_id"])
    assert app.lifecycle_state == "active"
    assert app.settings == {"publish_cadence": "daily"}


@pytest.mark.asyncio
async def test_resume_install_with_expired_token_rejected():
    ws = await _make_workspace()
    manifest = _minimal_app_manifest(
        package_name="resume-expired", with_settings_schema=True
    )
    lib = await _make_library_cp(manifest)

    first = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    # Mint a fresh expired token for this app_id, replacing the in-flight one.
    expired = issue_install_token(first["app_id"], ttl_hours=-1 / 3600.0)
    with pytest.raises(AppInstallTokenExpiredError):
        await finalize_install(
            app_id=first["app_id"],
            install_token=expired,
            settings={"publish_cadence": "weekly"},
            actor_id="u_1",
        )


@pytest.mark.asyncio
async def test_resume_install_replay_rejected_after_active():
    """Token replay against an already-active App raises AppLifecycleStateError."""
    ws = await _make_workspace()
    manifest = _minimal_app_manifest(
        package_name="replay-app", with_settings_schema=True
    )
    lib = await _make_library_cp(manifest)

    first = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    await finalize_install(
        app_id=first["app_id"],
        install_token=first["install_token"],
        settings={"publish_cadence": "weekly"},
        actor_id="u_1",
    )
    # Replay the same token after the App is active.
    with pytest.raises(AppLifecycleStateError):
        await finalize_install(
            app_id=first["app_id"],
            install_token=first["install_token"],
            settings={"publish_cadence": "daily"},
            actor_id="u_1",
        )


@pytest.mark.asyncio
async def test_resume_install_with_invalid_settings_rejected():
    ws = await _make_workspace()
    manifest = _minimal_app_manifest(
        package_name="bad-settings", with_settings_schema=True
    )
    lib = await _make_library_cp(manifest)
    first = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    # publish_cadence must be one of ["weekly", "daily"]
    with pytest.raises(ContentProfileValidationError):
        await finalize_install(
            app_id=first["app_id"],
            install_token=first["install_token"],
            settings={"publish_cadence": "monthly"},
            actor_id="u_1",
        )


# ---------------------------------------------------------------------------
# Settings post-install
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_round_trip():
    ws = await _make_workspace()
    manifest = _minimal_app_manifest(
        package_name="settings-rt", with_settings_schema=True
    )
    lib = await _make_library_cp(manifest)
    first = await install_app(
        workspace_id=ws.id,
        library_cp_id=lib.id,
        actor_id="u_1",
        settings={"publish_cadence": "weekly"},
    )
    out = await update_app_settings(
        app_id=first["app_id"],
        settings={"publish_cadence": "daily"},
        actor_id="u_1",
    )
    assert out["settings"] == {"publish_cadence": "daily"}
    app = await App.get(first["app_id"])
    assert app.settings == {"publish_cadence": "daily"}


@pytest.mark.asyncio
async def test_settings_validation_against_schema():
    ws = await _make_workspace()
    manifest = _minimal_app_manifest(
        package_name="settings-validate", with_settings_schema=True
    )
    lib = await _make_library_cp(manifest)
    first = await install_app(
        workspace_id=ws.id,
        library_cp_id=lib.id,
        actor_id="u_1",
        settings={"publish_cadence": "weekly"},
    )
    with pytest.raises(ContentProfileValidationError):
        await update_app_settings(
            app_id=first["app_id"],
            settings={"publish_cadence": "monthly"},
            actor_id="u_1",
        )


def test_apply_schema_defaults_fills_missing_and_preserves_caller_values():
    """apply_schema_defaults backfills declared defaults for absent keys only;
    caller-supplied values (even falsy ones) always win. A required key WITHOUT
    a default is left absent, so validation still rejects it."""
    from app.services.app_install import (
        apply_schema_defaults,
        validate_settings_against_schema,
    )

    schema = {
        "type": "object",
        "properties": {
            "cadence": {
                "type": "string",
                "enum": ["weekly", "daily"],
                "default": "weekly",
            },
            "platforms": {"type": "array", "default": ["instagram"], "minItems": 1},
            "note": {"type": "string"},  # no default
            "needs_value": {"type": "string"},  # required, no default
        },
        "required": ["cadence", "platforms", "needs_value"],
    }

    # Empty input → both defaulted keys filled; non-defaulted keys stay absent.
    filled = apply_schema_defaults({}, schema)
    assert filled == {"cadence": "weekly", "platforms": ["instagram"]}

    # Caller value wins over the default; only the missing default is added.
    filled2 = apply_schema_defaults({"cadence": "daily"}, schema)
    assert filled2 == {"cadence": "daily", "platforms": ["instagram"]}

    # Required key with NO default is not fabricated → validation still fails.
    with pytest.raises(ContentProfileValidationError):
        validate_settings_against_schema(filled, schema)

    # Supplying the no-default required key passes.
    ok = dict(filled)
    ok["needs_value"] = "x"
    validate_settings_against_schema(ok, schema)  # no raise


# ---------------------------------------------------------------------------
# Seeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seeds_planted_on_install():
    ws = await _make_workspace()
    manifest = _minimal_app_manifest(package_name="seeds-app", with_seeds=True)
    lib = await _make_library_cp(manifest)
    result = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    app = await App.get(result["app_id"])
    tracks = await app.nodes(edge=[CONTAINS], node=["Track"])
    assert len(tracks) >= 1
    demo = tracks[0]
    entries = await Entry.find({"track_id": demo.id})
    assert len(entries) == 2
    titles = {e.title for e in entries}
    assert titles == {"Welcome", "Second"}
    assert all(e.type_id for e in entries)


@pytest.mark.asyncio
async def test_seeds_repair_mistyped_entries_on_replant():
    """Re-planting upgrades legacy untyped seed rows to the track EntryType."""
    from app.services.app_lifecycle import _plant_seeds
    from app.services.content_profile_runtime import compile_canonical_manifest
    from app.services.entry_type_resolver import (
        resolve_seed_entry_type_id as _resolve_seed_entry_type_id,
    )

    ws = await _make_workspace()
    manifest = _minimal_app_manifest(package_name="seeds-retype", with_seeds=True)
    lib = await _make_library_cp(manifest)
    result = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    app = await App.get(result["app_id"])
    track = (await app.nodes(edge=[CONTAINS], node=["Track"]))[0]
    canonical = compile_canonical_manifest(manifest=manifest)
    expected_type_id = await _resolve_seed_entry_type_id(
        track,
        "demo_track",
        canonical,
        {"title": "Welcome"},
    )
    assert expected_type_id
    legacy = (await Entry.find({"track_id": track.id}))[0]
    legacy.type_id = ""
    await legacy.save()

    await _plant_seeds(app, canonical, "u_1")
    refreshed = await Entry.get(legacy.id)
    assert refreshed is not None
    assert refreshed.type_id == expected_type_id


@pytest.mark.asyncio
async def test_install_idempotent_when_bundle_already_active():
    ws = await _make_workspace()
    manifest = _minimal_app_manifest(
        package_name="idem-app",
        with_settings_schema=True,
        with_seeds=False,
    )
    manifest["package"]["slug"] = "idem-app"
    lib = await _make_library_cp(manifest)
    first = await install_app(
        workspace_id=ws.id,
        library_cp_id=lib.id,
        actor_id="u_1",
        settings={"publish_cadence": "weekly"},
    )
    assert first["status"] == "active"
    second = await install_app(
        workspace_id=ws.id,
        library_cp_id=lib.id,
        actor_id="u_1",
        settings={"publish_cadence": "weekly"},
    )
    assert second["status"] == "active"
    assert second["app_id"] == first["app_id"]
    apps = await App.find({"workspace_id": ws.id})
    assert len([a for a in apps if a.lifecycle_state != "uninstalled"]) == 1


@pytest.mark.asyncio
async def test_install_purges_duplicate_bundle_apps():
    """Re-installing a bundle removes stray duplicate Apps in the workspace."""
    ws = await _make_workspace()
    manifest = _minimal_app_manifest(
        package_name="Fixed Asset Management",
        with_seeds=False,
    )
    manifest["package"]["slug"] = "fixed-asset-management"
    lib = await _make_library_cp(manifest)
    now = utc_now_iso()
    for _ in range(3):
        await App.create(
            name="Fixed Asset Management",
            name_fold="fixed asset management",
            workspace_id=ws.id,
            lifecycle_state="active",
            owner_user_id="u_1",
            created_at=now,
            updated_at=now,
        )

    result = await install_app(
        workspace_id=ws.id,
        library_cp_id=lib.id,
        actor_id="u_1",
    )
    assert result["status"] == "active"

    apps = await App.find({"workspace_id": ws.id})
    active = [a for a in apps if a.lifecycle_state != "uninstalled"]
    assert len(active) == 1
    assert active[0].id == result["app_id"]


@pytest.mark.asyncio
async def test_install_idempotent_replants_and_repairs_seed_types():
    """Re-installing an active bundle re-runs seed planting and fixes entry types."""
    ws = await _make_workspace()
    manifest = _minimal_app_manifest(package_name="replant-app", with_seeds=True)
    manifest["package"]["slug"] = "replant-app"
    lib = await _make_library_cp(manifest)
    first = await install_app(
        workspace_id=ws.id,
        library_cp_id=lib.id,
        actor_id="u_1",
    )
    app = await App.get(first["app_id"])
    track = (await app.nodes(edge=[CONTAINS], node=["Track"]))[0]
    legacy = (await Entry.find({"track_id": track.id}))[0]
    post_types = await EntryType.find({"context.track_id": track.id})
    post = next(
        (et for et in post_types if (et.name or "").casefold() == "post"),
        None,
    )
    if post is None:
        now = utc_now_iso()
        post = await EntryType.create(
            name="Post",
            track_id=track.id,
            created_at=now,
            updated_at=now,
        )
    legacy.type_id = post.id
    await legacy.save()

    second = await install_app(
        workspace_id=ws.id,
        library_cp_id=lib.id,
        actor_id="u_1",
    )
    assert second["app_id"] == first["app_id"]
    refreshed = await Entry.get(legacy.id)
    assert refreshed is not None
    assert refreshed.type_id != post.id
    assert refreshed.type_id


@pytest.mark.asyncio
async def test_seeds_idempotent_via_deterministic_id():
    """Re-planting seeds onto the same App is idempotent (no duplicates)."""
    from app.services.app_lifecycle import _plant_seeds
    from app.services.content_profile_runtime import compile_canonical_manifest

    ws = await _make_workspace()
    manifest = _minimal_app_manifest(package_name="seeds-idemp", with_seeds=True)
    lib = await _make_library_cp(manifest)
    result = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    app = await App.get(result["app_id"])
    canonical = compile_canonical_manifest(manifest=manifest)
    # Call _plant_seeds again — should plant zero new entries.
    n = await _plant_seeds(app, canonical, "u_1")
    assert n == 0


# ---------------------------------------------------------------------------
# Pause / Resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_then_resume():
    ws = await _make_workspace()
    manifest = _minimal_app_manifest(package_name="pause-resume")
    lib = await _make_library_cp(manifest)
    result = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    paused = await pause_app(app_id=result["app_id"], actor_id="u_1")
    assert paused["status"] == "paused"
    app = await App.get(result["app_id"])
    assert app.lifecycle_state == "paused"
    resumed = await resume_app(app_id=result["app_id"], actor_id="u_1")
    assert resumed["status"] == "active"


@pytest.mark.asyncio
async def test_pause_rejects_non_active_state():
    ws = await _make_workspace()
    manifest = _minimal_app_manifest(
        package_name="pause-bad", with_settings_schema=True
    )
    lib = await _make_library_cp(manifest)
    # App is in awaiting_settings, not active.
    result = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    with pytest.raises(AppLifecycleStateError):
        await pause_app(app_id=result["app_id"], actor_id="u_1")


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uninstall_archives_by_default():
    ws = await _make_workspace()
    manifest = _minimal_app_manifest(package_name="uninstall-archive")
    lib = await _make_library_cp(manifest)
    result = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    out = await uninstall_app(app_id=result["app_id"], actor_id="u_1")
    assert out["status"] == "uninstalled"
    assert out["archived"] is True
    # App row should still exist with lifecycle_state="uninstalled".
    app = await App.get(result["app_id"])
    assert app is not None
    assert app.lifecycle_state == "uninstalled"


@pytest.mark.asyncio
async def test_force_uninstall_emits_force_action():
    """Force uninstall returns force_uninstalled status; emits app.force_uninstalled."""
    ws = await _make_workspace()
    manifest = _minimal_app_manifest(package_name="uninstall-force")
    lib = await _make_library_cp(manifest)
    result = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    out = await uninstall_app(app_id=result["app_id"], actor_id="u_1", force=True)
    assert out["status"] == "force_uninstalled"
    assert out["archived"] is False


@pytest.mark.asyncio
async def test_force_uninstall_in_awaiting_settings_emits_with_state_in_details():
    """Force-uninstall during awaiting_settings is the same code path as normal force."""
    ws = await _make_workspace()
    manifest = _minimal_app_manifest(package_name="force-aw", with_settings_schema=True)
    lib = await _make_library_cp(manifest)
    result = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    assert result["status"] == "awaiting_settings"
    out = await uninstall_app(app_id=result["app_id"], actor_id="u_1", force=True)
    assert out["status"] == "force_uninstalled"


@pytest.mark.asyncio
async def test_uninstall_blocked_by_hard_dep():
    """Installing a dependent then uninstalling the dependency without force is blocked."""
    ws = await _make_workspace()
    dep_manifest = _minimal_app_manifest(package_name="dep-app")
    dep_lib = await _make_library_cp(dep_manifest)
    dependent_manifest = _minimal_app_manifest(
        package_name="dependent-app",
        requires_apps=[{"key": "dep-app", "optional": False}],
    )
    dependent_lib = await _make_library_cp(dependent_manifest)

    dep_result = await install_app(
        workspace_id=ws.id, library_cp_id=dep_lib.id, actor_id="u_1"
    )
    dependent_result = await install_app(
        workspace_id=ws.id, library_cp_id=dependent_lib.id, actor_id="u_1"
    )
    assert dependent_result["status"] == "active"

    # Uninstall dep without force — should be blocked.
    with pytest.raises(AppUninstallBlockedError):
        await uninstall_app(app_id=dep_result["app_id"], actor_id="u_1")
    # With force — succeeds.
    out = await uninstall_app(app_id=dep_result["app_id"], actor_id="u_1", force=True)
    assert out["status"] == "force_uninstalled"


# ---------------------------------------------------------------------------
# Install — dep gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_blocked_by_missing_hard_dep():
    ws = await _make_workspace()
    dependent_manifest = _minimal_app_manifest(
        package_name="needs-missing",
        requires_apps=[{"key": "absent-app", "optional": False}],
    )
    dependent_lib = await _make_library_cp(dependent_manifest)
    with pytest.raises(AppDependencyError):
        await install_app(
            workspace_id=ws.id, library_cp_id=dependent_lib.id, actor_id="u_1"
        )


@pytest.mark.asyncio
async def test_install_soft_dep_missing_proceeds():
    """Soft (optional=True) deps generate a warning but do not block install."""
    ws = await _make_workspace()
    manifest = _minimal_app_manifest(
        package_name="soft-dep",
        requires_apps=[{"key": "absent-app", "optional": True}],
    )
    lib = await _make_library_cp(manifest)
    out = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    assert out["status"] == "active"


# ---------------------------------------------------------------------------
# Reaper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_awaiting_settings_reaper_compensates_abandoned(monkeypatch):
    """Reaper sweeps abandoned awaiting_settings Apps past TTL."""
    # Make TTL very small so the reaper pass treats our fresh App as
    # abandoned. We set 0 hours = anything older than now triggers a reap.
    monkeypatch.setenv("APP_INSTALL_TOKEN_TTL_HOURS", "0")

    ws = await _make_workspace()
    manifest = _minimal_app_manifest(package_name="reap-me", with_settings_schema=True)
    lib = await _make_library_cp(manifest)
    result = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    assert result["status"] == "awaiting_settings"

    # Reaper sweep.
    n = await run_reaper_pass()
    assert n >= 1

    # App row deleted (force=True → hard delete via cascade).
    app = await App.get(result["app_id"])
    assert app is None or app.lifecycle_state != "awaiting_settings"


@pytest.mark.asyncio
async def test_reaper_leaves_fresh_apps_alone(monkeypatch):
    """Reaper with default TTL ignores fresh awaiting_settings Apps."""
    monkeypatch.setenv("APP_INSTALL_TOKEN_TTL_HOURS", "24")  # 24h — fresh apps safe

    ws = await _make_workspace()
    manifest = _minimal_app_manifest(
        package_name="not-yet-abandoned", with_settings_schema=True
    )
    lib = await _make_library_cp(manifest)
    result = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    # Reaper sweep should NOT touch this fresh App.
    n = await run_reaper_pass()
    assert n == 0
    app = await App.get(result["app_id"])
    assert app.lifecycle_state == "awaiting_settings"


# ---------------------------------------------------------------------------
# Update-from-library
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_from_library_bumps_version():
    """Update-from-library re-merges and updates the version."""
    ws = await _make_workspace()
    manifest_v1 = _minimal_app_manifest(package_name="upd-app", version="1.0.0")
    lib = await _make_library_cp(manifest_v1)
    result = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    assert result["version"] == "1.0.0"

    # Bump the library's manifest version in place + re-merge.
    new_manifest = _minimal_app_manifest(package_name="upd-app", version="2.0.0")
    lib.manifest = new_manifest
    lib.version = "2.0.0"
    await lib.save()

    update_result = await update_app_from_library(
        app_id=result["app_id"], actor_id="u_1"
    )
    assert update_result["version_before"] == "1.0.0"
    assert update_result["version_after"] == "2.0.0"


# ---------------------------------------------------------------------------
# Legacy-track adoption (June 30 review R1)
# ---------------------------------------------------------------------------


def _divergent_key_manifest(package_name: str = "legacy-adopt-app") -> Dict[str, Any]:
    """Manifest whose entry-type key does NOT slug from its display name.

    Mirrors the Content Factory shape (key ``source_material``, name
    ``Source``) that broke seed planting against tracks materialized before
    ``_manifest_entry_type_key`` embedding existed.
    """
    manifest = _minimal_app_manifest(package_name=package_name, with_seeds=True)
    tspec = manifest["app"]["tracks"][0]
    tspec["entry_types"] = [
        {
            "key": "source_material",
            "name": "Source",
            "fields": [{"key": "tldr", "name": "TL;DR", "type": "text"}],
        }
    ]
    tspec["defaults"] = {"default_entry_type": "source_material"}
    return manifest


async def _strip_manifest_keys_for_track(track_id: str) -> int:
    """Simulate pre-manifest-key-era EntryTypes on a track."""
    stripped = 0
    for et in await EntryType.find({"context.track_id": track_id}):
        fs = dict(et.form_schema or {})
        if fs.pop("_manifest_entry_type_key", None) is not None:
            et.form_schema = fs
            await et.save()
            stripped += 1
    return stripped


@pytest.mark.asyncio
async def test_seed_resolver_falls_back_to_manifest_name_for_legacy_types():
    """Seed planting resolves legacy (keyless) EntryTypes via manifest name."""
    from app.services.content_profile_runtime import compile_canonical_manifest
    from app.services.entry_type_resolver import resolve_seed_entry_type_id

    ws = await _make_workspace()
    manifest = _divergent_key_manifest("legacy-resolve-app")
    lib = await _make_library_cp(manifest)
    result = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    app = await App.get(result["app_id"])
    track = (await app.nodes(edge=[CONTAINS], node=["Track"]))[0]

    assert await _strip_manifest_keys_for_track(track.id) >= 1

    canonical = compile_canonical_manifest(manifest=manifest)
    type_id = await resolve_seed_entry_type_id(
        track,
        "demo_track",
        canonical,
        {"title": "Welcome", "entry_type": "source_material"},
        require=True,
    )
    assert type_id, "name fallback must resolve legacy keyless EntryType"


@pytest.mark.asyncio
async def test_rematerialize_backfills_manifest_key_on_adopted_entry_types():
    """Re-running track materialization heals keyless legacy EntryTypes."""
    from app.services.app_install import materialize_tracks_for_app

    ws = await _make_workspace()
    manifest = _divergent_key_manifest("legacy-heal-app")
    lib = await _make_library_cp(manifest)
    result = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    app = await App.get(result["app_id"])
    track = (await app.nodes(edge=[CONTAINS], node=["Track"]))[0]

    assert await _strip_manifest_keys_for_track(track.id) >= 1

    # Re-materialize (what a reinstall/update does) — adopt path must
    # backfill the manifest key instead of leaving the node keyless.
    await materialize_tracks_for_app(app, "u_1")
    keys = {
        (et.form_schema or {}).get("_manifest_entry_type_key")
        for et in await EntryType.find({"context.track_id": track.id})
    }
    assert "source_material" in keys
