"""Postgres contract: AC-05 primitive + entry conditional updates."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

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


@pytest.mark.postgres
@pytest.mark.contract
async def test_entry_conditional_update_concurrent_one_wins():
    from app.models.nodes import Entry

    entry = await Entry.create(
        title="Contract asset",
        custom_fields={"lifecycle_state": "available"},
    )
    entry_id = entry.id
    await entry.save()

    with (
        patch(
            "app.services.permissions.resolve_role",
            new=AsyncMock(return_value="owner"),
        ),
        patch(
            "app.services.change_event.emit_change_event",
            new=AsyncMock(),
        ),
    ):
        results = await asyncio.gather(
            _try_checkout_entry("u1", entry_id),
            _try_checkout_entry("u1", entry_id),
        )
    assert sorted(results) == [False, True]
    refreshed = await Entry.get(entry_id)
    assert refreshed is not None
    assert (refreshed.custom_fields or {}).get("lifecycle_state") == "checked_out"
