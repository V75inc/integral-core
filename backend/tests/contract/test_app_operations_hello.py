"""Contract: typed app operations invoke (ADR-011)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.app_operations.dispatch import (
    invoke_app_operation,
)
from app.services.hooks.install_hook import register_bundle_on_install
from app.services.operational_model_runtime import compile_canonical_manifest

REPO = Path(__file__).resolve().parents[3]
REF_APP = REPO / "examples" / "reference-hello-app"


@pytest.fixture
def reference_root(monkeypatch):
    """Expose the reference package with a fresh runtime registration state."""
    assert REF_APP.is_dir()
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
async def test_invoke_echo_operation_via_tool_binding(
    reference_root, test_user, request
):
    """A declared operation returns its result and authorization provenance."""
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
    from tests.contract.operation_fixtures import owned_operation_app

    workspace, app = await owned_operation_app(test_user, request)
    ws, app_id = workspace.id, app.id
    await register_bundle_on_install(
        workspace_id=ws,
        canonical=canonical,
        bundle_dir=str(reference_root),
        app_id=app_id,
    )

    result = await invoke_app_operation(
        user_id=test_user.id,
        workspace_id=ws,
        app_id=app_id,
        operation_key="echo",
        payload={"message": "hello-ops"},
    )
    assert result["output"]["ok"] is True
    assert result["output"]["message"] == "hello-ops"
    assert result["evidence"]["policy_revision"].startswith("policy-sha256:")
