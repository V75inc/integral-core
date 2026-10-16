"""Contract: operation idempotency records (ADR-011)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.app_operations.dispatch import invoke_app_operation
from app.services.hooks.install_hook import register_bundle_on_install

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


@pytest.mark.contract
@pytest.mark.asyncio
async def test_idempotency_key_returns_cached_result(
    reference_root, test_user, request
):
    from app.services.operational_model_loader import (
        load_library_operational_models_with_issues,
    )
    from app.services.operational_model_runtime import compile_canonical_manifest

    specs, _ = load_library_operational_models_with_issues(
        package_paths=[str(reference_root.parent)],
        core_only=False,
        verify_signatures=False,
    )
    spec = next(s for s in specs if s.slug == "reference-hello-app")
    canonical = compile_canonical_manifest(manifest=spec.manifest)
    from tests.contract.operation_fixtures import owned_operation_app

    workspace, app = await owned_operation_app(test_user, request)
    ws, app_id = workspace.id, app.id
    await register_bundle_on_install(
        workspace_id=ws,
        canonical=canonical,
        bundle_dir=str(reference_root),
        app_id=app_id,
    )

    first = await invoke_app_operation(
        user_id=test_user.id,
        workspace_id=ws,
        app_id=app_id,
        operation_key="echo",
        payload={"message": "idem"},
        idempotency_key="k-1",
    )
    second = await invoke_app_operation(
        user_id=test_user.id,
        workspace_id=ws,
        app_id=app_id,
        operation_key="echo",
        payload={"message": "idem"},
        idempotency_key="k-1",
    )
    assert first == second
    assert first["output"]["message"] == "idem"
