"""Atomic draft → published promotion for OperationalModels.

Pillar 2 of the agent-authorable substrate. Promotion is two-stage:

  1. Validate: candidate (draft) manifest compiles cleanly; if a migration
     runner is wired in, run it to convergence with the user's chosen failure
     policy (abort vs. force).
  2. Swap: copy the draft's manifest onto the published CP node, bump
     ``version_number``, set ``published_at``, propagate composite metadata
     to ``EntryType.form_schema`` so existing materialized nodes stay aligned
     with the manifest, and emit a single ``operational_model.publish`` change
     event.

The swap deliberately does NOT clone or reattach edges. The published CP
keeps its identity (and therefore its attached relationship to whichever
Track or App references it via ``attached_operational_model_id``); only the
``manifest`` and lifecycle fields move. This avoids the full edge-clone
problem the plan flagged and keeps publish O(1) regardless of how many
EntryTypes / Tags / Views the Operational Model defines.

For library packages (``library_package=True``) the same publish flow runs;
the package's manifest replaces the published one in place. Catalog
membership is preserved.
"""

from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.exceptions import BadRequestError

logger = logging.getLogger(__name__)
from app.models.edges import CONTAINS
from app.models.nodes import OperationalModel
from app.services.change_event import emit_change_event
from app.services.operational_model_runtime import (
    compile_canonical_manifest,
    invalidate_manifest_cache,
)


async def fork_draft(
    *,
    published: OperationalModel,
    actor_id: Optional[str] = None,
) -> OperationalModel:
    """Fork a published CP into a new draft sibling.

    The draft is its own OperationalModel node — not the published one. It is
    not attached to any Track or App via ``HAS_OPERATIONAL_MODEL``; the agent
    mutates it via the patch DSL and publishes it via :func:`publish_draft`,
    which swaps the manifest onto the original published CP.
    """
    if published.status != "published":
        raise BadRequestError(
            message=(
                f"Can only fork a draft from a published CP "
                f"(status={published.status!r})"
            )
        )
    now = datetime.now(timezone.utc).isoformat()
    draft = await OperationalModel.create(
        name=f"{published.name} (draft)" if published.name else "Draft",
        version=published.version,
        manifest=dict(published.manifest or {}),
        scope=published.scope,
        workspace_id=published.workspace_id,
        app_id=published.app_id,
        library_package=False,
        description=published.description,
        created_at=now,
        updated_at=now,
        status="draft",
        published_at=None,
        draft_of_id=published.id,
        published_id=None,
        parent_version_id=published.id,
        version_number=int(published.version_number or 1) + 1,
        version_label=None,
        signature={"forked_by": actor_id, "forked_at": now},
    )
    # Phase 10.5 Plan 10.5-10 (I-GRAPH-01): wire published -HAS_DRAFT_PROFILE-> draft
    # so the in-flight authoring state is reachable from the rooted
    # OperationalModel subgraph. publish_draft preserves the draft node
    # (status flip + published_id stamp); discard_draft cascade-deletes
    # the draft AND the edge.
    try:
        from app.models.edges import HAS_DRAFT_PROFILE

        await published.connect(draft, edge=HAS_DRAFT_PROFILE, forked_at=now)
    except Exception:
        logger.exception(
            "fork_draft: HAS_DRAFT_PROFILE wire failed published=%s draft=%s",
            published.id,
            draft.id,
        )
    return draft


