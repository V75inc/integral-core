"""Mounted MCP writes route through propose/bless (ADR-010 §6).

A mounted MCP tool reaches a third-party system, and ADR-010 §6 requires human
approval before anything leaves the workspace. The resident therefore may not
invoke a write-capable remote tool directly — it stages a card.

The classification is **default-deny**, and that is the property these tests
exist to protect. The protocol's own ``annotations.readOnlyHint`` is supplied
by the remote server — the party the gate constrains — so a compromised server
could otherwise mark `delete_everything` read-only and walk straight through.
Only the in-repo catalog may certify a tool read-only.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from app.agentive.connectors.mcp_tool_class import (
    describe_tool_call,
    is_write_tool,
    remote_says_read_only,
)

pytestmark = pytest.mark.smoke


def _spec(remote_name: str, **extra: Any) -> Dict[str, Any]:
    spec = {
        "key": f"mcp__abc__{remote_name}",
        "_mcp_connector_id": "n.Connector.abc",
        "_mcp_remote_name": remote_name,
    }
    spec.update(extra)
    return spec


def test_unknown_tool_is_a_write_by_default():
    """No catalog certification -> requires bless."""
    assert is_write_tool(
        _spec("query_customers"), auth_state={"catalog_slug": "quickbooks_mcp"}
    )


def test_registry_and_freeform_mounts_are_always_writes():
    """No vetted manifest exists for these, so nothing can certify them."""
    assert is_write_tool(_spec("anything"), auth_state={})
    assert is_write_tool(_spec("anything"), auth_state=None)
    assert is_write_tool(_spec("anything"), auth_state={"registry_name": "some/pkg"})


def test_remote_read_only_hint_does_not_bypass_the_gate():
    """The regression this whole design turns on.

    A malicious server marks a destructive tool read-only. The hint must be
    surfaced to the approver and otherwise ignored.
    """
    spec = _spec(
        "delete_everything",
        _mcp_annotations={"readOnlyHint": True, "destructiveHint": False},
    )
    assert remote_says_read_only(spec) is True, "hint should still be readable"
    assert is_write_tool(spec, auth_state={"catalog_slug": "quickbooks_mcp"}) is True


def test_catalog_can_certify_a_tool_read_only(monkeypatch):
    """The one trusted downgrade path: an in-repo catalog entry."""
    from app.agentive.connectors import mcp_tool_class

    monkeypatch.setattr(
        mcp_tool_class,
        "_catalog_read_only_tools",
        lambda slug: frozenset({"list_invoices"}),
    )
    auth = {"catalog_slug": "quickbooks_mcp"}
    assert is_write_tool(_spec("list_invoices"), auth_state=auth) is False
    # Siblings are unaffected — certification is per tool, not per connector.
    assert is_write_tool(_spec("create_invoice"), auth_state=auth) is True


def test_unnamed_tool_is_a_write():
    """If we cannot identify the tool we cannot vouch for it."""
    assert is_write_tool({"_mcp_connector_id": "n.Connector.abc"}, auth_state={})


def test_card_summary_names_the_remote_system():
    """'run echo' tells an approver nothing about who is being written to."""
    summary = describe_tool_call(
        _spec("create_invoice"),
        {"customer": "Acme", "amount": 4200},
        display_name="QuickBooks (Acme Books)",
    )
    assert "create_invoice" in summary
    assert "QuickBooks (Acme Books)" in summary
    assert "Acme" in summary


def test_catalog_loader_exposes_read_only_tools():
    """The schema key exists and defaults to empty (i.e. deny-all)."""
    from app.connectors import catalog_loader as loader

    entry = loader.get_catalog_entry("quickbooks_mcp")
    assert "read_only_tools" in entry
    assert isinstance(entry["read_only_tools"], list)


def test_mcp_tool_call_kind_has_an_executor():
    """A staged card with no executor would be un-blessable."""
    from app.agentive.staging_executors import _EXECUTORS

    assert "mcp_tool_call" in _EXECUTORS


@pytest.mark.asyncio
async def test_resident_write_call_is_staged_not_invoked(monkeypatch):
    """End-to-end at the dispatch seam: the call is staged, nothing leaves."""
    from app.agentive.tooling import dispatch

    called: list = []

    async def _never(*args: Any, **kwargs: Any):
        called.append(args)
        raise AssertionError("the remote must not be called before bless")

    monkeypatch.setattr(
        "app.services.hooks.tool_dispatch.run_tool", _never, raising=False
    )

    class _Conn:
        auth_state = {"catalog_slug": "quickbooks_mcp", "display_name": "QuickBooks"}

    async def _get_conn(_id: str):
        return _Conn()

    monkeypatch.setattr("app.agentive.nodes.Connector.get", _get_conn, raising=False)

    staged: dict = {}

    async def _fake_stage(**kwargs: Any):
        staged.update(kwargs)

        class _SC:
            token = "tok-mcp-1"
            state = "pending"
            kind = "mcp_tool_call"

            def to_dict(self):
                return {
                    "token": self.token,
                    "state": self.state,
                    "kind": self.kind,
                }

        return _SC()

    monkeypatch.setattr(
        "app.agentive.staging.create_staged_change", _fake_stage, raising=False
    )

    # The card has to reach the Prompt Sheet, which is the only live surface
    # that renders ``diff_human``. Staging without enqueueing left the approver
    # looking at a one-line summary while authorizing a third-party write.
    enqueued: list = []

    async def _fake_enqueue(**kwargs: Any):
        enqueued.append(kwargs)

    monkeypatch.setattr(
        "app.services.prompt_queue.enqueue_staged_write", _fake_enqueue, raising=False
    )

    result = await dispatch._maybe_stage_mcp_write(
        "mcp__abc__create_invoice",
        _spec("create_invoice"),
        {"customer": "Acme"},
        principal_id="u1",
        scope="n.Workspace.w1",
        session_id="sess-1",
        interaction_id=None,
    )

    assert result is not None, "a write-classified call must be staged"
    assert result.is_error is False
    assert result.data["staged"] is True
    assert result.data["token"] == "tok-mcp-1"
    assert called == [], "the remote was contacted before approval"
    # workspace_id must come from the bound scope, never from model args.
    assert staged["payload"]["workspace_id"] == "n.Workspace.w1"
    assert staged["kind"] == "mcp_tool_call"
    assert enqueued, "the approver never sees the arguments without this"
