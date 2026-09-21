"""Regression tests for populated-App package upgrade safety."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.exceptions import ContentProfileValidationError
from app.services.application_upgrade_safety import (
    assert_package_upgrade_migration_safe,
    start_package_upgrade_migrations,
)


@pytest.mark.asyncio
async def test_upgrade_safety_rejects_unhandled_record_breaks():
    attached = SimpleNamespace(manifest={"scope": "app"})
    library = SimpleNamespace(manifest={"scope": "app"})
    impacts = [
        {
            "track_id": "vehicles",
            "would_need_migration": 2,
            "would_fail_validation": 0,
        }
    ]

    with (
        patch(
            "app.services.application_upgrade_safety."
            "preview_effective_app_manifest_after_library_merge",
            new=AsyncMock(return_value={"scope": "app", "migrations": []}),
        ),
        patch(
            "app.services.application_upgrade_safety."
            "compute_entry_impact_for_attached",
            new=AsyncMock(return_value=impacts),
        ),
    ):
        with pytest.raises(ContentProfileValidationError) as excinfo:
            await assert_package_upgrade_migration_safe(
                attached_profile=attached,  # type: ignore[arg-type]
                library_profile=library,  # type: ignore[arg-type]
            )

    assert excinfo.value.status_code == 422
    assert excinfo.value.details["unhandled_breaks"]
    assert excinfo.value.details["entry_impact"] == impacts


@pytest.mark.asyncio
async def test_upgrade_safety_accepts_declared_migration_and_returns_effective_candidate():
    attached = SimpleNamespace(manifest={"scope": "app"})
    library = SimpleNamespace(manifest={"scope": "app"})
    candidate = {
        "scope": "app",
        "migrations": [{"ops": [{"op": "rename_field", "from": "a", "to": "b"}]}],
    }
    impacts = [
        {
            "track_id": "vehicles",
            "would_need_migration": 1,
            "would_fail_validation": 0,
        }
    ]

    with (
        patch(
            "app.services.application_upgrade_safety."
            "preview_effective_app_manifest_after_library_merge",
            new=AsyncMock(return_value=candidate),
        ),
        patch(
            "app.services.application_upgrade_safety."
            "compute_entry_impact_for_attached",
            new=AsyncMock(return_value=impacts),
        ),
    ):
        result = await assert_package_upgrade_migration_safe(
            attached_profile=attached,  # type: ignore[arg-type]
            library_profile=library,  # type: ignore[arg-type]
        )

    assert result["candidate_manifest"] == candidate
    assert result["entry_impact"] == impacts
    assert result["unhandled_breaks"] == []


@pytest.mark.asyncio
async def test_upgrade_migrations_skip_when_preflight_has_no_affected_records():
    result = await start_package_upgrade_migrations(
        attached_profile=SimpleNamespace(),  # type: ignore[arg-type]
        safety={
            "candidate_manifest": {"scope": "app"},
            "entry_impact": [{"would_need_migration": 0, "would_fail_validation": 0}],
        },
        actor_id="owner-1",
    )

    assert result == {
        "executed": False,
        "status": "not_needed",
        "affected_entry_count": 0,
    }


@pytest.mark.asyncio
async def test_upgrade_migrations_start_existing_async_runner_for_affected_records():
    attached = SimpleNamespace()
    tracker = {"status": "running", "affected_entry_count": 3}
    with patch(
        "app.services.migrations.runner.run_migration_async",
        new=AsyncMock(return_value=tracker),
    ) as runner:
        result = await start_package_upgrade_migrations(
            attached_profile=attached,  # type: ignore[arg-type]
            safety={
                "candidate_manifest": {"scope": "app", "migrations": [{"ops": []}]},
                "entry_impact": [{"would_need_migration": 3}],
            },
            actor_id="owner-1",
        )

    assert result == {"executed": True, **tracker}
    runner.assert_awaited_once_with(
        published_cp=attached,
        compiled_manifest={"scope": "app", "migrations": [{"ops": []}]},
        actor_id="owner-1",
    )
