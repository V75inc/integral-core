"""Contract: AC-04 — direct, HTTP, and MCP share one operation dispatcher."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.agentive.tooling.dispatch import dispatch_tool
from app.services.app_operations.dispatch import invoke_app_operation
from app.services.content_profile_runtime import compile_canonical_manifest
from app.services.hooks.install_hook import register_bundle_on_install
from app.services.hooks.registry import clear_workspace_registrations

REPO = Path(__file__).resolve().parents[3]
REF_APP = REPO / "examples" / "reference-hello-app"
PAYLOAD = {"message": "shared-semantics"}


@pytest.fixture
def reference_root(monkeypatch):
    assert REF_APP.is_dir()
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(REF_APP.parent))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")
    from app.services.content_profile_library_sync import (
        reset_library_profiles_cache_for_testing,
    )

    reset_library_profiles_cache_for_testing()
    yield REF_APP
    reset_library_profiles_cache_for_testing()
    clear_workspace_registrations("ws-op-shared")


def _policy_patches():
    async def _allow(*_a, **_k):
        from app.schemas.policy import Decision

        return Decision(allowed=True, reason="contract-test")

    app_stub = type(
        "AppStub",
        (),
        {
            "id": "n.App.contract-shared",
            "workspace_id": "ws-op-shared",
            "lifecycle_state": "active",
        },
    )()
    return (
        patch(
            "app.services.app_operations.dispatch.policy_evaluate",
            new=AsyncMock(side_effect=_allow),
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
        patch(
            "app.api.app_extensions.resolve_workspace_id_from_request",
            new=AsyncMock(return_value="ws-op-shared"),
        ),
    )


@pytest.mark.contract
@pytest.mark.asyncio
async def test_direct_http_mcp_echo_operation_same_output(
    reference_root, test_user, authenticated_client
):
    from app.services.content_profile_loader import load_library_profiles_with_issues

    specs, _ = load_library_profiles_with_issues(
        package_paths=[str(reference_root.parent)],
        core_only=False,
        verify_signatures=False,
    )
    spec = next(s for s in specs if s.slug == "reference-hello-app")
    canonical = compile_canonical_manifest(manifest=spec.manifest)
    ws = "ws-op-shared"
    app_id = "n.App.contract-shared"
    user_id = test_user.id
    await register_bundle_on_install(
        workspace_id=ws,
        canonical=canonical,
        bundle_dir=str(reference_root),
        app_id=app_id,
    )

    patches = _policy_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
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
