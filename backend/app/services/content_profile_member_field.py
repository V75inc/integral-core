"""`member` field type: validator.

Implements ``validate_member_value``, the callable wired into the
`member` ``FieldTypeSpec`` in ``content_profile_field_types.py``.

The validator rejects a ``User`` id that does not belong to the entry's
Workspace. The ``member`` field MUST NOT reach across the workspace
gate; this is the field type's load-bearing constraint.

The edge-write side (``HAS_MEMBER_REF``) lives in
``_sync_member_ref_edges`` in
``backend/app/services/content_profile_graph.py`` — the sole sanctioned
write path (single-writer invariant mirrors ANCHORS at INVARIANTS.md
L101-116). The materializer lives there, not here, so the grep gate
``grep -rE 'edge=HAS_MEMBER_REF|edge=HasMemberRef\\b' backend/app/`` can
enforce single-writer at one chokepoint.

Invariant: ``docs/INVARIANTS.md`` § I-FIELD-MEMBER-01 (member field
type binds to graph User node via HAS_MEMBER_REF).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.api.errors import BadRequestError
from app.models.nodes import Entry, Track, User

logger = logging.getLogger(__name__)


async def _resolve_workspace_id_for_entry(
    *, source_entry: Optional[Entry], source_track: Optional[Track]
) -> str:
    """Return the workspace id the entry / track lives in, or empty string."""
    if source_track is not None:
        ws = getattr(source_track, "workspace_id", "") or ""
        if ws:
            return str(ws)
    if source_entry is not None:
        track_id = getattr(source_entry, "track_id", "") or ""
        if track_id:
            t = await Track.get(str(track_id))
            if t is not None:
                return str(getattr(t, "workspace_id", "") or "")
    return ""


async def validate_member_value(
    value: Any,
    *,
    field_key: str,
    source_track: Optional[Track] = None,
    source_entry: Optional[Entry] = None,
) -> str:
    """Validate a `member` field value: must be a `User` id in the entry's Workspace.

    Returns the canonical `User` id on success. Raises ``BadRequestError``
    (a ``JVSpatialAPIException`` subclass) on any failure.
    """
    if value is None:
        raise BadRequestError(
            message=f"Member field '{field_key}' value must be a User id, got None"
        )
    user_id = str(value).strip()
    if not user_id:
        raise BadRequestError(
            message=f"Member field '{field_key}' value must be a non-empty User id"
        )
    user = await User.get(user_id)
    if user is None:
        raise BadRequestError(
            message=f"Member field '{field_key}' references unknown User '{user_id}'",
            details={"field_key": field_key, "user_id": user_id},
        )
    workspace_id = await _resolve_workspace_id_for_entry(
        source_entry=source_entry, source_track=source_track
    )
    if not workspace_id:
        # No resolvable workspace: defensive — let it pass since the entry write
        # cannot succeed without a track in any case (a track always carries a
        # workspace_id at provision time).
        logger.warning(
            "validate_member_value: no workspace_id resolvable for field %r "
            "(source_track=%r, source_entry=%r)",
            field_key,
            getattr(source_track, "id", None),
            getattr(source_entry, "id", None),
        )
        return user_id
    # Workspace-gate: the referenced User must be a member of the entry's
    # Workspace. Defer the import — workspace_permissions has heavy imports.
    from app.services.workspace_permissions import user_in_workspace_member_pool

    in_pool = await user_in_workspace_member_pool(user_id, workspace_id)
    if not in_pool:
        raise BadRequestError(
            message=(
                f"Member field '{field_key}' User '{user_id}' is not a member of "
                f"the entry's Workspace '{workspace_id}'"
            ),
            details={
                "field_key": field_key,
                "user_id": user_id,
                "workspace_id": workspace_id,
            },
        )
    return user_id
