"""Save hooks observe the admitted revision and retain nested updates."""

import pytest

from tests.test_entry_create_rollback import _bootstrap_track

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


async def _entry(email):
    from app.models.nodes import Track
    from app.services.entry_create import create_entry_in_track

    user, workspace, track_id = await _bootstrap_track(email)
    entry = await create_entry_in_track(
        track=await Track.get(track_id),
        user_id=user,
        workspace_id=workspace,
        title="Before hook",
    )
    return user, workspace, entry


async def test_nested_update_hook_observes_saved_revision_and_retains_effect(
    monkeypatch,
):
    from app.api.errors import ResourceConflictError
    from app.models.nodes import Entry
    from app.services import entry_update
    from app.services.entry_update import update_entry_in_track

    user, workspace, entry = await _entry("hook-snapshot@example.com")
    observed = []

    async def hook(**kwargs):
        current = await Entry.find_one({"id": kwargs["entry"].id})
        observed.append((current.title, current.record_revision))
        assert current.title == "After hook"
        assert current.record_revision == 2
        await update_entry_in_track(
            entry_id=current.id,
            user_id=user,
            workspace_id=workspace,
            body="Nested hook effect",
            expected_record_revision=2,
        )

    monkeypatch.setattr(entry_update, "run_entry_save_hooks", hook)
    result = await update_entry_in_track(
        entry_id=entry.id,
        user_id=user,
        workspace_id=workspace,
        title="After hook",
        custom_fields={},
        expected_record_revision=1,
    )
    assert observed == [("After hook", 2)]
    assert result.title == "After hook" and result.body == "Nested hook effect"
    assert result.record_revision == 3
    with pytest.raises(ResourceConflictError):
        await update_entry_in_track(
            entry_id=entry.id,
            user_id=user,
            workspace_id=workspace,
            title="Stale write",
            expected_record_revision=1,
        )
    restored = await Entry.find_one({"id": entry.id})
    assert restored.title == "After hook" and restored.record_revision == 3


@pytest.mark.postgres
async def test_failed_nested_update_hook_rolls_back_outer_and_inner_effects(
    monkeypatch,
):
    from app.models.nodes import Entry
    from app.services import entry_update
    from app.services.entry_update import update_entry_in_track

    user, workspace, entry = await _entry("hook-rollback@example.com")

    async def hook(**kwargs):
        await update_entry_in_track(
            entry_id=kwargs["entry"].id,
            user_id=user,
            workspace_id=workspace,
            body="Must roll back",
            expected_record_revision=2,
        )
        raise RuntimeError("hook rollback probe")

    monkeypatch.setattr(entry_update, "run_entry_save_hooks", hook)
    with pytest.raises(RuntimeError, match="hook rollback probe"):
        await update_entry_in_track(
            entry_id=entry.id,
            user_id=user,
            workspace_id=workspace,
            title="Must roll back",
            custom_fields={},
            expected_record_revision=1,
        )
    restored = await Entry.find_one({"id": entry.id})
    assert restored.title == "Before hook" and restored.body == ""
    assert restored.record_revision == 1
