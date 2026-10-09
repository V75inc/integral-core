"""Generic external-agent dispatch uses the typed connector registry."""

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


def test_byoa_no_jvagent_dispatch_branches_anywhere():
    """The retired harness has no dispatch path anywhere in the agentive layer."""
    repo_root = Path(__file__).resolve().parents[2]
    agentive_dir = repo_root / "backend" / "app" / "agentive"
    offending: list[str] = []

    for py_path in agentive_dir.rglob("*.py"):
        rel = str(py_path.relative_to(repo_root))
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

    assert not offending, "Retired harness dispatch branches found:\n" + "\n".join(
        offending
    )
