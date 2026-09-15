"""User notification preferences — GET + PATCH (NOTIF-03, Phase 9 Plan 09-04).

Mounts two endpoints under ``/users/me/notification-preferences``:

- ``GET`` resolves the caller's ``NotificationPreferences`` from the User
  node, falling back to ``default_preferences()`` when the node carries
  ``None`` (i.e. the user has never customized prefs).
- ``PATCH`` accepts a partial ``NotificationPreferences`` body, deep-merges
  the ``kinds`` matrix, replaces ``channels`` / ``whatsapp`` wholesale,
  re-validates the merged result through the Pydantic boundary
  (``extra="forbid"`` from ``app.schemas.notification_preferences``), and
  emits EXACTLY ONE ``user.update`` ``ChangeEvent`` with
  ``details={"section": "notification_preferences"}`` so audit
  log consumers can distinguish prefs mutations from other user updates.

A3 invariant (locked decision from 09-03a): ``whatsapp.opted_in_at`` is
captured ONLY by the OTP verification flow
(``agentive/api/channels.py::whatsapp_verify_otp``). This PATCH handler
refuses any body that names ``opted_in_at`` inside the ``whatsapp`` slot
with a 400. Verified by the dedicated test case.

Single-Literal invariant (A1): no new ``ChangeEventAction`` members.
``user.update`` already covers this mutation (same Literal used by
``api/users.py::update_user`` and ``upload_avatar``).

Canonical user lookup (B1): ``await User.get(user_id)`` — the principal id
from the JWT (resolve_principal_id) is the User node id at every call site
that flows through ``request.state.user.id``. There is no fictitious
``User.find_by_user_id`` helper.
"""

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import (
    MissingAuthenticationError,
    ResourceNotFoundError,
)
from app.api.utils import resolve_principal_id
from app.exceptions import BadRequestError
from app.models.nodes import User
from app.schemas.notification_preferences import (
    NotificationPreferences,
    default_preferences,
)
from app.services.change_event import emit_change_event
from app.services.permissions import get_user_node


async def _resolve_user(caller_id: str) -> User | None:
    """Resolve the caller's User node by id, falling back to AuthUser-id lookup.

    Mirrors the dual-lookup idiom in ``api/users.py::update_user`` —
    ``User.get(caller_id)`` handles the User.id case; ``get_user_node``
    handles the AuthUser.id case. Both are explicit primitives; no
    fictitious helper is invented (B1).
    """
    user = await get_user_node(caller_id)
    if user is None:
        user = await User.get(caller_id)
    return user


@endpoint(
    "/users/me/notification-preferences",
    methods=["GET"],
    auth=True,
    tags=["Notifications"],
)
async def get_notification_preferences(request: Request) -> Dict[str, Any]:
    """Return the caller's resolved notification preferences.

    Resolution order:

    1. ``User.notification_preferences`` if non-None — validated through
       ``NotificationPreferences`` so stored dicts that drifted from the
       schema (e.g. from a future schema version) surface as a 400 at the
       boundary rather than silent dropped fields.
    2. ``default_preferences()`` if the User node carries ``None``.

    Returns the resolved dict directly (no envelope) so the frontend hook
    can hydrate the React-Query cache from ``response.data``.
    """
    caller_id = resolve_principal_id(request)
    if not caller_id:
        raise MissingAuthenticationError(message="Authentication required")
    user = await _resolve_user(caller_id)
    if user is None:
        raise ResourceNotFoundError(message="User not found")
    prefs_dict = user.notification_preferences
    if prefs_dict:
        prefs = NotificationPreferences.model_validate(prefs_dict)
    else:
        prefs = default_preferences()
    return prefs.model_dump()


