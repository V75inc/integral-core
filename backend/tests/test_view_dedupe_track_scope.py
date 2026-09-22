"""Regression: shared template CP Views registry must not cross-delete by track."""

from __future__ import annotations

from typing import List

import pytest

from app.api.views import _dedupe_duplicate_views, _dedupe_key


class _FakeView:
    def __init__(
        self,
        *,
        view_id: str,
        track_id: str,
        view_type: str = "kanban",
        name: str = "Tasks Board",
        manifest_key: str = "tasks-board",
        is_default: bool = False,
        created_at: str = "2026-01-01T00:00:00Z",
    ) -> None:
        self.id = view_id
        self.name = name
        self.type = view_type
        self.track_id = track_id
        self.is_default = is_default
        self.created_at = created_at
        self.entry_type_keys = ["task"]
        self.config = {"_manifest_view_key": manifest_key}
        self.deleted = False

    async def delete(self) -> None:
        self.deleted = True


def test_dedupe_key_includes_track_id():
    a = _FakeView(view_id="a", track_id="track-a")
    b = _FakeView(view_id="b", track_id="track-b")
    assert _dedupe_key(a) != _dedupe_key(b)
    assert "track-a" in _dedupe_key(a)
    assert "track-b" in _dedupe_key(b)


@pytest.mark.asyncio
async def test_dedupe_does_not_delete_sibling_track_views():
    """Same manifest key on two tracks must both survive dedupe."""
    views: List[_FakeView] = [
        _FakeView(view_id="va", track_id="track-a", is_default=True),
        _FakeView(view_id="vb", track_id="track-b", is_default=True),
    ]
    survivors = await _dedupe_duplicate_views(views)  # type: ignore[arg-type]
    assert {v.id for v in survivors} == {"va", "vb"}
    assert not any(v.deleted for v in views)


@pytest.mark.asyncio
async def test_dedupe_collapses_same_track_duplicates():
    views: List[_FakeView] = [
        _FakeView(
            view_id="keep",
            track_id="track-a",
            is_default=True,
            created_at="2026-01-01T00:00:00Z",
        ),
        _FakeView(
            view_id="drop",
            track_id="track-a",
            is_default=False,
            created_at="2026-01-02T00:00:00Z",
        ),
    ]
    survivors = await _dedupe_duplicate_views(views)  # type: ignore[arg-type]
    assert [v.id for v in survivors] == ["keep"]
    assert views[1].deleted is True


@pytest.mark.asyncio
async def test_dedupe_adopts_legacy_unkeyed_scaffold_view():
    """A manifest-backed view replaces an earlier direct scaffold copy."""
    legacy = _FakeView(
        view_id="legacy",
        track_id="track-a",
        name="All Tasks",
        manifest_key="",
        created_at="2026-01-01T00:00:00Z",
    )
    canonical = _FakeView(
        view_id="canonical",
        track_id="track-a",
        name="All Tasks",
        manifest_key="all_tasks",
        created_at="2026-01-02T00:00:00Z",
    )

    survivors = await _dedupe_duplicate_views([legacy, canonical])  # type: ignore[arg-type]

    assert [v.id for v in survivors] == ["canonical"]
    assert legacy.deleted is True
