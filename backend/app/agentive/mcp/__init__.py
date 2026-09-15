"""Lowlevel MCP server package — exposes the M2a tool catalogue over MCP.

The server itself is built lazily via :func:`app.agentive.mcp.server.build_mcp_server`
(and the Streamable-HTTP session manager via ``build_session_manager``). There are
NO import-time side effects: importing this package does not construct a server,
bind a transport, or read environment — that wiring belongs to the mount layer
(M2b Task 3).
"""

from app.agentive.mcp.server import (
    build_mcp_server,
    build_session_manager,
)

__all__ = [
    "build_mcp_server",
    "build_session_manager",
]
