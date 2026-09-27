"""One filing facet against an existing entry (W2.2).

Update and append reuse entry policy, revision checks, and protected-field
rejection. Tags and relation links in the same call commit together, or the
facet is restored and the receipt says nothing was filed. A retry that
repeats the authorized payload and revisions does not append twice.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from app.api.errors import BadRequestError
from app.models.edges import REFERENCES
from app.models.nodes import Entry, Track
from app.schemas.policy import Resource, Subject
from app.services.app_invariant_guards import enforce_protected_field_write
from app.services.policy_engine import evaluate as policy_evaluate
from app.utils.time import utc_now_iso

_PROBE: Optional[Callable[[str], None]] = None
_APPLIED: Dict[str, Dict[str, Any]] = {}
_FINGERPRINT_KEYS = (
    "mode",
    "entry_id",
    "text",
    "title",
    "fields",
    "tags",
    "relations",
    "append_field",
    "expected_record_revision",
    "expected_schema_revision",
    "entry_type_key",
    "replace_body",
)


def set_facet_probe(probe: Optional[Callable[[str], None]]) -> None:
    """Test hook. ``probe(step)`` runs before relation links and before tags."""
    global _PROBE
    _PROBE = probe


def reset_filing_idempotency() -> None:
    """Drop remembered filing retries. Tests start from an empty map."""
    _APPLIED.clear()


def _fingerprint(payload: Dict[str, Any]) -> str:
    body = {key: payload.get(key) for key in _FINGERPRINT_KEYS}
    return json.dumps(body, sort_keys=True, default=str)


def _probe(step: str) -> None:
    if _PROBE is not None:
        _PROBE(step)


def _refused(code: str, message: str) -> Dict[str, Any]:
    return {
        "error": True,
        "filed": False,
        "partial": False,
        "error_code": code,
        "message": message,
    }


async def _restore(entry: Entry, snapshot: Dict[str, Any], created: List[Any]) -> None:
    for edge in created:
        await edge.delete()
    fresh = await Entry.get(entry.id)
    if fresh is None:
        return
    fresh.title = snapshot["title"]
    fresh.body = snapshot["body"]
    fresh.custom_fields = snapshot["custom_fields"]
    fresh.tags = snapshot["tags"]
    fresh.record_revision = snapshot["record_revision"]
    fresh.updated_at = snapshot["updated_at"]
    await fresh.save()


async def apply_filing_facet(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Apply one update or append. Writes nothing when the preflight fails."""
    mode = str(payload.get("mode") or "").strip().lower()
    if mode not in ("update", "append"):
        return _refused("mode_unsupported", "mode must be update or append")
    entry_id = str(payload.get("entry_id") or "").strip()
    if not entry_id:
        return _refused("entry_required", "entry_id is required to update or append")
    if (
        payload.get("expected_record_revision") is None
        or payload.get("expected_schema_revision") is None
    ):
        return _refused(
            "revision_required",
            "expected_record_revision and expected_schema_revision are required",
        )

    fingerprint = _fingerprint(payload)
    idem = str(payload.get("idempotency_key") or "").strip()
    remembered = _APPLIED.get(f"{user_id}:{idem}") if idem else None
    if remembered is not None:
        if remembered["fingerprint"] == fingerprint:
            return {**remembered["result"], "retry": True}
        return _refused(
            "idempotency_conflict",
            "This idempotency key was already used for a different payload",
        )

    entry = await Entry.get(entry_id)
    if entry is None:
        return _refused("not_found", "Entry not found")

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.update",
        resource=Resource(kind="entry", id=entry_id, scope=f"entry:{entry_id}"),
    )
    if not decision.allowed:
        return _refused("denied", "Access denied")

    current_revision = int(getattr(entry, "record_revision", 1) or 1)
    if int(payload["expected_record_revision"]) != current_revision:
        return _refused(
            "record_revision_conflict",
            "Entry has changed since it was read",
        )
    current_schema = int(getattr(entry, "schema_revision", 1) or 1)
    if int(payload["expected_schema_revision"]) != current_schema:
        return _refused(
            "schema_revision_conflict",
            "Entry schema has changed since it was read",
        )

    text = str(payload.get("text") or "")
    append_field = str(payload.get("append_field") or "").strip()
    if mode == "append" and not append_field:
        return _refused("append_field_required", "append_field is required")
    if mode == "append" and not text.strip():
        return _refused("text_required", "text is required to append")

    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    relations = (
        payload.get("relations") if isinstance(payload.get("relations"), list) else []
    )
    proposed: Dict[str, Any] = dict(fields)
    if mode == "append" and append_field not in ("", "body"):
        proposed[append_field] = text
    entry_type_key = str(payload.get("entry_type_key") or "").strip()
    track = await Track.get(entry.track_id) if entry.track_id else None
    workspace_id = str(getattr(track, "workspace_id", "") or "")
    if entry_type_key and proposed:
        try:
            await enforce_protected_field_write(
                workspace_id=workspace_id,
                entry_type_key=entry_type_key,
                proposed_custom_fields=proposed,
            )
        except BadRequestError:
            return _refused(
                "protected_field_write",
                "Protected App fields cannot be written through generic Entry updates; "
                "use the App's typed operation",
            )

    targets = []
    for raw in relations:
        if not isinstance(raw, dict):
            return _refused(
                "relation_invalid", "Each relation needs field_key and target_entry_id"
            )
        field_key = str(raw.get("field_key") or "").strip()
        target_id = str(raw.get("target_entry_id") or "").strip()
        if not field_key or not target_id:
            return _refused(
                "relation_invalid", "Each relation needs field_key and target_entry_id"
            )
        target = await Entry.get(target_id)
        if target is None:
            return _refused("relation_target_missing", "Relation target was not found")
        targets.append((field_key, target))

    if mode == "append":
        current_value = (
            entry.body or ""
            if append_field == "body"
            else str((entry.custom_fields or {}).get(append_field) or "")
        )
        if current_value.endswith(text):
            result = {
                "filed": True,
                "partial": False,
                "appended": False,
                "already_applied": True,
                "entry_id": entry.id,
                "mode": mode,
                "record_revision": current_revision,
            }
            if idem:
                _APPLIED[f"{user_id}:{idem}"] = {
                    "fingerprint": fingerprint,
                    "result": result,
                }
            return result

    snapshot = {
        "title": entry.title,
        "body": entry.body,
        "custom_fields": dict(entry.custom_fields or {}),
        "tags": list(entry.tags or []),
        "record_revision": current_revision,
        "updated_at": entry.updated_at,
    }
    created: List[Any] = []
    try:
        if mode == "append":
            if append_field == "body":
                prior = entry.body or ""
                entry.body = (prior + ("\n" if prior else "") + text).strip()
            else:
                merged = dict(entry.custom_fields or {})
                prior = str(merged.get(append_field) or "")
                merged[append_field] = (prior + ("\n" if prior else "") + text).strip()
                entry.custom_fields = merged
        else:
            title = payload.get("title")
            if isinstance(title, str) and title.strip():
                entry.title = title.strip()
            if payload.get("replace_body") and text.strip():
                entry.body = text
            if fields:
                entry.custom_fields = {**(entry.custom_fields or {}), **fields}

        _probe("relations")
        if targets:
            merged = dict(entry.custom_fields or {})
            for field_key, target in targets:
                edge = await entry.connect(
                    target,
                    edge=REFERENCES,
                    field_key=field_key,
                    relation_type="operational_model",
                    cross_track=target.track_id != entry.track_id,
                )
                if edge is not None:
                    created.append(edge)
                merged[field_key] = target.id
            entry.custom_fields = merged

        _probe("tags")
        if payload.get("tags") is not None:
            entry.tags = list(payload.get("tags") or [])

        entry.record_revision = current_revision + 1
        entry.updated_at = utc_now_iso()
        await entry.save()
    except Exception as exc:  # noqa: BLE001 — a mid-facet failure rolls the facet back
        await _restore(entry, snapshot, created)
        refused = _refused("facet_rolled_back", str(exc) or "facet rolled back")
        refused["rolled_back"] = True
        return refused

    result = {
        "filed": True,
        "partial": False,
        "entry_id": entry.id,
        "mode": mode,
        "record_revision": entry.record_revision,
        "appended": mode == "append",
    }
    if idem:
        _APPLIED[f"{user_id}:{idem}"] = {"fingerprint": fingerprint, "result": result}
    return result
