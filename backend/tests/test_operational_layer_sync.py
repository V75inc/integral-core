"""Operational layer sync — skills/agents/hooks after merge and library update."""

from __future__ import annotations

import pytest

from app.agentive.services.skill_registry import get_skill_by_key
from app.models.nodes import App, ApplicationDefinition
from app.services.app_lifecycle import (
    sync_operational_layer_from_manifest,
    update_app_from_library,
)
from app.services.hooks.registry import get_workspace_hooks, get_workspace_tools
from app.services.operational_model_runtime import compile_canonical_manifest
from app.utils.time import utc_now_iso
from tests.test_app_lifecycle import (
    _make_library_cp,
    _make_workspace,
    _minimal_app_manifest,
    install_app,
)


def _manifest_with_skill(*, slug: str, skill_key: str, with_tool: bool = False) -> dict:
    manifest = _minimal_app_manifest(package_name=slug, version="1.0.0")
    manifest["package"]["slug"] = slug
    app = manifest.setdefault("app", {})
    app["skills"] = [
        {
            "key": skill_key,
            "name": skill_key.replace("_", " ").title(),
            "kind": "declarative",
            "prompt_template": f"skills/{skill_key}/SKILL.md",
        }
    ]
    if with_tool:
        manifest["package"]["trust_tier"] = "trusted"
        app["tools"] = [
            {
                "key": "example_tool",
                "handler_ref": "tools.example:run",
                "parameters_schema": {"type": "object"},
                "output_schema": {"type": "object"},
            }
        ]
        app["hooks"] = [
            {
                "point": "entry.precompute",
                "key": "example_hook",
                "mode": "tool",
                "tool": "example_tool",
            }
        ]
    return manifest


@pytest.mark.asyncio
async def test_sync_operational_layer_upserts_and_removes_stale_skills():
    ws = await _make_workspace()
    now = utc_now_iso()
    app = await App.create(
        name="Sync App",
        name_fold="sync app",
        workspace_id=ws.id,
        lifecycle_state="active",
        created_at=now,
        updated_at=now,
    )
    canonical_v1 = compile_canonical_manifest(
        manifest=_manifest_with_skill(slug="sync-app", skill_key="alpha")
    )
    await sync_operational_layer_from_manifest(app, canonical_v1, actor_id="u_sync")
    assert await get_skill_by_key(app.id, "alpha") is not None

    canonical_v2 = compile_canonical_manifest(
        manifest=_manifest_with_skill(slug="sync-app", skill_key="beta")
    )
    await sync_operational_layer_from_manifest(app, canonical_v2, actor_id="u_sync")
    assert await get_skill_by_key(app.id, "alpha") is None
    assert await get_skill_by_key(app.id, "beta") is not None


@pytest.mark.asyncio
async def test_sync_operational_layer_registers_bundle_tools():
    ws = await _make_workspace()
    now = utc_now_iso()
    app = await App.create(
        name="Hook App",
        name_fold="hook app",
        workspace_id=ws.id,
        lifecycle_state="active",
        created_at=now,
        updated_at=now,
    )
    canonical = compile_canonical_manifest(
        manifest=_manifest_with_skill(
            slug="hook-app", skill_key="hooked", with_tool=True
        )
    )
    await sync_operational_layer_from_manifest(app, canonical, actor_id="u_hook")
    tools = get_workspace_tools(ws.id)
    assert "example_tool" in tools
    hooks = get_workspace_hooks(ws.id, "entry.precompute")
    assert any(h.get("key") == "example_hook" for h in hooks)


@pytest.mark.asyncio
async def test_update_from_library_syncs_skills():
    ws = await _make_workspace()
    slug = "upd-skills-app"
    manifest_v1 = _manifest_with_skill(slug=slug, skill_key="v1_skill")
    lib = await _make_library_cp(manifest_v1)
    result = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    app_id = result["app_id"]
    assert await get_skill_by_key(app_id, "v1_skill") is not None

    manifest_v2 = _manifest_with_skill(slug=slug, skill_key="v2_skill")
    lib.manifest = manifest_v2
    await lib.save()

    await update_app_from_library(app_id=app_id, actor_id="u_1")
    assert await get_skill_by_key(app_id, "v1_skill") is None
    assert await get_skill_by_key(app_id, "v2_skill") is not None
    app_after = await App.get(app_id)
    assert app_after is not None
    definition = await ApplicationDefinition.get(app_after.active_definition_id)
    assert definition is not None
    evidence = {
        item["requirement_id"]: item for item in definition.materialization_evidence
    }
    assert evidence["skill:v2_skill"]["status"] == "verified"


@pytest.mark.asyncio
async def test_sync_skill_nodes_clears_stub_body_override():
    from scripts.sync_skill_nodes_from_disk import _heal_skill_node

    ws = await _make_workspace()
    now = utc_now_iso()
    app = await App.create(
        name="Stub Skill App",
        name_fold="stub skill app",
        workspace_id=ws.id,
        source_operational_model_slug="inventory-management",
        lifecycle_state="active",
        created_at=now,
        updated_at=now,
    )
    canonical = compile_canonical_manifest(
        manifest=_manifest_with_skill(
            slug="inventory-management", skill_key="issue_stock"
        )
    )
    canonical["app"]["skills"][0]["description"] = "Issues stock internally."
    await sync_operational_layer_from_manifest(app, canonical, actor_id="u_stub")
    skill = await get_skill_by_key(app.id, "issue_stock")
    assert skill is not None
    skill.body_override = (
        "> User: run this workflow for the item/asset named in the request.\n"
    )
    skill.description = ""
    await skill.save()

    changed, reasons = await _heal_skill_node(
        skill,
        manifest_spec=canonical["app"]["skills"][0],
        dry_run=False,
    )
    assert changed
    assert "body_override:clear_stub" in reasons
    skill = await get_skill_by_key(app.id, "issue_stock")
    assert skill.body_override is None
    assert skill.description == "Issues stock internally."
