"""Outbound MCP client: the seams that decide whether Integral is usable.

Each test here corresponds to a defect that shipped and was verified against
the live catalog servers on 2026-09-10. They are grouped by the property they
protect rather than by module, because the defects crossed module boundaries
(a catalog file, a spec builder, a staging key, a dispatch seam).
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

# Imported at module scope on purpose: importing an ``app`` module loads
# ``app.config``, which populates os.environ. Done inside a test body that
# lands in the conftest env-leak guard at teardown, so it has to happen at
# collection time instead.
from app.agentive import staging
from app.agentive.connectors.mcp_adapter import (
    _persistable_discovered,
    _specs_from_discovered,
    _tool_spec_from_remote,
)
from app.agentive.connectors.mcp_adapter import mcp_bundle_slug as adapter_slug
from app.agentive.connectors.mcp_adapter import (
    mcp_tool_name,
)
from app.agentive.connectors.mcp_client import _build_spawn_env
from app.agentive.connectors.mcp_mount import mcp_bundle_slug as mount_slug
from app.agentive.connectors.mcp_mount import tool_key_for
from app.agentive.connectors.mcp_tool_class import (
    describe_tool_call,
    is_write_tool,
    remote_says_read_only,
)
from app.agentive.staging import autonomy_key_for
from app.agentive.tooling import dispatch
from app.connectors.catalog_loader import load_catalog
from app.schemas.agentive.connectors import (
    contains_redaction_marker,
    mcp_safe_auth_state,
)

pytestmark = pytest.mark.smoke


# ---------------------------------------------------------------------------
# Reads must not require a human approval click
# ---------------------------------------------------------------------------


def _spec(remote_name: str) -> Dict[str, Any]:
    return {
        "key": f"mcp__abc__{remote_name}",
        "_mcp_connector_id": "n.Connector.abc",
        "_mcp_remote_name": remote_name,
    }


@pytest.mark.parametrize(
    "slug,tool",
    [
        ("google_drive", "search_files"),
        ("google_drive", "read_file_content"),
        ("google_drive", "get_file_metadata"),
        ("google_gmail", "search_threads"),
        ("google_gmail", "get_thread"),
        ("google_gmail", "list_labels"),
        ("google_sheets", "get_values"),
        ("google_sheets", "get_spreadsheet"),
    ],
)
def test_certified_reads_run_without_approval(slug: str, tool: str):
    """The usability half of ADR-010 §6.

    Default-deny classification is correct, but with no catalog naming a
    single read-only tool, EVERY mounted call staged a card — searching Drive
    required a human click. A gate that fires on reads is one users learn to
    click through, which is worse than no gate.
    """
    assert not is_write_tool(_spec(tool), auth_state={"catalog_slug": slug})


@pytest.mark.parametrize(
    "slug,tool",
    [
        ("google_drive", "create_file"),
        ("google_drive", "copy_file"),
        ("google_gmail", "create_draft"),
        ("google_gmail", "trash_thread"),
        ("google_gmail", "apply_sensitive_thread_label"),
        ("google_sheets", "update_values"),
        ("google_sheets", "insert_dimension"),
        # Uncertified connectors stay fully gated.
        ("notion", "search"),
        ("calendly", "list_events"),
        ("quickbooks_mcp", "query_customers"),
        # A tool the catalog does not name is a write even on a certified
        # connector — this is what makes the list a certification and not a
        # denylist.
        ("google_drive", "some_tool_google_adds_next_year"),
    ],
)
def test_writes_and_uncertified_tools_still_stage(slug: str, tool: str):
    assert is_write_tool(_spec(tool), auth_state={"catalog_slug": slug})


def test_every_mcp_catalog_entry_declares_its_read_only_stance():
    """An absent key and an empty list mean the same thing to the gate, but
    not to a reviewer: absent reads as an oversight, ``[]`` as a decision."""
    for entry in load_catalog():
        if entry.get("kind") != "mcp":
            continue
        assert "read_only_tools" in entry, (
            f"{entry['slug']}: declare read_only_tools (use [] to state that "
            "the surface has not been transcribed yet)"
        )


# ---------------------------------------------------------------------------
# Session autonomy must not span connectors or tools
# ---------------------------------------------------------------------------


def test_autonomy_key_is_per_connector_and_per_tool():
    """ "Approve & auto-allow" on a Drive search must not pre-bless QuickBooks.

    The grant is keyed by ``kind``, and every mounted MCP call shares the kind
    ``mcp_tool_call`` — so one click on the most innocuous card in the product
    auto-approved every write to every connected third party for the session.
    """
    drive_read = autonomy_key_for(
        "mcp_tool_call",
        {"connector_id": "n.Connector.drive", "remote_name": "search_files"},
    )
    qbo_write = autonomy_key_for(
        "mcp_tool_call",
        {"connector_id": "n.Connector.qbo", "remote_name": "create_invoice"},
    )
    drive_write = autonomy_key_for(
        "mcp_tool_call",
        {"connector_id": "n.Connector.drive", "remote_name": "create_file"},
    )
    assert drive_read != qbo_write, "grant must not span connectors"
    assert drive_read != drive_write, "grant must not span tools"
    assert drive_read.startswith("mcp_tool_call:")


def test_unidentifiable_mcp_call_gets_an_unmintable_key():
    """A payload with no target must not fall back to the bare kind."""
    assert autonomy_key_for("mcp_tool_call", {}) == "mcp_tool_call:<unidentified>"
    assert autonomy_key_for("mcp_tool_call", None).endswith(":<unidentified>")


def test_ordinary_kinds_keep_kind_level_grants():
    """The narrowing is targeted; substrate kinds are unaffected."""
    assert autonomy_key_for("create_entry", {"entry_id": "x"}) == "create_entry"
    assert autonomy_key_for("update_track", None) == "update_track"


@pytest.mark.asyncio
async def test_grant_autonomy_refuses_an_untargeted_mcp_grant():
    """The public helper must not be a back door to the coarse grant."""
    staging._autonomy.pop(("u1", "s1"), None)
    await staging.grant_autonomy(user_id="u1", session_id="s1", kind="mcp_tool_call")
    assert not staging._autonomy.get(("u1", "s1"))

    await staging.grant_autonomy(
        user_id="u1",
        session_id="s1",
        kind="mcp_tool_call",
        payload={"connector_id": "c1", "remote_name": "t1"},
    )
    assert staging.has_autonomy(
        "u1", "s1", "mcp_tool_call", {"connector_id": "c1", "remote_name": "t1"}
    )
    assert not staging.has_autonomy(
        "u1", "s1", "mcp_tool_call", {"connector_id": "c2", "remote_name": "t1"}
    )
    staging._autonomy.pop(("u1", "s1"), None)


# ---------------------------------------------------------------------------
# The approver must be able to see what is being sent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_staged_external_write_reaches_the_prompt_sheet(monkeypatch):
    """``diff_human`` is rendered by the Prompt Sheet and nowhere else that is
    live — the Inbox and Approvals rows show ``summary`` alone. Without the
    enqueue, the only approval in the product that leaves the workspace was
    also the only one that never showed its payload."""

    class _Conn:
        auth_state = {"catalog_slug": "google_gmail", "display_name": "Gmail"}

    async def _get_conn(_id: str):
        return _Conn()

    monkeypatch.setattr("app.agentive.nodes.Connector.get", _get_conn, raising=False)

    class _SC:
        token = "tok-1"
        state = "pending"

        def to_dict(self):
            return {"token": self.token, "state": self.state, "kind": "mcp_tool_call"}

    async def _fake_stage(**kwargs: Any):
        return _SC()

    monkeypatch.setattr(
        "app.agentive.staging.create_staged_change", _fake_stage, raising=False
    )

    enqueued: list = []

    async def _fake_enqueue(**kwargs: Any):
        enqueued.append(kwargs)

    monkeypatch.setattr(
        "app.services.prompt_queue.enqueue_staged_write", _fake_enqueue, raising=False
    )

    res = await dispatch._maybe_stage_mcp_write(
        "mcp__abc__create_draft",
        _spec("create_draft"),
        {"to": "a@b.c", "subject": "hi"},
        principal_id="u1",
        scope="n.Workspace.w1",
        session_id="sess-1",
        interaction_id=None,
    )
    assert res is not None and res.data["staged"] is True
    assert enqueued, "the card never reached the surface that renders diff_human"
    assert enqueued[0]["session_id"] == "sess-1"


def test_card_body_lists_the_arguments():
    """What the enqueue above puts in front of the approver."""
    summary = describe_tool_call(
        _spec("create_draft"),
        {"to": "cfo@acme.example", "subject": "Q3 numbers"},
        display_name="Gmail (work)",
    )
    assert "create_draft" in summary and "Gmail (work)" in summary


# ---------------------------------------------------------------------------
# Remote metadata must survive the HTTP mount path
# ---------------------------------------------------------------------------


class _FakeTool:
    def __init__(self, name, annotations=None, title=None):
        self.name = name
        self.description = "d"
        self.inputSchema = {"type": "object", "properties": {}}
        self.annotations = annotations
        self.title = title


def test_http_mount_carries_annotations_and_title():
    """Every hosted server in the catalog is streamable_http. That path built
    specs without ``annotations``, so ``remote_says_read_only`` — the note the
    approval card shows — was structurally unreachable for all of them."""
    spec = _tool_spec_from_remote(
        "n.Connector.x",
        _FakeTool("search_threads", annotations={"readOnlyHint": True}),
    )
    assert spec["_mcp_annotations"] == {"readOnlyHint": True}
    assert remote_says_read_only(spec) is True


def test_title_read_from_either_spec_field_or_annotations():
    """Google's hosted Gmail MCP puts the label in ``annotations.title``."""
    spec_field = _tool_spec_from_remote("c", _FakeTool("t", title="Proper Title"))
    ann_field = _tool_spec_from_remote(
        "c", _FakeTool("t", annotations={"title": "Search email threads"})
    )
    assert spec_field["title"] == "Proper Title"
    assert ann_field["title"] == "Search email threads"


