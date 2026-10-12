"""Persistent relationship clocks; history never grants access.

Mutators must hold the canonical workspace mutation fence and validate current
actor, target, principal and grant facts before calling record_grant_change.
The history edge survives removal of the actual grant, preventing absence ABA.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.models.access_lifecycle import OwnershipGrantHistory
from app.models.edges import (
    COLLABORATES_ON,
    IS_MEMBER_OF,
    OWNS,
    CollaborationGrantHistory,
    MembershipGrantHistory,
)
from app.utils.time import utc_now_iso

_HISTORY_CLASSES = {
    OWNS: OwnershipGrantHistory,
    IS_MEMBER_OF: MembershipGrantHistory,
    COLLABORATES_ON: CollaborationGrantHistory,
}
_FIELDS = ("revision", "grant_id", "last_grant_id", "grant_fingerprint", "changed_at")


def _digest(value: Any) -> str:
    try:
        payload = json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise AuthorizationUnavailableError() from exc
    return hashlib.sha256(payload.encode()).hexdigest()


def grant_fingerprint(row: dict | None) -> str:
    """Hash the complete stored grant without hydrated permission defaults."""
    if row is None:
        return _digest(None)
    normalized = dict(row)
    context = normalized.get("context")
    if not isinstance(context, dict):
        raise AuthorizationUnavailableError()
    normalized["context"] = dict(context)
    normalized["context"].setdefault("grant_revision", 0)
    revision = normalized["context"]["grant_revision"]
    if type(revision) is not int or revision < 0:
        raise AuthorizationUnavailableError()
    if "grant_revision" in normalized:
        if (
            type(normalized["grant_revision"]) is not int
            or normalized["grant_revision"] != revision
        ):
            raise AuthorizationUnavailableError()
        # A matching technical legacy mirror is the same clock representation;
        # it must not masquerade as a new permission or an untracked mutation.
        normalized.pop("grant_revision")
    return _digest(normalized)


def _history_class(grant_class: Any) -> Any:
    try:
        return _HISTORY_CLASSES[grant_class]
    except KeyError as exc:
        raise AuthorizationUnavailableError() from exc


async def read_grant_history(
    source_id: str, target_id: str, grant_class: Any
) -> dict | None:
    """Require one complete primary clock; duplicates or corruption deny."""
    history_class = _history_class(grant_class)
    rows = await get_default_context().database.find(
        "edge",
        {"source": source_id, "target": target_id, "entity": history_class.__name__},
        limit=2,
    )
    if not rows:
        return None
    if len(rows) != 1:
        raise AuthorizationUnavailableError()
    row = rows[0]
    if (
        row.get("source") != source_id
        or row.get("target") != target_id
        or row.get("entity") != history_class.__name__
        or not isinstance(row.get("id"), str)
        or not row["id"]
        or not isinstance(row.get("context"), dict)
    ):
        raise AuthorizationUnavailableError()
    context = row["context"]
    if any(field not in context for field in _FIELDS):
        raise AuthorizationUnavailableError()
    if any(
        field in row
        and (
            type(row[field]) is not type(context[field]) or row[field] != context[field]
        )
        for field in _FIELDS
    ):
        raise AuthorizationUnavailableError()
    if type(context["revision"]) is not int or context["revision"] < 1:
        raise AuthorizationUnavailableError()
    for field in ("grant_id", "last_grant_id"):
        value = context[field]
        if value is not None and (
            not isinstance(value, str) or not value or value != value.strip()
        ):
            raise AuthorizationUnavailableError()
    if context["last_grant_id"] is None or (
        context["grant_id"] is not None
        and context["grant_id"] != context["last_grant_id"]
    ):
        raise AuthorizationUnavailableError()
    fingerprint = context["grant_fingerprint"]
    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or any(c not in "0123456789abcdef" for c in fingerprint)
    ):
        raise AuthorizationUnavailableError()
    if not isinstance(context["changed_at"], str) or not context["changed_at"]:
        raise AuthorizationUnavailableError()
    try:
        if datetime.fromisoformat(context["changed_at"]).tzinfo is None:
            raise ValueError("Missing timezone")
    except ValueError as exc:
        raise AuthorizationUnavailableError() from exc
    return row


def _require_matching_image(history: dict, grant: dict | None) -> None:
    context = history["context"]
    if context["grant_id"] != (grant["id"] if grant is not None else None) or context[
        "grant_fingerprint"
    ] != grant_fingerprint(grant):
        # A nonparticipating writer or corrupt fact cannot be silently repaired
        # through ordinary admission; migration/recovery must audit disposition.
        raise AuthorizationUnavailableError()


async def grant_baseline(
    *, source_id: str, target_id: str, grant_class: Any, grant: dict | None
) -> str:
    """Bind a pending intent to a recipient, target and complete current clock."""
    history = await read_grant_history(source_id, target_id, grant_class)
    if history is not None:
        _require_matching_image(history, grant)
    return "grant-v2:" + _digest(
        {
            "source_id": source_id,
            "target_id": target_id,
            "grant_kind": grant_class.__name__,
            "history_id": history["id"] if history else None,
            "revision": history["context"]["revision"] if history else None,
            "fingerprint": grant_fingerprint(grant),
        }
    )


def unresolved_grant_baseline(target_id: str, grant_class: Any) -> str:
    """An unknown-at-issue recipient is distinct from a known absent lifetime."""
    _history_class(grant_class)
    return "unresolved-v2:" + _digest(
        {"target_id": target_id, "grant_kind": grant_class.__name__}
    )


async def grant_baseline_matches(
    *,
    baseline: str,
    source_id: str,
    target_id: str,
    grant_class: Any,
    grant: dict | None,
) -> bool:
    """Legacy hashes cannot prove untouched history; explicit reissue is required."""
    if baseline == unresolved_grant_baseline(target_id, grant_class):
        return (
            grant is None
            and await read_grant_history(source_id, target_id, grant_class) is None
        )
    return baseline == await grant_baseline(
        source_id=source_id, target_id=target_id, grant_class=grant_class, grant=grant
    )


async def record_grant_change(
    *,
    source: Any,
    target: Any,
    grant_class: Any,
    before: dict | None,
    after: dict | None,
) -> dict | None:
    """Advance the clock with its effect in the caller's held mutation unit.

    Exact no-ops do not advance. JSON callers have local ordering only; failure
    after a nontransactional effect leaves a mismatch that readers fail closed.
    """
    for image in (before, after):
        if image is not None and (
            image.get("source") != source.id
            or image.get("target") != target.id
            or image.get("entity") != grant_class.__name__
            or not isinstance(image.get("id"), str)
            or not image["id"]
        ):
            raise AuthorizationUnavailableError()
    history = await read_grant_history(source.id, target.id, grant_class)
    if history is not None:
        _require_matching_image(history, before)
    if grant_fingerprint(before) == grant_fingerprint(after):
        return history
    history_class = _history_class(grant_class)
    values = {
        "grant_id": after["id"] if after else None,
        "last_grant_id": after["id"] if after else before["id"],
        "grant_fingerprint": grant_fingerprint(after),
        "changed_at": utc_now_iso(),
    }
    if history is None:
        await source.connect(target, edge=history_class, revision=1, **values)
    else:
        updates = {f"context.{key}": value for key, value in values.items()}
        updates.update({key: value for key, value in values.items() if key in history})
        if "revision" in history:
            updates["revision"] = history["context"]["revision"] + 1
        result = await get_default_context().database.find_one_and_update(
            "edge",
            {
                "id": history["id"],
                "source": source.id,
                "target": target.id,
                "entity": history_class.__name__,
                "context.revision": history["context"]["revision"],
                "context.grant_fingerprint": history["context"]["grant_fingerprint"],
            },
            {"$set": updates, "$inc": {"context.revision": 1}},
        )
        if result is None:
            raise AuthorizationUnavailableError()
    current = await read_grant_history(source.id, target.id, grant_class)
    if current is None:
        raise AuthorizationUnavailableError()
    _require_matching_image(current, after)
    return current
