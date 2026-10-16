"""Transport DTOs distinguish recorded completion from readable result data."""

import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas.app_operations import AppOperationOutputWithheld, AppOperationResponse


def receipt():
    return {
        "id": "o.OperationExecutionReceipt." + "a" * 64,
        "status": "succeeded",
        "replayed": True,
    }


def test_withheld_completion_has_no_business_output_or_current_state_claim():
    result = AppOperationOutputWithheld(operation_receipt=receipt()).model_dump()
    assert result == {
        "operation_receipt": receipt(),
        "output_disposition": "withheld",
        "current_state_verified": False,
    }
    decoded = TypeAdapter(AppOperationResponse).validate_python(result)
    assert isinstance(decoded, AppOperationOutputWithheld)


@pytest.mark.parametrize(
    "extra",
    [
        {"output": {}},
        {"evidence": {}},
        {"object_refs": []},
        {"app_id": "private"},
        {"current_state_verified": True},
        {"output_disposition": "available"},
    ],
)
def test_withheld_dto_refuses_private_fields_or_false_state_claim(extra):
    with pytest.raises(ValidationError):
        AppOperationOutputWithheld.model_validate(
            {"operation_receipt": receipt(), **extra}
        )


@pytest.mark.parametrize(
    "change",
    [
        {"id": "not-a-receipt"},
        {"status": "claimed"},
        {"replayed": False},
        {"principal_id": "private"},
        {"idempotency_key": "secret-intent"},
    ],
)
def test_reference_refuses_ambiguous_or_private_execution_identity(change):
    with pytest.raises(ValidationError):
        AppOperationOutputWithheld(operation_receipt={**receipt(), **change})
