"""Fail-closed contracts for the live Integral Desktop capability host."""

import asyncio
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

from app.agentive.services import desktop_environment
from app.agentive.services.capability_broker import resolve_from_snapshot
from app.schemas.capability_broker import CapabilityInvocation


@pytest.fixture(autouse=True)
def clear_desktop_runtime() -> Iterator[None]:
    """Reset process-local host state around every test."""
    desktop_environment._tickets.clear()
    desktop_environment._connections.clear()
    desktop_environment._connections_by_connector.clear()
    yield
    desktop_environment._tickets.clear()
    desktop_environment._connections.clear()
    desktop_environment._connections_by_connector.clear()


@pytest.mark.asyncio
async def test_desktop_ticket_is_scoped_and_single_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host ticket carries scope and cannot be replayed."""

    async def fake_connector(**_kwargs: Any) -> Any:
        return SimpleNamespace(id="connector-1")

    monkeypatch.setattr(
        desktop_environment, "_find_or_create_connector", fake_connector
    )
    session = await desktop_environment.mint_session(
        principal_id="user-1",
        workspace_id="workspace-1",
        device_id="device_123",
        device_name="Test Desktop",
    )

    ticket = desktop_environment.consume_ticket(session["ticket"])

    assert "binding_id" not in session
    assert ticket is not None
    assert ticket.principal_id == "user-1"
    assert ticket.workspace_id == "workspace-1"
    assert ticket.connector_id == "connector-1"
    assert desktop_environment.consume_ticket(session["ticket"]) is None


@pytest.mark.asyncio
async def test_session_endpoint_returns_no_persisted_connector_selector(
    authenticated_client: Any,
) -> None:
    """HTTP registration returns only one-use connection material."""
    response = await authenticated_client.post(
        "/api/agentive/desktop-environments/session",
        json={"device_id": "device_endpoint_123", "device_name": "Test Desktop"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["websocket_path"] == desktop_environment.DESKTOP_WS_PATH
    assert payload["ticket"]
    assert "binding_id" not in payload


def test_live_catalogue_requires_exact_principal_and_workspace() -> None:
    """Tool discovery requires the exact live binding identity."""
    connection = desktop_environment.DesktopConnection(
        binding_id="connector-1",
        principal_id="user-1",
        workspace_id="workspace-1",
        connector_id="connector-1",
        websocket=SimpleNamespace(),
    )
    desktop_environment._connections[connection.binding_id] = connection
    desktop_environment._connections_by_connector[connection.connector_id] = connection

    allowed = desktop_environment.live_tool_specs(
        "connector-1", principal_id="user-1", workspace_id="workspace-1"
    )

    assert {item["name"] for item in allowed} == set(
        desktop_environment.DESKTOP_TOOL_NAMES
    )
    assert (
        desktop_environment.live_tool_specs(
            "connector-1", principal_id="user-2", workspace_id="workspace-1"
        )
        == []
    )
    assert (
        desktop_environment.live_tool_specs(
            "connector-1", principal_id="user-1", workspace_id="workspace-2"
        )
        == []
    )


def test_binding_status_names_the_failed_gate() -> None:
    """Status distinguishes a live binding from identity and selector failures."""
    connection = desktop_environment.DesktopConnection(
        binding_id="binding-status",
        principal_id="user-status",
        workspace_id="workspace-status",
        connector_id="connector-status",
        websocket=SimpleNamespace(),
    )
    desktop_environment._connections[connection.binding_id] = connection

    assert (
        desktop_environment.binding_status(
            connection.binding_id,
            principal_id=connection.principal_id,
            workspace_id=connection.workspace_id,
        )["reason"]
        == "live"
    )
    assert (
        desktop_environment.binding_status(
            "unknown-binding",
            principal_id=connection.principal_id,
            workspace_id=connection.workspace_id,
        )["reason"]
        == "selector_not_connected"
    )
    assert (
        desktop_environment.binding_status(
            connection.binding_id,
            principal_id="other-user",
            workspace_id=connection.workspace_id,
        )["reason"]
        == "principal_mismatch"
    )
    assert (
        desktop_environment.binding_status(
            connection.binding_id,
            principal_id=connection.principal_id,
            workspace_id="other-workspace",
        )["reason"]
        == "workspace_mismatch"
    )


@pytest.mark.asyncio
async def test_invoke_round_trip_uses_live_selected_connection() -> None:
    """An invocation resolves only through the selected live socket."""
    sent = asyncio.Event()

    class FakeWebSocket:
        async def send_text(self: "FakeWebSocket", raw: str) -> None:
            import json

            message = json.loads(raw)
            desktop_environment.accept_result(
                connection,
                {
                    "type": "result",
                    "id": message["id"],
                    "ok": True,
                    "data": {"platform": "test"},
                },
            )
            sent.set()

    connection = desktop_environment.DesktopConnection(
        binding_id="connector-1",
        principal_id="user-1",
        workspace_id="workspace-1",
        connector_id="connector-1",
        websocket=FakeWebSocket(),
    )
    desktop_environment._connections[connection.binding_id] = connection
    desktop_environment._connections_by_connector[connection.connector_id] = connection

    result = await desktop_environment.invoke(
        connector_id="connector-1",
        principal_id="user-1",
        workspace_id="workspace-1",
        tool_name="desktop__diagnostics",
        arguments={},
    )

    assert sent.is_set()
    assert result == {"platform": "test"}


@pytest.mark.asyncio
async def test_invoke_rejects_arguments_outside_canonical_schema() -> None:
    """Model-supplied extras fail before any host message is sent."""
    with pytest.raises(
        desktop_environment.DesktopEnvironmentError,
        match="declared schema",
    ):
        await desktop_environment.invoke(
            connector_id="connector-1",
            principal_id="user-1",
            workspace_id="workspace-1",
            tool_name="desktop__read_file",
            arguments={
                "root_id": "root-1",
                "path": "note.txt",
                "absolute_path": "/etc/passwd",
            },
        )


def test_connector_snapshot_does_not_authorize_undeclared_tool_by_id() -> None:
    """A connector id cannot authorize a key absent from its declaration."""
    snapshot = {
        "environments": [
            {
                "connector_id": "connector-1",
                "capabilities": ["desktop__read_file"],
                "tool_keys": [],
            }
        ]
    }
    invocation = CapabilityInvocation(
        run_id="run-1",
        principal_id="user-1",
        workspace_id="workspace-1",
        origin="chat",
        source="connector",
        connector_id="connector-1",
        capability_key="desktop__delete_path",
    )

    assert resolve_from_snapshot(snapshot, invocation) is None
