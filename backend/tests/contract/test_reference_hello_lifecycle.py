"""F0 contract: reference-hello-app install → upgrade → pause → uninstall."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.install_attempt import InstallAttempt
from app.models.nodes import App, ContentProfile
from app.services.app_lifecycle import (
    install_app,
    pause_app,
    resume_app,
    uninstall_app,
    update_app_from_library,
)
from app.services.content_profile_loader import load_library_profiles_with_issues
from app.services.hooks.registry import get_workspace_hooks
from app.utils.time import utc_now_iso
from tests.fixtures.workspaces import make_org_workspace

REPO = Path(__file__).resolve().parents[3]
REF_APP = REPO / "examples" / "reference-hello-app"


async def _seed_reference_library_cp() -> ContentProfile:
    specs, _ = load_library_profiles_with_issues(
        package_paths=[str(REF_APP.parent)],
        core_only=False,
        verify_signatures=False,
    )
    spec = next(s for s in specs if s.slug == "reference-hello-app")
    now = utc_now_iso()
    return await ContentProfile.create(
        name=spec.name or "Reference Hello",
        scope="app",
        manifest=spec.manifest,
        library_package=True,
        version=spec.version or "1.0.0",
        metadata={
            "slug": spec.slug,
            "bundle_fingerprint": getattr(spec, "bundle_fingerprint", "") or "test-fp",
            "package_class": spec.package_class,
            "bundle_dir": str(spec.bundle_dir) if spec.bundle_dir else str(REF_APP),
        },
        created_at=now,
        updated_at=now,
    )


@pytest.mark.contract
@pytest.mark.asyncio
async def test_reference_hello_app_lifecycle_e2e(monkeypatch):
    assert REF_APP.is_dir()
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(REF_APP.parent))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")

    ws = await make_org_workspace("ws-hello-contract")
    actor_id = "u_contract_hello"
    lib = await _seed_reference_library_cp()

    installed = await install_app(
        workspace_id=ws.id,
        library_cp_id=lib.id,
        actor_id=actor_id,
        include_seed_data=False,
    )
    assert installed["status"] == "active"
    app_id = installed["app_id"]
    app = await App.get(app_id)
    assert app is not None
    assert app.lifecycle_state == "active"
    assert app.installed_package_slug == "reference-hello-app"
    assert app.installed_package_version

    attempts = await InstallAttempt.find({"app_id": app_id})
    if not attempts:
        attempts = await InstallAttempt.find({"context.app_id": app_id})
    assert attempts, "expected InstallAttempt checkpoints during install"
    assert any(str(getattr(a, "step", "") or "") for a in attempts)

    hooks = get_workspace_hooks(ws.id, "entry.create")
    assert any(h.get("key") == "note_created" for h in hooks)

    upgraded = await update_app_from_library(app_id=app_id, actor_id=actor_id)
    assert upgraded["app_id"] == app_id
    assert "version_after" in upgraded

    paused = await pause_app(app_id=app_id, actor_id=actor_id)
    assert paused["status"] == "paused"
    assert not any(
        h.get("key") == "note_created" for h in get_workspace_hooks(ws.id, "entry.create")
    )

    resumed = await resume_app(app_id=app_id, actor_id=actor_id)
    assert resumed["status"] == "active"

    out = await uninstall_app(app_id=app_id, actor_id=actor_id, archive=True)
    assert out.get("status") == "uninstalled" or out.get("app_id") == app_id
    app2 = await App.get(app_id)
    assert app2 is not None
    assert app2.lifecycle_state == "uninstalled"
    assert not any(
        h.get("key") == "note_created" for h in get_workspace_hooks(ws.id, "entry.create")
    )
