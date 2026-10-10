"""WP-01: Core boot remains available when the resident harness cannot start."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.mark.asyncio
async def test_native_bootstrap_qualifies_skills_without_vendor_runtime():
    from app import main
    from app.modules.intelligence import intelligence_runtime_status

    await main._bootstrap_resident_harness()
    assert intelligence_runtime_status().available


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
