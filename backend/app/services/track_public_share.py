"""Resolve a track's manifest-declared public-share intent.

An App manifest may declare ``public_share`` on a prescribed track spec (see
``services/content_profile_compile._normalize_public_share_spec``). The
canonical example is an intake form: ``create_entries: true`` with every read
permission false, so anonymous submitters can post a record and read nothing
back.

That declaration is INTENT, never an instruction to mint. Provisioning does not
create a share link from it — share tokens are hash-only and disclosed once at
mint, so a mint with no recipient discards the only usable copy while leaving a
link that still reads as an active public share (see
``tests/test_provisioned_public_share.py``). The owner enables sharing
explicitly; this module supplies the permissions that enable should default to,
so the deliberately locked-down set an app author chose is what the owner is
offered — rather than the UI's generic ``read_entries: true`` default, which for
an intake track would publish exactly the records it was built to protect.

Intent is resolved from the manifest on read rather than copied onto the Track
at provision time: the manifest stays the single source of truth, and an app
upgrade that revises the declaration takes effect without a migration.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.models.nodes import App, Track

logger = logging.getLogger(__name__)


async def declared_public_share(track: Track) -> Optional[Dict[str, Any]]:
    """Return ``{"enabled": True, "permissions": {...}}`` when the track's App
    manifest declares public sharing for it, else ``None``.

    Never raises: a missing App, profile, or manifest simply means "no declared
    intent". This feeds a UI default, so a resolution failure must degrade to
    the generic defaults rather than break the share dialog.
    """
    template_id = str(getattr(track, "template_id", "") or "").strip()
    if not template_id:
        return None

    try:
        from app.services.app_graph import get_app_attached_content_profile
        from app.services.content_profile_compile import compile_canonical_manifest

        # "WorkspaceApp", not "App" — App.__entity_name__ is overridden to
        # avoid colliding with jvagent's own App node class.
        parents = await track.nodes(
            edge=["CONTAINS"], direction="in", node=["WorkspaceApp"]
        )
        app_node = next((p for p in parents if isinstance(p, App)), None)
        if app_node is None:
            return None

        profile = await get_app_attached_content_profile(app_node)
        if profile is None or not profile.manifest:
            return None

        canonical = compile_canonical_manifest(manifest=dict(profile.manifest))
        for spec in (canonical.get("app") or {}).get("tracks") or []:
            if not isinstance(spec, dict):
                continue
            if str(spec.get("key") or "") != template_id:
                continue
            declared = spec.get("public_share")
            return declared if isinstance(declared, dict) else None
    except Exception:
        logger.exception(
            "declared_public_share: resolution failed for track=%s",
            getattr(track, "id", "?"),
        )
        return None
    return None
