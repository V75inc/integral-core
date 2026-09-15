"""One dispatch contract for MCP tool specs (ADR-009).

``run_tool`` routes an MCP call by finding ``_mcp_connector_id`` on the spec and
otherwise falls through to ``resolve_handler(handler_ref)``, which requires a
``module:callable`` string. The HTTP-mount path (``mcp_adapter``) emitted
``handler_ref="mcp"`` and put the connector under ``connector_id``, so every
streamable_http-mounted tool registered successfully and then raised
``HookMisconfiguredError`` on first invoke — the mount looked healthy and the
tools were uncallable. The stdio path (``mcp_mount``) always emitted the proxy
contract. These tests pin the two paths to the same shape.
"""

from __future__ import annotations

import pytest

from app.agentive.connectors import mcp_adapter, mcp_mount
from app.agentive.connectors.mcp_client import RemoteTool

pytestmark = pytest.mark.smoke

_PROXY_REF = "app.agentive.connectors.mcp_proxy:invoke"


class _FakeRemoteTool:
    """Shape returned by the MCP SDK's ``tools/list``."""

    name = "echo"
    description = "Echo a message"
    # mixedCase mirrors the MCP SDK's own attribute; renaming it would stop
    # exercising the real shape.
    inputSchema = {"type": "object", "properties": {}}  # noqa: N815


def _assert_dispatchable(spec: dict) -> None:
    assert spec["handler_ref"] == _PROXY_REF
    assert ":" in spec["handler_ref"], "run_tool needs module:callable"
    assert spec["_mcp_connector_id"], "run_tool routes MCP on this key"
    assert spec["_mcp_remote_name"], "the proxy needs the remote tool name"


def test_http_discover_spec_is_dispatchable():
    """The streamable_http discovery path emits the proxy contract."""
    spec = mcp_adapter._tool_spec_from_remote("n.Connector.abc", _FakeRemoteTool())
    _assert_dispatchable(spec)


def test_http_rehydrate_spec_is_dispatchable():
    """The post-restart rehydration path emits it too.

    Rehydration rebuilds specs from ``auth_state.discovered_tools`` rather than
    from a live ``tools/list``, so it is a second place the contract can drift.
    """
    specs = mcp_adapter._specs_from_discovered(
        "n.Connector.abc",
        [{"name": "echo", "description": "d", "input_schema": {}}],
    )
    assert specs
    _assert_dispatchable(specs[0])


def test_stdio_and_http_paths_agree_on_the_contract():
    """Both mount transports must produce the same dispatch keys."""
    http_spec = mcp_adapter._tool_spec_from_remote("n.Connector.abc", _FakeRemoteTool())
    stdio_spec = mcp_mount._specs_from_tools(
        "n.Connector.abc",
        [RemoteTool(name="echo", description="d", input_schema={})],
    )[0]

    for key in ("handler_ref", "_mcp_connector_id", "_mcp_remote_name"):
        assert http_spec[key] == stdio_spec[key], f"{key} diverged between transports"


@pytest.mark.asyncio
async def test_http_spec_reaches_the_proxy_not_a_handler_error():
    """Regression: dispatch must reach the proxy, not HookMisconfiguredError.

    A policy refusal here is a *correct* outcome — it proves routing arrived at
    ``mcp_proxy.invoke``. The bug produced ``HookMisconfiguredError`` from
    ``resolve_handler`` instead, before any policy was consulted.
    """
    from app.agentive.connectors.mcp_proxy import McpProxyError
    from app.services.hooks.registry import ToolContext
    from app.services.hooks.tool_dispatch import run_tool

    spec = mcp_adapter._tool_spec_from_remote("n.Connector.missing", _FakeRemoteTool())
    ctx = ToolContext(user_id="u-nobody", workspace_id="ws-x", scope="tool:echo")

    with pytest.raises(McpProxyError):
        await run_tool(spec, {"message": "hi"}, ctx)


def test_health_vocabulary_is_single_sourced():
    """ADR-009 §8 allows ok|degraded|error|unknown — and only those.

    The stdio path wrote "healthy" while the HTTP path wrote "ok", so a UI
    switching on ``health_status`` was wrong for one of the two transports.
    """
    from app.agentive.connectors import mcp_client

    allowed = {
        mcp_client.HEALTH_OK,
        mcp_client.HEALTH_DEGRADED,
        mcp_client.HEALTH_ERROR,
        mcp_client.HEALTH_UNKNOWN,
    }
    assert allowed == {"ok", "degraded", "error", "unknown"}

    # Look for the old value being ASSIGNED, not merely mentioned — the
    # constants above carry an explanatory comment naming it.
    import re

    writes = re.compile(r'(=|:)\s*"healthy"')
    for module in ("mcp_mount", "mcp_adapter", "mcp_client"):
        src = __import__(f"app.agentive.connectors.{module}", fromlist=["x"]).__file__
        with open(src, encoding="utf-8") as fh:
            offenders = [
                line.strip()
                for line in fh
                if writes.search(line) and not line.lstrip().startswith("#")
            ]
        assert not offenders, f"{module} still writes the old vocabulary: {offenders}"
