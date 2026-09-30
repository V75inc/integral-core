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
    desktop_environment._artifacts.clear()
    yield
    desktop_environment._tickets.clear()
    desktop_environment._connections.clear()
    desktop_environment._connections_by_connector.clear()
    desktop_environment._artifacts.clear()


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
        driver_protocol=1,
        driver_generation=1,
        driver_capabilities=desktop_environment.DRIVER_TOOL_NAMES,
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


def test_driver_catalogue_requires_active_exact_host_manifest() -> None:
    """An older, inactive, or capability-inventing host cannot advertise driver tools."""
    connection = desktop_environment.DesktopConnection(
        binding_id="binding-manifest",
        principal_id="user-1",
        workspace_id="workspace-1",
        connector_id="connector-1",
        websocket=SimpleNamespace(),
    )
    desktop_environment._connections[connection.binding_id] = connection

    before = desktop_environment.live_tool_specs(
        connection.binding_id,
        principal_id=connection.principal_id,
        workspace_id=connection.workspace_id,
    )
    assert not {item["name"] for item in before} & set(
        desktop_environment.DRIVER_TOOL_NAMES
    )

    desktop_environment.accept_host_manifest(
        connection,
        {
            "type": "host_manifest",
            "driver_protocol": 1,
            "runtime_generation": 1,
            "enabled": True,
            "capabilities": sorted(desktop_environment.DRIVER_TOOL_NAMES),
        },
    )
    after = desktop_environment.live_tool_specs(
        connection.binding_id,
        principal_id=connection.principal_id,
        workspace_id=connection.workspace_id,
    )
    assert {item["name"] for item in after} & set(
        desktop_environment.DRIVER_TOOL_NAMES
    ) == set(desktop_environment.DRIVER_TOOL_NAMES)

    observation_only = desktop_environment.DesktopConnection(
        binding_id="binding-observe",
        principal_id="user-1",
        workspace_id="workspace-1",
        connector_id="connector-observe",
        websocket=SimpleNamespace(),
    )
    desktop_environment._connections[observation_only.binding_id] = observation_only
    desktop_environment.accept_host_manifest(
        observation_only,
        {
            "type": "host_manifest",
            "driver_protocol": 1,
            "runtime_generation": 2,
            "enabled": True,
            "capabilities": sorted(desktop_environment.DRIVER_OBSERVATION_TOOL_NAMES),
        },
    )
    observed = {
        item["name"]
        for item in desktop_environment.live_tool_specs(
            observation_only.binding_id,
            principal_id=observation_only.principal_id,
            workspace_id=observation_only.workspace_id,
        )
    }
    assert desktop_environment.DRIVER_OBSERVATION_TOOL_NAMES <= observed
    assert "driver__act" not in observed


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
async def test_driver_invoke_uses_narrow_versioned_rpc_and_marks_content_untrusted() -> (
    None
):
    """Driver reads never expose MCP and preserve the live binding generation."""

    class FakeWebSocket:
        async def send_text(self: "FakeWebSocket", raw: str) -> None:
            import json

            message = json.loads(raw)
            assert message["type"] == "driver_call"
            assert message["binding_generation"] == connection.binding_id
            assert message["operation"] == "driver__list_windows"
            assert message["arguments"] == {"pid": 42}
            assert message["deadline"].endswith("Z")
            desktop_environment.accept_result(
                connection,
                {
                    "type": "driver_result",
                    "id": message["id"],
                    "ok": True,
                    "runtime_generation": 3,
                    "content_untrusted": True,
                    "data": {
                        "windows": [
                            {
                                "pid": 42,
                                "windowId": "9007199254740993",
                                "title": "Document",
                            }
                        ]
                    },
                    "artifacts": [],
                },
            )

    connection = desktop_environment.DesktopConnection(
        binding_id="binding-driver",
        principal_id="user-1",
        workspace_id="workspace-1",
        connector_id="connector-1",
        websocket=FakeWebSocket(),
        driver_protocol=1,
        driver_generation=3,
        driver_capabilities=desktop_environment.DRIVER_OBSERVATION_TOOL_NAMES,
    )
    desktop_environment._connections[connection.binding_id] = connection
    desktop_environment._connections_by_connector[connection.connector_id] = connection

    result = await desktop_environment.invoke(
        connector_id="connector-1",
        principal_id="user-1",
        workspace_id="workspace-1",
        tool_name="driver__list_windows",
        arguments={"pid": 42},
    )

    assert result["runtime_generation"] == 3
    assert result["content_untrusted"] is True
    assert result["data"]["windows"][0]["windowId"] == "9007199254740993"


