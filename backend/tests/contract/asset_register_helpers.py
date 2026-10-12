"""Shared helpers for asset-register contract tests."""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict

from app.models.nodes import OperationalModel
from app.services.operational_model_loader import (
    load_library_operational_models_with_issues,
)
from tests.fixtures.library_models import publish_library_spec

REPO = Path(__file__).resolve().parents[3]
ASSET_APP = REPO / "examples" / "asset-register"


def load_asset_register_spec(*, bundle_dir: Path | None = None):
    bundle_dir = bundle_dir or ASSET_APP
    specs, _ = load_library_operational_models_with_issues(
        package_paths=[str(bundle_dir.parent)],
        core_only=False,
        verify_signatures=False,
    )
    return next(s for s in specs if s.slug == "asset-register")


async def seed_asset_register_library_cp(
    *,
    version: str = "1.0.0",
    manifest_override: Dict[str, Any] | None = None,
    bundle_dir: Path | None = None,
) -> OperationalModel:
    spec = load_asset_register_spec(bundle_dir=bundle_dir)
    manifest = copy.deepcopy(spec.manifest)
    if manifest_override:
        manifest.update(manifest_override)
    manifest.setdefault("package", {})
    manifest["package"]["version"] = version
    return await publish_library_spec(
        replace(
            spec,
            manifest=manifest,
            version=version,
            bundle_fingerprint=spec.bundle_fingerprint or "test-fp",
            bundle_dir=spec.bundle_dir or bundle_dir or ASSET_APP,
        )
    )


async def grant_asset_operator(*, actor_id: str, app_id: str):
    """Explicit fixture grant; installing or owning an App grants no function authority."""
    from app.services.access_delegation import create_binding

    return await create_binding(
        actor_id=actor_id,
        app_id=app_id,
        subject_kind="human",
        subject_id=actor_id,
        role="asset-operator",
        conditions=[],
    )


async def create_asset_entry(ctx, **kwargs):
    """Create fixture data through one exact declared initialization permit."""
    from app.services.app_operation_authority import operation_permit

    await grant_asset_operator(actor_id=ctx.user_id, app_id=ctx.app_id)
    async with operation_permit(
        principal_id=ctx.user_id,
        workspace_id=ctx.workspace_id,
        app_id=ctx.app_id,
        operation_key="register_asset",
        payload={"assets_track_id": kwargs["track_id"]},
    ):
        return await ctx.create_entry(**kwargs)


async def run_authorized_asset_tool(tool_spec, payload, ctx):
    """Keep handler-level contracts while admitting explicit current exact targets."""
    from app.services.app_operation_authority import operation_permit
    from app.services.app_operations.context import OperationContext
    from app.services.hooks.registry import get_workspace_tools
    from app.services.hooks.tool_dispatch import run_tool

    key = tool_spec["key"]
    if key not in {"register_asset", "check_out_asset", "check_in_asset"}:
        return await run_tool(tool_spec, payload, ctx)
    await grant_asset_operator(actor_id=ctx.user_id, app_id=ctx.app_id)
    ctx.operation_key = key
    readonly = OperationContext(
        user_id=ctx.user_id,
        workspace_id=ctx.workspace_id,
        scope=ctx.scope,
        app_id=ctx.app_id,
        operation_key=key,
        read_only=True,
    )
    resolved = await run_tool(
        get_workspace_tools(ctx.workspace_id)["asset_register_targets"],
        payload,
        readonly,
    )
    body = {**payload, **resolved}
    async with operation_permit(
        principal_id=ctx.user_id,
        workspace_id=ctx.workspace_id,
        app_id=ctx.app_id,
        operation_key=key,
        payload=body,
    ):
        return await run_tool(tool_spec, body, ctx)
