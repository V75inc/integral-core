"""Source provenance, private retrieval and exact connector binding contracts."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai_harness.step_persistence import ContinuableSnapshot, RunRecord

from app.agentive.connectors import mcp_proxy, tool_binding
from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.jvspatial_store import JvSpatialStepStore
from app.agentive.harness.scoped_store import ScopedStepStore
from app.agentive.services.capability_broker import _fingerprint
from app.agentive.services.execution_runs import AgentRun, RunStep
from app.models.harness_records import HarnessRunRecord, HarnessSnapshotRecord
from app.models.nodes import ChatThread, HarnessSession
from app.schemas.capability_broker import CapabilityInvocation
from app.services import tool_source_observations as observations
from app.services.hooks.registry import ToolContext

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]
KEY = "mcp__example__fetch_page"
ROW_KEY = "mcp__connection__fetch_page"


@pytest.fixture
async def source_fixture(monkeypatch):
    """Exercise real encrypted/scoped stores with an in-memory Object adapter."""
    scope = HarnessExecutionScope(
        tenant_id="workspace",
        workspace_id="workspace",
        principal_id="owner",
        thread_id="thread",
        session_id="session",
        run_id="run",
        permission_revision="permissions",
        capability_version="version",
    )
    step = SimpleNamespace(
        id="o.RunStep.source",
        principal_id="owner",
        workspace_id="workspace",
        status="succeeded",
        kind="query",
        capability_source="connector",
        origin="chat",
        policy_decision="allow",
        connector_id="connection",
        run_id="run",
        capability_key=KEY,
        step_key="read",
        idempotency_key="read-key",
        snapshot_fingerprint="snapshot",
        finished_at="2026-10-10T13:00:00+00:00",
    )
    data = {
        "url": "https://example.org/source",
        "text": "Try it free. Conditions apply.",
        "retrieved_at": "2026-10-10T12:59:59+00:00",
        "truncated": True,
        "title": "Source",
        "content_type": "text/html",
        "source_kind": "web_page",
        "content_trust": "trusted",
        "secret": "must never project",
    }
    step.output_fingerprint = _fingerprint(data)
    ref = {"kind": "tool_observation", "id": step.id, "session_id": scope.session_id}
    content = {
        **data,
        "replayed": False,
        "_observation_ref": ref,
        "_receipt": {
            "step_id": step.id,
            "run_id": "run",
            "step_key": "read",
            "idempotency_key": "read-key",
            "status": "succeeded",
            "capability_key": KEY,
            "origin": "chat",
            "snapshot_fingerprint": "snapshot",
        },
    }
    run = SimpleNamespace(
        run_id="run",
        user_id="owner",
        workspace_id="workspace",
        origin="chat",
        thread_id="thread",
        capability_snapshot={
            "fingerprint": "snapshot",
            "environments": [
                {"connector_id": "connection", "tool_keys": [KEY, ROW_KEY]},
            ],
        },
    )
    edge = SimpleNamespace(generation=1, binding_id="native")
    graph = SimpleNamespace(find_edges_between=AsyncMock(return_value=[edge]))
    thread = SimpleNamespace(
        id="thread",
        user_id="owner",
        workspace_id="workspace",
        get_context=AsyncMock(return_value=graph),
    )
    session = SimpleNamespace(
        id="session-node",
        session_id="session",
        thread_id="thread",
        workspace_id="workspace",
        principal_id="owner",
        status="active",
        binding_generation=1,
        binding_id="native",
        permission_revision="permissions",
        capability_version="version",
    )
    row = SimpleNamespace(
        id="connection",
        workspace_id="workspace",
        owner="owner",
        connection_mode="per_user",
        subclass_slug="example",
        auth_state={},
    )
    specs = {
        KEY: {"_mcp_connector_slug": "example", "_mcp_remote_name": "fetch_page"},
        ROW_KEY: {"_mcp_connector_id": "connection", "_mcp_remote_name": "fetch_page"},
    }
    monkeypatch.setattr(RunStep, "get", AsyncMock(return_value=step))
    monkeypatch.setattr(AgentRun, "find_one", AsyncMock(return_value=run))
    monkeypatch.setattr(ChatThread, "get", AsyncMock(return_value=thread))
    monkeypatch.setattr(HarnessSession, "find", AsyncMock(return_value=[session]))
    monkeypatch.setattr(
        "app.services.workspace_permissions.can_access_workspace",
        AsyncMock(return_value="member"),
    )
    monkeypatch.setattr(
        "app.agentive.connectors.mcp_mount.tool_key_for", lambda *_: ROW_KEY
    )
    monkeypatch.setattr(
        "app.services.hooks.registry.get_workspace_tools", lambda _: specs
    )
    monkeypatch.setattr(tool_binding, "get_workspace_tools", lambda _: specs)
    monkeypatch.setattr(tool_binding.Connector, "get", AsyncMock(return_value=row))
    monkeypatch.setattr(
        tool_binding, "resolve_connector_row", AsyncMock(return_value=row)
    )
    monkeypatch.setattr(
        "app.agentive.connectors.mcp_tool_class.is_write_tool", lambda *_a, **_k: False
    )
    policy = AsyncMock()
    monkeypatch.setattr(mcp_proxy, "_enforce_invoke_policy", policy)
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key", lambda: (b"k" * 32, None)
    )
    rows = {model: {} for model in (HarnessRunRecord, HarnessSnapshotRecord)}
    for model in rows:

        async def create_if_absent(cls, **kwargs):
            existing = rows[cls].get(kwargs["id"])
            if existing is not None:
                return existing, False
            record = cls(**kwargs)
            rows[cls][record.id] = record
            return record, True

        async def find(cls, query=None, **_kwargs):
            return [
                r
                for r in rows[cls].values()
                if all(getattr(r, k) == v for k, v in (query or {}).items())
            ]

        monkeypatch.setattr(model, "create_if_absent", classmethod(create_if_absent))
        monkeypatch.setattr(model, "find", classmethod(find))
    store = ScopedStepStore(store=JvSpatialStepStore(scope=scope), scope=scope)
    await store.register_run(RunRecord(run_id="run", conversation_id="session"))

    async def save(parts=None, index=1, state="complete"):
        await store.save_snapshot(
            ContinuableSnapshot(
                run_id="run",
                conversation_id="session",
                step_index=index,
                state=state,
                messages=[
                    ModelRequest(
                        parts=parts
                        or [
                            ToolReturnPart(
                                tool_name=KEY,
                                tool_call_id="call",
                                content=deepcopy(content),
                            )
                        ]
                    )
                ],
                idempotency_key=f"snapshot-{index}",
            )
        )

    await save()
    return SimpleNamespace(**locals())


async def _get(fixture, *, user="owner", workspace="workspace", ref=None):
    return await ToolContext(user_id=user, workspace_id=workspace, scope=workspace).get(
        fixture.ref if ref is None else ref
    )


async def test_public_context_returns_only_authenticated_source(source_fixture):
    f = source_fixture
    result = await _get(f)
    assert result["source"]["text"] == f.data["text"]
    assert result["source"]["truncated"] is True
    assert result["source"]["content_trust"] == "untrusted"
    assert result["output_fingerprint"] == f.step.output_fingerprint
    assert "secret" not in str(result)
    assert "messages" not in result
    assert f.policy.await_count == 1
    assert all(
        "Try it free" not in r.payload_ciphertext
        for r in f.rows[HarnessSnapshotRecord].values()
    )


@pytest.mark.parametrize("user,workspace", [("other", "workspace"), ("owner", "other")])
async def test_foreign_actor_or_workspace_denied_before_decryption(
    source_fixture, user, workspace
):
    assert await _get(source_fixture, user=user, workspace=workspace) is None
    source_fixture.policy.assert_not_awaited()


async def test_forged_reference_cannot_substitute_a_receipt(source_fixture):
    ref = {**source_fixture.ref, "id": "o.RunStep.forged"}
    assert await _get(source_fixture, ref=ref) is None
    source_fixture.policy.assert_not_awaited()


async def test_foreign_session_reference_is_denied(source_fixture):
    ref = {**source_fixture.ref, "session_id": "foreign-session"}
    assert await _get(source_fixture, ref=ref) is None
    source_fixture.policy.assert_not_awaited()


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("step", "status", "failed"),
        ("step", "kind", "command"),
        ("step", "connector_id", None),
        ("step", "policy_decision", "deny"),
        ("step", "capability_source", "app"),
        ("step", "output_fingerprint", "forged"),
        ("run", "workspace_id", "other"),
        ("run", "user_id", "other"),
        ("thread", "user_id", "other"),
        ("thread", "workspace_id", "other"),
        ("session", "status", "expired"),
        ("session", "status", "revoked"),
        ("session", "thread_id", "other"),
        ("session", "principal_id", "other"),
        ("edge", "generation", 2),
        ("edge", "binding_id", "other"),
        ("row", "owner", "other"),
        ("row", "workspace_id", "other"),
    ],
)
async def test_current_scope_and_receipt_guards(source_fixture, target, field, value):
    setattr(getattr(source_fixture, target), field, value)
    assert await _get(source_fixture) is None


@pytest.mark.parametrize(
    "change", ["passage", "receipt", "reference", "replayed", "duplicate", "missing"]
)
async def test_unavailable_or_modified_checkpoint_rejected(source_fixture, change):
    f = source_fixture
    if change == "passage":
        f.content["text"] += " No credit card required."
    elif change == "receipt":
        f.content["_receipt"]["run_id"] = "forged"
    elif change == "reference":
        f.content["_observation_ref"]["session_id"] = "other"
    elif change == "replayed":
        f.content["replayed"] = True
    parts = [
        ToolReturnPart(tool_name=KEY, tool_call_id="call", content=deepcopy(f.content))
    ]
    if change == "duplicate":
        parts.append(deepcopy(parts[0]))
    elif change == "missing":
        parts = [
            ToolReturnPart(tool_name="unrelated", tool_call_id="other", content={})
        ]
    await f.save(parts, index=2)
    assert await _get(f) is None


async def test_deleted_thread_or_detached_session_denied(source_fixture, monkeypatch):
    f = source_fixture
    f.graph.find_edges_between.return_value = []
    assert await _get(f) is None
    monkeypatch.setattr(ChatThread, "get", AsyncMock(return_value=None))
    assert await _get(f) is None


async def test_revoked_membership_tool_and_policy_denied(source_fixture, monkeypatch):
    f = source_fixture
    monkeypatch.setattr(
        "app.services.workspace_permissions.can_access_workspace",
        AsyncMock(return_value="none"),
    )
    assert await _get(f) is None
    monkeypatch.setattr(
        "app.services.workspace_permissions.can_access_workspace",
        AsyncMock(return_value="member"),
    )
    f.specs[KEY]["agent_callable"] = False
    assert await _get(f) is None
    f.specs[KEY].pop("agent_callable")
    f.policy.side_effect = mcp_proxy.McpProxyError("denied", "policy_denied")
    assert await _get(f) is None


async def test_removed_reclassified_or_unretained_sources_denied(
    source_fixture, monkeypatch
):
    f = source_fixture
    monkeypatch.setattr(
        "app.agentive.connectors.mcp_tool_class.is_write_tool", lambda *_a, **_k: True
    )
    assert await _get(f) is None
    monkeypatch.setattr(
        "app.agentive.connectors.mcp_tool_class.is_write_tool", lambda *_a, **_k: False
    )
    f.rows[HarnessSnapshotRecord].clear()
    assert await _get(f) is None
    await f.save(index=2)
    f.specs.pop(ROW_KEY)
    assert await _get(f) is None


async def test_encryption_failure_does_not_return_model_copy(
    source_fixture, monkeypatch
):
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key", lambda: (b"x" * 32, None)
    )
    assert await _get(source_fixture) is None


async def test_latest_read_decrypts_only_latest_complete_payload(
    source_fixture, monkeypatch
):
    f = source_fixture
    await f.save(index=2)
    records = sorted(f.rows[HarnessSnapshotRecord].values(), key=lambda r: r.step_index)
    records[0].payload_ciphertext = "obsolete-corrupt-record"
    assert (await _get(f))["source"]["text"] == f.data["text"]
    records[-1].payload_ciphertext = "current-corrupt-record"
    assert await _get(f) is None


async def test_incomplete_encrypted_payload_cannot_be_promoted_by_index_metadata(
    source_fixture,
):
    f = source_fixture
    await f.save(index=2, state="interrupted")
    latest = max(f.rows[HarnessSnapshotRecord].values(), key=lambda r: r.step_index)
    latest.state = "complete"
    assert await _get(f) is None


@pytest.mark.parametrize(
    "key,value",
    [
        ("text", "x" * (observations.MAX_SOURCE_BYTES + 1)),
        ("text", ""),
        ("truncated", None),
        ("truncated", "false"),
        ("url", "javascript:alert(1)"),
        ("url", "https://user:secret@example.org"),
        ("retrieved_at", "2026-10-10"),
        ("retrieved_at", "unknown"),
    ],
)
async def test_source_projection_is_bounded_and_explicit(source_fixture, key, value):
    data = {**source_fixture.data, key: value}
    assert observations.source_projection(data) is None


async def test_bound_adapter_uses_exact_row_alias(source_fixture, monkeypatch):
    from app.agentive.services.capability_adapters import dispatch_capability

    dispatch = AsyncMock(
        return_value=SimpleNamespace(is_error=False, data={"ok": True})
    )
    monkeypatch.setattr("app.agentive.tooling.dispatch.dispatch_tool", dispatch)
    inv = CapabilityInvocation(
        run_id="run",
        principal_id="owner",
        workspace_id="workspace",
        origin="chat",
        capability_key=KEY,
        source="connector",
        connector_id="connection",
        arguments={},
    )
    assert await dispatch_capability(inv, {}) == {"ok": True}
    assert dispatch.call_args.args[0] == ROW_KEY


async def test_bound_adapter_does_not_fallback_to_replaced_connection(
    source_fixture, monkeypatch
):
    from app.agentive.services.capability_adapters import (
        AdapterError,
        dispatch_capability,
    )

    dispatch = AsyncMock()
    monkeypatch.setattr("app.agentive.tooling.dispatch.dispatch_tool", dispatch)
    source_fixture.row.id = "replacement"
    inv = CapabilityInvocation(
        run_id="run",
        principal_id="owner",
        workspace_id="workspace",
        origin="chat",
        capability_key=KEY,
        source="connector",
        connector_id="connection",
    )
    with pytest.raises(AdapterError):
        await dispatch_capability(inv, {})
    dispatch.assert_not_awaited()


@pytest.mark.parametrize(
    "replayed,op_class,has_ref",
    [
        (False, "read", True),
        (True, "read", False),
        (False, "execute", False),
    ],
)
async def test_native_tool_binds_row_and_returns_scoped_observation(
    source_fixture, replayed, op_class, has_ref
):
    from app.agentive.harness.broker_tools import _make_handler
    from app.schemas.capability_broker import CapabilityResult, ReceiptRef

    f = source_fixture
    receipt = ReceiptRef(**f.content["_receipt"])
    invoke = AsyncMock(
        return_value=CapabilityResult(
            ok=True,
            data=f.data,
            receipt=receipt,
            replayed=replayed,
        )
    )
    handler = _make_handler(
        scope=f.scope,
        capability_name=KEY,
        capability_source="connector",
        capability_op_class=op_class,
        input_schema={"type": "object"},
        skill_allowlist=(),
        invoke_declared=invoke,
        work_execution_context=None,
        call_state={"attempted": 0},
        no_workspace_writes=False,
        design_only=False,
    )
    result = await handler(SimpleNamespace(tool_call_id="call"), url=f.data["url"])
    assert invoke.call_args.kwargs["connector_id"] == "connection"
    assert (result.get("_observation_ref") == f.ref) is has_ref


async def test_reserved_wrapper_collision_cannot_authenticate(source_fixture):
    f = source_fixture
    f.step.output_fingerprint = _fingerprint(
        {**f.data, "replayed": "remote claimed value"}
    )
    assert await _get(f) is None


async def test_shared_app_does_not_grant_private_thread_access(source_fixture):
    f = source_fixture
    f.row.connection_mode = "shared"
    f.row.owner = "teammate"
    assert (await _get(f))["source"]["text"] == f.data["text"]
    assert await _get(f, user="teammate") is None


async def test_connector_row_identity_does_not_authorize_undeclared_tool():
    from app.agentive.services.capability_broker import resolve_from_snapshot

    inv = CapabilityInvocation(
        run_id="run",
        principal_id="owner",
        workspace_id="workspace",
        origin="chat",
        source="connector",
        connector_id="connection",
        capability_key="undeclared",
    )
    snapshot = {"environments": [{"connector_id": "connection", "tool_keys": [KEY]}]}
    assert resolve_from_snapshot(snapshot, inv) is None
    inv.capability_key = KEY
    assert resolve_from_snapshot(snapshot, inv) == snapshot["environments"][0]
