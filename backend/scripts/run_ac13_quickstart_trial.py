#!/usr/bin/env python3
"""AC-13 quickstart trial — load external package, register, invoke modified echo.

Usage (from backend/):

  INTEGRAL_PACKAGE_PATHS=/path/to/packages INTEGRAL_CORE_ONLY=0 \\
    TESTING=1 .venv/bin/python scripts/run_ac13_quickstart_trial.py

Expects a package directory named ``trial-hello-app`` under INTEGRAL_PACKAGE_PATHS
(see docs/developer/quickstart-trial-log.md for scaffold steps).
"""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch


async def _run() -> None:
    pkg_root = (os.environ.get("INTEGRAL_PACKAGE_PATHS") or "").strip()
    if not pkg_root:
        print("INTEGRAL_PACKAGE_PATHS must be set", file=sys.stderr)
        sys.exit(2)

    from app.services.app_operations.dispatch import invoke_app_operation
    from app.services.hooks.install_hook import register_bundle_on_install
    from app.services.hooks.registry import clear_workspace_registrations
    from app.services.operational_model_loader import (
        load_library_operational_models_with_issues,
    )
    from app.services.operational_model_runtime import compile_canonical_manifest

    specs, issues = load_library_operational_models_with_issues(
        package_paths=[pkg_root],
        core_only=False,
        verify_signatures=False,
    )
    if issues:
        print("LOAD_ISSUES", issues, file=sys.stderr)
        sys.exit(1)
    spec = next((s for s in specs if s.slug == "trial-hello-app"), None)
    if spec is None:
        print("trial-hello-app not found under INTEGRAL_PACKAGE_PATHS", file=sys.stderr)
        sys.exit(1)

    canonical = compile_canonical_manifest(manifest=spec.manifest)
    ws = "ws-ac13-trial"
    app_id = "n.App.ac13-trial"
    await register_bundle_on_install(
        workspace_id=ws,
        canonical=canonical,
        bundle_dir=str(spec.bundle_dir),
        app_id=app_id,
    )

    async def _allow(*_a, **_k):
        from app.schemas.policy import Decision

        return Decision(allowed=True, reason="ac13-trial")

    with (
        patch(
            "app.services.app_operations.dispatch.policy_evaluate",
            new=AsyncMock(side_effect=_allow),
        ),
        patch(
            "app.services.app_operations.dispatch.App.get",
            new=AsyncMock(
                return_value=type(
                    "A",
                    (),
                    {"id": app_id, "workspace_id": ws, "lifecycle_state": "active"},
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
            user_id="u-ac13",
            workspace_id=ws,
            app_id=app_id,
            operation_key="echo",
            payload={"message": "independent-dev"},
        )

    output = result.get("output") or {}
    message = str(output.get("message") or "")
    if not message.startswith("trial:"):
        print("INVOKE_FAIL expected trial: prefix, got", message, file=sys.stderr)
        sys.exit(1)

    clear_workspace_registrations(ws)
    print("AC13_TRIAL_OK", message)


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
