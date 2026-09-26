"""Capability broker: snapshot authority, receipts, four-surface equivalence."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from app.agentive.services import capability_broker as broker
from app.agentive.services import execution_runs
from app.schemas.capability_broker import CapabilityInvocation


class _Record:
    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)
        self.saved = 0

    async def save(self) -> None:
        self.saved += 1


def _snapshot(*, fingerprint: str = "fp-1") -> Dict[str, Any]:
    return {
        "manifest_version": "1.0.0",
        "fingerprint": fingerprint,
        "capabilities": [
            {
                "name": "integral_list_tracks",
                "op_class": "read",
                "policy_action": "track.read",
            },
            {
                "name": "integral_create_entry",
                "op_class": "propose",
                "policy_action": "entry.create",
            },
            {
                "name": "integral_query_spec",
                "op_class": "read",
                "policy_action": None,
            },
        ],
        "apps": [
            {
                "app_id": "app-1",
                "package_slug": "hello",
                "package_version": "1.0.0",
                "operations": [
                    {"key": "echo", "kind": "execute", "policy_action": "app.read"}
                ],
                "queries": [
                    {
                        "key": "recent_open",
                        "kind": "read",
                        "handler_key": "recent_open",
                        "input_schema": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                        "output_schema": {"type": "object"},
                        "query_template": {
                            "resource": "entry",
                            "select": ["id"],
                            "filters": [],
                            "sort": [],
                            "traversal": [],
                            "limit": 10,
                            "cost_ceiling": 100,
                            "cursor": None,
                        },
                    }
                ],
            }
        ],
        "environments": [
            {
                "connector_id": "conn-1",
                "kind": "mcp",
                "subclass_slug": "mcp",
                "capabilities": ["mcp__c__ping"],
                "tool_keys": ["mcp__c__ping"],
                "permissions": ["mcp.read"],
                "health_status": "healthy",
            }
        ],
        "unresolved_apps": [],
    }


def _run(**overrides: Any) -> _Record:
    fields = {
        "run_id": "run-1",
        "user_id": "user-1",
        "workspace_id": "ws-1",
        "origin": "chat",
        "status": "running",
        "capability_snapshot": _snapshot(),
        "capability_version": "1.0.0",
        "app_id": None,
        "finished_at": None,
        "error": None,
    }
    fields.update(overrides)
    return _Record(**fields)


@pytest.fixture
def run_store(monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    runs: Dict[str, _Record] = {}
    steps: List[_Record] = []
    adapter_calls: List[str] = []
    adapter_invocations: List[CapabilityInvocation] = []
    store = {
        "runs": runs,
        "steps": steps,
        "adapter_calls": adapter_calls,
        "adapter_invocations": adapter_invocations,
    }

    async def find_run(query: Dict[str, Any]) -> Optional[_Record]:
        return runs.get(str(query.get("run_id") or ""))

    async def create_step(**fields: Any) -> _Record:
        step = _Record(**fields)
        steps.append(step)
        return step

    async def find_step(query: Dict[str, Any]) -> Optional[_Record]:
        run_id = query.get("run_id")
        key = query.get("idempotency_key")
        step_key = query.get("step_key")
        for step in steps:
            if run_id and step.run_id != run_id:
                continue
            if key and getattr(step, "idempotency_key", None) != key:
                continue
            if step_key and step.step_key != step_key:
                continue
            return step
        return None

    async def adapter(inv: CapabilityInvocation, cap: Dict[str, Any]) -> Dict[str, Any]:
        adapter_calls.append(inv.capability_key)
        adapter_invocations.append(inv.model_copy(deep=True))
        if inv.capability_key == "boom":
            raise RuntimeError("adapter exploded")
        if inv.capability_key == "integral_query_spec":
            replayed = adapter_calls.count("integral_query_spec") > 1
            return {
                "items": None if replayed else [],
                "replayed": replayed,
                "result_set_id": "result-1",
                "normalized_plan": {"resource": "entry"},
                "graph_revision": "sha256:revision",
                "item_provenance": [],
                "redaction_state": "none",
                "next_cursor": None,
            }
        if inv.source == "app" and isinstance(cap.get("query_template"), dict):
            replayed = adapter_calls.count(inv.capability_key) > 1
            return {
                "items": None if replayed else [],
                "replayed": replayed,
                "result_set_id": "result-app-query",
                "normalized_plan": {"resource": "entry"},
                "graph_revision": "sha256:app-query",
                "item_provenance": [],
                "redaction_state": "none",
                "next_cursor": None,
            }
        if inv.source == "app":
            return {"output": {"ok": True, "message": inv.arguments.get("message")}}
        if inv.op_class == "propose":
            return {"_kind": "staged_change", "token": "tok-1", "state": "pending"}
        return {"tracks": [{"title": "Seeded Track"}]}

    monkeypatch.setattr(execution_runs.AgentRun, "find_one", find_run)
    monkeypatch.setattr(execution_runs.RunStep, "find_one", find_step)
    monkeypatch.setattr(execution_runs.RunStep, "create", create_step)
    monkeypatch.setattr(broker, "_call_adapter", adapter)

    async def current_snapshot(_workspace_id: str) -> Dict[str, Any]:
        return dict(store.get("current_snapshot") or _snapshot())

    monkeypatch.setattr(execution_runs, "build_capability_snapshot", current_snapshot)
    monkeypatch.setattr(broker, "build_capability_snapshot", current_snapshot)

    async def finish(
        run_id: str, *, status: str, error: Any = None
    ) -> Optional[_Record]:
        run = runs.get(run_id)
        if run is None or run.status in {"succeeded", "failed", "cancelled"}:
            return run
        run.status = status
        run.error = error
        await run.save()
        return run

    monkeypatch.setattr(execution_runs, "finish_run", finish)
    monkeypatch.setattr(broker, "finish_run", finish)

    store["current_snapshot"] = _snapshot()
    return store


def _inv(**overrides: Any) -> CapabilityInvocation:
    fields = {
        "run_id": "run-1",
        "principal_id": "user-1",
        "workspace_id": "ws-1",
        "origin": "chat",
        "capability_key": "integral_list_tracks",
        "source": "core",
        "op_class": "read",
        "arguments": {},
    }
    fields.update(overrides)
    return CapabilityInvocation(**fields)


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_unknown_capability_fails_before_adapter(
    run_store: Dict[str, Any],
) -> None:
    run_store["runs"]["run-1"] = _run()
    result = await broker.invoke(_inv(capability_key="integral_not_a_tool"))
    assert result.ok is False
    assert result.error_code == "capability.not_in_snapshot"
    assert run_store["adapter_calls"] == []


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_workspace_mismatch_fails_before_adapter(
    run_store: Dict[str, Any],
) -> None:
    run_store["runs"]["run-1"] = _run()
    result = await broker.invoke(_inv(workspace_id="ws-other"))
    assert result.ok is False
    assert result.error_code == "capability.workspace_mismatch"
    assert run_store["adapter_calls"] == []


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_identity_mismatch_fails_before_adapter(
    run_store: Dict[str, Any],
) -> None:
    run_store["runs"]["run-1"] = _run()
    result = await broker.invoke(_inv(principal_id="user-other"))
    assert result.ok is False
    assert result.error_code == "capability.identity_mismatch"
    assert run_store["adapter_calls"] == []


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_core_read_writes_receipt_then_returns_envelope(
    run_store: Dict[str, Any],
) -> None:
    run_store["runs"]["run-1"] = _run()
    result = await broker.invoke(_inv())
    assert result.ok is True
    assert result.data == {"tracks": [{"title": "Seeded Track"}]}
    assert result.receipt.run_id == "run-1"
    assert result.receipt.step_key
    assert result.policy_decision == "allow"
    assert result.snapshot_fingerprint == "fp-1"
    assert len(run_store["steps"]) == 1
    step = run_store["steps"][0]
    assert step.status == "succeeded"
    assert step.capability_key == "integral_list_tracks"
    assert step.result_json
    assert "Seeded Track" not in (step.result_json or "")


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_duplicate_idempotency_key_does_not_reinvoke_adapter(
    run_store: Dict[str, Any],
) -> None:
    run_store["runs"]["run-1"] = _run()
    first = await broker.invoke(_inv(idempotency_key="idem-1"))
    second = await broker.invoke(_inv(idempotency_key="idem-1"))
    assert first.ok is True
    assert second.ok is True
    assert second.replayed is True
    assert second.data == {"replayed": True, "result_unavailable": True}
    assert (
        second.message
        == "Prior result payload is not retained; capability was not executed again."
    )
    assert second.for_model() == {
        "replayed": True,
        "result_unavailable": True,
        "_receipt": second.receipt.model_dump(),
    }
    assert "Seeded Track" not in str(second.model_dump())
    assert run_store["adapter_calls"] == ["integral_list_tracks"]
    assert len(run_store["steps"]) == 1


@pytest.mark.smoke
@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["chat", "http", "mcp", "view"])
async def test_non_query_replay_marker_is_uniform_across_surfaces(
    run_store: Dict[str, Any], origin: str
) -> None:
    """Every surface receives the same metadata-only replay result."""
    run_id = f"run-{origin}"
    run_store["runs"][run_id] = _run(run_id=run_id, origin=origin)
    invocation = _inv(
        run_id=run_id,
        origin=origin,
        idempotency_key=f"idem-{origin}",
    )

    first = await broker.invoke(invocation)
    second = await broker.invoke(invocation)

    assert first.data == {"tracks": [{"title": "Seeded Track"}]}
    assert second.ok is True
    assert second.replayed is True
    assert second.data == {"replayed": True, "result_unavailable": True}
    assert second.receipt == first.receipt
    assert run_store["adapter_calls"].count("integral_list_tracks") == 1


@pytest.mark.smoke
@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["chat", "http", "mcp", "view"])
async def test_app_command_replay_returns_durable_operation_result_across_surfaces(
    run_store: Dict[str, Any], origin: str
) -> None:
    """App command replay delegates to its receipt-bound dispatcher result.

    The broker records one capability step, then asks the App dispatcher to
    replay the already-committed operation using the same idempotency key. The
    real dispatcher resolves this from its durable receipt and does not invoke
    the App handler again.
    """
    run_id = f"run-app-command-{origin}"
    run_store["runs"][run_id] = _run(run_id=run_id, origin=origin)
    invocation = _inv(
        run_id=run_id,
        origin=origin,
        capability_key="echo",
        source="app",
        app_id="app-1",
        op_class="execute",
        arguments={"message": "retry-safe"},
        idempotency_key=f"app-command-{origin}",
    )

    first = await broker.invoke(invocation)
    second = await broker.invoke(invocation)

    assert first.ok is True
    assert second.ok is True
    assert second.replayed is True
    assert second.data == first.data
    assert second.receipt == first.receipt
    assert run_store["adapter_calls"].count("echo") == 2
    assert len(run_store["steps"]) == 1


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_query_spec_receipt_uses_broker_derived_idempotency(
    run_store: Dict[str, Any],
) -> None:
    """The adapter and receipt share the broker's normalized idempotency key."""
    run_store["runs"]["run-1"] = _run()
    result = await broker.invoke(
        _inv(
            capability_key="integral_query_spec",
            arguments={"spec": {"resource": "entry", "select": ["id"]}},
        )
    )

    assert result.ok is True
    assert result.data["receipt"] == result.receipt.model_dump()
    assert result.receipt.idempotency_key
    assert (
        run_store["adapter_invocations"][0].idempotency_key
        == result.receipt.idempotency_key
    )


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_query_spec_replay_returns_metadata_without_stored_rows(
    run_store: Dict[str, Any],
) -> None:
    """A reused broker receipt delegates to QueryResultSet metadata replay."""
    run_store["runs"]["run-1"] = _run()
    invocation = _inv(
        capability_key="integral_query_spec",
        arguments={"spec": {"resource": "entry", "select": ["id"]}},
        idempotency_key="query-idem",
    )

    first = await broker.invoke(invocation)
    second = await broker.invoke(invocation)

    assert first.data["items"] == []
    assert second.replayed is True
    assert second.data["items"] is None
    assert second.data["replayed"] is True
    assert second.data["result_set_id"] == first.data["result_set_id"]
    assert second.receipt == first.receipt
    assert run_store["adapter_calls"] == [
        "integral_query_spec",
        "integral_query_spec",
    ]


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_generic_app_query_reclassifies_before_receipt_and_replays_metadata(
    run_store: Dict[str, Any],
) -> None:
    """Generic invocation persists only the declared App query read receipt."""
    run_store["runs"]["run-1"] = _run()
    kwargs = {
        "principal_id": "user-1",
        "workspace_id": "ws-1",
        "capability_key": "integral_invoke_app_operation",
        "origin": "chat",
        "source": "core",
        "op_class": "execute",
        "arguments": {
            "app_id": "app-1",
            "operation_key": "recent_open",
            "input": {},
        },
        "run_id": "run-1",
        "idempotency_key": "generic-idem",
    }

    first = await broker.invoke_declared_capability(**kwargs)
    second = await broker.invoke_declared_capability(**kwargs)

    assert first.ok is True
    assert first.receipt.capability_key == "recent_open"
    assert first.data["items"] == []
    assert second.ok is True
    assert second.replayed is True
    assert second.data["items"] is None
    assert second.data["replayed"] is True
    assert second.data["result_set_id"] == "result-app-query"
    assert second.data["receipt"] == second.receipt.model_dump()
    assert len(run_store["steps"]) == 1
    assert run_store["adapter_calls"] == ["recent_open", "recent_open"]


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_generic_app_query_rejects_ambiguous_legacy_snapshot(
    run_store: Dict[str, Any],
) -> None:
    """Legacy operation/query key collisions fail before receipt or execution."""
    snapshot = _snapshot()
    snapshot["apps"][0]["operations"].append(
        {"key": "recent_open", "kind": "execute", "policy_action": "app.read"}
    )
    run_store["runs"]["run-1"] = _run(capability_snapshot=snapshot)

    result = await broker.invoke_declared_capability(
        principal_id="user-1",
        workspace_id="ws-1",
        capability_key="integral_invoke_app_operation",
        origin="mcp",
        source="core",
        op_class="execute",
        arguments={
            "app_id": "app-1",
            "operation_key": "recent_open",
            "input": {},
        },
        run_id="run-1",
        idempotency_key="ambiguous-idem",
    )

    assert result.ok is False
    assert result.error_code == "capability.ambiguous_declaration"
    assert result.message == "App capability key is ambiguous in run snapshot"
    assert result.receipt is None
    assert run_store["steps"] == []
    assert run_store["adapter_calls"] == []


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_execute_fails_closed_on_snapshot_mismatch(
    run_store: Dict[str, Any],
) -> None:
    run_store["runs"]["run-1"] = _run()
    upgraded = _snapshot(fingerprint="fp-upgraded")
    upgraded["apps"][0]["package_version"] = "2.0.0"
    run_store["current_snapshot"] = upgraded
    result = await broker.invoke(
        _inv(
            capability_key="echo",
            source="app",
            app_id="app-1",
            op_class="execute",
            arguments={"message": "hi"},
        )
    )
    assert result.ok is False
    assert result.error_code == "capability.upgraded"
    assert run_store["adapter_calls"] == []


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_new_app_created_by_the_run_does_not_block_its_next_write(
    run_store: Dict[str, Any],
) -> None:
    run_store["runs"]["run-1"] = _run()
    current = _snapshot(fingerprint="fp-new-app")
    current["apps"].append({"app_id": "app-2", "operations": [], "queries": []})
    run_store["current_snapshot"] = current
    result = await broker.invoke(
        _inv(capability_key="integral_create_entry", op_class="propose")
    )
    assert result.ok is True
    assert run_store["adapter_calls"] == ["integral_create_entry"]


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_read_may_continue_after_snapshot_divergence(
    run_store: Dict[str, Any],
) -> None:
    run_store["runs"]["run-1"] = _run()
    run_store["current_snapshot"] = _snapshot(fingerprint="fp-upgraded")
    result = await broker.invoke(_inv())
    assert result.ok is True
    assert result.snapshot_divergence is True
    assert run_store["adapter_calls"] == ["integral_list_tracks"]


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_revoked_app_capability_blocks_execute(run_store: Dict[str, Any]) -> None:
    run_store["runs"]["run-1"] = _run()
    current = _snapshot()
    current["apps"] = []
    current["fingerprint"] = "fp-revoked"
    run_store["current_snapshot"] = current
    result = await broker.invoke(
        _inv(
            capability_key="echo",
            source="app",
            app_id="app-1",
            op_class="execute",
            arguments={"message": "hi"},
        )
    )
    assert result.ok is False
    assert result.error_code == "capability.revoked"
    assert run_store["adapter_calls"] == []


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_propose_receipt_is_waiting_for_human(run_store: Dict[str, Any]) -> None:
    run_store["runs"]["run-1"] = _run()
    result = await broker.invoke(
        _inv(
            capability_key="integral_create_entry",
            op_class="propose",
            arguments={"title": "x"},
        )
    )
    assert result.ok is True
    assert result.data["token"] == "tok-1"
    step = run_store["steps"][0]
    assert step.status == "waiting_for_human"
    assert step.approval_ref == "tok-1"


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_four_origins_share_deny_semantics(run_store: Dict[str, Any]) -> None:
    codes = []
    for origin in ("chat", "http", "mcp", "view"):
        run_id = f"run-{origin}"
        run_store["runs"][run_id] = _run(run_id=run_id, origin=origin)
        result = await broker.invoke(
            _inv(run_id=run_id, origin=origin, capability_key="nope")
        )
        codes.append(result.error_code)
    assert codes == ["capability.not_in_snapshot"] * 4


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_short_lived_http_run_terminates(run_store: Dict[str, Any]) -> None:
    run_store["runs"]["run-1"] = _run(origin="http")
    result = await broker.invoke(_inv(origin="http"))
    assert result.ok is True
    assert run_store["runs"]["run-1"].status == "succeeded"
    assert run_store["runs"]["run-1"].saved == 1


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_chat_run_stays_open_after_tool(run_store: Dict[str, Any]) -> None:
    run_store["runs"]["run-1"] = _run(origin="chat")
    await broker.invoke(_inv(origin="chat"))
    assert run_store["runs"]["run-1"].status == "running"
