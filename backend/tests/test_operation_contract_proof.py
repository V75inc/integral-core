"""Invalid effect contract metadata is refused before a journal is touched."""

import pytest

from app.services.app_operations.execution_receipts import (
    execute_operation_once,
    lookup_operation_outcome,
)
from tests.test_operation_outcome_observation import identity

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize(
    "value", [True, False, 42, "", "g" * 64, "a" * 63, "a" * 65, "a" * 64 + "\n"]
)
@pytest.mark.parametrize("observing", [False, True])
async def test_unvalidated_contract_never_reaches_storage_or_handler(value, observing):
    if observing:
        with pytest.raises(ValueError, match="exact effect contract"):
            await lookup_operation_outcome(
                identity=identity(),
                request_hash="input",
                database=object(),
                expected_effect_contract_fingerprint=value,
            )
    else:

        async def forbidden():
            pytest.fail("Invalid contract reached handler")

        with pytest.raises(ValueError, match="exact effect contract"):
            await execute_operation_once(
                identity=identity(),
                request_hash="input",
                database=object(),
                execute=forbidden,
                effect_contract_fingerprint=value,
            )


@pytest.mark.parametrize(
    "value", [True, False, 42, "", "g" * 64, "a" * 63, "a" * 65, "a" * 64 + "\n"]
)
@pytest.mark.parametrize("observing", [False, True])
async def test_invalid_decision_intent_proof_never_reaches_storage(value, observing):
    async def forbidden():
        pytest.fail("Invalid original-decision proof reached a handler")

    with pytest.raises(ValueError):
        if observing:
            await lookup_operation_outcome(
                identity=identity(),
                request_hash="input",
                database=object(),
                expected_decision_intent_fingerprint=value,
            )
        else:
            await execute_operation_once(
                identity=identity(),
                request_hash="input",
                database=object(),
                execute=forbidden,
                decision_intent_fingerprint=value,
            )
