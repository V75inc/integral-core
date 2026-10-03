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


async def _try_revision_write(
    entry_id: str, value: str, expected_revision: int
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
        user_id="u1",
        scope="test:revision-cas",
        event_sink=lambda _event: None,
    )
    return ok


@pytest.mark.postgres
@pytest.mark.contract
async def test_entry_conditional_update_concurrent_one_wins(postgres_raw_db):
    from jvspatial.core.context import GraphContext, _default_context_var

    from app.models.edges import CONTAINS
    from app.models.nodes import App, Entry, IntegralApp, Track
    from app.services.app_graph import ensure_integral_app_graph

    token = _default_context_var.set(GraphContext(database=postgres_raw_db))
    suffix = uuid.uuid4().hex[:12]
    try:
        await ensure_integral_app_graph(include_library=False)
        integral_app = await IntegralApp.get("n.IntegralApp.integral")
        app = await App.create(
            id=f"n.WorkspaceApp.cas-{suffix}",
            name="CAS contract",
            workspace_id=f"ws-cas-{suffix}",
        )
        track = await Track.create(
            id=f"n.Track.cas-{suffix}",
            title="CAS contract",
            workspace_id=app.workspace_id,
        )
        entry = await Entry.create(
            id=f"n.Entry.cas-{suffix}",
            title="Contract asset",
            track_id=track.id,
            custom_fields={"lifecycle_state": "available"},
        )
        # Build an actual rooted persisted graph; this exercises jvspatial's
        # node/context serialization and the normal Entry.get path.
        await integral_app.connect(app, edge=CONTAINS)
        await app.connect(track, edge=CONTAINS)
        await track.connect(entry, edge=CONTAINS)

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
                _try_checkout_entry("u1", entry.id),
                _try_checkout_entry("u1", entry.id),
            )
        assert sorted(results) == [False, True]
        refreshed = await Entry.get(entry.id)
        assert refreshed is not None
        assert refreshed.custom_fields["lifecycle_state"] == "checked_out"
        assert refreshed.record_revision == 2
        revision_results = await asyncio.gather(
            _try_revision_write(entry.id, "first", 2),
            _try_revision_write(entry.id, "second", 2),
        )
        assert sorted(revision_results) == [False, True]
        latest = await Entry.get(entry.id)
        assert latest is not None
        assert latest.record_revision == 3
        assert latest.custom_fields["claim_note"] in {"first", "second"}
    finally:
        _default_context_var.reset(token)
