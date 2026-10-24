"""Missing primary hints may deny early, never authorize a history snapshot."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.errors import QueryUnavailableError
from app.models.nodes import Entry
from app.services.event_history import _missing_primary_events

pytestmark = [pytest.mark.unit, pytest.mark.smoke, pytest.mark.asyncio]


def event(kind, resource_id, action="entry.update"):
    return SimpleNamespace(
        resource_type=kind,
        resource_id=resource_id,
        action=action,
        before=None,
        after=None,
    )


async def test_many_deleted_records_need_one_bounded_existence_query(monkeypatch):
    database = SimpleNamespace(find=AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "jvspatial.core.context.get_default_context",
        lambda: SimpleNamespace(database=database),
    )
    events = [event("entry", f"entry-{n}") for n in range(100)]
    missing = await _missing_primary_events(events)
    assert len(missing) == 100
    database.find.assert_awaited_once()
    assert database.find.call_args.kwargs["limit"] == 101


async def test_prefilter_retains_existing_primaries_and_special_audiences(monkeypatch):
    database = SimpleNamespace(
        find=AsyncMock(
            return_value=[{"id": "existing", "entity": Entry._entity_name()}]
        )
    )
    monkeypatch.setattr(
        "jvspatial.core.context.get_default_context",
        lambda: SimpleNamespace(database=database),
    )
    missing = await _missing_primary_events(
        [
            event("entry", "existing"),
            event("entry", "missing"),
            event("entry", "forensic", "policy.deny"),
            event("connector", "removed", "connector.delete"),
        ]
    )
    assert missing == {("entry", "missing")}


async def test_existence_store_failure_is_not_an_empty_history(monkeypatch):
    database = SimpleNamespace(
        find=AsyncMock(side_effect=RuntimeError("Store unavailable"))
    )
    monkeypatch.setattr(
        "jvspatial.core.context.get_default_context",
        lambda: SimpleNamespace(database=database),
    )
    with pytest.raises(QueryUnavailableError):
        await _missing_primary_events([event("entry", "entry")])
