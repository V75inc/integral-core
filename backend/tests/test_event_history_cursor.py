"""Continuation cannot reveal withheld identifiers or move across authority."""

import pytest

from app.api.errors import BadRequestError
from app.services.event_history_cursor import (
    decode_history_cursor,
    encode_history_cursor,
)

pytestmark = pytest.mark.smoke


def test_private_position_round_trip_and_legacy_compatibility():
    binding = {
        "principal_id": "owner",
        "workspace_id": "workspace",
        "scope": "track:one",
    }
    cursor = encode_history_cursor("private-event-position", binding=binding)
    assert decode_history_cursor(cursor, binding=binding) == "private-event-position"
    assert "private-event-position" not in cursor
    assert (
        decode_history_cursor("legacy-visible-row", binding=binding)
        == "legacy-visible-row"
    )


@pytest.mark.parametrize("field", ["principal_id", "workspace_id", "scope"])
def test_cursor_cannot_cross_request_binding(field):
    binding = {
        "principal_id": "owner",
        "workspace_id": "workspace",
        "scope": "track:one",
    }
    cursor = encode_history_cursor("private-position", binding=binding)
    with pytest.raises(BadRequestError):
        decode_history_cursor(cursor, binding={**binding, field: "other"})


def test_tampering_is_rejected():
    cursor = encode_history_cursor("private-position", binding={})
    with pytest.raises(BadRequestError):
        decode_history_cursor(cursor[:10] + "!" + cursor[11:], binding={})
