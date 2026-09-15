"""Lowlevel MCP server exposing the M2a catalogue over Streamable HTTP.

Identity = OAuth ``sub`` (the resource-server middleware sets
``request.state.user``; read via :func:`resolve_principal_id`). Scope =
``X-Integral-Scope`` (validated by :func:`resolve_workspace_id_from_request`).
Both are read ONLY from the per-request Starlette request, NEVER from a tool
argument.

Fail-closed: with no authenticated principal the server lists no tools and
refuses every dispatch.

OAuth token scopes (Full Sweep B2 / S6) filter the advertised catalogue when
present on the principal. Mapping (most permissive wins):

* ``integral:execute`` → read + propose + execute
* ``integral:propose`` OR bare ``integral`` alone → read + propose
* ``integral:read`` (or other ``integral*`` without propose/execute) → read

When no OAuth scopes are available on the request (in-app session JWT /
resident parity), the full catalogue is advertised.

This module builds the server via :func:`build_mcp_server` /
:func:`build_session_manager`. There are no import-time side effects — nothing
is constructed at import. The transport mount + lifespan wiring is M2b Task 3.

SDK contract (mcp 1.27.1, verified against the installed package):

* ``types.Tool(name=, description=, inputSchema=)`` — ``inputSchema`` is the
  camelCase field name.
* The ``@server.call_tool()`` handler may return one of:
  ``Iterable[ContentBlock]`` (unstructured), a ``dict`` (structured — wrapped
  into ``CallToolResult.structuredContent`` with a JSON ``TextContent``
  mirror), a ``(content, structured)`` tuple, or a ``types.CallToolResult``
  (passed through verbatim — the only way to surface ``isError=True`` from the
  handler without raising). We exploit the dict path for success and the
  ``CallToolResult`` path for fail-closed / dispatch errors.
* ``server.request_context`` raises ``LookupError`` outside a request context;
  inside one, ``server.request_context.request`` is the Starlette request.
"""

from __future__ import annotations

from typing import Any, Collection, FrozenSet, Optional, Sequence

import mcp.types as types
from mcp.server.lowlevel.server import Server as MCPServer
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from app.agentive.tooling import build_tool_catalogue, dispatch_tool
from app.api.utils import resolve_principal_id
from app.services.request_scope import resolve_workspace_id_from_request

_INSTRUCTIONS = (
    "Integral knowledge-substrate tools. Reads return data; proposes stage a "
    "change for human approval in Integral."
)

_ALL_OP_CLASSES: FrozenSet[str] = frozenset({"read", "propose", "execute"})
_READ_PROPOSE: FrozenSet[str] = frozenset({"read", "propose"})
_READ_ONLY: FrozenSet[str] = frozenset({"read"})


def _oauth_scopes_from_request(req: Any) -> Optional[list[str]]:
    """Extract OAuth scope tokens from the request principal, or None.

    jvspatial OAuth bearer auth maps the token ``scope`` claim onto
    ``UserResponse.permissions`` with empty ``roles``. Session JWTs carry
    RBAC roles and are treated as "no OAuth scopes" so the in-app / resident
    path keeps the full catalogue.
    """
    if req is None or not hasattr(req, "state"):
        return None
    user = getattr(req.state, "user", None)
    if user is None:
        return None

    for attr in ("oauth_scopes", "scopes"):
        val = getattr(user, attr, None)
        if val is None and isinstance(user, dict):
            val = user.get(attr)
        if isinstance(val, str):
            return [s for s in val.split() if s]
        if isinstance(val, (list, tuple)):
            return [str(s) for s in val if s]

    roles = getattr(user, "roles", None)
    if roles is None and isinstance(user, dict):
        roles = user.get("roles")
    perms = getattr(user, "permissions", None)
    if perms is None and isinstance(user, dict):
        perms = user.get("permissions")

    # OAuth-shaped principal: empty roles + permissions = space-split scopes.
    if roles == [] and perms is not None:
        if isinstance(perms, str):
            return [s for s in perms.split() if s]
        return [str(s) for s in perms if s]
    return None


