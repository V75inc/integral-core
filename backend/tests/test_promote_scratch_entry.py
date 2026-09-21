"""Regression tests for ``promote_scratch_entry`` (Plan 04-04 — MEM-03).

Covers:

- Happy path: new Entry on the target Track carries
  ``provenance.derived_from = [source.id]``, source is archived
  (``status="archived"``) — NOT deleted.
- Permission denial on source read raises
  :class:`InsufficientPermissionsError` (and the policy engine emits a
  ``policy.deny`` ChangeEvent via Phase 3 D-12 — not asserted here, but
  exercised via the engine call).
- Permission denial on target create raises
  :class:`InsufficientPermissionsError`.
- Two ChangeEvents emitted on success: ``entry.create`` for the new
  target-side entry, ``entry.update`` for the archived source.
- REST endpoint ``POST /api/tracks/{target}/promote-scratch-entry``
  round-trips 200 with the new-entry response shape.
- Missing source raises a 404 / ResourceNotFoundError shape.
- Single-Literal grep gates still at 1 each.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.api.errors import InsufficientPermissionsError, ResourceNotFoundError
from app.models.nodes import Entry, EntryType, Track
from app.schemas.policy import Decision
from app.services import agent_scratch as agent_scratch_module
from app.services.agent_scratch import (
    promote_scratch_entry,
    provision_scratch_track,
)
from app.services.app_graph import (
    ensure_track_attached_operational_model,
    get_track_attached_operational_model,
)


@pytest.fixture(autouse=True)
def _reset_scratch_cache():
    agent_scratch_module._SCRATCH_TRACK_ID_CACHE.clear()
    yield
    agent_scratch_module._SCRATCH_TRACK_ID_CACHE.clear()


async def _make_target_track(user, *, title: str = "Promote Target") -> Track:
    """Create a plain Track + default attached CP so it has at least one EntryType."""
    from datetime import datetime, timezone

    from app.models.edges import OWNS
    from app.services.app_graph import catalog_track
    from app.services.personal_workspace import ensure_personal_workspace

    ws = await ensure_personal_workspace(user)
    now = datetime.now(timezone.utc).isoformat()
    track = await Track.create(
        title=title,
        title_fold=title.casefold(),
        owner_id=user.id,
        purpose="Promote target",
        workspace_id=ws.id,
        created_at=now,
        updated_at=now,
    )
    await user.connect(track, edge=OWNS, role="owner", granted_at=now)
    try:
        await catalog_track(track)
    except Exception:
        pass
    await ensure_track_attached_operational_model(track)
    return track


async def _make_source_entry_in_scratch(user, *, title: str = "Scratch obs") -> Entry:
    """Provision the user's scratch Track and create a single source entry in it."""
    from datetime import datetime, timezone

    from app.models.edges import CONTAINS, IS_OF_TYPE

    track = await provision_scratch_track(user_id=user.id)
    # Pick the first available EntryType under the scratch CP. The library
    # merge materializes ``observation`` first; if for some reason no
    # EntryType is materialized (manifest-only merge), fall back to the
    # CP-default route via ``ensure_track_attached_operational_model``.
    types = await EntryType.find({"context.track_id": track.id})
    if not types:
        await ensure_track_attached_operational_model(track)
        types = await EntryType.find({"context.track_id": track.id})
    assert types, "scratch Track has no EntryType — library merge regression"
    et = types[0]
    now = datetime.now(timezone.utc).isoformat()
    entry = await Entry.create(
        type_id=et.id,
        title=title,
        author_id=user.id,
        track_id=track.id,
        status="active",
        body="raw scratch content",
        created_at=now,
        updated_at=now,
    )
    await track.connect(entry, edge=CONTAINS, added_at=now)
    await entry.connect(et, edge=IS_OF_TYPE, assigned_at=now)
    return entry


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promote_preserves_derived_from_and_archives_source(test_user):
    """MEM-03: new entry carries derived_from; source goes to status='archived'."""
    if test_user is None:
        pytest.skip("no test_user node available")
    source = await _make_source_entry_in_scratch(test_user)
    target = await _make_target_track(test_user)
    new_entry = await promote_scratch_entry(
        user_id=test_user.id,
        entry_id=source.id,
        target_track_id=target.id,
    )
    assert new_entry.track_id == target.id
    derived = (
        new_entry.provenance.derived_from if new_entry.provenance is not None else []
    )
    assert source.id in derived
    # Reload the source to confirm archive transition.
    reloaded = await Entry.get(source.id)
    assert reloaded is not None
    assert reloaded.status == "archived"


