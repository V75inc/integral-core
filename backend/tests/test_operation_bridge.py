"""W1.7 Phase A: a prose operation becomes a spec and a developer skeleton."""

from __future__ import annotations

import copy
import subprocess
import sys

import pytest
import yaml

from app.models.edges import CONTAINS
from app.models.nodes import ChatMessage, ChatThread
from app.services import chat_threads
from app.services.operation_bridge import (
    operation_bridge,
    specify_operation,
    validate_skeleton,
)

_PURPOSE = (
    "When someone takes an asset, move it from available to checked out "
    "and open one custody record. If it is already checked out, refuse. "
    "Retrying the same request does not open a second custody record."
)

_BLUEPRINT = {
    "app": {"id": "app", "name": "Asset Register"},
    "tracks": [
        {
            "id": "assets",
            "name": "Assets",
            "entry_types": [
                {
                    "name": "Asset",
                    "fields": [
                        {"key": "status", "name": "Status", "type": "text"},
                    ],
                }
            ],
        }
    ],
    "operations": [
        {
            "id": "op.checkout",
            "name": "Check out asset",
            "purpose": _PURPOSE,
            "inputs": [
                {"name": "asset_id", "type": "string"},
                {"name": "custodian_id", "type": "string", "required": True},
            ],
            "policy_action": "entry.create",
        }
    ],
}


def test_checkout_prose_is_a_protected_transition_spec():
    """A checkout purpose becomes a full operation contract."""
    spec = specify_operation({"name": "Check out asset", "purpose": _PURPOSE})
    assert spec["description"] == _PURPOSE
    assert spec["effects"] == [_PURPOSE]
    assert spec["policy_action"] == "unspecified"
    assert spec["staging_level"] == "required"
    assert spec["idempotency_key"] == "supported"
    assert spec["live"] is False
    assert spec["input_schema"]["properties"]["record_id"]["type"] == "string"
    assert spec["output_schema"]["properties"]["ok"]["type"] == "boolean"
    assert spec["output_schema"]["properties"]["receipt_id"]["type"] == "string"
    assert "conflict" in spec["conflict_rule"].lower()
    assert {item["name"] for item in spec["tests"]} == {
        "applies_once",
        "same_key_different_payload",
    }


def test_unknown_policy_action_is_flagged_not_guessed():
    """An unrecognized policy action is left for the developer to choose."""
    spec = specify_operation(
        {
            "name": "Check out asset",
            "purpose": _PURPOSE,
            "policy_action": "not.a.real.action",
        }
    )
    assert spec["policy_action"] == "unspecified"


def test_skeleton_passes_its_generated_contract_test(tmp_path):
    """The generated package parses and its own contract test passes."""
    bridge = operation_bridge(_BLUEPRINT)
    assert bridge is not None
    assert bridge["live"] is False
    spec = bridge["specs"][0]
    assert spec["key"] == "check_out_asset"
    assert spec["policy_action"] == "entry.create"
    assert spec["input_schema"]["required"] == ["custodian_id"]
    parsed = validate_skeleton(bridge["files"])
    assert parsed[0]["tool"] == "check_out_asset"
    assert parsed[0]["idempotency_key"] == "supported"
    for rel, content in bridge["files"].items():
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--rootdir",
            str(tmp_path),
            "--noconftest",
            "-c",
            str(tmp_path / "pytest.ini"),
            str(tmp_path / "tests" / "test_operation_contract.py"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    loaded = yaml.safe_load(bridge["files"]["operational-model.yaml"])
    assert loaded["package"]["slug"] == "asset_register"
    assert loaded["app"]["operations"][0]["effects"][0] == _PURPOSE


_PROPOSAL = (
    "**Asset Register** app (fresh).\n\n"
    "- **Assets** — status\n"
    "- Check out asset needs a custom add-on that can't be set up from chat.\n"
)


async def _thread(session_id: str) -> ChatThread:
    thread = await ChatThread.create(user_id="u1", provider_session_id=session_id)
    msg = await ChatMessage.create(role="user", thread_id=thread.id)
    await thread.connect(msg, edge=CONTAINS)
    return thread


@pytest.mark.asyncio
async def test_propose_stores_the_operation_spec_artifact():
    """Recording a design with an operation stores the spec, marked not live."""
    thread = await _thread("op-bridge")
    result = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="op-bridge",
        summary="Asset Register with checkout",
        proposal=_PROPOSAL,
        blueprint=copy.deepcopy(_BLUEPRINT),
    )
    assert result.get("ok") is True, result
    assert result["operation_bridge"]["live"] is False
    assert result["operation_bridge"]["operation_keys"] == ["check_out_asset"]
    reloaded = await ChatThread.get(thread.id)
    artifact = reloaded.artifacts["operation_bridge"]
    assert artifact["kind"] == "operation_spec"
    assert artifact["metadata"]["live"] is False
    assert "check_out_asset" in artifact["body"]


@pytest.mark.asyncio
async def test_propose_without_operations_clears_the_spec():
    """A later design with no operations removes the stored spec."""
    await _thread("op-bridge-clear")
    first = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="op-bridge-clear",
        summary="Asset Register with checkout",
        proposal=_PROPOSAL,
        blueprint=copy.deepcopy(_BLUEPRINT),
    )
    assert first.get("ok") is True, first
    bare = copy.deepcopy(_BLUEPRINT)
    bare["operations"] = []
    second = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="op-bridge-clear",
        summary="Asset Register without checkout",
        proposal=_PROPOSAL,
        blueprint=bare,
    )
    assert second.get("ok") is True, second
    assert "operation_bridge" not in second
    reloaded = await chat_threads.get_thread_by_session("op-bridge-clear")
    assert "operation_bridge" not in (reloaded.artifacts or {})


@pytest.mark.asyncio
async def test_a_failing_skeleton_keeps_the_design_and_drops_the_old_spec(
    monkeypatch,
):
    """A skeleton error never fails a recorded proposal or leaves a stale spec."""
    from app.services import operation_bridge as bridge_module

    await _thread("op-bridge-fail")
    first = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="op-bridge-fail",
        summary="Asset Register with checkout",
        proposal=_PROPOSAL,
        blueprint=copy.deepcopy(_BLUEPRINT),
    )
    assert first.get("ok") is True, first

    def broken(_blueprint):
        raise SyntaxError("bad stub")

    monkeypatch.setattr(bridge_module, "operation_bridge", broken)
    second = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="op-bridge-fail",
        summary="Asset Register with checkout, revised",
        proposal=_PROPOSAL,
        blueprint=copy.deepcopy(_BLUEPRINT),
    )
    assert second.get("ok") is True, second
    assert "operation_bridge" not in second
    reloaded = await chat_threads.get_thread_by_session("op-bridge-fail")
    assert "operation_bridge" not in (reloaded.artifacts or {})
