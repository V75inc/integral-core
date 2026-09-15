"""In-repo stdio MCP echo server — ADR-009 acceptance fixture.

Run as::

    python -m tests.fixtures.mcp_echo_server

Exposes two tools over stdio MCP: ``echo`` and ``add``. Used by
``test_mcp_connector_adapter.py`` to mount a real external MCP server into a
test workspace without network dependencies.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("integral-echo")


@mcp.tool()
def echo(message: str) -> str:
    """Return ``message`` unchanged."""
    return message


@mcp.tool()
def add(a: float, b: float) -> float:
    """Return the sum of ``a`` and ``b``."""
    return a + b


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
