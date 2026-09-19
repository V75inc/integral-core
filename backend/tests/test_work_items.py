"""WorkItem identity, schema, and legal-transition contracts (Task 1)."""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from app.agentive.services import work_items
from app.agentive.work_models import WorkItem
from app.schemas.agentive.work import RetryPolicy, WorkError


def _expected_object_id(
    *,
    kind: str,
    origin: str,
    principal_id: str,
    workspace_id: str,
    idempotency_key: str,
) -> str:
    canonical = json.dumps(
        [kind, origin, principal_id, workspace_id, idempotency_key],
        separators=(",", ":"),
        sort_keys=False,
    )
    return "o.WorkItem." + hashlib.sha256(canonical.encode()).hexdigest()


def test_work_item_schema_rejects_unknown_kind_and_illegal_retry_policy() -> None:
    from app.schemas.agentive.work import EnqueueWorkRequest

    with pytest.raises(ValidationError):
        EnqueueWorkRequest.model_validate(
            {
                "kind": "not_a_kind",
                "origin": "http",
                "principal_id": "u",
                "workspace_id": "w",
                "idempotency_key": "k",
                "input_payload": {},
            }
        )
    with pytest.raises(ValidationError):
        RetryPolicy.model_validate(
            {
                "max_attempts": 0,
                "base_delay_seconds": 1,
                "max_delay_seconds": 10,
                "jitter_ratio": 0.1,
            }
        )
    with pytest.raises(ValidationError):
        RetryPolicy.model_validate(
            {
                "max_attempts": 3,
                "base_delay_seconds": 1,
                "max_delay_seconds": 10,
                "jitter_ratio": 0.9,
            }
        )


def test_work_item_id_is_deterministic_and_namespaced() -> None:
    expected = _expected_object_id(
        kind="capability",
        origin="http",
        principal_id="user-1",
        workspace_id="ws-1",
        idempotency_key="idem-1",
    )
    assert work_items.work_item_object_id(
        kind="capability",
        origin="http",
        principal_id="user-1",
        workspace_id="ws-1",
        idempotency_key="idem-1",
    ) == expected
    assert expected.startswith("o.WorkItem.")


@pytest.mark.asyncio
async def test_enqueue_reuses_identical_identity_without_rewriting_payload() -> None:
    first = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="user-1",
        workspace_id="ws-1",
        idempotency_key="same-key",
        input_payload={"capability_key": "integral_list_entries"},
    )
    second = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="user-1",
        workspace_id="ws-1",
        idempotency_key="same-key",
        input_payload={"capability_key": "integral_list_entries"},
    )
    assert first.id == second.id
    assert first.work_item_id == second.work_item_id
    assert first.input_payload == {"capability_key": "integral_list_entries"}
    assert second.input_payload == first.input_payload
    records = await WorkItem.find({"context.idempotency_key": "same-key"})
    assert len(list(records)) == 1


@pytest.mark.asyncio
async def test_enqueue_rejects_same_identity_with_changed_input() -> None:
    await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="user-2",
        workspace_id="ws-2",
        idempotency_key="conflict-key",
        input_payload={"capability_key": "a"},
    )
    with pytest.raises(WorkError) as exc:
        await work_items.enqueue_work_item(
            kind="capability",
            origin="http",
            principal_id="user-2",
            workspace_id="ws-2",
            idempotency_key="conflict-key",
            input_payload={"capability_key": "b"},
        )
    assert exc.value.code == "work.idempotency_conflict"
    winner = await WorkItem.get(
        work_items.work_item_object_id(
            kind="capability",
            origin="http",
            principal_id="user-2",
            workspace_id="ws-2",
            idempotency_key="conflict-key",
        )
    )
    assert winner is not None
    assert winner.input_payload == {"capability_key": "a"}


@pytest.mark.asyncio
async def test_terminal_transition_cannot_reopen() -> None:
    item = await work_items.enqueue_work_item(
        kind="routine_turn",
        origin="scheduler",
        principal_id="user-3",
        workspace_id="ws-3",
        idempotency_key="term-key",
        input_payload={"routine_id": "r-1"},
    )
    running = await work_items.transition_work_item(
        item.work_item_id,
        expected_status="queued",
        target="running",
        fields={"attempt": 1},
    )
    succeeded = await work_items.transition_work_item(
        running.work_item_id,
        expected_status="running",
        target="succeeded",
    )
    assert succeeded.status == "succeeded"
    with pytest.raises(WorkError) as exc:
        await work_items.transition_work_item(
            succeeded.work_item_id,
            expected_status="succeeded",
            target="queued",
        )
    assert exc.value.code == "work.invalid_transition"
