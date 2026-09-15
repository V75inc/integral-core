"""D-11 vendor-agnostic dispatch smoke test.

Proves /agentive/chat dispatches by AgentConfig.agent_type WITHOUT type-narrowing
on jvagent. Registering AgentConfig(agent_type='mcp') routes to McpStubConnector;
registering AgentConfig(agent_type='jvagent') routes to JvAgentConnector. This is
the proof-of-vendor-neutrality artifact called out in CONTEXT.md <specifics>.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_byoa_mcp_routes_to_stub(authenticated_client, monkeypatch):
    """agent_type='mcp' → McpStubConnector returns echo `[mcp-stub] received: ...`."""
    from app.agentive.services import uplink_registry as uplink_registry_mod

    class _McpConfig:
        agent_type = "mcp"
        preferences = {"mcp_endpoint": "http://stub.example.com"}

    async def _mock_get_system():
        return _McpConfig()

    monkeypatch.setattr(
        uplink_registry_mod.uplink_registry, "get_system_agent", _mock_get_system
    )
    # CRITICAL: do NOT monkeypatch get_chat_connector — we WANT real registry resolution
    # to prove the registry routes correctly to McpStubConnector by agent_type.

    r = await authenticated_client.post(
        "/api/agentive/chat/message",
        json={"message": "hello"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["agent_type"] == "mcp"
    # The stub's deterministic echo:
    assert body["message"].startswith("[mcp-stub] received:"), body["message"]
    assert "hello" in body["message"]


@pytest.mark.asyncio
async def test_byoa_jvagent_routes_to_jvagent(authenticated_client, monkeypatch):
    """agent_type='jvagent' → JvAgentConnector; does NOT route to mcp.

    Patches `JvAgentConnector.send_turn` directly (rather than `httpx.AsyncClient.post`)
    because the in-process httpx test client itself uses `AsyncClient.post` to talk
    to the FastAPI app — patching at the httpx level would intercept the test request
    itself. Patching at the connector level still proves vendor-neutral routing:
    the registry must resolve `agent_type="jvagent"` to `JvAgentConnector` for our
    patched `send_turn` to be invoked at all.
    """
    from app.agentive.connectors.base import ChatTurnResult
    from app.agentive.connectors.jvagent_connector import JvAgentConnector
    from app.agentive.services import uplink_registry as uplink_registry_mod

    class _JvConfig:
        agent_type = "jvagent"
        preferences = {
            "jvagent_base_url": "http://jvagent.test",
            "jvagent_agent_id": "test-agent",
        }

    async def _mock_get_system():
        return _JvConfig()

    # Capture which connector class actually got invoked. The registry-resolution
    # path is the architectural proof: if `chat.py` had a `== "jvagent"` branch
    # bypassing the registry, this patch wouldn't fire and the test would fail.
    invocations: list[type] = []

    async def _patched_send_turn(self, ctx, *, preferences):
        invocations.append(type(self))
        return ChatTurnResult(
            message="jvagent response text",
            session_id="jvs-1",
            agent_user_id="uid-1",
        )

    monkeypatch.setattr(
        uplink_registry_mod.uplink_registry, "get_system_agent", _mock_get_system
    )
    monkeypatch.setattr(JvAgentConnector, "send_turn", _patched_send_turn)

    r = await authenticated_client.post(
        "/api/agentive/chat/message",
        json={"message": "hello"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "agent_type" in body, f"missing agent_type in body: {body!r}"
    assert body["agent_type"] == "jvagent"
    # Critically: the response is NOT the [mcp-stub] echo — proves routing went to jvagent.
    assert not body["message"].startswith(
        "[mcp-stub]"
    ), f"jvagent agent_type incorrectly routed to mcp stub: {body!r}"
    # And the message reflects the mocked jvagent payload.
    assert body["message"] == "jvagent response text"
    assert body["session_id"] == "jvs-1"
    # And specifically JvAgentConnector handled the turn (registry routed correctly).
    assert invocations and invocations[0] is JvAgentConnector, (
        f"registry did not route agent_type='jvagent' to JvAgentConnector: "
        f"invocations={invocations!r}"
    )


def test_byoa_no_jvagent_dispatch_branches_outside_jvagent_connector_module():
    """D-11 invariant (B2 revision): no `== "jvagent"` Compare branches and no
    `case "jvagent":` match patterns exist OUTSIDE `connectors/jvagent_connector.py`
    and `agentive/types.py`.

    Re-asserted here at the dispatch-test layer because regressions to vendor neutrality
    often surface as new branches in chat.py / registry.py first.

    AST-based per B2 revision: the prior grep approach false-positived on legitimate
    side-effect imports like `from app.agentive.connectors import jvagent_connector`
    in registry.py and `from app.agentive.api import ... jvagent_connector` in
    __init__.py. We now walk the AST and look ONLY for actual dispatch branches
    (Compare with Eq op against Constant("jvagent") and match-case patterns).
    Imports / type aliases / comments / docstrings are naturally ignored.
    """
    repo_root = Path(__file__).resolve().parents[2]
    agentive_dir = repo_root / "backend" / "app" / "agentive"
    offending: list[str] = []

    for py_path in agentive_dir.rglob("*.py"):
        rel = str(py_path.relative_to(repo_root))
        if rel.endswith("connectors/jvagent_connector.py"):
            continue
        if rel.endswith("agentive/types.py"):
            continue
        try:
            tree = ast.parse(py_path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            # Pattern 1: `<expr> == "jvagent"` Compare
            if isinstance(node, ast.Compare):
                if any(isinstance(op, ast.Eq) for op in node.ops):
                    operands = [node.left] + list(node.comparators)
                    for operand in operands:
                        if (
                            isinstance(operand, ast.Constant)
                            and isinstance(operand.value, str)
                            and operand.value == "jvagent"
                        ):
                            offending.append(
                                f"{rel}:{node.lineno}: == 'jvagent' branch"
                            )
                            break
            # Pattern 2: `match x: case "jvagent": ...`
            if isinstance(node, ast.match_case):
                pat = node.pattern
                if (
                    isinstance(pat, ast.MatchValue)
                    and isinstance(pat.value, ast.Constant)
                    and pat.value.value == "jvagent"
                ):
                    offending.append(f"{rel}:{pat.value.lineno}: match case 'jvagent'")

    assert not offending, (
        "Vendor-neutrality regression — jvagent dispatch branches found outside "
        "jvagent_connector.py / types.py:\n" + "\n".join(offending)
    )
