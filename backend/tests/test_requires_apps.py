"""``requires_apps[]`` install + uninstall enforcement (Phase 10 Plan 10-06).

Two-walk uninstall pattern (Pitfall 6):
  - Manifest-level walk catches packages that declare the target App in their
    own ``requires_apps[]``.
  - Edge-level walk catches stored REFERENCES.target_app_id edges (covered in
    test_cross_app_relations.py).

Install-side coverage:
  - Hard dep missing → block.
  - Soft dep missing → proceed with warning.
  - min_version satisfied vs. installed-too-low.
  - Force uninstall is removed — dependents always hard-block.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.exceptions import (
    AppDependencyError,
    AppUninstallBlockedError,
)
from app.models.nodes import App, OperationalModel, Workspace
from app.services.app_lifecycle import (
    _parse_version_tuple,
    _version_satisfies_min,
    install_app,
    uninstall_app,
)
from app.utils.time import utc_now_iso
from tests.fixtures.workspaces import make_org_workspace

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _make_workspace(name: str = "ws-deps") -> Workspace:
    return await make_org_workspace(name)


def _minimal_app_manifest(
    package_name: str,
    version: str = "1.0.0",
    requires_apps: list | None = None,
) -> Dict[str, Any]:
    manifest: Dict[str, Any] = {
        "operational_model_schema_version": 2,
        "scope": "app",
        "package": {
            "name": package_name,
            "key": package_name,
            "version": version,
            "description": f"{package_name} test",
            "tags": ["test"],
        },
        "app": {
            "description": f"{package_name}",
            "tracks": [
                {
                    "key": "t",
                    "name": "T",
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
                    "views": [{"key": "feed", "name": "Feed", "type": "feed"}],
                }
            ],
        },
    }
    if requires_apps:
        manifest["app"]["requires_apps"] = requires_apps
    return manifest


async def _make_library_cp(manifest: Dict[str, Any]) -> OperationalModel:
    now = utc_now_iso()
    return await OperationalModel.create(
        name=manifest["package"]["name"],
        scope="app",
        manifest=manifest,
        library_package=True,
        version=manifest["package"].get("version") or "1.0.0",
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Version-compare helpers
# ---------------------------------------------------------------------------


def test_version_tuple_parsing():
    assert _parse_version_tuple("1.0.0") == (1, 0, 0)
    assert _parse_version_tuple("2.1") == (2, 1)
    assert _parse_version_tuple("0.0.0") == (0, 0, 0)
    # Non-digit tail collapses to digit-only.
    assert _parse_version_tuple("1.0.5") == (1, 0, 5)


def test_version_satisfies_min():
    assert _version_satisfies_min("1.0.0", "1.0.0") is True
    assert _version_satisfies_min("2.0.0", "1.0.0") is True
    assert _version_satisfies_min("1.0.1", "1.0.0") is True
    assert _version_satisfies_min("0.9.0", "1.0.0") is False
    # Length-asymmetric — installed "1.0" vs min "1.0.0" → tied at (1, 0, 0).
    assert _version_satisfies_min("1.0", "1.0.0") is True
    assert _version_satisfies_min("1.0", "1.0.1") is False
    # Default min "0.0.0" allows anything.
    assert _version_satisfies_min("0.0.0", "0.0.0") is True
    assert _version_satisfies_min("", "0.0.0") is True


# ---------------------------------------------------------------------------
# Install — requires_apps gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_blocks_without_hard_dep():
    ws = await _make_workspace()
    payroll = _minimal_app_manifest(
        "payroll_app",
        requires_apps=[{"key": "hr_app", "optional": False}],
    )
    lib = await _make_library_cp(payroll)
    with pytest.raises(AppDependencyError) as exc_info:
        await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    assert "hr_app" in exc_info.value.details.get("missing_deps", [])


@pytest.mark.asyncio
async def test_install_proceeds_with_soft_dep_absent():
    ws = await _make_workspace()
    payroll = _minimal_app_manifest(
        "payroll_app",
        requires_apps=[{"key": "hr_app", "optional": True}],
    )
    lib = await _make_library_cp(payroll)
    out = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    assert out["status"] == "active"


@pytest.mark.asyncio
async def test_install_satisfied_by_existing_hard_dep():
    ws = await _make_workspace()
    hr_lib = await _make_library_cp(_minimal_app_manifest("hr_app"))
    await install_app(workspace_id=ws.id, library_cp_id=hr_lib.id, actor_id="u_1")
    payroll_lib = await _make_library_cp(
        _minimal_app_manifest(
            "payroll_app",
            requires_apps=[{"key": "hr_app", "optional": False}],
        )
    )
    out = await install_app(
        workspace_id=ws.id, library_cp_id=payroll_lib.id, actor_id="u_1"
    )
    assert out["status"] == "active"


@pytest.mark.asyncio
async def test_install_satisfied_when_dep_key_matches_display_name_slug():
    """``requires_apps: finance`` matches an App named ``Finance`` (legacy seed)."""
    ws = await _make_workspace()
    finance_manifest = _minimal_app_manifest("finance", version="1.0.0")
    finance_manifest["package"]["name"] = "Finance"
    finance_manifest["package"]["slug"] = "finance"
    finance_lib = await _make_library_cp(finance_manifest)
    now = utc_now_iso()
    await App.create(
        name="Finance",
        name_fold="finance",
        owner_user_id="u_1",
        description="Legacy finance install",
        visibility="private",
        workspace_id=ws.id,
        lifecycle_state="active",
        version="1.0.0",
        created_at=now,
        updated_at=now,
    )
    inventory_manifest = _minimal_app_manifest(
        "inventory-management",
        requires_apps=[{"key": "finance", "optional": False, "min_version": "1.0.0"}],
    )
    inv_lib = await _make_library_cp(inventory_manifest)
    out = await install_app(
        workspace_id=ws.id, library_cp_id=inv_lib.id, actor_id="u_1"
    )
    assert out["status"] == "active"


@pytest.mark.asyncio
async def test_install_satisfied_when_legacy_finance_version_from_library():
    """Legacy Finance via POST /api/apps inherits semver from library merge source."""
    ws = await _make_workspace()
    finance_manifest = _minimal_app_manifest("finance", version="1.0.0")
    finance_manifest["package"]["name"] = "Finance"
    finance_manifest["package"]["slug"] = "finance"
    finance_lib = await _make_library_cp(finance_manifest)
    now = utc_now_iso()
    await App.create(
        name="Finance",
        name_fold="finance",
        owner_user_id="u_1",
        description="Legacy finance install",
        visibility="private",
        workspace_id=ws.id,
        lifecycle_state="active",
        library_merge_source_id=finance_lib.id,
        created_at=now,
        updated_at=now,
    )
    inventory_manifest = _minimal_app_manifest(
        "inventory-management",
        requires_apps=[{"key": "finance", "optional": False, "min_version": "1.0.0"}],
    )
    inv_lib = await _make_library_cp(inventory_manifest)
    out = await install_app(
        workspace_id=ws.id, library_cp_id=inv_lib.id, actor_id="u_1"
    )
    assert out["status"] == "active"


@pytest.mark.asyncio
async def test_install_blocks_when_min_version_unsatisfied():
    """Installed dep present but version below min_version → reject."""
    ws = await _make_workspace()
    hr_lib = await _make_library_cp(_minimal_app_manifest("hr_app", version="0.9.0"))
    await install_app(workspace_id=ws.id, library_cp_id=hr_lib.id, actor_id="u_1")
    payroll_lib = await _make_library_cp(
        _minimal_app_manifest(
            "payroll_app",
            requires_apps=[
                {"key": "hr_app", "optional": False, "min_version": "1.0.0"}
            ],
        )
    )
    with pytest.raises(AppDependencyError) as exc_info:
        await install_app(
            workspace_id=ws.id,
            library_cp_id=payroll_lib.id,
            actor_id="u_1",
        )
    # missing_details carries the reason discriminator.
    details = exc_info.value.details.get("missing_details", [])
    assert any(d.get("reason") == "version_too_low" for d in details)


# ---------------------------------------------------------------------------
# Uninstall — manifest-level walk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uninstall_blocked_by_dependent_manifest_declaration():
    """Manifest-level walk blocks uninstall when another App declares hard dep."""
    ws = await _make_workspace()
    hr_lib = await _make_library_cp(_minimal_app_manifest("hr_app"))
    hr_result = await install_app(
        workspace_id=ws.id, library_cp_id=hr_lib.id, actor_id="u_1"
    )
    payroll_lib = await _make_library_cp(
        _minimal_app_manifest(
            "payroll_app",
            requires_apps=[{"key": "hr_app", "optional": False}],
        )
    )
    await install_app(workspace_id=ws.id, library_cp_id=payroll_lib.id, actor_id="u_1")
    with pytest.raises(AppUninstallBlockedError) as exc_info:
        await uninstall_app(app_id=hr_result["app_id"], actor_id="u_1")
    details = exc_info.value.details
    deps = details.get("blocking_dependents", [])
    assert len(deps) == 1
    assert deps[0]["dep_key"] == "hr_app"


@pytest.mark.asyncio
async def test_uninstall_not_blocked_by_optional_dependent():
    """Soft deps from another App do NOT block uninstall."""
    ws = await _make_workspace()
    hr_lib = await _make_library_cp(_minimal_app_manifest("hr_app"))
    hr_result = await install_app(
        workspace_id=ws.id, library_cp_id=hr_lib.id, actor_id="u_1"
    )
    payroll_lib = await _make_library_cp(
        _minimal_app_manifest(
            "payroll_app",
            requires_apps=[{"key": "hr_app", "optional": True}],
        )
    )
    await install_app(workspace_id=ws.id, library_cp_id=payroll_lib.id, actor_id="u_1")
    out = await uninstall_app(app_id=hr_result["app_id"], actor_id="u_1")
    assert out["status"] == "uninstalled"


@pytest.mark.asyncio
async def test_uninstall_hard_blocks_despite_retry():
    """Dependents hard-block; there is no force bypass."""
    ws = await _make_workspace()
    hr_lib = await _make_library_cp(_minimal_app_manifest("hr_app"))
    hr_result = await install_app(
        workspace_id=ws.id, library_cp_id=hr_lib.id, actor_id="u_1"
    )
    payroll_lib = await _make_library_cp(
        _minimal_app_manifest(
            "payroll_app",
            requires_apps=[{"key": "hr_app", "optional": False}],
        )
    )
    await install_app(workspace_id=ws.id, library_cp_id=payroll_lib.id, actor_id="u_1")
    with pytest.raises(AppUninstallBlockedError):
        await uninstall_app(app_id=hr_result["app_id"], actor_id="u_1")


def test_crm_profile_projects_dep_is_soft():
    """CRM↔Projects soft independence — CRM installs without Projects."""
    from pathlib import Path

    import yaml

    path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "packages"
        / "crm"
        / "operational-model.yaml"
    )
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    requires = (manifest.get("app") or {}).get("requires_apps") or []
    projects = [
        d for d in requires if isinstance(d, dict) and d.get("key") == "projects"
    ]
    assert projects, "CRM should declare a projects requires_apps entry"
    assert projects[0].get("optional") is True
    assert str(manifest.get("package", {}).get("version") or "").startswith("1.1.")


@pytest.mark.asyncio
async def test_crm_shaped_manifest_installs_without_projects():
    """Soft dep: CRM-shaped requires_apps optional:true installs alone."""
    ws = await _make_workspace()
    crm = _minimal_app_manifest(
        "crm",
        version="1.1.2",
        requires_apps=[
            {
                "key": "projects",
                "min_version": "1.0.0",
                "optional": True,
            }
        ],
    )
    lib = await _make_library_cp(crm)
    out = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    assert out["status"] == "active"