async def publish_draft(
    *,
    draft: OperationalModel,
    published: Optional[OperationalModel] = None,
    actor_id: Optional[str] = None,
    run_migrations: bool = True,
    abort_on_migration_failure: bool = True,
    force: bool = False,
    await_runner: bool = False,
) -> Dict[str, Any]:
    """Promote a draft's manifest onto its published parent.

    Returns a summary dict with the published CP id, new version number, and
    the migration run record (when migrations executed).

    Phase 5 Plan 05-02 additions:
      - ``force`` (default False) — when True, bypasses the no-migration-path
        reject gate. Forced publishes do NOT migrate existing entries; they
        remain on the prior schema until manually fixed (documented
        destructive escape hatch, I-MIG-03).
      - The default path (``force=False``) runs the reject gate BEFORE the
        atomic swap: if the diff produces ``would_need_migration`` impacts
        without matching ``migrations[].ops[]``, raises 422 with
        ``details.unhandled_breaks``.
      - After the swap, when ``run_migrations`` and not ``force``, spawns
        the async runner (``asyncio.create_task``) instead of awaiting
        ``run_publish_migrations`` inline. The HTTP response returns
        immediately with the tracker payload.
      - ``await_runner`` (default False) — test-only knob; awaits the
        spawned task inline so callers observe terminal state without
        polling. Production callers leave False.

    Caller must guarantee :func:`compute_manifest_diff` and entry-impact have
    already been surfaced to the user; this function is the *commit* step.
    """
    if draft.status != "draft":
        raise BadRequestError(message="Only draft CPs can be published")
    if published is None:
        if not draft.draft_of_id:
            raise BadRequestError(message="Draft is not linked to a published parent")
        published = await OperationalModel.get(draft.draft_of_id)
        if published is None:
            raise BadRequestError(message="Draft's published parent has been deleted")

    # Validate compilability up-front so we never half-swap a broken manifest.
    try:
        compiled = compile_canonical_manifest(manifest=dict(draft.manifest or {}))
    except BadRequestError as exc:
        raise BadRequestError(message=f"Draft manifest does not compile: {exc.message}")

    # Phase 5 Plan 05-02 — no-migration-path reject gate (MIG-03). Runs
    # BEFORE the atomic swap so a rejected publish never half-mutates the
    # graph. force=True is the documented destructive escape (I-MIG-03).
    if run_migrations and not force:
        try:
            from app.services.migrations.reject_gate import (
                detect_unhandled_breaks,
            )
            from app.services.operational_model_diff import (
                compute_entry_impact_for_attached,
            )

            impacts = await compute_entry_impact_for_attached(
                cp=published,
                candidate_manifest=compiled,
                sample_limit=20,
            )
            unhandled = detect_unhandled_breaks(
                impacts=impacts,
                migrations=list((compiled or {}).get("migrations") or []),
            )
            if unhandled:
                raise BadRequestError(
                    message=(
                        "Manifest change has no migration path for "
                        f"{unhandled[:5]}. Use ?force=true to publish "
                        "without migration (destructive — existing entries "
                        "will remain on the prior schema)."
                    ),
                    details={
                        "unhandled_breaks": unhandled,
                        "would_need_migration": impacts,
                    },
                )
        except BadRequestError:
            raise
        except ImportError as exc:
            # The MIG-03 reject gate is a substrate invariant: silently
            # skipping it would let a breaking manifest publish without a
            # migration path. Fail loud instead.
            raise RuntimeError(
                "publish_draft: MIG-03 reject gate unavailable "
                "(operational_model_diff / migrations.reject_gate failed to "
                f"import: {exc}). Refusing to publish without the gate; "
                "pass force=True only as the documented destructive escape."
            ) from exc

    migration_run: Dict[str, Any] = {
        "executed": False,
        "ops": [],
        "errors": [],
    }
    # Phase 5 Plan 05-02 — the inline ``run_publish_migrations`` call that
    # used to live here is gone. The async runner is spawned AFTER the
    # atomic swap (below) so the new manifest is in place by the time the
    # per-Entry transforms run. Forced publishes skip migration entirely.

    prior_snapshot = {
        "manifest": dict(published.manifest or {}),
        "version_number": int(published.version_number or 1),
    }

    # Compensation snapshot — the swap below is N+2 saves (published, draft,
    # one per materialized EntryType) with no transaction. If any of them
    # fails mid-way we restore the prior manifest, lifecycle fields and
    # EntryType schemas so the graph is never left half-published.
    snapshot = await _snapshot_publish_state(published, draft)

    now = datetime.now(timezone.utc).isoformat()
    published.manifest = dict(compiled)
    published.version_number = int(published.version_number or 1) + 1
    published.version_label = draft.version_label or published.version_label
    published.parent_version_id = published.id
    published.published_id = draft.id  # last promoted draft
    published.published_at = now
    published.updated_at = now
    sig = dict(published.signature or {})
    sig.update(
        {
            "published_by": actor_id,
            "published_at": now,
            "from_draft_id": draft.id,
        }
    )
    published.signature = sig
    try:
        await published.save()

        # Mark draft as consumed so it can't be re-published accidentally.
        draft.status = "published"  # consumed; identity preserved for audit
        draft.published_at = now
        draft.published_id = published.id
        draft.signature = {
            **(draft.signature or {}),
            "promoted_at": now,
            "promoted_by": actor_id,
            "promoted_to_id": published.id,
        }
        await draft.save()

        # Propagate composite metadata down to materialized
        # EntryType.form_schema so that the entry-validation path sees
        # ``composite.base`` on each field without re-resolving the manifest.
        await _sync_entry_type_form_schemas(published, compiled)
    except Exception:
        logger.exception(
            "publish_draft: swap failed for CP %s — restoring prior state",
            published.id,
        )
        await _restore_publish_state(snapshot)
        invalidate_manifest_cache()
        raise

    invalidate_manifest_cache()

    await emit_change_event(
        actor_kind="agent" if actor_id is None else "human",
        actor_id=actor_id or "",
        action="operational_model.publish",
        resource_type="OperationalModel",
        resource_id=published.id,
        before=prior_snapshot,
        after={
            "manifest": published.manifest,
            "version_number": published.version_number,
        },
        scope=f"operational_model:{published.id}",
    )

    # Phase 3.1 Plan 03.1-03 ANC-03 — refresh governance Policies for anchor
    # fields. Best-effort: a registry failure must NOT roll back the publish
    # (the swap has already committed). If governance materialization fails,
    # the anchor.create gate falls through to fail-closed-no-policy for
    # agent/connector subjects (defensive default per CONTEXT D-04); human
    # subjects pass via the can_edit_track default-human path.
    try:
        from app.services.policy_registry import (
            materialize_governance_policies_for_operational_model,
        )

        await materialize_governance_policies_for_operational_model(
            published.id, compiled
        )
    except Exception as exc:  # pragma: no cover — best-effort
        logger.warning(
            "publish_draft: governance Policy materialization failed for CP %s: %s",
            published.id,
            exc,
        )

    # Phase 5 Plan 05-02 — async migration runner spawn. Runs AFTER the
    # atomic swap so per-Entry transforms see the new manifest. The
    # runner pre-marks affected entries 'pending' synchronously, then
    # spawns ``asyncio.create_task`` and returns the tracker payload
    # immediately. force=True skips migration entirely (entries remain on
    # the prior schema — I-MIG-03 documented destructive escape).
    if run_migrations and not force:
        try:
            from app.services.migrations.runner import run_migration_async

            tracker = await run_migration_async(
                published_cp=published,
                compiled_manifest=compiled,
                actor_id=actor_id,
                await_runner=await_runner,
            )
            migration_run = {"executed": True, **tracker}
        except ImportError:
            # migrations subpackage unavailable — fall back to inline runner
            # (preserves Phase 4 behaviour when the subpackage isn't deployed).
            try:
                from app.services.operational_model_migrations import (
                    run_publish_migrations,
                )

                migration_run = await run_publish_migrations(
                    published_cp=published,
                    candidate_manifest=compiled,
                    abort_on_failure=abort_on_migration_failure,
                    actor_id=actor_id,
                )
            except ImportError:
                migration_run = {
                    "executed": False,
                    "ops": [],
                    "errors": [],
                    "skipped_reason": "migrations module unavailable",
                }
    elif force:
        migration_run = {
            "executed": False,
            "ops": [],
            "errors": [],
            "skipped_reason": (
                "force_publish — entries not migrated; remain on prior schema"
            ),
        }

    return {
        "published_id": published.id,
        "version_number": published.version_number,
        "migration_run": migration_run,
        # Phase 5 Plan 05-02 — locked alias for downstream callers that
        # consume the tracker shape directly. Mirrors migration_run for
        # back-compat with Phase 4 callers.
        "migration_tracker": migration_run,
    }


