"""Bounded input and public-reference contract for journal-only observation."""

from dataclasses import FrozenInstanceError, replace

import pytest

from app.contracts.operations import OperationIdentity
from app.services.app_operations.execution_receipts import (
    OperationOutcomeObservation,
    lookup_operation_outcome,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def identity():
    return OperationIdentity.create(
        workspace_id="workspace",
        app_id="app",
        operation_key="operation",
        principal_id="subject",
        idempotency_key="private-key",
    )


@pytest.mark.parametrize(
    "field",
    ["workspace_id", "app_id", "operation_key", "principal_id", "idempotency_key"],
)
@pytest.mark.parametrize("value", ["", " surrounding-space ", None])
async def test_unvalidated_identity_never_reaches_storage(field, value):
    with pytest.raises(ValueError):
        await lookup_operation_outcome(
            identity=replace(identity(), **{field: value}),
            request_hash="request",
            database=object(),
        )


@pytest.mark.parametrize("request_hash", ["", None, 123])
async def test_missing_or_untyped_request_hash_never_reaches_storage(request_hash):
    with pytest.raises(ValueError):
        await lookup_operation_outcome(
            identity=identity(),
            request_hash=request_hash,
            database=object(),
        )


@pytest.mark.parametrize("aliases", ["a", b"a", ("",), (" a ",), (None,), ("a", "b")])
async def test_unvalidated_aliases_never_reach_storage(aliases):
    with pytest.raises(ValueError):
        await lookup_operation_outcome(
            identity=identity(),
            request_hash="request",
            principal_aliases=aliases,
            database=object(),
        )


async def test_alias_iterator_stops_after_two_values():
    consumed = []

    def aliases():
        while True:
            consumed.append("alias")
            if len(consumed) > 2:
                pytest.fail("Alias validation consumed an unbounded iterable")
            yield "alias"

    with pytest.raises(ValueError):
        await lookup_operation_outcome(
            identity=identity(),
            request_hash="request",
            principal_aliases=aliases(),
            database=object(),
        )
    assert len(consumed) == 2


async def test_observation_is_immutable_and_reference_copies_are_independent():
    observation = OperationOutcomeObservation(identity())
    with pytest.raises(FrozenInstanceError):
        observation.identity = replace(identity(), principal_id="other")
    reference = observation.reference()
    reference["status"] = "changed by caller"
    assert observation.reference()["status"] == "succeeded"
    assert set(observation.reference()) == {"id", "status", "replayed"}
    assert "private-key" not in repr(observation)
    assert not hasattr(observation, "result")
