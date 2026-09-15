"""Phase 0 enabler tools — bulk ops, relation wiring, CRUD-fill stagers/executors."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.agentive.tooling import bindings


def test_bulk_update_stager_allowlists_keys():
    """Bulk-update stager keeps only known patch keys (drops smuggled args)."""
    staged = bindings._stage_bulk_update_entries(
        {
            "entry_ids": ["e1", "e2"],
            "updates": {"status": "done", "user_id": "evil", "title": "x"},
        }
    )
    assert staged["kind"] == "bulk_update_entries"
    assert staged["payload"]["entry_ids"] == ["e1", "e2"]
    assert "user_id" not in staged["payload"]["updates"]
    assert staged["payload"]["updates"] == {"status": "done", "title": "x"}


def test_link_entries_stager_shape():
    """Link stager carries source/field/target into a link_entries kind."""
    staged = bindings._stage_link_entries(
        {"source_entry_id": "e1", "field_key": "contact", "target_id": "e2"}
    )
    assert staged["kind"] == "link_entries"
    assert staged["payload"] == {
        "source_entry_id": "e1",
        "field_key": "contact",
        "target_id": "e2",
    }


def test_create_tag_stager_drops_empty():
    """Create-tag stager forwards only provided optional fields."""
    staged = bindings._stage_create_tag({"name": "Urgent", "track_id": "t1"})
    assert staged["kind"] == "create_tag"
    assert staged["payload"] == {"name": "Urgent", "track_id": "t1"}


@pytest.mark.asyncio
async def test_bulk_update_executor_loops_and_fail_stops(monkeypatch):
    """Bulk-update executor calls the single executor per entry and fail-stops."""
    from app.agentive import staging_executors

    seen: List[str] = []

    async def _fake_update(user_id: str, payload: Dict[str, Any]):
        seen.append(payload["entry_id"])
        if payload["entry_id"] == "e2":
            return {"error": True, "message": "denied"}
        return {"ok": True}

    monkeypatch.setattr(staging_executors, "_x_update_entry", _fake_update)

    result = await staging_executors._x_bulk_update_entries(
        "u1", {"entry_ids": ["e1", "e2", "e3"], "updates": {"status": "done"}}
    )
    assert result["error"] is True
    assert result["error_code"] == "bulk_partial_failure"
    assert result["updated"] == 1 and result["total"] == 3
    assert seen == ["e1", "e2"]  # stopped at the failure, never reached e3


@pytest.mark.asyncio
async def test_bulk_update_executor_all_ok(monkeypatch):
    """All entries succeed → updated == total."""
    from app.agentive import staging_executors

    async def _fake_update(user_id: str, payload: Dict[str, Any]):
        return {"ok": True}

    monkeypatch.setattr(staging_executors, "_x_update_entry", _fake_update)
    result = await staging_executors._x_bulk_update_entries(
        "u1", {"entry_ids": ["e1", "e2"], "updates": {"status": "done"}}
    )
    assert result == {"updated": 2, "total": 2}


def test_stagers_require_args_cleanly():
    """A missing required arg raises a clear ValueError, not a KeyError."""
    import pytest

    with pytest.raises(ValueError, match="required argument"):
        bindings._stage_add_entry_tag({"entry_id": "e1"})  # no tag_id
    with pytest.raises(ValueError, match="required argument"):
        bindings._stage_link_entries({"source_entry_id": "e1"})  # no field/target
    with pytest.raises(ValueError, match="required argument"):
        bindings._stage_create_tag({})  # no name


def test_enabler_tools_have_bindings():
    """Every implemented enabler tool is wired in the registry."""
    for name in (
        "integral_bulk_update_entries",
        "integral_bulk_delete_entries",
        "integral_add_entry_tag",
        "integral_remove_entry_tag",
        "integral_create_tag",
        "integral_update_app",
        "integral_link_entries",
    ):
        assert name in bindings.TOOL_BINDINGS
        assert bindings.TOOL_BINDINGS[name].stager is not None


def test_enabler_kinds_have_executors():
    """Every enabler staging kind has a registered executor."""
    from app.agentive import staging_executors

    for kind in (
        "bulk_update_entries",
        "bulk_delete_entries",
        "add_entry_tag",
        "remove_entry_tag",
        "create_tag",
        "update_app",
        "link_entries",
    ):
        assert staging_executors.supports(kind)


def test_bulk_update_stager_rejects_patch_that_filters_to_nothing():
    """A patch whose every key is unknown is refused, not staged as a no-op.

    Regression from the Personal Context end-to-end walk: the agent sent
    ``{"handled": "promoted"}`` to mark two observations promoted. ``handled``
    is a custom field, so the allowlist filtered it away and the empty patch was
    staged as a card reading "Update 2 entries" that reported "Done" and changed
    nothing -- leaving both observations pending and re-promotable.
    """
    with pytest.raises(ValueError) as exc:
        bindings._stage_bulk_update_entries(
            {"entry_ids": ["e1", "e2"], "updates": {"handled": "promoted"}}
        )
    msg = str(exc.value)
    assert "handled" in msg
    # The message has to be recoverable: name the allowed keys and point at the
    # wrapper the caller actually needed.
    assert "fields" in msg


def test_bulk_update_stager_rejects_empty_updates():
    """No keys at all is refused with the allowed set named."""
    with pytest.raises(ValueError) as exc:
        bindings._stage_bulk_update_entries({"entry_ids": ["e1"], "updates": {}})
    assert "at least one field" in str(exc.value)
    assert "status" in str(exc.value)


def test_bulk_update_stager_requires_entry_ids():
    """An empty target list is refused rather than staged as "Update 0 entries"."""
    with pytest.raises(ValueError, match="entry_ids is required"):
        bindings._stage_bulk_update_entries(
            {"entry_ids": [], "updates": {"status": "done"}}
        )


def test_bulk_update_stager_still_drops_smuggled_keys_alongside_valid_ones():
    """The allowlist keeps protecting a mixed patch -- the guard is not a bypass."""
    staged = bindings._stage_bulk_update_entries(
        {
            "entry_ids": ["e1"],
            "updates": {"fields": {"handled": "promoted"}, "user_id": "evil"},
        }
    )
    assert staged["payload"]["updates"] == {"fields": {"handled": "promoted"}}


def test_bulk_update_diff_human_names_the_keys_it_will_apply():
    """The card text lists real keys, so an empty bracket can't reach a reviewer."""
    staged = bindings._stage_bulk_update_entries(
        {"entry_ids": ["e1", "e2"], "updates": {"status": "done"}}
    )
    assert staged["diff_human"] == "Apply ['status'] to 2 entries"
