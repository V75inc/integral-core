"""Live, user-bound Integral Desktop capability host.

The renderer never executes host operations. A packaged Electron main process
connects here with a short-lived ticket and services a small, canonical,
read-only tool surface. The live connection is an additional authority gate:
persisted connector declarations alone never make a desktop callable.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from fastapi import WebSocket

from app.agentive.nodes import Connector
from app.agentive.services.connector_registry_node import create_connector
from app.utils.time import utc_now_iso

DESKTOP_WS_PATH = "/ws/desktop-environment"
TICKET_TTL_SECONDS = 45
CALL_TIMEOUT_SECONDS = 30.0
MAX_ARGUMENT_BYTES = 64 * 1024

# Server-owned contract. The host only reports whether it implements this
# version; it cannot invent tools or schemas.
DESKTOP_TOOL_SPECS: tuple[Dict[str, Any], ...] = (
    {
        "name": "desktop__list_roots",
        "description": (
            "List the local folders the user explicitly granted to Integral "
            "Desktop. Use this first when the user asks about files on their "
            "computer, system, desktop, or local machine; workspace attachment "
            "tools are not local filesystem tools."
        ),
        "op_class": "read",
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "desktop__list_directory",
        "description": (
            "List one directory below a user-granted desktop root. Use this for "
            "local computer files, not Integral workspace attachments."
        ),
        "op_class": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "root_id": {"type": "string"},
                "path": {"type": "string", "default": ""},
            },
            "required": ["root_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "desktop__read_file",
        "description": ("Read a UTF-8 text file below a user-granted desktop root."),
        "op_class": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "root_id": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["root_id", "path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "desktop__find_path",
        "description": (
            "Find paths by case-insensitive name below a user-granted desktop " "root."
        ),
        "op_class": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "root_id": {"type": "string"},
                "query": {"type": "string"},
                "path": {"type": "string", "default": ""},
            },
            "required": ["root_id", "query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "desktop__grep",
        "description": ("Search UTF-8 text files below a user-granted desktop root."),
        "op_class": "read",
        "input_schema": {
            "type": "object",
            "properties": {
                "root_id": {"type": "string"},
                "pattern": {"type": "string"},
                "path": {"type": "string", "default": ""},
            },
            "required": ["root_id", "pattern"],
            "additionalProperties": False,
        },
    },
    {
        "name": "desktop__diagnostics",
        "description": (
            "Return allowlisted OS and Integral Desktop runtime diagnostics."
        ),
        "op_class": "read",
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
)
DESKTOP_TOOL_NAMES = frozenset(str(item["name"]) for item in DESKTOP_TOOL_SPECS)


class DesktopEnvironmentError(Exception):
    """Stable refusal or transport failure returned by the desktop host."""

    def __init__(self: "DesktopEnvironmentError", code: str, message: str) -> None:
        """Retain a machine code and safe model-facing message."""
        super().__init__(code, message)
        self.code = code
        self.message = message


@dataclass
class _Ticket:
    principal_id: str
    workspace_id: str
    connector_id: str
    expires_at: float


@dataclass
class DesktopConnection:
    """One authenticated live Electron main-process connection."""

    binding_id: str
    principal_id: str
    workspace_id: str
    connector_id: str
    websocket: WebSocket
    pending: Dict[str, asyncio.Future[Any]] = field(default_factory=dict)


_tickets: Dict[str, _Ticket] = {}
_connections: Dict[str, DesktopConnection] = {}
_connections_by_connector: Dict[str, DesktopConnection] = {}


def _ticket_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _clean_expired_tickets() -> None:
    now = time.monotonic()
    for key, ticket in list(_tickets.items()):
        if ticket.expires_at <= now:
            _tickets.pop(key, None)


async def _find_or_create_connector(
    *, principal_id: str, workspace_id: str, device_id: str, device_name: str
) -> Connector:
    candidates = await Connector.find(
        {"owner": principal_id, "workspace_id": workspace_id}
    )
    for connector in candidates:
        state = dict(getattr(connector, "auth_state", None) or {})
        if (
            getattr(connector, "subclass_slug", "") == "integral_desktop"
            and state.get("device_id") == device_id
        ):
            connector.capabilities = sorted(DESKTOP_TOOL_NAMES)
            connector.health_status = "unknown"
            connector.last_health_at = utc_now_iso()
            state.update(
                {
                    "device_id": device_id,
                    "device_name": device_name,
                    "manifest_version": "1",
                    "discovered_tools": [
                        {"name": item["name"], "op_class": item["op_class"]}
                        for item in DESKTOP_TOOL_SPECS
                    ],
                }
            )
            connector.auth_state = state
            await connector.save()
            return connector

    connector = await create_connector(
        owner=principal_id,
        kind="custom",
        workspace_id=workspace_id,
        skip_default_policy=True,
        capabilities=sorted(DESKTOP_TOOL_NAMES),
        permissions=["connector.read", "tool.invoke"],
        auth_state={
            "device_id": device_id,
            "device_name": device_name,
            "manifest_version": "1",
            "discovered_tools": [
                {"name": item["name"], "op_class": item["op_class"]}
                for item in DESKTOP_TOOL_SPECS
            ],
        },
    )
    connector.subclass_slug = "integral_desktop"
    connector.health_status = "unknown"
    connector.last_health_at = utc_now_iso()
    await connector.save()
    return connector


async def mint_session(
    *, principal_id: str, workspace_id: str, device_id: str, device_name: str
) -> Dict[str, Any]:
    """Mint a single-use ticket for one principal/workspace/device binding."""
    connector = await _find_or_create_connector(
        principal_id=principal_id,
        workspace_id=workspace_id,
        device_id=device_id,
        device_name=device_name,
    )
    _clean_expired_tickets()
    raw_ticket = secrets.token_urlsafe(32)
    _tickets[_ticket_key(raw_ticket)] = _Ticket(
        principal_id=principal_id,
        workspace_id=workspace_id,
        connector_id=str(connector.id),
        expires_at=time.monotonic() + TICKET_TTL_SECONDS,
    )
    return {
        # Internal handler metadata; response validation intentionally drops it.
        "connector_id": str(connector.id),
        "ticket": raw_ticket,
        "websocket_path": DESKTOP_WS_PATH,
        "expires_in": TICKET_TTL_SECONDS,
        "capabilities": sorted(DESKTOP_TOOL_NAMES),
    }


def consume_ticket(raw_ticket: str) -> Optional[_Ticket]:
    """Consume a ticket exactly once, returning None when absent or expired."""
    _clean_expired_tickets()
    ticket = _tickets.pop(_ticket_key(raw_ticket), None)
    if ticket is None or ticket.expires_at <= time.monotonic():
        return None
    return ticket


async def register_connection(
    ticket: _Ticket, websocket: WebSocket
) -> DesktopConnection:
    """Replace any prior socket for this binding and mark it healthy."""
    binding_id = secrets.token_urlsafe(24)
    previous = _connections_by_connector.pop(ticket.connector_id, None)
    if previous is not None:
        _connections.pop(previous.binding_id, None)
        await previous.websocket.close(code=4002, reason="Desktop reconnected")
    connection = DesktopConnection(
        binding_id=binding_id,
        principal_id=ticket.principal_id,
        workspace_id=ticket.workspace_id,
        connector_id=ticket.connector_id,
        websocket=websocket,
    )
    _connections[binding_id] = connection
    _connections_by_connector[ticket.connector_id] = connection
    connector = await Connector.get(ticket.connector_id)
    if connector is not None:
        connector.health_status = "healthy"
        connector.last_error = None
        connector.last_health_at = utc_now_iso()
        await connector.save()
    return connection


async def unregister_connection(connection: DesktopConnection) -> None:
    """Drop a live socket and fail all of its pending requests."""
    if _connections.get(connection.binding_id) is connection:
        _connections.pop(connection.binding_id, None)
    if _connections_by_connector.get(connection.connector_id) is connection:
        _connections_by_connector.pop(connection.connector_id, None)
    for future in connection.pending.values():
        if not future.done():
            future.set_exception(
                DesktopEnvironmentError(
                    "environment.unavailable", "Desktop environment disconnected"
                )
            )
    connection.pending.clear()
    connector = await Connector.get(connection.connector_id)
    if connector is not None:
        connector.health_status = "unknown"
        connector.last_health_at = utc_now_iso()
        await connector.save()


def live_tool_specs(
    binding_id: Optional[str], *, principal_id: str, workspace_id: str
) -> List[Dict[str, Any]]:
    """Return tools only for the exact live principal/workspace binding."""
    if not binding_id:
        return []
    connection = _connections.get(binding_id)
    if (
        connection is None
        or connection.principal_id != principal_id
        or connection.workspace_id != workspace_id
    ):
        return []
    return [
        dict(item, connector_id=connection.connector_id) for item in DESKTOP_TOOL_SPECS
    ]


def validate_live_binding(
    binding_id: Optional[str], *, principal_id: str, workspace_id: str
) -> Optional[str]:
    """Validate a client selector without treating it as authority."""
    return (
        binding_id
        if live_tool_specs(
            binding_id, principal_id=principal_id, workspace_id=workspace_id
        )
        else None
    )


def binding_status(
    binding_id: Optional[str], *, principal_id: str, workspace_id: str
) -> Dict[str, Any]:
    """Explain binding rejection without exposing another binding's identity."""
    scoped = [
        connection
        for connection in _connections.values()
        if connection.principal_id == principal_id
        and connection.workspace_id == workspace_id
    ]
    reason = "live"
    connection = _connections.get(binding_id or "")
    if not binding_id:
        reason = "selector_missing"
    elif connection is None:
        reason = "selector_not_connected"
    elif connection.principal_id != principal_id:
        reason = "principal_mismatch"
    elif connection.workspace_id != workspace_id:
        reason = "workspace_mismatch"
    binding_live = reason == "live"
    return {
        # The backend surface is always available; the desktop application's
        # authenticated live binding is the capability gate.
        "enabled": True,
        "selector_present": bool(binding_id),
        "binding_live": binding_live,
        "reason": reason,
        "live_bindings_for_scope": len(scoped),
        "capabilities": sorted(DESKTOP_TOOL_NAMES) if binding_live else [],
    }


