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
        ],
        "apps": [
            {
                "app_id": "app-1",
                "package_slug": "hello",
                "package_version": "1.0.0",
                "operations": [
                    {"key": "echo", "kind": "execute", "policy_action": "app.read"}
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
    store = {"runs": runs, "steps": steps, "adapter_calls": adapter_calls}

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
        if inv.capability_key == "boom":
            raise RuntimeError("adapter exploded")
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
    assert run_store["adapter_calls"] == ["integral_list_tracks"]
    assert len(run_store["steps"]) == 1


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_execute_fails_closed_on_snapshot_mismatch(
    run_store: Dict[str, Any],
) -> None:
    run_store["runs"]["run-1"] = _run()
    run_store["current_snapshot"] = _snapshot(fingerprint="fp-upgraded")
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
