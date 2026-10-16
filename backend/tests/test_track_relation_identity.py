"""Legacy identity recovery requires a unique matching Core creation snapshot."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import change_event_logger
from app.services.track_relation_identity import relation_track_type

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


def track(**overrides):
    """An already-renamed untemplated legacy Track."""
    return SimpleNamespace(
        id="n.Track.legacy",
        workspace_id="ws",
        created_at="created",
        title="Business Directory",
        template_id=None,
        relation_type_key="",
        **overrides,
    )


@pytest.mark.parametrize(
    "invalid", [None, "id", "workspace_id", "created_at", "duplicate"]
)
async def test_only_unique_matching_creation_evidence_recovers_identity(
    monkeypatch, invalid
):
    """Foreign, conflicting or reclaimed evidence cannot rename the type implicitly."""
    item = track()
    snapshot = {
        "id": item.id,
        "workspace_id": item.workspace_id,
        "created_at": item.created_at,
        "title": "Businesses",
    }
    if invalid in {"id", "workspace_id", "created_at"}:
        snapshot[invalid] = "foreign"
    rows = [{"context": {"log_data": {"after": snapshot}}}]
    if invalid == "duplicate":
        rows *= 2
    find = AsyncMock(return_value=rows)
    monkeypatch.setattr(
        change_event_logger,
        "get_change_event_logger",
        lambda: SimpleNamespace(
            _get_log_context=lambda: SimpleNamespace(
                database=SimpleNamespace(find=find)
            )
        ),
    )
    assert await relation_track_type(item) == (
        "Businesses" if invalid is None else item.title
    )
    assert find.call_args.kwargs["limit"] == 2
    assert item.relation_type_key == ""  # Reads never rewrite the target.


async def test_declared_template_identity_beats_display_and_recovery():
    """A legacy title or new display title cannot bypass a typed declaration."""
    item = track()
    item.template_id = "contacts"
    item.relation_type_key = "Businesses"
    assert await relation_track_type(item) == "contacts"
