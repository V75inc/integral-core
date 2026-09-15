"""Hook framework <-> app_lifecycle integration: install + uninstall."""

import pytest


@pytest.mark.asyncio
async def test_install_registers_tools_and_hooks(monkeypatch):
    from app.services.hooks.install_hook import register_bundle_on_install
    from app.services.hooks.registry import (
        clear_workspace_registrations,
        get_workspace_hooks,
        get_workspace_tools,
    )

    ws = "n.Workspace.LH1"
    clear_workspace_registrations(ws)

    canonical = {
        "package": {"slug": "test-bundle", "trust_tier": "trusted"},
        "app": {
            "tools": [
                {
                    "key": "t1",
                    "handler_ref": "tests.fixtures.tool_echo:echo",
                    "parameters_schema": {},
                    "output_schema": {},
                }
            ],
            "hooks": [
                {
                    "point": "entry.transform",
                    "key": "h1",
                    "match": {},
                    "mode": "declarative",
                    "declarative": {"copy_fields": []},
                }
            ],
        },
    }
    await register_bundle_on_install(workspace_id=ws, canonical=canonical)
    assert "t1" in get_workspace_tools(ws)
    assert len(get_workspace_hooks(ws, "entry.transform")) == 1


@pytest.mark.asyncio
async def test_uninstall_clears_only_target_bundle():
    from app.services.hooks.install_hook import unregister_bundle_on_uninstall
    from app.services.hooks.registry import (
        clear_workspace_registrations,
        get_workspace_hooks,
        get_workspace_tools,
        register_workspace_hooks,
        register_workspace_tools,
    )

    ws = "n.Workspace.LH2"
    clear_workspace_registrations(ws)
    register_workspace_tools(
        ws,
        "bundle-a",
        [
            {
                "key": "t1",
                "handler_ref": "m:fn",
                "parameters_schema": {},
                "output_schema": {},
            }
        ],
    )
    register_workspace_tools(
        ws,
        "bundle-b",
        [
            {
                "key": "t2",
                "handler_ref": "m:fn2",
                "parameters_schema": {},
                "output_schema": {},
            }
        ],
    )
    register_workspace_hooks(
        ws,
        "bundle-a",
        [
            {
                "point": "entry.transform",
                "key": "h1",
                "match": {},
                "mode": "declarative",
                "declarative": {"copy_fields": []},
            }
        ],
    )
    register_workspace_hooks(
        ws,
        "bundle-b",
        [
            {
                "point": "entry.precompute",
                "key": "h2",
                "match": {},
                "mode": "tool",
                "tool": "t2",
            }
        ],
    )

    await unregister_bundle_on_uninstall(workspace_id=ws, bundle_slug="bundle-a")

    tools = get_workspace_tools(ws)
    assert "t1" not in tools
    assert "t2" in tools
    assert get_workspace_hooks(ws, "entry.transform") == []
    assert len(get_workspace_hooks(ws, "entry.precompute")) == 1