def test_annotations_survive_persist_and_rehydrate():
    """A restart must not silently drop the metadata."""
    live = [
        _tool_spec_from_remote(
            "n.Connector.x",
            _FakeTool("get_thread", annotations={"readOnlyHint": True}, title="Get"),
        )
    ]
    rehydrated = _specs_from_discovered("n.Connector.x", _persistable_discovered(live))
    assert rehydrated[0]["_mcp_annotations"] == {"readOnlyHint": True}
    assert rehydrated[0]["title"] == "Get"
    # The dispatch contract must survive too — this shape was uncallable once.
    assert ":" in rehydrated[0]["handler_ref"]
    assert rehydrated[0]["_mcp_remote_name"] == "get_thread"


# ---------------------------------------------------------------------------
# A redacted read must never be writable back over live credentials
# ---------------------------------------------------------------------------


def test_redaction_marker_is_detected_at_any_depth():
    assert contains_redaction_marker({"headers": {"Authorization": "[redacted]"}})
    assert contains_redaction_marker({"a": [{"b": "[redacted]"}]})
    assert contains_redaction_marker("[redacted]")
    assert not contains_redaction_marker({"headers": {"Authorization": "Bearer x"}})
    assert not contains_redaction_marker({})


def test_redacted_view_round_trips_into_the_guard():
    """Close the loop: what GET emits is exactly what PATCH must refuse."""
    safe = mcp_safe_auth_state(
        {
            "url": "https://mcp.example/mcp",
            "headers": {"Authorization": "Bearer real-token"},
            "oauth": {"status": "authorized", "access_token": "real"},
        }
    )
    assert "real-token" not in str(safe)
    assert contains_redaction_marker(safe), (
        "the Edit dialog prefills from this exact payload; the write path must "
        "reject it rather than store it"
    )


