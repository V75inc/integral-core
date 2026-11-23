"""Exact, read-only voice status over disposable durable chat and review facts."""

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.agentive import staging_store
from app.agentive.services.staging_execution import execution_record_id
from app.services.native_voice_thread_status import (
    VoiceThreadStatus,
    read_voice_thread_status,
)

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]
SCOPE = ("fixture-owner", "fixture-workspace", "fixture-thread")
TYPED_ID = "typed"
TOKEN = "fixture-do-not-forward-token"


def _work(identity, stamp, *, typed=True, scope=SCOPE, role="user"):
    return {
        "id": f"o.WorkItem.{identity}",
        "entity": "WorkItem",
        "context": {
            "work_item_id": identity,
            "principal_id": scope[0],
            "workspace_id": scope[1],
            "thread_id": scope[2],
            "kind": "chat_turn",
            "origin": "interactive_chat",
            "status": "succeeded",
            "updated_at": stamp.isoformat(),
            "run_id": f"run-{identity}",
            "input_payload": {
                **({"message_role": role} if role != "user" else {}),
                **({"client_payload_digest": "7" * 64} if typed else {}),
            },
        },
    }


def _proposal(identity, work, stamp, *, scope=SCOPE):
    principal, workspace, thread = scope
    return {
        "id": f"o.StagedChangeRecord.{identity}",
        "entity": "StagedChangeRecord",
        "context": {
            "token": identity,
            "kind": "create_dashboard",
            "state": "pending",
            "payload": {"private-command-value": "not released"},
            "summary": "private summary",
            "user_id": principal,
            "workspace_id": workspace,
            "session_id": thread,
            "created_at": stamp.isoformat(),
            "expires_at": (stamp + timedelta(minutes=4)).isoformat(),
            "origin_proof": {
                "version": "work-proposal-v1",
                "principal_id": principal,
                "workspace_id": workspace,
                "thread_id": thread,
                "work_item_id": work,
                "run_id": f"run-{work}",
            },
        },
    }


def _marker(proposal, now, *, state):
    data = proposal["context"]
    return {
        "id": staging_store._decision_record_id(data["token"]),
        "entity": "StagingDecisionRecord",
        "context": {
            "user_id": data["user_id"],
            "kind": data["kind"],
            "state": state,
            "intent_fingerprint": staging_store.decision_intent_fingerprint(data),
            "resolved_at": now.isoformat(),
            "retained_until": (now + timedelta(hours=1)).isoformat(),
        },
    }


async def test_pending_is_exact_typed_work_not_own_voice_or_foreign_scope(
    postgres_raw_db,
):
    now = datetime.now(timezone.utc)
    other = (SCOPE[0], SCOPE[1], "other-thread")
    other_member = ("other-member", SCOPE[1], SCOPE[2])
    other_workspace = (SCOPE[0], "other-workspace", SCOPE[2])
    for record in (
        _work(TYPED_ID, now),
        _work("voice", now, typed=False),
        _work("other", now, scope=other),
        _work("other-user", now, scope=other_member),
        _work("other-workspace", now, scope=other_workspace),
        _work("host", now, role="system"),
        _proposal(TOKEN, TYPED_ID, now),
        _proposal("voice-token", "voice", now),
        _proposal("other-token", "other", now, scope=other),
        _proposal("other-user-token", "other-user", now, scope=other_member),
        _proposal(
            "other-workspace-token", "other-workspace", now, scope=other_workspace
        ),
        _proposal("host-token", "host", now),
        _proposal("widened-token", TYPED_ID, now, scope=other),
    ):
        await postgres_raw_db.save("object", record)
    status, seen = await read_voice_thread_status(*SCOPE)
    assert status == VoiceThreadStatus(typed_turn="finished", awaiting=1)
    assert len(seen) == 1 and TOKEN not in repr(seen)
    text = status.as_context()
    assert "1 awaiting review" in text
    assert not any(
        term in text for term in (*SCOPE, TOKEN, "private-command", "private summary")
    )
    assert (await read_voice_thread_status(*other, seen))[0].unverified == 0


@pytest.mark.parametrize(
    ("state", "label"),
    [
        ("revoked", "declined"),
        ("expired", "expired"),
        ("consumed", "applied"),
    ],
)
async def test_missing_proposal_needs_exact_terminal_and_effect_evidence(
    postgres_raw_db, state, label
):
    now = datetime.now(timezone.utc)
    work = _work(TYPED_ID, now)
    proposal = _proposal(TOKEN, TYPED_ID, now)
    await postgres_raw_db.save("object", work)
    await postgres_raw_db.save("object", proposal)
    status, seen = await read_voice_thread_status(*SCOPE)
    assert status.awaiting == 1
    await postgres_raw_db.delete("object", proposal["id"])
    lost, _ = await read_voice_thread_status(*SCOPE, seen)
    assert lost.unverified == 1 and lost.applied == 0 and lost.awaiting == 0

    mark = _marker(proposal, now, state=state)
    invalid = {**mark, "context": {**mark["context"], "intent_fingerprint": "wrong"}}
    await postgres_raw_db.save("object", invalid)
    assert (await read_voice_thread_status(*SCOPE, seen))[0].unverified == 1
    await postgres_raw_db.save("object", mark)
    if state == "consumed":
        assert (await read_voice_thread_status(*SCOPE, seen))[0].unverified == 1
        command = hashlib.sha256(
            json.dumps(
                proposal["context"]["payload"], sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        await postgres_raw_db.save(
            "object",
            {
                "id": execution_record_id(TOKEN),
                "entity": "StagingExecutionRecord",
                "context": {
                    "principal_id": SCOPE[0],
                    "workspace_id": SCOPE[1],
                    "kind": proposal["context"]["kind"],
                    "request_hash": command,
                    "state": "applied",
                    "result_ciphertext": "private-fixture-evidence",
                },
            },
        )
    resolved, _ = await read_voice_thread_status(*SCOPE, seen)
    assert getattr(resolved, label) == 1 and resolved.unverified == 0
    assert TOKEN not in resolved.as_context()
    assert "private-fixture-evidence" not in resolved.as_context()
