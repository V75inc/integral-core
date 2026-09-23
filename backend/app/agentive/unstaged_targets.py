"""Which agent writes may skip staging — ADR-006 / I-PC-01.

The approval surface exists so a person can refuse a change to their
substrate. An App that takes notes about the person fills that surface with
items where refusal means only "do not remember that", and the surface stops
meaning anything. So a narrow class of write bypasses it.

Narrow is the whole design. A write is exempt only when EVERY one of these
holds:

1. the write targets a Track (a payload with no ``track_id`` is never exempt);
2. that Track belongs to an App whose attached manifest lists the Track's
   manifest key in ``app.unstaged_tracks``;
3. that App is ``active``;
4. the App lives in a ``kind="personal"`` Workspace;
5. the acting principal OWNS that workspace.

Conditions 4 and 5 are what make this safe rather than merely declared. A
bundle can claim the exemption in its own manifest — the compiler gates that
on ``trust_tier`` and caps it at four tracks — but the claim only ever cashes
out inside the claiming user's own personal workspace, acting as themselves.
It cannot reach another workspace, another user, or a shared surface.

The substrate names no bundle here (I-SUBSTRATE-01): the exempt set is read
from whatever manifest the App carries.

Fails closed. Any error, any missing link, any ambiguity resolves to "stage
it" — the safe direction is always the one that asks.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: Staging kinds that can even be considered. A kind not listed here always
#: stages, regardless of target — the exemption is for writing rows into a
#: track, never for reshaping the substrate around it.
UNSTAGEABLE_KINDS = frozenset({"create_entry", "update_entry"})


def _track_id_from_payload(payload: Dict[str, Any]) -> str:
    return str((payload or {}).get("track_id") or "").strip()


async def _app_for_track(track: Any) -> Optional[Any]:
    """Return the App that CONTAINS ``track``, or None."""
    from app.models.edges import CONTAINS

    try:
        parents = await track.nodes(
            edge=[CONTAINS], node=["WorkspaceApp"], direction="in"
        )
    except Exception:  # noqa: BLE001
        logger.exception("unstaged gate: App lookup failed for track %s", track.id)
        return None
    return parents[0] if parents else None


async def _unstaged_track_keys(app_node: Any) -> frozenset:
    """Read ``app.unstaged_tracks`` from the active App contract.

    This controls whether an agent write bypasses review staging, so a mutable
    attached operational model is only a legacy fallback. Pending authoring edits do not
    grant a new bypass until they become the active definition.
    """
    from app.services.app_graph import get_app_attached_operational_model
    from app.services.application_definitions import get_active_application_definition

    try:
        definition = await get_active_application_definition(app_node)
        if definition is not None and getattr(definition, "canonical_manifest", None):
            manifest = definition.canonical_manifest
        else:
            cp = await get_app_attached_operational_model(app_node)
            manifest = getattr(cp, "manifest", None) if cp is not None else None
    except Exception:  # noqa: BLE001
        logger.exception("unstaged gate: manifest lookup failed for %s", app_node.id)
        return frozenset()
    manifest = manifest or {}
    app_block = manifest.get("app") if isinstance(manifest, dict) else None
    if not isinstance(app_block, dict):
        return frozenset()
    declared = app_block.get("unstaged_tracks") or []
    if not isinstance(declared, list):
        return frozenset()
    return frozenset(str(k).strip() for k in declared if str(k).strip())


async def is_unstaged_target(
    *, user_id: str, kind: str, payload: Dict[str, Any]
) -> bool:
    """True when this write may land without a card. See module docstring."""
    if kind not in UNSTAGEABLE_KINDS:
        return False
    if not user_id:
        return False
    track_id = _track_id_from_payload(payload)
    if not track_id:
        return False

    try:
        from app.models.nodes import Track, Workspace

        track = await Track.get(track_id)
        if track is None:
            return False

        # The manifest key the track was materialized from. Tracks created by
        # hand carry no template_id and are never exempt.
        track_key = str(getattr(track, "template_id", "") or "").strip()
        if not track_key:
            return False

        app_node = await _app_for_track(track)
        if app_node is None:
            return False
        if str(getattr(app_node, "lifecycle_state", "") or "") != "active":
            return False
        if track_key not in await _unstaged_track_keys(app_node):
            return False

        workspace_id = str(getattr(app_node, "workspace_id", "") or "").strip()
        if not workspace_id:
            return False
        workspace = await Workspace.get(workspace_id)
        if workspace is None or getattr(workspace, "kind", "") != "personal":
            return False

        from app.services.permissions import get_user_node
        from app.services.workspace_permissions import get_workspace_owner_user_id

        owner_id = await get_workspace_owner_user_id(workspace_id)
        if not owner_id:
            return False
        # The acting principal may arrive as an AuthUser id; compare on the
        # resolved User node, which is what the owner edge points at.
        actor = await get_user_node(user_id)
        if actor is None or actor.id != owner_id:
            return False
        return True
    except Exception:  # noqa: BLE001
        logger.exception(
            "unstaged gate raised for kind=%s track=%s; staging instead",
            kind,
            track_id,
        )
        return False
