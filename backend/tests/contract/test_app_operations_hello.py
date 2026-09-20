"""Contract: typed app operations invoke (ADR-011)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.app_operations.dispatch import (
    invoke_app_operation,
)
from app.services.content_profile_runtime import compile_canonical_manifest
from app.services.hooks.install_hook import register_bundle_on_install
from app.services.hooks.registry import clear_workspace_registrations

REPO = Path(__file__).resolve().parents[3]
REF_APP = REPO / "examples" / "reference-hello-app"


@pytest.fixture
def reference_root(monkeypatch):
    """Expose the reference package with a fresh runtime registration state."""
    assert REF_APP.is_dir()
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(REF_APP.parent))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")
    from app.services.content_profile_library_sync import (
        reset_library_profiles_cache_for_testing,
    )

    reset_library_profiles_cache_for_testing()
    yield REF_APP
    reset_library_profiles_cache_for_testing()
    clear_workspace_registrations("ws-op-hello")


@pytest.mark.contract
@pytest.mark.asyncio
async def test_invoke_echo_operation_via_tool_binding(reference_root):
    """A declared operation returns its result and authorization provenance."""
    from app.services.content_profile_loader import load_library_profiles_with_issues

    specs, _ = load_library_profiles_with_issues(
        package_paths=[str(reference_root.parent)],
        core_only=False,
        verify_signatures=False,
    )
    spec = next(s for s in specs if s.slug == "reference-hello-app")
    canonical = compile_canonical_manifest(manifest=spec.manifest)
    ws = "ws-op-hello"
    app_id = "n.App.contract-hello"
    await register_bundle_on_install(
        workspace_id=ws,
        canonical=canonical,
        bundle_dir=str(reference_root),
        app_id=app_id,
    )

    # Policy gate uses resolve_role — stub by using a workspace owner path is heavy;
    # register + invoke with monkeypatched policy for contract slice.
    async def _allow(*_args: object, **_kwargs: object):
        from app.schemas.policy import Decision

        return Decision(allowed=True, reason="contract-test")

    from unittest.mock import AsyncMock, patch

    with (
        patch(
            "app.services.app_operations.dispatch.policy_evaluate",
            new=AsyncMock(side_effect=_allow),
        ),
        patch(
            "app.services.app_operations.dispatch.App.get",
            new=AsyncMock(
                return_value=type(
                    "AppStub",
                    (),
                    {
                        "id": app_id,
                        "workspace_id": ws,
                        "lifecycle_state": "active",
                    },
                )()
            ),
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
        result = await invoke_app_operation(
            user_id="u1",
            workspace_id=ws,
            app_id=app_id,
            operation_key="echo",
            payload={"message": "hello-ops"},
        )
    assert result["output"]["ok"] is True
    assert result["output"]["message"] == "hello-ops"
    assert result["evidence"]["policy_revision"].startswith("policy-sha256:")
