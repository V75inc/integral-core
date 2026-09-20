"""Contract tests for the stable operation idempotency namespace."""

from __future__ import annotations

import pytest

from app.contracts.operations import OperationIdentity, canonical_request_hash


def test_operation_identity_normalizes_and_scopes_every_dimension() -> None:
    identity = OperationIdentity.create(
        workspace_id=" ws-a ",
        app_id=" app-a ",
        operation_key=" check_out ",
        principal_id=" person-a ",
        idempotency_key=" key-a ",
    )

    assert identity.cache_key() == (
        "ws-a",
        "app-a",
        "check_out",
        "person-a",
        "key-a",
    )


@pytest.mark.parametrize(
    "field",
    ["workspace_id", "app_id", "operation_key", "principal_id", "idempotency_key"],
)
def test_operation_identity_rejects_missing_scope(field: str) -> None:
    values = {
        "workspace_id": "ws-a",
        "app_id": "app-a",
        "operation_key": "check_out",
        "principal_id": "person-a",
        "idempotency_key": "key-a",
    }
    values[field] = " "

    with pytest.raises(ValueError, match=field):
        OperationIdentity.create(**values)


def test_request_hash_is_order_independent() -> None:
    assert canonical_request_hash({"a": 1, "b": [2, 3]}) == canonical_request_hash(
        {"b": [2, 3], "a": 1}
    )