def allowed_op_classes_for_scopes(
    scopes: Optional[Collection[str]],
) -> Optional[FrozenSet[str]]:
    """Map OAuth scopes → allowed tool ``op_class`` set.

    Returns ``None`` when *scopes* is ``None`` (no OAuth scope context — keep
    the full catalogue). Otherwise returns a frozenset of allowed op classes.
    """
    if scopes is None:
        return None

    scope_set = {str(s).strip() for s in scopes if s and str(s).strip()}
    if not scope_set:
        # Authenticated OAuth client with empty grant — fail soft to read+propose.
        return _READ_PROPOSE

    if "integral:execute" in scope_set:
        return _ALL_OP_CLASSES
    if "integral:propose" in scope_set:
        return _READ_PROPOSE
    # Bare ``integral`` alone → read+propose (external MCP safety default).
    if scope_set == {"integral"}:
        return _READ_PROPOSE
    if "integral:read" in scope_set or any(
        s == "integral" or s.startswith("integral:") for s in scope_set
    ):
        return _READ_ONLY
    # No integral* scopes — still default to read+propose rather than empty.
    return _READ_PROPOSE


async def _list_tools_impl(
    scopes: Optional[Sequence[str]] = None,
) -> list[types.Tool]:
    """Map the M2a catalogue to ``types.Tool``, optionally filtered by scopes.

    When *scopes* is ``None``, advertise the full dispatchable catalogue
    (in-app / resident parity). When *scopes* is a list (possibly empty),
    filter by :func:`allowed_op_classes_for_scopes`.
    """
    allowed = allowed_op_classes_for_scopes(None if scopes is None else list(scopes))
    tools: list[types.Tool] = []
    for t in build_tool_catalogue():
        if allowed is not None and t.get("op_class") not in allowed:
            continue
        tools.append(
            types.Tool(
                name=t["name"],
                description=t["description"],
                inputSchema=t["input_schema"],
            )
        )
    return tools


async def _call_tool_impl(
    name: str,
    arguments: Optional[dict],
    *,
    principal_id: Optional[str],
    scope: Optional[str],
) -> Any:
    """Route a tool call to :func:`dispatch_tool` under an explicit principal+scope.

    Returns either a plain ``dict`` (success — becomes ``structuredContent``
    downstream) or a ``types.CallToolResult`` with ``isError=True`` (fail-closed
    or dispatch error). Kept module-level so it is unit-testable without a
    request context — identity and scope are passed in, never read here.
    """
    if not principal_id:
        # Fail-closed: no authenticated principal -> no dispatch.
        return types.CallToolResult(
            isError=True,
            content=[
                types.TextContent(
                    type="text",
                    text="unauthorized: no authenticated principal",
                )
            ],
        )

    res = await dispatch_tool(
        name,
        arguments or {},
        principal_id=principal_id,
        scope=scope,
    )
    if res.is_error:
        return types.CallToolResult(
            isError=True,
            content=[
                types.TextContent(
                    type="text",
                    text=f"{res.error_code}: {res.message}",
                )
            ],
        )

    # Success: a dict becomes structuredContent; wrap a non-dict scalar so the
    # SDK always sees structured content.
    return res.data if isinstance(res.data, dict) else {"result": res.data}


def build_mcp_server() -> MCPServer:
    """Construct the lowlevel MCP server with list/call handlers registered.

    Identity and scope are read from the per-request Starlette request and
    threaded into the module-level impls. The list surface is fail-closed: an
    unauthenticated request lists no tools. OAuth scopes (when present on the
    principal) further filter the advertised catalogue.
    """
    mcp: MCPServer = MCPServer(name="integral", instructions=_INSTRUCTIONS)

    @mcp.list_tools()
    async def _list() -> list[types.Tool]:
        try:
            req = mcp.request_context.request
        except LookupError:
            req = None
        if req is not None and not resolve_principal_id(req):
            # Fail-closed: an unauthenticated request lists no tools.
            return []
        scopes = _oauth_scopes_from_request(req) if req is not None else None
        return await _list_tools_impl(scopes)

    @mcp.call_tool()
    async def _call(name: str, arguments: dict) -> Any:
        try:
            req = mcp.request_context.request
        except LookupError:
            req = None
        principal_id = resolve_principal_id(req) if req is not None else None
        scope = (
            await resolve_workspace_id_from_request(req, principal_id)
            if (req is not None and principal_id)
            else None
        )
        return await _call_tool_impl(
            name, arguments, principal_id=principal_id, scope=scope
        )

    return mcp


def build_session_manager() -> tuple[MCPServer, StreamableHTTPSessionManager]:
    """Build the MCP server and a stateless JSON Streamable-HTTP session manager.

    The mount + lifespan wiring (running the manager) is M2b Task 3; this only
    constructs the pair so the mount layer can adopt it.
    """
    mcp = build_mcp_server()
    mgr = StreamableHTTPSessionManager(app=mcp, json_response=True, stateless=True)
    return mcp, mgr