def accept_result(connection: DesktopConnection, message: Dict[str, Any]) -> None:
    """Resolve one pending invocation from a host result message."""
    request_id = str(message.get("id") or "")
    future = connection.pending.get(request_id)
    if future is None or future.done():
        return
    if message.get("ok") is True:
        future.set_result(message.get("data"))
    else:
        error = message.get("error") or {}
        future.set_exception(
            DesktopEnvironmentError(
                str(error.get("code") or "environment.adapter_failed"),
                str(error.get("message") or "Desktop capability failed"),
            )
        )


def _validate_arguments(tool_name: str, arguments: Dict[str, Any]) -> None:
    """Enforce the canonical schemas again at the trusted dispatch seam."""
    allowed: Dict[str, frozenset[str]] = {
        "desktop__list_roots": frozenset(),
        "desktop__diagnostics": frozenset(),
        "desktop__list_directory": frozenset({"root_id", "path"}),
        "desktop__read_file": frozenset({"root_id", "path"}),
        "desktop__find_path": frozenset({"root_id", "query", "path"}),
        "desktop__grep": frozenset({"root_id", "pattern", "path"}),
    }
    required: Dict[str, frozenset[str]] = {
        "desktop__list_directory": frozenset({"root_id"}),
        "desktop__read_file": frozenset({"root_id", "path"}),
        "desktop__find_path": frozenset({"root_id", "query"}),
        "desktop__grep": frozenset({"root_id", "pattern"}),
    }
    unknown = set(arguments) - allowed.get(tool_name, frozenset())
    missing = required.get(tool_name, frozenset()) - set(arguments)
    if unknown or missing:
        raise DesktopEnvironmentError(
            "environment.invalid_arguments",
            "Desktop capability arguments do not match its declared schema",
        )
    if any(not isinstance(value, str) for value in arguments.values()):
        raise DesktopEnvironmentError(
            "environment.invalid_arguments",
            "Desktop capability arguments must be strings",
        )
    if len(json.dumps(arguments, separators=(",", ":"))) > MAX_ARGUMENT_BYTES:
        raise DesktopEnvironmentError(
            "environment.invalid_arguments",
            "Desktop capability arguments exceed the size limit",
        )


