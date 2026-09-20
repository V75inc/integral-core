"""WP-01: Core boot remains available when the resident harness cannot start."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_harness_bootstrap_failure_is_recorded_without_raising() -> None:
    """A resident-harness failure must not abort ordinary Core startup."""
    from app import main
    from app.modules.intelligence import intelligence_runtime_status

    with (
        patch("app.main._purge_dead_resident_action_orphans", new=AsyncMock()),
        patch(
            "jvagent.embed.bootstrap",
            new=AsyncMock(side_effect=RuntimeError("model provider unavailable")),
        ),
    ):
        await main._bootstrap_resident_harness()

    status = intelligence_runtime_status()
    assert status.available is False
    assert status.reason == "bootstrap_failed"