@endpoint(
    "/users/me/notification-preferences",
    methods=["PATCH"],
    auth=True,
    tags=["Notifications"],
)
async def patch_notification_preferences(request: Request) -> Dict[str, Any]:
    """Partially update the caller's notification preferences.

    Merge semantics:

    - ``channels`` — replaced wholesale (a partial channel matrix in the
      body replaces the stored one; the Pydantic boundary on the merged
      result fills in missing fields via field defaults). Practically the
      frontend always sends the full matrix; the merge is just defensive.
    - ``kinds`` — deep-merge per kind. ``body.kinds.mention.email=false``
      flips one cell without touching the other 14.
    - ``whatsapp`` — replaced wholesale, with the A3 ``opted_in_at`` gate.

    A3 invariant: ``whatsapp.opted_in_at`` cannot be set via this handler.
    It is only set by ``agentive/api/channels.py::whatsapp_verify_otp``
    after the OTP round-trip succeeds (the verification IS the consent
    event). If the body contains the key, the handler 400s — the field
    on the merged result is preserved from the stored value (the
    pre-merge wholesale replacement reads from ``current["whatsapp"]``
    keys NOT in the body).

    Emits EXACTLY ONE ``user.update`` ``ChangeEvent`` per successful
    PATCH (D-05 single-emission, single Literal — A1). The
    ``details.section == "notification_preferences"`` discriminator lets
    audit log consumers filter prefs mutations distinct from profile
    updates / avatar uploads (which share the same Literal action).
    """
    caller_id = resolve_principal_id(request)
    if not caller_id:
        raise MissingAuthenticationError(message="Authentication required")

    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise BadRequestError(message=f"Invalid JSON body: {exc}") from exc
    if not isinstance(body, dict):
        raise BadRequestError(message="Body must be a JSON object")

    # A3 invariant — refuse direct set of opted_in_at. Done BEFORE user
    # lookup so an unauthenticated spoofing attempt fast-fails.
    whatsapp_in = body.get("whatsapp")
    if isinstance(whatsapp_in, dict) and "opted_in_at" in whatsapp_in:
        raise BadRequestError(
            message=(
                "whatsapp.opted_in_at cannot be set via this endpoint; "
                "use the WhatsApp OTP verification flow"
            ),
        )

    user = await _resolve_user(caller_id)
    if user is None:
        raise ResourceNotFoundError(message="User not found")

    # Resolve current dict (defaults if None).
    current_model: NotificationPreferences = (
        NotificationPreferences.model_validate(user.notification_preferences)
        if user.notification_preferences
        else default_preferences()
    )
    current = current_model.model_dump()
    before = current  # snapshot for ChangeEvent.before

    # Merge.
    merged: Dict[str, Any] = dict(current)
    if "channels" in body:
        ch_in = body.get("channels") or {}
        if not isinstance(ch_in, dict):
            raise BadRequestError(message="channels must be a JSON object")
        merged["channels"] = {**(current.get("channels") or {}), **ch_in}
    if "whatsapp" in body:
        wa_in = body.get("whatsapp") or {}
        if not isinstance(wa_in, dict):
            raise BadRequestError(message="whatsapp must be a JSON object")
        merged["whatsapp"] = {**(current.get("whatsapp") or {}), **wa_in}
    if "kinds" in body:
        kinds_in = body.get("kinds") or {}
        if not isinstance(kinds_in, dict):
            raise BadRequestError(message="kinds must be a JSON object")
        kinds_out: Dict[str, Any] = {**(current.get("kinds") or {})}
        for kind, matrix in kinds_in.items():
            if matrix is not None and not isinstance(matrix, dict):
                raise BadRequestError(
                    message=f"kinds.{kind} must be a JSON object or null",
                )
            kinds_out[kind] = {
                **(kinds_out.get(kind) or {}),
                **(matrix or {}),
            }
        merged["kinds"] = kinds_out

    # Reject unknown top-level keys outside the recognized set BEFORE
    # Pydantic validation so the error message is precise. The boundary
    # itself also rejects extras (extra="forbid").
    unknown = set(body.keys()) - {"channels", "kinds", "whatsapp"}
    if unknown:
        raise BadRequestError(
            message=f"Unknown fields in body: {sorted(unknown)}",
        )

    # Validate via the canonical schema boundary (extra="forbid" rejects
    # unknown nested fields too).
    try:
        validated = NotificationPreferences.model_validate(merged)
    except Exception as exc:  # noqa: BLE001
        raise BadRequestError(
            message=f"Invalid preferences payload: {exc}",
        ) from exc

    user.notification_preferences = validated.model_dump()
    user.updated_at = datetime.now(timezone.utc).isoformat()
    await user.save()

    # D-05 single emission — exactly one user.update per successful PATCH.
    await emit_change_event(
        actor_kind="human",
        actor_id=caller_id,
        action="user.update",
        resource_type="User",
        resource_id=user.id,
        before=before,
        after=validated.model_dump(),
        scope=f"user:{user.id}",
        details={"section": "notification_preferences"},
    )

    return validated.model_dump()
