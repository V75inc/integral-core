"""Every App hard-delete path must tear down its bundle registrations.

The hook + tool registry (``services/hooks/registry``) is in-process and
per-workspace. ``uninstall_app`` unregisters, but two other paths delete Apps
without it, so the registry kept dispatching for a deleted App until restart:

* ``workspace_lifecycle.delete_workspace_cascade`` — called
  ``delete_app_cascade`` directly.
* ``api/apps.delete_app`` non-library branch — likewise, and it is reachable
  with LIVE registrations because ``sync_operational_layer_from_manifest``
  registers bundles for Apps carrying no ``installed_from_library_id``.

Both now route through ``app_lifecycle.purge_app_with_bundle_teardown``, which
resolves the bundle slug BEFORE the cascade strips the attached OperationalModel
(afterwards the slug resolves to "" and the unregister silently no-ops).
"""

from __future__ import annotations

from typing import Any, Dict

import pytest
from httpx import AsyncClient

from app.models.nodes import App
from app.services.app_graph import ensure_app_attached_operational_model
from app.services.app_lifecycle import sync_operational_layer_from_manifest
from app.services.hooks.registry import get_workspace_hooks, get_workspace_tools
from app.services.operational_model_runtime import compile_canonical_manifest
from app.utils.time import utc_now_iso
from tests.test_app_lifecycle import _make_workspace, _minimal_app_manifest


def _bundle_manifest(*, slug: str, tool_key: str) -> Dict[str, Any]:
    """A trusted app manifest declaring one tool + one hook binding."""
    manifest = _minimal_app_manifest(package_name=slug, version="1.0.0")
    manifest["package"]["slug"] = slug
    manifest["package"]["trust_tier"] = "trusted"
    app_block = manifest.setdefault("app", {})
    app_block["tools"] = [
        {
            "key": tool_key,
            "handler_ref": "tools.example:run",
            "parameters_schema": {"type": "object"},
            "output_schema": {"type": "object"},
        }
    ]
    app_block["hooks"] = [
        {
            "point": "entry.precompute",
            "key": f"{tool_key}_hook",
            "mode": "tool",
            "tool": tool_key,
        }
    ]
    return manifest


async def _attach_live_bundle(app_node: App, *, slug: str, tool_key: str) -> None:
    """Give ``app_node`` an attached bundle manifest and register it live."""
    manifest = _bundle_manifest(slug=slug, tool_key=tool_key)
    cp = await ensure_app_attached_operational_model(app_node)
    cp.manifest = manifest
    await cp.save()
    canonical = compile_canonical_manifest(manifest=manifest)
    await sync_operational_layer_from_manifest(
        app_node, canonical, actor_id="u_purge_test"
    )


def _hook_keys(workspace_id: str, tool_key: str) -> list:
    """Hook binding keys at entry.precompute contributed by this test's bundle."""
    return [
        h.get("key")
        for h in get_workspace_hooks(workspace_id, "entry.precompute")
        if h.get("key") == f"{tool_key}_hook"
    ]


@pytest.mark.asyncio
async def test_delete_app_endpoint_unregisters_bundle(
    authenticated_client: AsyncClient, test_user
):
    """DELETE /apps/{id} (non-library branch) drops the bundle's hooks + tools."""
    resp = await authenticated_client.post(
        "/api/apps", json={"name": "Endpoint Purge App"}
    )
    assert resp.status_code == 200, resp.text
    app_id = resp.json()["app"]["id"]

    app_node = await App.get(app_id)
    assert app_node is not None
    # The branch under test is the one for Apps NOT installed from the library.
    assert not getattr(app_node, "installed_from_library_id", None)
    workspace_id = app_node.workspace_id

    tool_key = "purge_endpoint_tool"
    await _attach_live_bundle(app_node, slug="purge-endpoint-bundle", tool_key=tool_key)
    assert tool_key in get_workspace_tools(workspace_id)
    assert _hook_keys(workspace_id, tool_key) == [f"{tool_key}_hook"]

    del_resp = await authenticated_client.delete(f"/api/apps/{app_id}")
    assert del_resp.status_code == 200, del_resp.text

    assert tool_key not in get_workspace_tools(
        workspace_id
    ), "DELETE /apps left the bundle's tool dispatching for a deleted App"
    assert (
        _hook_keys(workspace_id, tool_key) == []
    ), "DELETE /apps left the bundle's hook binding dispatching for a deleted App"


@pytest.mark.asyncio
async def test_delete_workspace_cascade_unregisters_bundle():
    """delete_workspace_cascade(cascade=True) drops each App's registrations."""
    from app.services.app_graph import catalog_app
    from app.services.workspace_lifecycle import delete_workspace_cascade

    ws = await _make_workspace("Purge Cascade WS")
    now = utc_now_iso()
    app_node = await App.create(
        name="Cascade Purge App",
        name_fold="cascade purge app",
        workspace_id=ws.id,
        lifecycle_state="active",
        created_at=now,
        updated_at=now,
    )
    # I-GRAPH-01: wire the App into the workspace branch registry.
    await catalog_app(app_node)

    tool_key = "purge_cascade_tool"
    await _attach_live_bundle(app_node, slug="purge-cascade-bundle", tool_key=tool_key)
    assert tool_key in get_workspace_tools(ws.id)
    assert _hook_keys(ws.id, tool_key) == [f"{tool_key}_hook"]

    await delete_workspace_cascade(ws, cascade=True)

    assert tool_key not in get_workspace_tools(
        ws.id
    ), "workspace cascade left the bundle's tool dispatching for a deleted App"
    assert (
        _hook_keys(ws.id, tool_key) == []
    ), "workspace cascade left the bundle's hook binding dispatching"


@pytest.mark.asyncio
async def test_purge_helper_resolves_slug_before_the_cascade():
    """The shared helper unregisters even though the cascade strips the Operational Model.

    Regression guard on the ordering: resolving the slug AFTER
    ``delete_app_cascade`` yields "" (the attached OperationalModel is gone) and
    the unregister becomes a silent no-op.
    """
    from app.services.app_graph import catalog_app
    from app.services.app_lifecycle import purge_app_with_bundle_teardown

    ws = await _make_workspace("Purge Helper WS")
    now = utc_now_iso()
    app_node = await App.create(
        name="Helper Purge App",
        name_fold="helper purge app",
        workspace_id=ws.id,
        lifecycle_state="active",
        created_at=now,
        updated_at=now,
    )
    await catalog_app(app_node)

    tool_key = "purge_helper_tool"
    await _attach_live_bundle(app_node, slug="purge-helper-bundle", tool_key=tool_key)
    assert tool_key in get_workspace_tools(ws.id)

    await purge_app_with_bundle_teardown(app_node)

    assert tool_key not in get_workspace_tools(ws.id)
    assert _hook_keys(ws.id, tool_key) == []
    assert await App.get(app_node.id) is None, "helper did not cascade-delete the App"
