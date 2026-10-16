"""Contract: AC-04 — direct, HTTP, and MCP share one operation dispatcher."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agentive.tooling.dispatch import dispatch_tool
from app.services.app_operations.dispatch import invoke_app_operation
from app.services.hooks.install_hook import register_bundle_on_install
from app.services.operational_model_runtime import compile_canonical_manifest

REPO = Path(__file__).resolve().parents[3]
REF_APP = REPO / "examples" / "reference-hello-app"
PAYLOAD = {"message": "shared-semantics"}


@pytest.fixture
def reference_root(monkeypatch):
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
async def test_direct_http_mcp_echo_operation_same_output(
    reference_root, test_user, authenticated_client, request
):
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
    user_id = test_user.id
    await register_bundle_on_install(
        workspace_id=ws,
        canonical=canonical,
        bundle_dir=str(reference_root),
        app_id=app_id,
    )

    direct = await invoke_app_operation(
        user_id=user_id,
        workspace_id=ws,
        app_id=app_id,
        operation_key="echo",
        payload=PAYLOAD,
    )

    resp = await authenticated_client.post(
        f"/api/extensions/{app_id}/operations/echo",
        json={"input": PAYLOAD},
        headers={"X-Integral-Scope": f"ws:{ws}"},
    )
    assert resp.status_code == 200, resp.text
    http = resp.json()

    mcp = await dispatch_tool(
        "integral_invoke_app_operation",
        {
            "app_id": app_id,
            "operation_key": "echo",
            "input": PAYLOAD,
        },
        principal_id=user_id,
        scope=ws,
    )

    assert direct["output"]["ok"] is True
    assert direct["output"]["message"] == PAYLOAD["message"]
    assert http["output"] == direct["output"]
    assert http["evidence"] is not None
    assert http["evidence"]["applied_scope"] == direct["evidence"]["applied_scope"]
    assert http["evidence"]["package_slug"] == direct["evidence"]["package_slug"]
    assert http["object_refs"] == direct["object_refs"]
    assert not mcp.is_error
    assert mcp.data["output"] == direct["output"]