# ---------------------------------------------------------------------------
# One tool-name derivation across both mount paths
# ---------------------------------------------------------------------------


def test_both_mount_paths_derive_the_same_tool_name():
    """HTTP connectors register through ``mcp_adapter`` but rehydrate after a
    restart — and refresh — through ``mcp_mount``. While the two derivations
    disagreed, every HTTP-mounted tool silently changed name on the first
    backend restart, breaking anything holding the old one."""
    cid = "n.Connector.0123456789abcdef01234567"
    assert mcp_tool_name(cid, "search_files") == tool_key_for(cid, "search_files")
    # The unregister key has to agree too, or a refresh leaves orphan rows.
    assert adapter_slug(cid) == mount_slug(cid)


def test_tool_names_are_sanitized_for_model_apis():
    """A remote may name a tool with characters a model API rejects."""
    name = mcp_tool_name("n.Connector.abcdef123456", "weird.tool-name")
    assert all(c.isalnum() or c == "_" for c in name), name
    assert len(name) < 64


# ---------------------------------------------------------------------------
# A stdio server must keep the credentials the server itself generated
# ---------------------------------------------------------------------------


def test_quickbooks_token_store_path_survives_the_spawn_allowlist():
    """``persist_quickbooks_token_store`` writes a 0600 dotenv and passes its
    location in the child's environment. The RCE-hardening allowlist dropped
    the key, so the connector mounted, reported ``ok`` (initial discovery still
    held the provisional env) and then ran every later spawn — health, refresh,
    and every tool call — with no credentials at all."""
    env = _build_spawn_env(
        {
            "catalog_slug": "quickbooks_mcp",
            "transport": "stdio",
            "env": {
                "QUICKBOOKS_TOKEN_STORE_PATH": "/secure/qb.env",
                "QUICKBOOKS_CLIENT_ID": "cid",
                "QUICKBOOKS_REFRESH_TOKEN": "rt",
            },
        }
    )
    assert env.get("QUICKBOOKS_TOKEN_STORE_PATH") == "/secure/qb.env"
    assert env.get("QUICKBOOKS_REFRESH_TOKEN") == "rt"


def test_spawn_allowlist_still_refuses_process_hijack_keys():
    """The fix above must not widen the allowlist into the RCE it closed."""
    env = _build_spawn_env(
        {
            "catalog_slug": "quickbooks_mcp",
            "transport": "stdio",
            "env": {
                "PATH": "/tmp/evil",
                "NODE_OPTIONS": "--require /tmp/evil.js",
                "LD_PRELOAD": "/tmp/evil.so",
                "QUICKBOOKS_TOKEN_STORE_PATH": "/secure/qb.env",
            },
        }
    )
    assert env.get("PATH") != "/tmp/evil"
    assert "NODE_OPTIONS" not in env
    assert "LD_PRELOAD" not in env
