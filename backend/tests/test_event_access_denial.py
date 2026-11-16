"""Expected release denial withholds one event, not the healthy transport."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.api.errors import InsufficientPermissionsError
from app.exceptions import AuthorizationUnavailableError
from app.services import event_access
from app.services.change_event_logger import ChangeEventEnvelope

pytestmark = [pytest.mark.asyncio, pytest.mark.unit, pytest.mark.smoke]


@pytest.mark.parametrize(
    "failure",
    [InsufficientPermissionsError, AuthorizationUnavailableError, RuntimeError],
)
async def test_only_explicit_denial_is_withheld_after_admission_unwinds(
    monkeypatch, failure
):
    from app.services import release_admission

    unwound = []

    @asynccontextmanager
    async def deny(*args):
        try:
            raise failure("Access unavailable")
            yield
        finally:
            unwound.append(True)

    monkeypatch.setattr(release_admission, "release_admission", deny)
    monkeypatch.setattr(
        event_access, "event_release_workspace", AsyncMock(return_value="workspace")
    )
    event = ChangeEventEnvelope(
        id="denied",
        ts="2026-11-15T00:00:00Z",
        actor_kind="human",
        actor_id="owner",
        action="app.update",
        resource_type="App",
        resource_id="private-app",
        scope="app:private-app",
        before=None,
        after={"name": "Private"},
    )
    evaluate = AsyncMock()
    if failure is InsufficientPermissionsError:
        assert (
            await event_access.prepare_event_payload(
                "outsider", event, action="event_feed.subscribe", evaluate=evaluate
            )
            is None
        )
    else:
        with pytest.raises(failure):
            await event_access.prepare_event_payload(
                "outsider", event, action="event_feed.subscribe", evaluate=evaluate
            )
    assert unwound == [True]
    evaluate.assert_not_awaited()
