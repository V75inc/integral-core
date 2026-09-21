"""Contract: paused apps reject typed operation invoke (WP-04 lifecycle)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.api.errors import BadRequestError
from app.services.app_operations.dispatch import invoke_app_operation
from app.services.hooks.install_hook import register_bundle_on_install
from app.services.hooks.registry import clear_workspace_registrations
from app.services.operational_model_runtime import compile_canonical_manifest

REPO = Path(__file__).resolve().parents[3]
REF_APP = REPO / "examples" / "reference-hello-app"


@pytest.fixture
def reference_root(monkeypatch):
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(REF_APP.parent))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")
    from app.services.operational_model_library_sync import (
        reset_library_operational_models_cache_for_testing,
    )

    reset_library_operational_models_cache_for_testing()
    yield REF_APP
    reset_library_operational_models_cache_for_testing()
    clear_workspace_registrations("ws-paused-op")


@pytest.mark.contract
@pytest.mark.asyncio
async def test_paused_app_rejects_operation_invoke(reference_root):
    from app.services.operational_model_loader import (
        load_library_operational_models_with_issues,
    )

    specs, _ = load_library_operational_models_with_issues(
        package_paths=[str(reference_root.parent)],
        core_only=False,
        verify_signatures=False,
    )
    spec = next(s for s in specs if s.slug == "reference-hello-app")
    canonical = compile_canonical_manifest(manifest=spec.manifest)
    ws = "ws-paused-op"
    app_id = "n.App.paused"

    await register_bundle_on_install(
        workspace_id=ws,
        canonical=canonical,
        bundle_dir=str(reference_root),
        app_id=app_id,
    )

    paused_app = type(
        "AppStub",
        (),
        {
            "id": app_id,
            "workspace_id": ws,
            "lifecycle_state": "paused",
        },
    )()

    with (
        patch(
            "app.services.app_operations.dispatch.App.get",
            new=AsyncMock(return_value=paused_app),
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
        with pytest.raises(BadRequestError, match="not active"):
            await invoke_app_operation(
                user_id="u1",
                workspace_id=ws,
                app_id=app_id,
                operation_key="echo",
                payload={"message": "hi"},
            )
