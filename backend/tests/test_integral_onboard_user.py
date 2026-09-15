"""Phase 9 Plan 09-05 (ONBD-01) — integral_onboard_user state-machine tests.

These tests exercise the onboarding state machine directly via the
``app.agentive.services.integral_onboard_user`` module. ``integral_onboard_user``
is a multi-turn orchestration that is DEFERRED from the manifest tool catalogue
(resident-only); the legacy external-MCP registration + ``execute_tool``
dispatch assertions were retired in M2a Task 7 along with that surface. The
behavioral coverage of the underlying state machine is preserved in full.

Coverage:

1. User node has ``onboarded_at`` field.
2. step="start" returns next_step="ask_domain" + completed=False.
4. Full walk start → finalize ends with completed=True + onboarded_at set.
5. finalize emits EXACTLY ONE user.update ChangeEvent.
6. B4 acceptance — full walk leaves the user with >=1 App, >=1 Track,
   theme set, retrieval_mode set, and onboarded_at populated.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

# Module-level top-of-file imports kept minimal so test collection in
# environments with partial deps (e.g. CI workers missing PIL/jinja2 for
# unrelated avatar/notification surfaces) doesn't blow up before the
# pytest discovery completes. Heavy imports happen inside each test.
from app.models.nodes import User

# ---------------------------------------------------------------------------
# 1. User node has onboarded_at field
# ---------------------------------------------------------------------------


def test_user_has_onboarded_at_field():
    """``User.onboarded_at`` is declared on the Pydantic model."""
    assert "onboarded_at" in User.model_fields
    # Default value is None (Plan 09-05 — new users haven't onboarded yet).
    u = User()
    assert u.onboarded_at is None


# ---------------------------------------------------------------------------
# 2. step="start" → next_step="ask_domain"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_step_advances_to_ask_domain(test_user, monkeypatch):
    from app.agentive.services.integral_onboard_user import integral_onboard_user

    if test_user is None:
        pytest.skip("no test_user node available")
    result = await integral_onboard_user(user_id=test_user.id, step="start")
    assert result["next_step"] == "ask_domain"
    assert result["completed"] is False
    assert "Welcome" in result["prompt"]


# ---------------------------------------------------------------------------
# 4. Full walk start → finalize sets onboarded_at
# ---------------------------------------------------------------------------


async def _seed_library_space_package(name: str = "Onboarding Test App") -> str:
    """Seed a single library-package ContentProfile with scope='space'.

    The onboarding tool's create_tracks step needs >=1 candidate to drive
    the App + Track AC. Manifest is minimal (no tracks declared) so the
    space provisioner doesn't fan out to additional tracks beyond what the
    tool itself creates.
    """
    from app.models.nodes import ContentProfile

    now = datetime.now(timezone.utc).isoformat()
    cp = await ContentProfile.create(
        name=name,
        name_fold=name.casefold(),
        version="0.0.1",
        manifest={
            "scope": "app",
            "content_profile_schema_version": 2,
            "app": {"tracks": [], "relations": []},
        },
        scope="app",
        library_package=True,
        description="Test library package for onboarding flow.",
        created_at=now,
        updated_at=now,
    )
    return cp.id


@pytest.mark.asyncio
async def test_full_walk_finalize_sets_onboarded_at(test_user, monkeypatch):
    from app.agentive.services.integral_onboard_user import integral_onboard_user

    if test_user is None:
        pytest.skip("no test_user node available")

    await _seed_library_space_package()

    ctx: dict = {}
    last = await integral_onboard_user(user_id=test_user.id, step="start", context=ctx)
    assert last["next_step"] == "ask_domain"
    last = await integral_onboard_user(
        user_id=test_user.id, step="ask_domain", context=ctx
    )
    assert last["next_step"] == "propose_spaces"
    last = await integral_onboard_user(
        user_id=test_user.id, step="propose_spaces", context=ctx
    )
    # Pick the first suggestion (the seeded library package).
    suggestions = last.get("suggestions") or []
    assert suggestions, "expected at least one suggested library package"
    ctx["selected_spaces"] = [suggestions[0]]
    last = await integral_onboard_user(
        user_id=test_user.id, step="create_tracks", context=ctx
    )
    assert last["next_step"] == "set_retrieval_mode"
    ctx["retrieval_mode"] = "hybrid"
    last = await integral_onboard_user(
        user_id=test_user.id, step="set_retrieval_mode", context=ctx
    )
    assert last["next_step"] == "set_theme"
    ctx["theme"] = "system"
    last = await integral_onboard_user(
        user_id=test_user.id, step="set_theme", context=ctx
    )
    assert last["next_step"] == "finalize"
    last = await integral_onboard_user(
        user_id=test_user.id, step="finalize", context=ctx
    )
    assert last["completed"] is True
    assert last["next_step"] is None

    refreshed = await User.get(test_user.id)
    assert refreshed is not None
    assert refreshed.onboarded_at is not None


# ---------------------------------------------------------------------------
# 5. finalize emits EXACTLY ONE user.update ChangeEvent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_emits_single_user_update_change_event(test_user, monkeypatch):
    from app.agentive.services.integral_onboard_user import integral_onboard_user

    if test_user is None:
        pytest.skip("no test_user node available")

    from app.services.change_event_logger import get_change_event_logger

    def _is_user_update(row) -> bool:
        return getattr(row, "event_code", None) == "user.update"

    scope = f"user:{test_user.id}"
    before_rows = await get_change_event_logger().find_all(
        scope=scope, actor_kind="human"
    )
    before_count = sum(1 for r in before_rows if _is_user_update(r))

    result = await integral_onboard_user(
        user_id=test_user.id, step="finalize", context={}
    )
    assert result["completed"] is True

    after_rows = await get_change_event_logger().find_all(
        scope=scope, actor_kind="human"
    )
    after_filtered = [r for r in after_rows if _is_user_update(r)]
    after_count = len(after_filtered)
    delta = after_count - before_count
    assert delta == 1, (
        f"expected exactly 1 new user.update ChangeEvent on finalize, "
        f"got delta={delta} (before={before_count}, after={after_count})"
    )
    new_event = after_filtered[-1]
    details = getattr(new_event, "log_data", {}).get("details") or {}
    assert details.get("section") == "onboarded_at", details


# ---------------------------------------------------------------------------
# 6. B4 ROADMAP AC — finalize leaves user with space + track + theme +
#    retrieval_mode + onboarded_at
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_leaves_user_with_space_track_theme_retrieval(
    test_user, monkeypatch
):
    """ROADMAP AC: full onboarding flow leaves the user with >=1 App,
    >=1 Track, theme set, retrieval_mode set, onboarded_at populated."""
    from app.agentive.services.integral_onboard_user import (
        _create_starter_spaces_and_tracks,
        integral_onboard_user,
    )

    if test_user is None:
        pytest.skip("no test_user node available")

    await _seed_library_space_package(name="ONBD AC App")

    created = await _create_starter_spaces_and_tracks(user_id=test_user.id, selected=[])
    app_ids = created.get("app_ids") or []
    track_ids = created.get("track_ids") or []
    assert len(app_ids) >= 1, (app_ids, created)
    assert len(track_ids) >= 1, (track_ids, created)

    # Walk through set_retrieval_mode + set_theme + finalize so the
    # remaining AC fields are populated.
    ctx = {"retrieval_mode": "hybrid"}
    await integral_onboard_user(
        user_id=test_user.id, step="set_retrieval_mode", context=ctx
    )
    ctx["theme"] = "dark"
    await integral_onboard_user(user_id=test_user.id, step="set_theme", context=ctx)
    finalize_result = await integral_onboard_user(
        user_id=test_user.id, step="finalize", context=ctx
    )
    assert finalize_result["completed"] is True

    refreshed = await User.get(test_user.id)
    assert refreshed is not None
    assert refreshed.onboarded_at is not None
    prefs = refreshed.preferences or {}
    assert prefs.get("theme") is not None
    assert prefs.get("retrieval_mode") is not None
