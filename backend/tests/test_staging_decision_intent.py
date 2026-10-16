"""Private terminal intent proof is stable, strict and never approval authority."""

import copy

import pytest

from app.agentive.staging_store import decision_intent_fingerprint

pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def fields():
    return {
        "token": "private-token",
        "user_id": "human",
        "workspace_id": "workspace",
        "session_id": "conversation",
        "kind": "invoke_app_operation",
        "payload": {
            "input": {"title": "Reviewed"},
            "declaration_fingerprint": "a" * 64,
        },
        "created_at": "2026-10-15T00:00:00+00:00",
        "expires_at": "2026-10-15T00:10:00+00:00",
        "summary": "Reviewed command",
        "diff_human": "Reviewed change",
        "diff_machine": {"before": "old", "after": "new"},
        "interaction_id": "original-message",
        "idempotency_key": "original-intent",
    }


@pytest.mark.parametrize("key", list(fields()))
def test_each_original_intent_field_is_bound(key):
    original = fields()
    changed = copy.deepcopy(original)
    changed[key] = (
        {"different": True} if isinstance(changed[key], dict) else "different"
    )
    assert decision_intent_fingerprint(changed) != decision_intent_fingerprint(original)


def test_mutable_execution_bookkeeping_does_not_change_original_intent():
    original = fields()
    changed = {
        **original,
        "state": "consumed",
        "progress": {"completed": 1},
        "last_error": {"message": "Post-commit delivery failed"},
        "blessed_at": "later",
        "resolved_at": "later",
        "decision_source": "human",
    }
    assert decision_intent_fingerprint(changed) == decision_intent_fingerprint(original)
    assert decision_intent_fingerprint(
        dict(reversed(list(original.items())))
    ) == decision_intent_fingerprint(original)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), b"not-json"])
def test_noncanonical_values_are_not_silently_coerced(value):
    original = fields()
    original["payload"]["invalid"] = value
    with pytest.raises((TypeError, ValueError)):
        decision_intent_fingerprint(original)


@pytest.mark.parametrize("value", [{1: "coerced key"}, ("coerced sequence",)])
def test_non_json_container_shapes_cannot_share_a_json_intent(value):
    original = fields()
    original["payload"]["invalid"] = value
    with pytest.raises((TypeError, ValueError)):
        decision_intent_fingerprint(original)
