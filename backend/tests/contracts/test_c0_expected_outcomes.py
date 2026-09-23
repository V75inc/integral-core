"""C0 expected outcomes. No model. Domain nouns stay in the fixture data."""

from __future__ import annotations

import pytest

from app.agentive import staging
from app.agentive.staging import (
    StagingError,
    bless_token,
    cancel_batch,
    consume_token,
    create_staged_change,
    is_batch_open,
    open_batch,
)
from app.contracts.information import (
    FieldDefinition,
    FieldNamespace,
    resolve_legacy_entry_field_path_value,
    resolve_legacy_entry_field_value,
)


@pytest.fixture(autouse=True)
def _clean_staging():
    staging._tokens.clear()
    staging._autonomy.clear()
    yield
    staging._tokens.clear()


def _records():
    return [
        {
            "id": "target",
            "status": "active",
            "custom_fields": {"workflow_state": "open", "status": "open"},
        },
        {
            "id": "decoy",
            "status": "active",
            "custom_fields": {"workflow_state": "closed", "status": "closed"},
        },
    ]


def test_decoy_record_is_excluded_by_the_business_field():
    business = FieldDefinition(
        id="fld.item.workflow_state",
        key="workflow_state",
        label="Workflow state",
        type="select",
        namespace=FieldNamespace.BUSINESS,
        schema_revision=1,
    )
    matched = [
        record["id"]
        for record in _records()
        if resolve_legacy_entry_field_value(business, record) == "open"
    ]
    assert matched == ["target"]


def test_colliding_status_does_not_change_the_platform_field():
    platform = FieldDefinition(
        id="sys.entry.status",
        key="status",
        label="Record status",
        type="text",
        namespace=FieldNamespace.PLATFORM,
        owner="integral-core",
        schema_revision=1,
    )
    record = _records()[0]
    assert resolve_legacy_entry_field_value(platform, record) == "active"
    assert (
        resolve_legacy_entry_field_path_value("custom_fields.status", record) == "open"
    )
    assert record["status"] == "active"


def test_stored_field_matches_the_rendered_readback():
    record = _records()[0]
    stored = record["custom_fields"]["workflow_state"]
    rendered = resolve_legacy_entry_field_path_value(
        "custom_fields.workflow_state", record
    )
    assert rendered == stored == "open"


@pytest.mark.asyncio
async def test_cancelled_batch_does_not_remain_open():
    await open_batch(user_id="u1", session_id="s-cancel")
    assert await cancel_batch(user_id="u1", session_id="s-cancel") is True
    assert is_batch_open("u1", "s-cancel") is False


@pytest.mark.asyncio
async def test_a_consumed_approval_cannot_apply_twice():
    staged = await create_staged_change(
        user_id="u1",
        session_id="s-once",
        kind="update_entry",
        summary="Set workflow state",
        diff_human="- workflow_state",
        diff_machine={"entry_id": "target"},
        payload={"entry_id": "target", "fields": {"workflow_state": "open"}},
    )
    await bless_token(user_id="u1", token=staged.token)
    first = await consume_token(
        user_id="u1", token=staged.token, expected_kind="update_entry"
    )
    assert first["entry_id"] == "target"
    with pytest.raises(StagingError) as consumed:
        await consume_token(
            user_id="u1", token=staged.token, expected_kind="update_entry"
        )
    assert consumed.value.code == "already_consumed"