@pytest.mark.asyncio
async def test_promote_emits_two_change_events(test_user):
    """One entry.create for the new entry; one entry.update for archived source."""
    if test_user is None:
        pytest.skip("no test_user node available")
    source = await _make_source_entry_in_scratch(test_user)
    target = await _make_target_track(test_user)

    seen: List[str] = []
    real_emit = agent_scratch_module.emit_change_event

    async def _spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(kwargs.get("action", ""))
        return await real_emit(*args, **kwargs)

    with patch.object(agent_scratch_module, "emit_change_event", new=_spy):
        await promote_scratch_entry(
            user_id=test_user.id,
            entry_id=source.id,
            target_track_id=target.id,
        )
    assert seen.count("entry.create") >= 1
    assert seen.count("entry.update") >= 1


# ---------------------------------------------------------------------------
# Permission denial branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promote_denies_when_source_read_blocked(test_user):
    """Source-read denial raises InsufficientPermissionsError."""
    if test_user is None:
        pytest.skip("no test_user node available")
    source = await _make_source_entry_in_scratch(test_user)
    target = await _make_target_track(test_user)

    async def _deny_read(**kwargs):  # type: ignore[no-untyped-def]
        if kwargs.get("action") == "entry.read":
            return Decision(allowed=False, reason="test_deny_read")
        return Decision(allowed=True, reason="test_allow")

    with patch.object(agent_scratch_module, "policy_evaluate", new=_deny_read):
        with pytest.raises(InsufficientPermissionsError):
            await promote_scratch_entry(
                user_id=test_user.id,
                entry_id=source.id,
                target_track_id=target.id,
            )


@pytest.mark.asyncio
async def test_promote_denies_when_target_create_blocked(test_user):
    """Target-create denial raises InsufficientPermissionsError."""
    if test_user is None:
        pytest.skip("no test_user node available")
    source = await _make_source_entry_in_scratch(test_user)
    target = await _make_target_track(test_user)

    async def _deny_create(**kwargs):  # type: ignore[no-untyped-def]
        if kwargs.get("action") == "entry.create":
            return Decision(allowed=False, reason="test_deny_create")
        return Decision(allowed=True, reason="test_allow")

    with patch.object(agent_scratch_module, "policy_evaluate", new=_deny_create):
        with pytest.raises(InsufficientPermissionsError):
            await promote_scratch_entry(
                user_id=test_user.id,
                entry_id=source.id,
                target_track_id=target.id,
            )


# ---------------------------------------------------------------------------
# Missing-source branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promote_raises_on_missing_source(test_user):
    """Unknown source entry id raises ResourceNotFoundError."""
    if test_user is None:
        pytest.skip("no test_user node available")
    target = await _make_target_track(test_user)
    with pytest.raises(ResourceNotFoundError):
        await promote_scratch_entry(
            user_id=test_user.id,
            entry_id="n.Entry.does-not-exist",
            target_track_id=target.id,
        )


# ---------------------------------------------------------------------------
# REST endpoint round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promote_scratch_entry_rest_round_trip(
    authenticated_client: AsyncClient, test_user
):
    """``POST /api/tracks/{tgt}/promote-scratch-entry`` returns 200 + entry shape."""
    if test_user is None:
        pytest.skip("no test_user node available")
    source = await _make_source_entry_in_scratch(test_user, title="REST source")
    target = await _make_target_track(test_user, title="REST target")

    resp = await authenticated_client.post(
        f"/api/tracks/{target.id}/promote-scratch-entry",
        json={"source_entry_id": source.id},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert "entry" in payload
    entry_shape: Dict[str, Any] = payload["entry"]
    assert entry_shape.get("track_id") == target.id
    assert payload.get("message") == "Entry promoted successfully"


# ---------------------------------------------------------------------------
# Substrate invariant — single-Literal grep gates unchanged
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    # backend/tests/test_promote_scratch_entry.py -> repo root
    return Path(__file__).resolve().parents[2]


def _grep_literal_count(prefix: str) -> int:
    """Return the count of ``^<prefix>\\s*=\\s*Literal`` matches under backend/app.

    Uses Python ``re`` rather than shelling out to ``grep`` so the gate is
    portable across GNU grep builds that do not treat ``\\s`` as whitespace.
    """
    import re

    backend_app = _repo_root() / "backend" / "app"
    rx = re.compile(rf"^{prefix}\s*=\s*Literal")
    count = 0
    for py in backend_app.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        for line in py.read_text(encoding="utf-8").splitlines():
            if rx.search(line):
                count += 1
    return count


def test_single_literal_invariants_preserved():
    """Plan 04-04 must not add new PolicyAction / ChangeEventAction / ActorKind decls."""
    assert _grep_literal_count("PolicyAction") == 1
    assert _grep_literal_count("ChangeEventAction") == 1
    assert _grep_literal_count("ActorKind") == 1
