"""Quiet execution must preserve failures and require an explicit settled signal."""

import pytest

from app.services.scheduled_turn import is_silent_routine

pytestmark = pytest.mark.smoke


def test_quiet_signal_suppresses_only_successful_routine_delivery():
    assert is_silent_routine([{"type": "routine-no-message"}, {"type": "step"}])
    assert not is_silent_routine([{"type": "text-delta", "delta": "No items"}])
    assert not is_silent_routine(
        [{"type": "routine-no-message"}, {"type": "error", "code": "provider_failed"}]
    )