async def invoke(
    *,
    connector_id: str,
    principal_id: str,
    workspace_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
) -> Any:
    """Invoke one canonical tool through an exact live desktop connection."""
    if tool_name not in DESKTOP_TOOL_NAMES:
        raise DesktopEnvironmentError(
            "environment.capability_revoked",
            "Desktop capability is not declared",
        )
    _validate_arguments(tool_name, arguments)
    connection = _connections_by_connector.get(connector_id)
    if (
        connection is None
        or connection.principal_id != principal_id
        or connection.workspace_id != workspace_id
    ):
        raise DesktopEnvironmentError(
            "environment.unavailable",
            "The selected Integral Desktop environment is not connected",
        )
    request_id = uuid.uuid4().hex
    future = asyncio.get_running_loop().create_future()
    connection.pending[request_id] = future
    try:
        await connection.websocket.send_text(
            json.dumps(
                {
                    "type": "invoke",
                    "id": request_id,
                    "tool": tool_name,
                    "arguments": dict(arguments or {}),
                },
                separators=(",", ":"),
            )
        )
        return await asyncio.wait_for(future, timeout=CALL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        raise DesktopEnvironmentError(
            "environment.timeout", "Desktop capability timed out"
        ) from exc
    finally:
        connection.pending.pop(request_id, None)