@pytest.mark.asyncio
async def test_driver_snapshot_artifact_is_verified_outside_the_control_message() -> (
    None
):
    """Binary screenshot bytes are digest-checked and represented by an expiring URL."""
    import hashlib
    import json

    screenshot = b"\x89PNG\r\n\x1a\nbounded-png-bytes"
    digest = hashlib.sha256(screenshot).hexdigest()

    class FakeWebSocket:
        async def send_text(self: "FakeWebSocket", raw: str) -> None:
            message = json.loads(raw)
            header = json.dumps(
                {
                    "type": "driver_artifact_chunk",
                    "call_id": message["id"],
                    "artifact_id": "artifact-1",
                    "binding_generation": connection.binding_id,
                    "runtime_generation": 8,
                    "sequence": 0,
                    "final": True,
                    "content_type": "image/png",
                    "total_bytes": len(screenshot),
                    "sha256": digest,
                },
                separators=(",", ":"),
            ).encode()
            frame = len(header).to_bytes(4, "big") + header + screenshot
            desktop_environment.accept_artifact_frame(connection, frame)
            desktop_environment.accept_result(
                connection,
                {
                    "type": "driver_result",
                    "id": message["id"],
                    "ok": True,
                    "runtime_generation": 8,
                    "data": {"snapshotId": "snapshot-1"},
                    "artifacts": [
                        {
                            "id": "artifact-1",
                            "content_type": "image/png",
                            "bytes": len(screenshot),
                            "sha256": digest,
                        }
                    ],
                },
            )

    connection = desktop_environment.DesktopConnection(
        binding_id="binding-artifact",
        principal_id="user-1",
        workspace_id="workspace-1",
        connector_id="connector-1",
        websocket=FakeWebSocket(),
        driver_protocol=1,
        driver_generation=8,
        driver_capabilities=desktop_environment.DRIVER_OBSERVATION_TOOL_NAMES,
    )
    desktop_environment._connections[connection.binding_id] = connection
    desktop_environment._connections_by_connector[connection.connector_id] = connection

    result = await desktop_environment.invoke(
        connector_id="connector-1",
        principal_id="user-1",
        workspace_id="workspace-1",
        tool_name="driver__snapshot_window",
        arguments={"pid": 42, "window_id": "7"},
    )

    artifact = result["artifacts"][0]
    assert artifact["download_path"] == (
        "/api/agentive/desktop-environments/artifacts/artifact-1"
    )
    stored = desktop_environment.read_artifact(
        "artifact-1", principal_id="user-1", workspace_id="workspace-1"
    )
    assert stored is not None
    assert stored.data == screenshot
    assert (
        desktop_environment.read_artifact(
            "artifact-1", principal_id="other-user", workspace_id="workspace-1"
        )
        is None
    )


def test_partial_driver_artifact_is_discarded_when_control_result_arrives() -> None:
    """A host cannot retain partial screenshot memory after ending a call."""
    import hashlib
    import json

    loop = asyncio.new_event_loop()
    try:
        future = loop.create_future()
        connection = desktop_environment.DesktopConnection(
            binding_id="binding-partial",
            principal_id="user-1",
            workspace_id="workspace-1",
            connector_id="connector-1",
            websocket=SimpleNamespace(),
            driver_protocol=1,
            driver_generation=1,
            pending={"call-1": future},
        )
        complete = b"\x89PNG\r\n\x1a\ncomplete"
        first = complete[:8]
        digest = hashlib.sha256(complete).hexdigest()
        header = json.dumps(
            {
                "type": "driver_artifact_chunk",
                "call_id": "call-1",
                "artifact_id": "artifact-partial",
                "binding_generation": connection.binding_id,
                "runtime_generation": 1,
                "sequence": 0,
                "final": False,
                "content_type": "image/png",
                "total_bytes": len(complete),
                "sha256": digest,
            },
            separators=(",", ":"),
        ).encode()
        desktop_environment.accept_artifact_frame(
            connection, len(header).to_bytes(4, "big") + header + first
        )

        desktop_environment.accept_result(
            connection,
            {
                "type": "driver_result",
                "id": "call-1",
                "ok": True,
                "runtime_generation": 1,
                "data": {},
                "artifacts": [],
            },
        )

        assert connection.artifact_uploads == {}
        with pytest.raises(
            desktop_environment.DesktopEnvironmentError,
            match="final chunk",
        ):
            future.result()
    finally:
        loop.close()


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


@pytest.mark.asyncio
async def test_driver_act_requires_fresh_snapshot_token_and_local_action_lease() -> None:
    """Background actions never dispatch without a snapshot token or advertised lease."""
    with pytest.raises(
        desktop_environment.DesktopEnvironmentError,
        match="declared schema",
    ):
        await desktop_environment.invoke(
            connector_id="connector-1",
            principal_id="user-1",
            workspace_id="workspace-1",
            tool_name="driver__act",
            arguments={"action": "click", "pid": 42, "window_id": "7"},
        )

    sent: list[str] = []

    class FakeWebSocket:
        async def send_text(self: "FakeWebSocket", raw: str) -> None:
            sent.append(raw)
            import json

            message = json.loads(raw)
            desktop_environment.accept_result(
                connection,
                {
                    "type": "driver_result",
                    "id": message["id"],
                    "ok": True,
                    "runtime_generation": 4,
                    "content_untrusted": True,
                    "data": {"outcome": "completed", "action": "click"},
                    "artifacts": [],
                },
            )

    connection = desktop_environment.DesktopConnection(
        binding_id="binding-act",
        principal_id="user-1",
        workspace_id="workspace-1",
        connector_id="connector-act",
        websocket=FakeWebSocket(),
        driver_protocol=1,
        driver_generation=4,
        driver_capabilities=desktop_environment.DRIVER_OBSERVATION_TOOL_NAMES,
    )
    desktop_environment._connections[connection.binding_id] = connection
    desktop_environment._connections_by_connector[connection.connector_id] = connection

    with pytest.raises(
        desktop_environment.DesktopEnvironmentError,
        match="does not expose computer use",
    ):
        await desktop_environment.invoke(
            connector_id="connector-act",
            principal_id="user-1",
            workspace_id="workspace-1",
            tool_name="driver__act",
            arguments={
                "action": "click",
                "pid": 42,
                "window_id": "7",
                "snapshot_id": "snapshot-1",
                "element_token": "token-7",
            },
        )
    assert sent == []

    connection.driver_capabilities = desktop_environment.DRIVER_TOOL_NAMES
    result = await desktop_environment.invoke(
        connector_id="connector-act",
        principal_id="user-1",
        workspace_id="workspace-1",
        tool_name="driver__act",
        arguments={
            "action": "click",
            "pid": 42,
            "window_id": "7",
            "snapshot_id": "snapshot-1",
            "element_token": "token-7",
        },
    )
    assert result["data"]["outcome"] == "completed"
    assert result["content_untrusted"] is True
