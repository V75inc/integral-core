"""Declared, read-only cross-App reads.

An App's screens (queries and read operations) run bound to their own App and
cannot read another App's records. An App can opt in per dependency with
``requires_apps: [{key: <app>, read: true}]``. ``OperationContext.for_app(key)``
then returns a read-only context bound to that required App, so every existing
facade read (entries, tracks, record pages, search, settings) runs unchanged
under the target App's own scope and the viewer's own roles.

This module only answers "which installed App does this declaration name":

- the declaring App's *active definition* must list the key with ``read: true``;
- the target must be an active install in the same workspace (the shared
  ``resolve_target_app`` resolver, I-APP-05);
- the answer is remembered for a short window; it grants nothing by itself
  because every record read still checks containment and the viewer's role.
"""

from __future__ import annotations

import os
import time
from typing import Optional

from app.exceptions import (
    AmbiguousCrossAppTargetError,
    CrossAppTargetNotFoundError,
    CrossWorkspaceTargetRejectedError,
)

_MEMO: dict[tuple, tuple[float, Optional[str]]] = {}
_MEMO_MAX = 256


def _ttl_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("INTEGRAL_DECLARED_READ_TTL_SECONDS", "30")))
    except ValueError:
        return 30.0


def invalidate_declared_reads() -> None:
    """Forget remembered answers (tests, App install/uninstall)."""
    _MEMO.clear()


def _declared_key(requires_apps: object, wanted: str) -> Optional[str]:
    """The declared ``key`` for ``wanted`` when that entry says ``read: true``."""
    from app.services.operational_model_runtime import slug_manifest_key

    want = wanted.strip()
    if not want or not isinstance(requires_apps, list):
        return None
    forms = {want, want.casefold(), slug_manifest_key(want)}
    for entry in requires_apps:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "").strip()
        if not key:
            continue
        if {key, key.casefold(), slug_manifest_key(key)} & forms:
            return key if entry.get("read") is True else None
    return None


async def resolve_declared_read_app(
    workspace_id: str, source_app_id: str, app_key: str
) -> Optional[str]:
    """App id of the required App ``app_key`` the source App may read, else ``None``."""
    workspace_id = str(workspace_id or "").strip()
    source_app_id = str(source_app_id or "").strip()
    app_key = str(app_key or "").strip()
    if not (workspace_id and source_app_id and app_key):
        return None
    from app.models.nodes import App
    from app.services.access_delegation import current_definition
    from app.services.relation_runtime import resolve_target_app

    app, definition = await current_definition(source_app_id, execution=False)
    context = definition.get("context") or {}
    revision = context.get("revision")
    memo_key = (workspace_id, source_app_id, revision, app_key.casefold())
    now = time.monotonic()
    remembered = _MEMO.get(memo_key)
    if remembered is not None and remembered[0] > now:
        return remembered[1]

    manifest = context.get("canonical_manifest") or {}
    requires = (manifest.get("app") or {}).get("requires_apps")
    declared = _declared_key(requires, app_key)
    target_id: Optional[str] = None
    if declared:
        source = await App.get(source_app_id)
        try:
            target_id = await resolve_target_app(
                workspace_id=workspace_id,
                target_app_key=declared,
                source_app=source,
            )
        except (
            CrossAppTargetNotFoundError,
            AmbiguousCrossAppTargetError,
            CrossWorkspaceTargetRejectedError,
        ):
            target_id = None
        if target_id == source_app_id:
            target_id = None
    ttl = _ttl_seconds()
    if ttl > 0:
        if len(_MEMO) >= _MEMO_MAX:
            _MEMO.pop(next(iter(_MEMO)), None)
        _MEMO[memo_key] = (now + ttl, target_id)
    return target_id
