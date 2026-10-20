"""Phase 0 enabler tools — bulk ops, relation wiring, CRUD-fill stagers/executors."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from app.agentive.tooling import bindings


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


def test_update_tag_stager_only_carries_explicit_tag_fields():
    staged = bindings._stage_update_tag(
        {"tag_id": "tag-1", "name": "Priority", "aliases": ["P1"], "user_id": "evil"}
    )
    assert staged["kind"] == "update_tag"
    assert staged["payload"] == {
        "tag_id": "tag-1",
        "updates": {"name": "Priority", "aliases": ["P1"]},
    }


def test_merge_tags_stager_is_revision_bound_and_value_free(monkeypatch):
    async def _prepare(**kwargs):
        return {
            "source_tag_id": "source",
            "target_tag_id": "target",
            "affected_count": 2,
            "entry_ids": ["e1", "e2"],
            "preview_fingerprint": "fp",
        }

    monkeypatch.setattr("app.services.tag_merge.prepare_tag_merge", _prepare)
    principal_token = bindings._propose_principal.set("u1")
    try:
        staged = asyncio.run(
            bindings._stage_merge_tags(
                {"source_tag_id": "source", "target_tag_id": "target"}
            )
        )
    finally:
        bindings._propose_principal.reset(principal_token)
    assert staged["kind"] == "merge_tags"
    assert staged["payload"] == {
        "source_tag_id": "source",
        "target_tag_id": "target",
        "preview_fingerprint": "fp",
        "affected_count": 2,
        "entry_ids": ["e1", "e2"],
    }
    assert "entry_ids" not in staged["diff_human"]


@pytest.mark.asyncio
async def test_bulk_update_executor_loops_and_fail_stops(monkeypatch):
    """Bulk-update executor calls the single executor per entry and fail-stops."""
    from app.agentive import staging_executors

    seen: List[str] = []

    async def _fake_update(user_id: str, payload: Dict[str, Any]):
        seen.append(payload["entry_id"])
        if payload["entry_id"] == "e2":
            return {
                "error": True,
                "status_code": 409,
                "error_code": "conflict",
                "message": "stale revision",
                "details": {"error_code": "record_revision_conflict"},
            }
        return {"ok": True}

    monkeypatch.setattr(staging_executors, "_x_update_entry", _fake_update)

    result = await staging_executors._x_bulk_update_entries(
        "u1", {"entry_ids": ["e1", "e2", "e3"], "updates": {"status": "done"}}
    )
    assert result["error"] is True
    assert result["error_code"] == "bulk_partial_failure"
    assert result["updated"] == 1 and result["total"] == 3
    assert result["conflicts"] == [
        {
            "entry_id": "e2",
            "index": 1,
            "status_code": 409,
            "error_code": "conflict",
            "message": "stale revision",
            "details": {"error_code": "record_revision_conflict"},
        }
    ]
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
