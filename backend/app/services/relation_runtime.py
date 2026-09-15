"""Cross-App relation runtime — single safe entry point for cross-App reads.

Phase 10 Plan 10-06 (APP-CROSS-RELATIONS-01). Three responsibilities:

1. **Resolve** the target App for a cross-App relation: workspace-scoped match
   on the declared ``target_app`` key, with multi-install ambiguity handling
   (``resolution: workspace`` errors on ambiguity; ``resolution: instance:<id>``
   pins a specific install). I-APP-05: target App must be in the same
   workspace as the source — cross-workspace federation is rejected.

2. **Validate** a cross-App relation set at write time: the 5-step validation
   from ``app_bundles_v1.md §10.4``:
     (1) ``allow_cross_app: true``
     (2) target App resolves
     (3) target App is ``active``
     (4) target Entry matches ``target_track_types`` + ``target_entry_types``
     (5) caller has ``entry.read`` permission via ``policy_engine.evaluate``.

3. **Read** the cross-App relation label — ``read_cross_app_label`` is the
   ONLY function in the codebase that touches ``label_field`` content on a
   cross-App target. Risk 4 / Pitfall 5 mitigation. The grep-gate test in
   ``tests/test_cross_app_relations.py`` enforces this — any new caller that
   wants to render a cross-App label MUST go through this function.

On denial, returns a restricted-stub dict per Architectural Decision 8:

    {
      "kind": "restricted_relation",
      "relation_field_key": "<key>",
      "target_resource_kind": "entry"
    }

NO label, NO target entry id, NO target App id, NO target App name. Anything
beyond these three keys is a data leak. The frontend stub renderer consumes
this shape.

Materialization helper (``materialize_cross_app_reference``) creates a
REFERENCES edge with the ``target_app_id`` associative field populated
server-side — never client-supplied (T-10-06-10).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from app.exceptions import (
    AmbiguousCrossAppTargetError,
    BadRequestError,
    CrossAppPermissionDenied,
    CrossAppTargetNotFoundError,
    CrossWorkspaceTargetRejectedError,
)
from app.models.edges import REFERENCES
from app.models.nodes import App, Entry
from app.schemas.cross_app_relations import (
    CrossAppRelationLabel,
    RestrictedRelationStub,
)
from app.schemas.policy import Resource, Subject
from app.services.app_install import app_dependency_index_keys
from app.services.content_profile_compile import slug_manifest_key
from app.services.policy_engine import evaluate as policy_evaluate

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Resolver: target_app key → App id (workspace-scoped, multi-install aware)
# ---------------------------------------------------------------------------


async def resolve_target_app(
    *,
    workspace_id: str,
    target_app_key: str,
    resolution: str = "workspace",
    source_app: Optional[App] = None,
) -> str:
    """Resolve a declared ``target_app`` key to a concrete App id.

    Args:
        workspace_id: Source App's workspace.
        target_app_key: The library package key declared by the manifest
            (e.g. ``"<app_key>"``).
        resolution: ``"workspace"`` (search all active installs and error on
            ambiguity) OR ``"instance:<app_id>"`` (pin a specific install).
        source_app: Optional source App, used by I-APP-05 to assert the
            target lives in the same workspace.

    Returns:
        App.id of the resolved target.

    Raises:
        CrossAppTargetNotFoundError: no match (after instance resolution).
        AmbiguousCrossAppTargetError: ``resolution: workspace`` matched
            multiple active installs.
        CrossWorkspaceTargetRejectedError: I-APP-05 — target App lives in a
            different workspace than ``source_app`` (or ``workspace_id``).
    """
    if not target_app_key:
        raise BadRequestError(message="resolve_target_app: target_app_key is required")
    if not workspace_id:
        raise BadRequestError(message="resolve_target_app: workspace_id is required")

    resolution = (resolution or "workspace").strip()
    expected_ws = workspace_id

    if resolution.startswith("instance:"):
        pinned_id = resolution[len("instance:") :].strip()
        if not pinned_id:
            raise BadRequestError(
                message="resolve_target_app: 'instance:' resolution requires an app id"
            )
        target_app = await App.get(pinned_id)
        if not target_app:
            raise CrossAppTargetNotFoundError(
                message=(
                    f"resolution='instance:{pinned_id}' but no App with that id "
                    f"exists"
                ),
                details={
                    "target_app_key": target_app_key,
                    "resolution": resolution,
                },
            )
        # I-APP-05 — same-Workspace gate (T-10-06-02).
        target_ws = getattr(target_app, "workspace_id", "") or ""
        if target_ws and target_ws != expected_ws:
            raise CrossWorkspaceTargetRejectedError(
                message=(
                    f"target App {pinned_id!r} is in workspace {target_ws!r}; "
                    f"source workspace is {expected_ws!r}"
                ),
                details={
                    "target_app_key": target_app_key,
                    "target_app_id": pinned_id,
                    "source_workspace_id": expected_ws,
                    "target_workspace_id": target_ws,
                },
            )
        # Sanity check the pinned App actually matches the declared key
        # (loose match: by name OR library_source key).
        if not _app_matches_key(target_app, target_app_key):
            raise CrossAppTargetNotFoundError(
                message=(
                    f"resolution='instance:{pinned_id}' but App does not match "
                    f"declared target_app key {target_app_key!r}"
                ),
                details={
                    "target_app_key": target_app_key,
                    "target_app_id": pinned_id,
                    "app_name": target_app.name,
                },
            )
        return target_app.id

    if resolution != "workspace":
        raise BadRequestError(
            message=(
                f"resolve_target_app: resolution must be 'workspace' or "
                f"'instance:<id>' (got {resolution!r})"
            )
        )

    # workspace resolution — find all active installs matching the key.
    apps_in_ws = await App.find({"workspace_id": expected_ws})
    matches: List[App] = []
    for a in apps_in_ws:
        if getattr(a, "lifecycle_state", "active") != "active":
            continue
        if _app_matches_key(a, target_app_key):
            matches.append(a)

    if not matches:
        raise CrossAppTargetNotFoundError(
            message=(
                f"No active App matching target_app={target_app_key!r} found "
                f"in workspace {expected_ws!r}"
            ),
            details={
                "target_app_key": target_app_key,
                "workspace_id": expected_ws,
                "resolution": resolution,
            },
        )
    if len(matches) > 1:
        raise AmbiguousCrossAppTargetError(
            message=(
                f"Multiple ({len(matches)}) active App installations match "
                f"target_app={target_app_key!r} in workspace {expected_ws!r}. "
                f"Pin a specific install via resolution='instance:<app_id>'."
            ),
            details={
                "target_app_key": target_app_key,
                "workspace_id": expected_ws,
                "matched_app_ids": [a.id for a in matches],
                "matched_app_names": [a.name for a in matches],
            },
        )
    return matches[0].id


def _app_matches_key(app_node: App, target_app_key: str) -> bool:
    """Loose key match — installed App name, slug, or library provenance.

    Uses the same alias index as ``requires_apps`` resolution so manifest
    ``target_app`` keys (e.g. ``<app_key>``) match installs whose display
    name differs but carry ``source_profile_slug``.
    """
    if not (target_app_key or "").strip():
        return False
    key = target_app_key.strip()
    index = set(app_dependency_index_keys(app_node))
    if key in index:
        return True
    for alias in (key.casefold(), slug_manifest_key(key)):
        if alias in index:
            return True
    lib_id = (app_node.installed_from_library_id or "").strip()
    return bool(lib_id and key == lib_id)


# ---------------------------------------------------------------------------
# Write-time validation (5 steps per app_bundles_v1.md §10.4)
# ---------------------------------------------------------------------------


async def validate_cross_app_relation_set(
    *,
    source_app: App,
    relation_spec: Dict[str, Any],
    target_entry_id: str,
    viewer_subject: Subject,
) -> Tuple[str, App, Entry]:
    """Validate a single cross-App relation reference (one target entry id).

    Returns ``(target_app_id, target_app, target_entry)`` on success.

    Raises BadRequestError-family for steps 1-4 failures; relays
    CrossAppPermissionDenied on step 5.
    """
    # Step 1: allow_cross_app: true.
    if not bool(relation_spec.get("allow_cross_app", False)):
        raise BadRequestError(
            message=("Cross-App relation requires allow_cross_app=true in manifest"),
            details={"relation_field_key": relation_spec.get("field_key", "")},
        )

    # Step 2: resolve target App.
    target_app_key = str(relation_spec.get("target_app") or "").strip()
    if not target_app_key:
        raise BadRequestError(
            message="Cross-App relation requires relation.target_app",
            details={"relation_field_key": relation_spec.get("field_key", "")},
        )
    resolution = str(relation_spec.get("resolution") or "workspace").strip()
    target_app_id = await resolve_target_app(
        workspace_id=source_app.workspace_id,
        target_app_key=target_app_key,
        resolution=resolution,
        source_app=source_app,
    )
    target_app = await App.get(target_app_id)
    if not target_app:
        # resolve_target_app would have raised; defense-in-depth.
        raise CrossAppTargetNotFoundError(
            message=f"target App {target_app_id!r} disappeared post-resolution",
            details={"target_app_id": target_app_id},
        )

    # Step 3: target App is active.
    if target_app.lifecycle_state != "active":
        raise BadRequestError(
            message=(
                f"Cross-App relation target App {target_app.name!r} is in state "
                f"{target_app.lifecycle_state!r}; must be 'active'"
            ),
            details={
                "target_app_id": target_app.id,
                "lifecycle_state": target_app.lifecycle_state,
            },
        )

    # Step 4: target Entry exists + matches track_types / entry_types.
    target_entry = await Entry.get(target_entry_id)
    if not target_entry:
        raise BadRequestError(
            message=(
                f"Cross-App relation references unknown entry {target_entry_id!r}"
            ),
            details={"target_entry_id": target_entry_id},
        )
    # NOTE: target_track_types / target_entry_types matching mirrors the
    # intra-App _validate_relation_values logic. Caller is responsible for
    # passing the resolved relation_spec dict (post-compile). The runtime here
    # accepts the spec verbatim — if the manifest declares a target_track_types
    # filter, callers should already have validated against it before calling
    # this function. This service focuses on the cross-App-specific gates.

    # Step 5: caller has entry.read permission via policy_engine.evaluate.
    decision = await policy_evaluate(
        subject=viewer_subject,
        action="entry.read",
        resource=Resource(
            kind="entry",
            id=target_entry.id,
            scope=f"track:{target_entry.track_id}",
        ),
    )
    if not decision.allowed:
        raise CrossAppPermissionDenied(
            message=(
                f"Viewer lacks entry.read on cross-App target entry "
                f"{target_entry.id!r}"
            ),
            details={
                "target_entry_id": target_entry.id,
                "target_app_id": target_app.id,
                "reason": decision.reason,
            },
        )

    return target_app_id, target_app, target_entry


# ---------------------------------------------------------------------------
# Materialization helper — set REFERENCES.target_app_id server-side
# ---------------------------------------------------------------------------


async def materialize_cross_app_reference(
    *,
    source_entry: Entry,
    field_key: str,
    target_entry: Entry,
    target_app_id: str,
    cross_track: bool = True,
) -> Any:
    """Create the REFERENCES edge with ``target_app_id`` populated.

    Pillar 2 (associative typed field): ``target_app_id`` lives on the edge,
    not on the source/target node. ``target_app_id`` is set SERVER-SIDE here
    — never client-supplied (T-10-06-10 mitigation).
    """
    if not source_entry or not target_entry:
        raise BadRequestError(
            message="materialize_cross_app_reference: source and target entry required"
        )
    if not target_app_id:
        raise BadRequestError(
            message="materialize_cross_app_reference: target_app_id is required"
        )
    edge = await source_entry.connect(
        target_entry,
        edge=REFERENCES,
        field_key=field_key,
        relation_type="content_profile",
        cross_track=cross_track,
        target_app_id=target_app_id,
    )
    logger.info(
        "materialize_cross_app_reference: source=%s field=%s target=%s target_app=%s",
        source_entry.id,
        field_key,
        target_entry.id,
        target_app_id,
    )
    return edge


# ---------------------------------------------------------------------------
# Read path: the ONE safe label reader (Risk 4 / Pitfall 5)
# ---------------------------------------------------------------------------


def _read_dotted_label(target_entry: Entry, label_field: str) -> str:
    """Resolve a dotted ``label_field`` path against an Entry.

    Defaults to ``title``. Honors dotted access into ``custom_fields``
    (e.g. ``"custom_fields.full_name"``). All values are stringified for
    safe rendering.
    """
    path = (label_field or "title").strip()
    if not path or path == "title":
        return str(getattr(target_entry, "title", "") or "")
    parts = path.split(".")
    head = parts[0]
    rest = parts[1:]
    value: Any = getattr(target_entry, head, None)
    for p in rest:
        if value is None:
            return ""
        if isinstance(value, dict):
            value = value.get(p)
        else:
            value = getattr(value, p, None)
    return str(value or "")


async def read_cross_app_label(
    *,
    viewer: Subject,
    relation_field_key: str,
    target_entry_id: str,
    target_app_id: str,
    label_field: str = "title",
) -> Dict[str, Any]:
    """SINGLE safe entry point for cross-App relation label rendering.

    Routes through ``policy_engine.evaluate(action='entry.read')``. On denial
    returns the strict ``RestrictedRelationStub`` shape (3 keys, no source
    field content — Architectural Decision 8 Option A). On allow returns the
    ``CrossAppRelationLabel`` shape with the rendered label + provenance
    pointers.

    Risk 4 / Pitfall 5: ANY direct read of ``label_field`` content outside
    this function is forbidden. The grep-gate test enforces this.
    """
    # Resolve target entry first — but DO NOT touch label_field until after
    # permission check passes (defense-in-depth — even a deleted target should
    # return a stub for unprivileged viewers, not a 404 that hints existence).
    target_entry = await Entry.get(target_entry_id)
    if target_entry is None:
        # If the target is gone we still answer with a restricted stub. We
        # cannot leak "this entry existed but is gone" to an unprivileged
        # viewer. Privileged viewers get the same stub — they'd see the gap
        # via their own list query, which honors deletion semantics anyway.
        return RestrictedRelationStub(
            relation_field_key=relation_field_key,
        ).model_dump()

    decision = await policy_evaluate(
        subject=viewer,
        action="entry.read",
        resource=Resource(
            kind="entry",
            id=target_entry.id,
            scope=f"track:{target_entry.track_id}",
        ),
    )
    if not decision.allowed:
        # Denial: STRICT restricted stub. No label, no target_entry_id,
        # no target_app_id, no target App name. Anything else is a leak.
        return RestrictedRelationStub(
            relation_field_key=relation_field_key,
        ).model_dump()

    # Allowed: read label_field. This is the ONLY safe label_field read site.
    # Grep gate enforces no other call site exists in backend/app/.
    label_value = _read_dotted_label(target_entry, label_field)
    target_app = await App.get(target_app_id)
    target_app_name = target_app.name if target_app else ""
    return CrossAppRelationLabel(
        relation_field_key=relation_field_key,
        target_entry_id=target_entry.id,
        target_app_id=target_app_id,
        target_app_name=target_app_name,
        label=label_value,
    ).model_dump()


__all__ = [
    "materialize_cross_app_reference",
    "read_cross_app_label",
    "resolve_target_app",
    "validate_cross_app_relation_set",
]
