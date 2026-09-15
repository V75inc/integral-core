"""GET + PATCH /users/me/notification-preferences (Phase 9 Plan 09-04, NOTIF-03).

Six cases (locked in plan 09-04 § Task 2 acceptance criteria):

1. GET returns defaults when ``User.notification_preferences is None``.
2. GET returns persisted prefs verbatim when set.
3. PATCH ``{kinds: {mention: {email: false}}}`` deep-merges and persists.
4. PATCH ``{whatsapp: {opted_in_at: ...}}`` is refused with 400 (A3 invariant —
   only the OTP verify path may set this field).
5. PATCH with an unknown top-level key returns 400 (``extra="forbid"`` from
   ``NotificationPreferences`` boundary).
6. PATCH emits EXACTLY ONE ``user.update`` ``ChangeEvent`` with
   ``details.section == "notification_preferences"`` (D-05 / single-Literal A1).

The tests reuse the standard ``authenticated_client`` + ``test_user``
fixtures from ``conftest.py``; the User node for the principal already
exists when these fixtures resolve.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

PATH = "/api/users/me/notification-preferences"


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_defaults_when_unset(
    authenticated_client: AsyncClient, test_user
):
    """GET resolves to ``default_preferences()`` when User.notification_preferences is None."""
    # Sanity — fixture leaves the field unset.
    assert getattr(test_user, "notification_preferences", None) in (None, {})

    resp = await authenticated_client.get(PATH)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Default channel matrix: in_app + email on, whatsapp off.
    assert body["channels"]["in_app"] is True
    assert body["channels"]["email"] is True
    assert body["channels"]["whatsapp"] is False

    # Per-kind defaults: noisy human kinds get email; system gets in_app only.
    assert body["kinds"]["mention"]["email"] is True
    assert body["kinds"]["system"]["email"] is False
    assert body["kinds"]["whatsapp_welcome"]["whatsapp"] is True
    # WhatsApp slot: opt-in is unset by default.
    assert body["whatsapp"]["opted_in_at"] is None


@pytest.mark.asyncio
async def test_get_returns_persisted_prefs(
    authenticated_client: AsyncClient, test_user
):
    """GET round-trips a custom matrix that was written directly to the node."""
    from app.schemas.notification_preferences import default_preferences

    custom = default_preferences().model_dump()
    # Override mention.email = false, channels.email = false.
    custom["channels"]["email"] = False
    custom["kinds"]["mention"]["email"] = False
    test_user.notification_preferences = custom
    await test_user.save()

    resp = await authenticated_client.get(PATH)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["channels"]["email"] is False
    assert body["kinds"]["mention"]["email"] is False
    # Untouched fields preserved.
    assert body["kinds"]["share"]["email"] is True


# ---------------------------------------------------------------------------
# PATCH — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_deep_merges_kinds(authenticated_client: AsyncClient, test_user):
    """PATCH with a single nested cell flips that cell and leaves others intact."""
    resp = await authenticated_client.patch(
        PATH,
        json={"kinds": {"mention": {"email": False}}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # The flipped cell.
    assert body["kinds"]["mention"]["email"] is False
    # Adjacent cells in the same kind preserved.
    assert body["kinds"]["mention"]["in_app"] is True
    # Other kinds preserved.
    assert body["kinds"]["share"]["email"] is True
    # Channel defaults preserved.
    assert body["channels"]["email"] is True

    # Verify persistence — a fresh GET returns the same shape.
    follow = await authenticated_client.get(PATH)
    assert follow.status_code == 200, follow.text
    assert follow.json()["kinds"]["mention"]["email"] is False


# ---------------------------------------------------------------------------
# PATCH — A3 invariant (opted_in_at refused)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_refuses_opted_in_at(authenticated_client: AsyncClient, test_user):
    """A3: setting whatsapp.opted_in_at via this PATCH must 400.

    Only the OTP verification flow may set this field (the verification
    IS the consent event).
    """
    resp = await authenticated_client.patch(
        PATH,
        json={"whatsapp": {"opted_in_at": "2026-01-01T00:00:00Z"}},
    )
    assert resp.status_code == 400, resp.text
    err = resp.json()
    # Error envelope shape (5-key per JVSpatialAPIException) — error_code OR
    # detail/message may surface depending on FastAPI / jvspatial coercion.
    err_text = (err.get("message") or err.get("detail") or "").lower() + str(err)
    assert "opted_in_at" in err_text.lower() or "opted_in_at" in str(err).lower()

    # The User node was not mutated.
    follow = await authenticated_client.get(PATH)
    assert follow.status_code == 200, follow.text
    assert follow.json()["whatsapp"]["opted_in_at"] is None


# ---------------------------------------------------------------------------
# PATCH — extra: forbid (unknown top-level field)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_rejects_unknown_field(
    authenticated_client: AsyncClient, test_user
):
    """Unknown top-level field → 400 (extra: forbid at the boundary)."""
    resp = await authenticated_client.patch(
        PATH,
        json={"foo": "bar"},
    )
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# PATCH — single user.update ChangeEvent (D-05 / A1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_emits_single_user_update_change_event(
    authenticated_client: AsyncClient, test_user
):
    """EXACTLY ONE user.update ChangeEvent with details.section emitted per PATCH."""
    from app.services.change_event_logger import get_change_event_logger

    # DBLog rows store the action on ``event_code`` (per
    # ``ChangeEventLogger.persist``). ``log_data["action"]`` is NOT
    # populated by the persist path — only ``log_data.message`` mirrors it.
    # We filter via ``event_code`` to match the actual on-disk shape.
    def _is_user_update(row) -> bool:
        return getattr(row, "event_code", None) == "user.update"

    before_logger = get_change_event_logger()
    scope = f"user:{test_user.id}"
    before_rows = await before_logger.find_all(scope=scope, actor_kind="human")
    before_user_updates = [r for r in before_rows if _is_user_update(r)]

    resp = await authenticated_client.patch(
        PATH,
        json={"kinds": {"share": {"email": False}}},
    )
    assert resp.status_code == 200, resp.text

    after_logger = get_change_event_logger()
    after_rows = await after_logger.find_all(scope=scope, actor_kind="human")
    after_user_updates = [r for r in after_rows if _is_user_update(r)]
    # Exactly one NEW user.update event since the PATCH.
    delta = len(after_user_updates) - len(before_user_updates)
    assert delta == 1, (
        f"expected exactly 1 new user.update ChangeEvent, got delta={delta} "
        f"(before={len(before_user_updates)}, after={len(after_user_updates)})"
    )

    # The new event carries the section discriminator in details.
    new_event = after_user_updates[-1]
    details = getattr(new_event, "log_data", {}).get("details") or {}
    assert details.get("section") == "notification_preferences", details