async def discard_draft(
    *,
    draft: OperationalModel,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Delete a draft CP (and its lifecycle metadata) without publishing."""
    if draft.status != "draft":
        raise BadRequestError(message="Only drafts can be discarded")
    draft_id = draft.id
    parent_id = draft.draft_of_id
    await draft.delete()
    return {
        "discarded": True,
        "draft_id": draft_id,
        "draft_of_id": parent_id,
    }


_PUBLISHED_SNAPSHOT_FIELDS = (
    "manifest",
    "version_number",
    "version_label",
    "parent_version_id",
    "published_id",
    "published_at",
    "updated_at",
    "signature",
)
_DRAFT_SNAPSHOT_FIELDS = ("status", "published_at", "published_id", "signature")


async def _snapshot_publish_state(
    published: OperationalModel, draft: OperationalModel
) -> Dict[str, Any]:
    """Capture everything the swap mutates so it can be restored on failure."""
    entry_types: List[Tuple[Any, Any]] = []
    try:
        for et in await published.nodes(edge=[CONTAINS], node=["EntryType"]):
            entry_types.append((et, copy.deepcopy(et.form_schema)))
    except Exception:  # pragma: no cover — defensive
        logger.exception(
            "publish_draft: could not snapshot EntryTypes for CP %s", published.id
        )
    return {
        "published": published,
        "published_fields": {
            f: copy.deepcopy(getattr(published, f, None))
            for f in _PUBLISHED_SNAPSHOT_FIELDS
        },
        "draft": draft,
        "draft_fields": {
            f: copy.deepcopy(getattr(draft, f, None)) for f in _DRAFT_SNAPSHOT_FIELDS
        },
        "entry_types": entry_types,
    }


async def _restore_publish_state(snapshot: Dict[str, Any]) -> None:
    """Compensation for a failed swap. Best-effort per node; never raises."""
    published = snapshot["published"]
    for field, value in snapshot["published_fields"].items():
        setattr(published, field, value)
    try:
        await published.save()
    except Exception:
        logger.exception(
            "publish_draft: restore of published CP %s failed", published.id
        )

    draft = snapshot["draft"]
    for field, value in snapshot["draft_fields"].items():
        setattr(draft, field, value)
    try:
        await draft.save()
    except Exception:
        logger.exception("publish_draft: restore of draft CP %s failed", draft.id)

    for et, prior_schema in snapshot["entry_types"]:
        if et.form_schema == prior_schema:
            continue
        et.form_schema = prior_schema
        try:
            await et.save()
        except Exception:
            logger.exception(
                "publish_draft: restore of EntryType %s failed", getattr(et, "id", "")
            )


async def _sync_entry_type_form_schemas(
    published_cp: OperationalModel,
    compiled_manifest: Dict[str, Any],
) -> None:
    """Push composite metadata onto materialized EntryType nodes.

    For each EntryType linked under the published CP via ``CONTAINS``, find
    the corresponding ``track.entry_types[]`` (or
    ``app_node.tracks[].entry_types[]``) entry in the compiled manifest and copy
    the canonical fields list (with ``composite`` metadata) onto
    ``EntryType.form_schema.fields``. This keeps the entry validator's
    dispatch consistent without requiring a re-compile on every entry write.
    """
    if compiled_manifest.get("scope") == "track":
        ets_in_manifest = (compiled_manifest.get("track") or {}).get(
            "entry_types"
        ) or []
    else:
        ets_in_manifest = []
        for t in (compiled_manifest.get("app") or {}).get("tracks") or []:
            ets_in_manifest.extend((t or {}).get("entry_types") or [])

    by_key = {
        str((et or {}).get("key") or ""): et
        for et in ets_in_manifest
        if isinstance(et, dict)
    }
    by_name = {
        str((et or {}).get("name") or "").strip().lower(): et
        for et in ets_in_manifest
        if isinstance(et, dict)
    }

    materialized_ets = await published_cp.nodes(edge=[CONTAINS], node=["EntryType"])
    for et in materialized_ets:
        from app.services.operational_model_compile import _slug

        key = _slug(str(et.name or ""))
        spec = by_key.get(key) or by_name.get(str(et.name or "").strip().lower())
        if spec is None:
            continue
        new_form_schema = {
            "fields": list(spec.get("fields") or []),
            "base_fields": dict(spec.get("base_fields") or {}),
            "required_tag_groups": list(spec.get("required_tag_groups") or []),
        }
        if et.form_schema != new_form_schema:
            et.form_schema = new_form_schema
            await et.save()
