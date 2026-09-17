"""Contract: denied operations fail closed (AC-06, WP-08)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.api.errors import InsufficientPermissionsError
from app.schemas.policy import Decision
from app.services.app_operations.dispatch import invoke_app_operation
from app.services.content_profile_runtime import compile_canonical_manifest
from app.services.hooks.install_hook import register_bundle_on_install
from app.services.hooks.registry import clear_workspace_registrations

REPO = Path(__file__).resolve().parents[3]
REF_APP = REPO / "examples" / "reference-hello-app"


@pytest.fixture
def reference_root(monkeypatch):
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(REF_APP.parent))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")
    from app.services.content_profile_library_sync import (
        reset_library_profiles_cache_for_testing,
    )

    reset_library_profiles_cache_for_testing()
    yield REF_APP
    reset_library_profiles_cache_for_testing()
    clear_workspace_registrations("ws-policy")


@pytest.mark.contract
@pytest.mark.asyncio
async def test_denied_policy_blocks_operation_invoke(reference_root):
    from app.services.content_profile_loader import load_library_profiles_with_issues

    specs, _ = load_library_profiles_with_issues(
        package_paths=[str(reference_root.parent)],
        core_only=False,
        verify_signatures=False,
    )
    spec = next(s for s in specs if s.slug == "reference-hello-app")
    canonical = compile_canonical_manifest(manifest=spec.manifest)
    ws = "ws-policy"
    app_id = "n.App.policy"

    await register_bundle_on_install(
        workspace_id=ws,
        canonical=canonical,
        bundle_dir=str(reference_root),
        app_id=app_id,
    )

    async def _deny(*_a, **_k):
        return Decision(allowed=False, reason="contract-deny")

    app_stub = type(
        "AppStub",
        (),
        {"id": app_id, "workspace_id": ws, "lifecycle_state": "active"},
    )()

    with (
        patch(
            "app.services.app_operations.dispatch.policy_evaluate",
            new=AsyncMock(side_effect=_deny),
        ),
        patch(
            "app.services.app_operations.dispatch.App.get",
            new=AsyncMock(return_value=app_stub),
        ),
        patch(
            "app.services.app_operations.dispatch.resolve_role",
            new=AsyncMock(return_value="owner"),
        ),
        patch(
            "app.services.app_operations.dispatch.can_access_workspace",
            new=AsyncMock(return_value="owner"),
        ),
    ):
        with pytest.raises(InsufficientPermissionsError):
            await invoke_app_operation(
                user_id="u1",
                workspace_id=ws,
                app_id=app_id,
                operation_key="echo",
                payload={"message": "nope"},
            )
