"""F0 extension-contract tests — external reference App (no Core code change)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.services.content_profile_loader import load_library_profiles_with_issues
from app.services.content_profile_runtime import compile_canonical_manifest
from app.services.hooks.install_hook import (
    register_bundle_on_install,
    unregister_bundle_on_uninstall,
)
from app.services.hooks.registry import (
    clear_workspace_registrations,
    get_workspace_hooks,
)
from app.services.package_paths import resolve_package_class, should_include_package

REPO = Path(__file__).resolve().parents[3]
REF_APP = REPO / "examples" / "reference-hello-app"


@pytest.fixture
def reference_root(monkeypatch):
    assert REF_APP.is_dir(), f"missing reference app at {REF_APP}"
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(REF_APP.parent))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")
    from app.services.content_profile_library_sync import (
        reset_library_profiles_cache_for_testing,
    )

    reset_library_profiles_cache_for_testing()
    yield REF_APP
    reset_library_profiles_cache_for_testing()
    clear_workspace_registrations("ws-contract-hello")


@pytest.mark.contract
def test_reference_hello_app_loads_from_external_path(reference_root):
    specs, issues = load_library_profiles_with_issues(
        package_paths=[reference_root.parent],
        core_only=False,
        verify_signatures=False,
    )
    by_slug = {s.slug: s for s in specs}
    assert "reference-hello-app" in by_slug
    spec = by_slug["reference-hello-app"]
    assert spec.package_class == "community_app"
    assert resolve_package_class(slug=spec.slug, declared="community_app") == (
        "community_app"
    )


@pytest.mark.contract
def test_reference_hello_app_compiles_and_registers(reference_root):
    specs, _ = load_library_profiles_with_issues(
        package_paths=[str(reference_root.parent)],
        core_only=False,
        verify_signatures=False,
    )
    spec = next(s for s in specs if s.slug == "reference-hello-app")
    canonical = compile_canonical_manifest(manifest=spec.manifest)
    assert canonical["app"]["tools"]
    assert canonical["app"]["hooks"]
    assert canonical["app"]["operations"]
    assert any(h.get("key") == "note_created" for h in canonical["app"]["hooks"])


@pytest.mark.contract
@pytest.mark.asyncio
async def test_reference_hello_app_hook_registration_roundtrip(reference_root):
    specs, _ = load_library_profiles_with_issues(
        package_paths=[str(reference_root.parent)],
        core_only=False,
        verify_signatures=False,
    )
    spec = next(s for s in specs if s.slug == "reference-hello-app")
    canonical = compile_canonical_manifest(manifest=spec.manifest)
    ws = "ws-contract-hello"
    await register_bundle_on_install(
        ws,
        canonical,
        bundle_dir=str(spec.bundle_dir) if spec.bundle_dir else None,
    )
    hooks = get_workspace_hooks(ws, "entry.create")
    assert any(h.get("key") == "note_created" for h in hooks)
    await unregister_bundle_on_uninstall(ws, "reference-hello-app")
    assert not any(
        h.get("key") == "note_created" for h in get_workspace_hooks(ws, "entry.create")
    )


@pytest.mark.core_only
def test_core_only_excludes_reference_app(monkeypatch):
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "1")
    monkeypatch.setenv(
        "INTEGRAL_PACKAGE_PATHS",
        str(REPO / "backend" / "app" / "profiles"),
    )
    from app.services.content_profile_library_sync import (
        reset_library_profiles_cache_for_testing,
    )

    reset_library_profiles_cache_for_testing()
    specs, _ = load_library_profiles_with_issues(
        core_only=True, verify_signatures=False
    )
    slugs = {s.slug for s in specs}
    assert "agent-scratch" in slugs
    assert "personal-context" not in slugs
    assert "crm" not in slugs
    assert "payroll-app" not in slugs
    assert should_include_package(
        slug="agent-scratch", package_class="core_package", core_only=True
    )
    assert not should_include_package(
        slug="personal-context", package_class="commercial_app", core_only=True
    )
    assert not should_include_package(
        slug="crm", package_class="community_app", core_only=True
    )
    os.environ.pop("INTEGRAL_CORE_ONLY", None)
    reset_library_profiles_cache_for_testing()
