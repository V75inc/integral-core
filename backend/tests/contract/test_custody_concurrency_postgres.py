"""Postgres contract: AC-05 primitive + entry conditional updates."""

from __future__ import annotations

import asyncio
import uuid

import pytest

pytestmark = [pytest.mark.postgres, pytest.mark.contract, pytest.mark.asyncio]


async def _try_claim(db, doc_id: str) -> bool:
    result = await db.find_one_and_update(
        "spike_assets",
        {"id": doc_id, "state": "available"},
        {"$set": {"state": "claimed"}},
    )
    return result is not None and result.get("state") == "claimed"


@pytest.mark.postgres
@pytest.mark.contract
async def test_postgres_concurrent_claim_exactly_one_wins(postgres_raw_db):
    doc_id = f"contract-{uuid.uuid4().hex[:12]}"
    await postgres_raw_db.save(
        "spike_assets",
        {"id": doc_id, "state": "available", "tag": "laptop-1"},
    )
    results = await asyncio.gather(
        _try_claim(postgres_raw_db, doc_id),
        _try_claim(postgres_raw_db, doc_id),
    )
    assert sorted(results) == [False, True]
    final = await postgres_raw_db.find_one("spike_assets", {"id": doc_id})
    assert final is not None
    assert final["state"] == "claimed"
    await postgres_raw_db.delete("spike_assets", doc_id)


async def _try_checkout_entry(user_id: str, entry_id: str) -> bool:
    from app.services.entry_conditional_update import (
        conditional_update_entry_custom_fields,
    )

    ok, _err = await conditional_update_entry_custom_fields(
        user_id=user_id,
        entry_id=entry_id,
        state_field="lifecycle_state",
        expected_state="available",
        updates={"lifecycle_state": "checked_out"},
    )
    return ok


async def _try_revision_write(
    entry_id: str, value: str, expected_revision: int, user_id: str
) -> bool:
    from app.models.nodes import Entry
    from app.services.entry_conditional_update import (
        update_entry_custom_fields_if_revision,
    )

    entry = await Entry.get(entry_id)
    assert entry is not None
    ok, _error = await update_entry_custom_fields_if_revision(
        entry=entry,
        expected_revision=expected_revision,
        updates={**entry.custom_fields, "claim_note": value},
        schema_revision=int(entry.schema_revision),
        user_id=user_id,
        scope="test:revision-cas",
        event_sink=lambda _event: None,
    )
    return ok


@pytest.mark.postgres
@pytest.mark.contract
async def test_entry_conditional_update_concurrent_one_wins(postgres_raw_db):
    from jvspatial.core.context import GraphContext, _default_context_var

    from app.models.edges import CONTAINS, IS_OF_TYPE
    from app.models.nodes import Entry, EntryType
    from app.services.app_graph import ensure_track_attached_operational_model
    from tests.test_resource_location import graph

    token = _default_context_var.set(GraphContext(database=postgres_raw_db))
    try:
        owner, _, _, track, entry = await graph()
        model = await ensure_track_attached_operational_model(track)
        entry_type = await EntryType.create(
            name="Contract asset",
            track_id=track.id,
            operational_model_id=model.id,
            form_schema={
                "fields": [
                    {"key": "lifecycle_state", "type": "text"},
                    {"key": "claim_note", "type": "text"},
                ]
            },
        )
        await model.connect(entry_type, edge=CONTAINS)
        await entry.connect(entry_type, edge=IS_OF_TYPE)
        entry.type_id = entry_type.id
        entry.custom_fields = {"lifecycle_state": "available"}
        await entry.save()
        # Race through real current identity, containment and Entry authority.
        results = await asyncio.gather(
            _try_checkout_entry(owner.id, entry.id),
            _try_checkout_entry(owner.id, entry.id),
        )
        assert sorted(results) == [False, True]
        refreshed = await Entry.get(entry.id)
        assert refreshed is not None
        assert refreshed.custom_fields["lifecycle_state"] == "checked_out"
        assert refreshed.record_revision == 2
        revision_results = await asyncio.gather(
            _try_revision_write(entry.id, "first", 2, owner.id),
            _try_revision_write(entry.id, "second", 2, owner.id),
        )
        assert sorted(revision_results) == [False, True]
        latest = await Entry.get(entry.id)
        assert latest is not None
        assert latest.record_revision == 3
        assert latest.custom_fields["claim_note"] in {"first", "second"}
    finally:
        _default_context_var.reset(token)
