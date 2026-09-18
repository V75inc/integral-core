"""Contract tests for ADR-012 QuerySpec + protected fields + catalogue."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.governed_query import QuerySpec
from app.services.app_invariant_guards import (
    enforce_protected_field_write,
    register_protected_fields,
    reset_operation_write_active,
    set_operation_write_active,
    unregister_protected_fields,
)


def test_query_spec_declared_requires_capability_key():
    with pytest.raises(ValidationError):
        QuerySpec.model_validate({"mode": "declared_capability"})


def test_query_spec_core_open_requires_resource():
    with pytest.raises(ValidationError):
        QuerySpec.model_validate({"mode": "core_open"})


def test_query_spec_declared_ok():
    spec = QuerySpec.model_validate(
        {
            "mode": "declared_capability",
            "capability_key": "warranties_expiring",
            "params": {"horizon_days": 45},
        }
    )
    assert spec.mode == "declared_capability"
    assert spec.params["horizon_days"] == 45


@pytest.mark.asyncio
async def test_protected_fields_block_generic_write():
    ws = "ws_test_guard"
    app = "app_test_guard"
    register_protected_fields(
        ws, app, {"asset": ["lifecycle_state", "current_custodian"]}
    )
    try:
        from app.api.errors import BadRequestError

        with pytest.raises(BadRequestError) as ei:
            await enforce_protected_field_write(
                workspace_id=ws,
                entry_type_key="asset",
                proposed_custom_fields={"lifecycle_state": "checked_out"},
            )
        assert ei.value.details.get("error_code") == "protected_field_write"
    finally:
        unregister_protected_fields(ws, app)


@pytest.mark.asyncio
async def test_protected_fields_allow_during_operation():
    ws = "ws_test_guard2"
    app = "app_test_guard2"
    register_protected_fields(ws, app, {"asset": ["lifecycle_state"]})
    token = set_operation_write_active(True)
    try:
        await enforce_protected_field_write(
            workspace_id=ws,
            entry_type_key="asset",
            proposed_custom_fields={"lifecycle_state": "checked_out"},
        )
    finally:
        reset_operation_write_active(token)
        unregister_protected_fields(ws, app)
