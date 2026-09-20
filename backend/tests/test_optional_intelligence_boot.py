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


@pytest.mark.asyncio
async def test_readiness_remains_ready_when_intelligence_is_unavailable() -> None:
    """Database readiness is independent from an optional model provider."""
    from app.api.meta import readiness
    from app.modules.intelligence import mark_intelligence_unavailable

    database = type("Database", (), {"count": AsyncMock(return_value=0)})()
    manager = type("Manager", (), {"get_prime_database": lambda _self: database})()
    mark_intelligence_unavailable("bootstrap_failed")

    with patch("jvspatial.db.get_database_manager", return_value=manager):
        response = await readiness()

    assert response == {
        "status": "ready",
        "intelligence": {"available": False, "reason": "bootstrap_failed"},
    }
