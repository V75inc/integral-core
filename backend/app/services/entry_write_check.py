"""Would this record save? The same checks a real create or update runs, without writing.

Forms ask this while a person fills them in, so the reason a save would be refused is shown before
they press Save, and Save/Create stay disabled until it passes. The rules live once, on the server:
field validation (required fields, types, relations) and every App's ``before_write`` hooks, which
receive ``dry_run: true`` and run with a read-only context. The real save runs the same rules again
and stays authoritative; this answer is advisory and grants nothing.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.api.errors import BadRequestError, InsufficientPermissionsError, ResourceNotFoundError
from app.models.nodes import Entry, EntryType, Track
from app.schemas.policy import Resource, Subject


def _refused(message: str, field: str = "") -> Dict[str, Any]:
    return {"ok": False, "message": message, **({"field": field} if field else {})}


async def check_entry_write(
    *,
    user_id: str,
    track_id: str = "",
    type_id: str = "",
    entry_id: str = "",
    title: str = "",
    custom_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """``{"ok": True}`` or ``{"ok": False, "message", "field"?}`` for a proposed create or update."""
    from app.services.hooks.entry_save_runtime import (
        entry_type_hook_slug,
        run_entry_before_write_hooks,
    )
    from app.services.operational_model_entry_fields import (
        validate_and_materialize_entry_custom_fields,
    )
    from app.services.operational_model_runtime import resolve_track_runtime_profile
    from app.services.policy_engine import evaluate as policy_evaluate

    entry: Optional[Entry] = None
    if entry_id:
        entry = await Entry.get(entry_id)
        if entry is None:
            raise ResourceNotFoundError(message="Entry not found")
        track_id = str(entry.track_id or track_id)
        type_id = str(getattr(entry, "type_id", "") or type_id)
        decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="entry.update",
            resource=Resource(kind="entry", id=entry_id, scope=f"track:{track_id}"),
        )
    else:
        decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="entry.create",
            resource=Resource(kind="entry", id="", scope=f"track:{track_id}"),
        )
    if not decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    track = await Track.get(track_id) if track_id else None
    if track is None:
        raise ResourceNotFoundError(message="Track not found")
    entry_type = await EntryType.get(type_id) if type_id else None
    if entry_type is None or (entry_type.track_id and entry_type.track_id != track.id):
        raise ResourceNotFoundError(message="Entry type not found on track")

    proposed = dict(custom_fields or {})
    fields = {**(getattr(entry, "custom_fields", None) or {}), **proposed} if entry else proposed
    _model, runtime_tier, _ = await resolve_track_runtime_profile(track)
    # Required fields are the form's own job (it marks them, and the real save enforces them); a form that is
    # still being filled in, or an App editor that sets values only when Save is pressed, must not be blocked
    # for them here. This check is for the rules a form can't see: the App's save rules and other checks.
    for spec in (entry_type.form_schema or {}).get("fields") or []:
        key = str(spec.get("key") or "")
        if key and spec.get("required") and fields.get(key) in (None, "", []):
            fields.setdefault(key, None)
    try:
        validated, _relations = await validate_and_materialize_entry_custom_fields(
            track=track,
            entry_type=entry_type,
            custom_fields=fields,
            runtime_tier=runtime_tier,
            entry=entry,
            actor_user_id=user_id,
            source_entry_title=title or str(getattr(entry, "title", "") or ""),
            materialize=False,
        )
        await run_entry_before_write_hooks(
            workspace_id=str(track.workspace_id or ""),
            track_id=str(track.id),
            actor_id=user_id,
            hook_point="entry.update" if entry else "entry.create",
            entry_type_slug=entry_type_hook_slug(entry_type),
            entry_type_id=str(entry_type.id),
            operation="update" if entry else "create",
            custom_fields=validated,
            entry_id=str(entry.id) if entry else "",
            write_actor=user_id,
            dry_run=True,
        )
    except BadRequestError as exc:
        if "is required" in str(exc.message):
            return {"ok": True}
        details = getattr(exc, "details", None) or {}
        return _refused(str(exc.message), str(details.get("field") or "") if isinstance(details, dict) else "")
    except ValueError as exc:
        return _refused(str(exc))
    except PermissionError as exc:
        return _refused(str(exc))
    return {"ok": True}
