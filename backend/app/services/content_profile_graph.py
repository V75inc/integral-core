"""Graph materialization helpers for content profiles (edges, anchors, taxonomy)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from jvspatial.core import Edge

from app.exceptions import BadRequestError
from app.models.edges import (
    ANCHORS,
    CATALOGS,
    CONTAINS,
    HAS_CONTENT_PROFILE,
    HAS_MEMBER_REF,
    REFERENCES,
    TEMPLATED_FROM,
    Anchors,
    HasMemberRef,
)
from app.models.nodes import App, ContentProfile, Entry, EntryType, Tag, Track, View
from app.services.app_graph import (
    get_app_attached_content_profile,
    get_or_create_views_registry_for_content_profile,
)
from app.services.content_profile_compile import (
    SCHEMA_VERSION,
    _as_dict,
    _as_list,
    _dedupe_specs_by_key,
    _slug,
    compile_canonical_manifest,
    find_app_track_template_spec_by_key,
)

logger = logging.getLogger(__name__)


def is_space_track_template_profile(content_profile: ContentProfile) -> bool:
    """True when CP is a shared anchor-track template (not a dedicated track CP)."""
    manifest = content_profile.manifest or {}
    mat = manifest.get("materialization")
    if not isinstance(mat, dict):
        return False
    return str(mat.get("kind") or "") == "space_track_template"


def _entry_type_manifest_key(et: EntryType) -> str:
    fs = et.form_schema or {}
    mk = str(fs.get("_manifest_entry_type_key") or "").strip()
    if mk:
        return _slug(mk)
    return _slug(str(et.name or ""))


def _entry_type_sync_precedence(et: EntryType) -> tuple:
    """Prefer track-materialized entry types over template shells when deduping."""
    track_id = str(getattr(et, "track_id", "") or "").strip()
    field_count = len((et.form_schema or {}).get("fields") or [])
    return (1 if track_id else 0, field_count)


def _view_manifest_key(v: View) -> str:
    cfg = v.config or {}
    return str(cfg.get("_manifest_view_key") or _slug(str(v.name or "")))


def _view_sync_precedence(v: View) -> tuple:
    track_id = str(getattr(v, "track_id", "") or "").strip()
    return (1 if track_id else 0,)


async def sync_relation_edges(
    *,
    source_entry: Entry,
    relation_refs: List[Dict[str, Any]],
) -> None:
    """Materialize relation-field edges from compiled relation_refs.

    Phase 3.1 (ANC-02): routes by ``relation_refs[i]["target"]``:

      - ``"entry"`` (default) -> REFERENCES (entry -> entry)  — existing behavior.
      - ``"track"``           -> ANCHORS    (entry -> track)  — new ANC-01 path.

    Per CONTEXT decisions Conformance / invariants: ANCHORS edge writes occur
    ONLY inside ``_sync_anchor_edges``. The grep gate
    ``grep -rE 'edge=ANCHORS|edge=Anchors\\b' backend/app/ --include='*.py'``
    must return matches only inside this module.
    """
    entry_refs = [
        r for r in relation_refs if str(r.get("target") or "entry") == "entry"
    ]
    track_refs = [
        r for r in relation_refs if str(r.get("target") or "entry") == "track"
    ]
    member_refs = [
        r for r in relation_refs if str(r.get("target") or "entry") == "user"
    ]
    if entry_refs:
        await _sync_reference_edges(source_entry=source_entry, relation_refs=entry_refs)
    if track_refs:
        await _sync_anchor_edges(source_entry=source_entry, relation_refs=track_refs)
    if member_refs:
        await _sync_member_ref_edges(
            source_entry=source_entry, relation_refs=member_refs
        )


async def _sync_reference_edges(
    *,
    source_entry: Entry,
    relation_refs: List[Dict[str, Any]],
) -> None:
    """Materialize REFERENCES edges (entry -> entry).

    Body is the verbatim pre-3.1 ``sync_relation_edges`` implementation —
    preserved so existing entry-target regressions remain green untouched.
    """
    ctx = await source_entry.get_context()
    old_targets = await source_entry.nodes(
        edge=["REFERENCES"], direction="out", node=["Entry"]
    )
    for target in old_targets:
        old_edges = await ctx.find_edges_between(
            source_entry.id, target.id, edge_class=REFERENCES
        )
        for edge in old_edges:
            await edge.delete()
    for rel in relation_refs:
        field_key = str(rel.get("field_key") or "")
        allow_cross_track = bool(rel.get("allow_cross_track", False))
        for target_id in rel.get("targets") or []:
            target = await Entry.get(str(target_id))
            if not target:
                continue
            existing = await ctx.find_edges_between(
                source_entry.id, target.id, edge_class=REFERENCES
            )
            if existing:
                continue
            await source_entry.connect(
                target,
                edge=REFERENCES,
                field_key=field_key,
                relation_type="content_profile",
                cross_track=allow_cross_track,
            )


async def _maybe_reuse_existing_anchor(
    *,
    source_entry: Optional[Entry],
    field_key: str,
) -> Optional[str]:
    """Return existing ANCHORS-target Track id for ``field_key``, or None.

    Used by the auto-provision hook on the update path: if the entry already
    anchors a Track for this relation field, prefer to reuse it instead of
    re-provisioning a new template Track on every update. On the create path
    ``source_entry`` is None and this is a no-op.

    Idempotency contract: at most one anchored Track per (entry, field_key).
    If multiple ANCHORS edges exist for the same field_key (data corruption),
    the first encountered id is returned and a warning is logged.
    """
    if source_entry is None:
        return None
    try:
        ctx = await source_entry.get_context()
    except Exception:  # pragma: no cover - context fetch failure is non-recoverable
        return None
    existing_tracks = await source_entry.nodes(
        edge=["ANCHORS"], direction="out", node=["Track"]
    )
    matches: List[str] = []
    for tnode in existing_tracks:
        edges_between = await ctx.find_edges_between(
            source_entry.id, tnode.id, edge_class=Anchors
        )
        for e in edges_between:
            ek = getattr(e, "field_key", None) or ""
            if str(ek) == str(field_key):
                matches.append(str(tnode.id))
                break
    if not matches:
        return None
    if len(matches) > 1:
        logger.warning(
            "_maybe_reuse_existing_anchor: multiple anchors for "
            "(entry=%r, field_key=%r); reusing first=%r",
            source_entry.id,
            field_key,
            matches[0],
        )
    return matches[0]


async def materialize_anchor_track(
    *,
    source_track: Track,
    template_key: str,
    field_key: str,
    actor_user_id: str = "",
    actor_kind: str = "human",
    source_entry_title: str = "",
) -> Track:
    """Auto-provision an anchored Track from a ``app.track_templates[]`` entry.

    Phase 3.1 ANC-04 hook (Plan 03.1-02). Fired by
    ``validate_and_materialize_entry_custom_fields`` when an entry-type
    relation field has ``target: track`` + ``auto_provision: true`` and the
    caller does NOT supply an explicit value. The newly-created Track:

      - lives in the SAME Workspace as ``source_track`` (ANC-08 — cross-workspace
        anchoring is out of scope for v1; enforced both here and at
        ``_validate_relation_values`` for defense in depth);
      - is contained by the SAME parent App as ``source_track`` (via
        ``CONTAINS``) — siblings of the source track, not children;
      - receives the template's ContentProfile BY REFERENCE via
        ``HAS_CONTENT_PROFILE`` + ``Track.attached_content_profile_id`` scalar
        (researcher note: HAS_CONTENT_PROFILE is polymorphic-target, so many
        Tracks may share one CP — the template ContentProfile node is
        materialized lazily on first use and reused thereafter for any
        subsequent Track from the same template_key in this App);
      - carries a ``TEMPLATED_FROM`` lineage edge (Track → CP) with
        ``template_key`` payload so the provenance is graph-walkable.

    Raises ``BadRequestError`` when:
      - ``source_track`` has no parent App (anchors require an App context;
        standalone Tracks under a Workspace are not supported for v1);
      - the App-attached ContentProfile is missing or does not declare
        ``template_key`` under ``app.track_templates[]``.

    Per CONTEXT decisions: emits a single ``track.create`` ChangeEvent
    (Plan 03.1-03 will swap the action to ``anchor.create`` after extending
    the ChangeEventAction Literal).
    """
    # 1) Resolve parent App (anchors require an App — single source of
    # truth for the track_templates registry).
    apps = await source_track.nodes(
        edge=["CONTAINS"], direction="in", node=["WorkspaceApp"]
    )
    app_node: Optional[App] = None
    for cand in apps:
        if isinstance(cand, App):
            app_node = cand
            break
    if app_node is None:
        raise BadRequestError(
            message=(
                f"Anchor auto-provision requires a parent App for source Track "
                f"'{source_track.id}' (field '{field_key}')"
            )
        )

    # 2) Resolve the App-attached ContentProfile + its compiled manifest.
    app_cp = await get_app_attached_content_profile(app_node)
    if app_cp is None or not app_cp.manifest:
        raise BadRequestError(
            message=(
                f"Anchor auto-provision requires an App-attached ContentProfile "
                f"on App '{app_node.id}' declaring track_templates "
                f"(field '{field_key}', template_key '{template_key}')"
            )
        )
    canonical = compile_canonical_manifest(
        manifest=_as_dict(app_cp.manifest, where="app content profile manifest")
    )
    template_spec = find_app_track_template_spec_by_key(canonical, template_key)
    if template_spec is None:
        raise BadRequestError(
            message=(
                f"Anchor auto-provision: template '{template_key}' not found "
                f"in app_node.track_templates[] for App '{app_node.id}' "
                f"(field '{field_key}')"
            )
        )

    now = datetime.now(timezone.utc).isoformat()

    # 2.5) Phase 3.1 Plan 03.1-03 ANC-03 — anchor.create governance gate.
    # Evaluate ``anchor.create`` BEFORE any Track materialization so denied
    # callers receive a 403 with no partial graph state. The denial-emit path
    # in policy_engine.evaluate (Phase 3 Plan 03-05) fires a ``policy.deny``
    # ChangeEvent automatically; we additionally emit ``anchor.deny`` for
    # forensic clarity (subscribers know this denial was anchor-specific).
    #
    # CONTEXT-locked: subject_kind defaults to "human" (callers thread the
    # actor through validate_and_materialize_entry_custom_fields); governance
    # Policies persisted at content-profile publish time live under
    # subject_kind="system" and are consulted by Policy.find on scope match
    # (separate from the HAS_POLICY traversal used for agent/connector
    # subjects). See policy_registry.materialize_governance_policies_for_content_profile.
    from typing import cast as _cast

    from app.api.errors import InsufficientPermissionsError
    from app.schemas.policy import Resource as _Resource
    from app.schemas.policy import Subject as _Subject
    from app.schemas.provenance import ActorKind as _ActorKind
    from app.services import policy_engine as _policy_engine

    cp_id_for_scope = getattr(app_cp, "id", "") or ""
    # entry_type_key is not directly known here — the auto-provision hook
    # fires from validate_and_materialize_entry_custom_fields where the
    # field is part of an entry type. We use the template_key as the entry
    # type discriminator on the scope string; downstream Policy.find by
    # scope-prefix can match. (See policy_registry helper which writes the
    # Policy scope at publish time using the same shape.)
    _scope = f"anchor_field:{cp_id_for_scope}:{template_key}:{field_key}"
    # When the caller does not supply an actor (internal scaffolding / test
    # fixtures), fall through to the system subject — policy_engine.evaluate
    # short-circuits to allow for Subject(kind='system', ...) per CONTEXT
    # D-10 + Phase 3 Plan 03-05 bypass. Real HTTP callers always thread
    # request.state.user.id via the entries.py update + create paths.
    if actor_user_id:
        _eff_kind: _ActorKind = _cast(_ActorKind, actor_kind)
        _eff_id: str = actor_user_id
    else:
        _eff_kind = "system"
        _eff_id = "anchor_auto_provision"
    # resource.id carries the SOURCE Track id so the default-human path can
    # consult can_edit_track on the parent. resource.scope carries the
    # governance scope so agentive Policy matchers (subject_kind="system" /
    # subject_kind="agent" with anchor_field scope) match deterministically.
    _decision = await _policy_engine.evaluate(
        subject=_Subject(kind=_eff_kind, id=_eff_id),
        action="anchor.create",
        resource=_Resource(
            kind="track",
            id=source_track.id,
            scope=_scope,
        ),
    )
    if not _decision.allowed:
        # Plan 03-05's denial-emit fires policy.deny automatically inside
        # policy_engine.evaluate. We additionally emit anchor.deny for the
        # parallel forensic record. Both events have the same actor + scope;
        # WS broadcast skip set covers anchor.cascade only — anchor.deny is
        # a low-volume signal so we let it broadcast normally.
        try:
            from app.services.change_event import emit_change_event as _emit_deny

            await _emit_deny(
                actor_kind=_eff_kind,
                actor_id=_eff_id,
                action="anchor.deny",
                resource_type="Track",
                resource_id="",
                before=None,
                after=None,
                scope=_scope,
                details={
                    "failed_action": "anchor.create",
                    "decision_reason": _decision.reason,
                    "matched_policy_id": _decision.matched_policy_id,
                    "field_key": field_key,
                    "template_key": template_key,
                },
            )
        except Exception as exc:  # pragma: no cover — best-effort forensic emit
            logger.warning(
                "anchor materialize_anchor_track: anchor.deny emit failed: %s",
                exc,
            )
        raise InsufficientPermissionsError(
            message="Anchor governance denies anchor.create",
            details={
                "decision_reason": _decision.reason,
                "matched_policy_id": _decision.matched_policy_id,
                "field_key": field_key,
                "template_key": template_key,
            },
        )

    # 3) Resolve-or-materialize the template ContentProfile node. The template
    # is identified by (app_id, template_key); a per-App CP node is
    # created the first time and reused thereafter (by-reference semantics).
    template_cp = await _resolve_or_create_template_content_profile(
        app_node=app_node,
        template_key=template_key,
        template_spec=template_spec,
        now=now,
    )

    # Materialize views + entry types on the shared template CP the first time
    # (or patch them forward when the template spec evolves). Without this,
    # anchored tracks list zero views until a separate merge/upgrade runs.
    from app.services.content_profile_merge import (
        refresh_app_track_template_materialization,
    )

    await refresh_app_track_template_materialization(app_node, template_key)

    # 4) Create the anchored Track in the same workspace.
    source_ws = getattr(source_track, "workspace_id", "") or ""
    template_name = str(template_spec.get("name") or template_key or "Anchor")
    # When the caller supplies the source entry's title, suffix the anchor
    # title with it so siblings in the parent App stay visually distinct
    # ("Project Details: Onboarding: Contoso e-commerce" vs
    # "Project Details: API hardening sprint"). Falls back to the template
    # name alone for callers that don't thread the title (back-compat).
    clean_title = (source_entry_title or "").strip()
    track_title = f"{template_name}: {clean_title}" if clean_title else template_name
    anchor_track = await Track.create(
        title=track_title,
        title_fold=track_title.strip().casefold(),
        owner_id=getattr(source_track, "owner_id", None) or "",
        purpose="",
        icon="",
        visibility="inherit",
        template_id=template_key,
        workspace_id=source_ws,
        attached_content_profile_id=template_cp.id,
        created_at=now,
        updated_at=now,
    )

    # 5) ANC-08 belt + suspenders: assert the just-created track is in the
    # source workspace. ``Track.create`` accepted ``workspace_id=source_ws``
    # so the only way this trips is a bug — fail loudly.
    if (getattr(anchor_track, "workspace_id", "") or "") != source_ws:
        raise BadRequestError(
            message=(
                f"Anchor auto-provision workspace mismatch: anchored Track "
                f"workspace='{anchor_track.workspace_id}' != source workspace "
                f"'{source_ws}' (field '{field_key}')"
            )
        )

    # 6) Wire CONTAINS (App → Track) so the track appears as a normal
    # sibling under the parent App.
    await app_node.connect(anchor_track, edge=CONTAINS, added_at=now)
    # Keep workspace Tracks branch in sync so surfaces that read the branch
    # directly (e.g. Mission Control workspace totals) include anchored tracks.
    from app.services.app_graph import catalog_track

    await catalog_track(anchor_track)

    # 7) Wire HAS_CONTENT_PROFILE (Track → CP) by reference and TEMPLATED_FROM
    # (Track → CP) for lineage. attached_content_profile_id was set on
    # create — keep the scalar + edge in lockstep.
    await anchor_track.connect(template_cp, edge=HAS_CONTENT_PROFILE, attached_at=now)
    await anchor_track.connect(
        template_cp,
        edge=TEMPLATED_FROM,
        template_key=template_key,
        provisioned_at=now,
    )

    from app.services.content_profile_runtime import (
        ensure_track_views_materialized_from_tier,
    )

    await ensure_track_views_materialized_from_tier(anchor_track)

    # 8) Single anchor.create ChangeEvent. Phase 3.1 Plan 03.1-03 switched
    # this from the Plan 03.1-02 placeholder ``track.create`` once
    # ChangeEventAction grew to include the anchor.* mutations.
    try:
        from app.services.change_event import emit_change_event

        await emit_change_event(
            actor_kind=_eff_kind,
            actor_id=_eff_id,
            action="anchor.create",
            resource_type="Track",
            resource_id=anchor_track.id,
            before=None,
            after={
                "id": anchor_track.id,
                "title": anchor_track.title,
                "workspace_id": anchor_track.workspace_id,
                "template_id": anchor_track.template_id,
                "attached_content_profile_id": anchor_track.attached_content_profile_id,
                "anchor_field_key": field_key,
                "anchor_template_key": template_key,
                "anchor_source_track_id": source_track.id,
                "anchor_space_id": app_node.id,
            },
            scope=f"app:{app_node.id}",
        )
    except Exception as exc:  # pragma: no cover - emit is best-effort here
        logger.warning(
            "anchor materialize_anchor_track: emit_change_event failed: %s",
            exc,
        )

    return anchor_track


async def _resolve_or_create_template_content_profile(
    *,
    app_node: App,
    template_key: str,
    template_spec: Dict[str, Any],
    now: str,
) -> ContentProfile:
    """Return the per-App template ContentProfile for ``template_key``.

    Discriminator: the CP carries
    ``manifest["materialization"] = {"kind": "space_track_template",
    "template_key": "<key>", "app_id": "<app_node.id>"}`` so subsequent
    lookups in the same App are deterministic. This avoids overloading
    ``ContentProfile.name`` for lineage and keeps the discriminator inside
    the manifest payload (no node-schema change required).

    By-reference contract: subsequent Tracks anchored from the SAME
    ``template_key`` in this App reuse the SAME ContentProfile node —
    edits to the template CP's manifest therefore propagate to every
    anchored Track that shares it. This is intentional per CONTEXT
    Resolved Fork 3 + 4 (the template registry is the single source of
    truth; anchored Tracks are views over it).
    """
    # Build the canonical template manifest up-front so we can either return
    # it (fresh create) or use it to refresh a stale stored manifest on hit.
    template_manifest: Dict[str, Any] = {
        "content_profile_schema_version": SCHEMA_VERSION,
        "scope": "track",
        "track": {
            "entry_types": list(template_spec.get("entry_types") or []),
            "views": list(template_spec.get("views") or []),
            "taxonomy": dict(template_spec.get("taxonomy") or {}),
            "defaults": dict(template_spec.get("defaults") or {}),
        },
        "package": {},
        "migrations": [],
        # Discriminator used by this resolver on subsequent lookups.
        "materialization": {
            "kind": "space_track_template",
            "template_key": str(template_key),
            "app_id": str(app_node.id),
        },
    }
    # Walk the App's outgoing CONTAINS edges to ContentProfile children.
    # (Cataloging via CONTAINS is the existing pattern — see
    # ensure_app_attached_content_profile for the attached-profile case.)
    candidates = await app_node.nodes(
        edge=["CONTAINS"], direction="out", node=["ContentProfile"]
    )
    for cand in candidates:
        if not isinstance(cand, ContentProfile):
            continue
        stored = cand.manifest or {}
        mat = stored.get("materialization") if isinstance(stored, dict) else None
        if not isinstance(mat, dict):
            mat = {}
        # Match by the materialization discriminator. Template CPs created
        # via this path always carry ``materialization.kind ==
        # 'space_track_template'`` with the template_key + app_id stamped on.
        discriminator_match = (
            str(mat.get("kind") or "") == "space_track_template"
            and str(mat.get("template_key") or "") == str(template_key)
            and str(mat.get("app_id") or "") == str(app_node.id)
        )
        if discriminator_match:
            # Refresh the stored manifest from the current spec so manifest
            # edits (library version bumps, agent-authored revisions) reach
            # every anchored Track that shares this template CP. Without this
            # refresh the CP is frozen at first-materialization time and
            # users hit validation errors like
            # ``track.defaults.default_entry_type must match an entry type
            # key in this tier`` after the template spec evolves.
            stored_track = stored.get("track") if isinstance(stored, dict) else None
            manifest_stale = (
                stored_track != template_manifest["track"]
                or stored.get("materialization") != template_manifest["materialization"]
            )
            # Belt-and-braces: a manifest-text diff is NOT proof the real
            # EntryType/View graph nodes exist. If a prior call to
            # _materialize_template_content_profile_nodes raised (e.g. a
            # view_type wasn't registered yet in that process — plugin
            # discovery hadn't run) and the caller swallowed it, the CP is
            # left with a manifest that already matches the current spec
            # (so the diff check above never trips) but zero real View
            # nodes cataloged — every anchored Track sharing this CP then
            # shows a permanent "View not found" for its whole lifetime,
            # since nothing ever re-checks. Confirmed live: 6 of 10
            # space_track_template CPs across this DB were stuck this way.
            # Cheaply verify the graph state actually matches the promise
            # before trusting the fast path.
            needs_node_heal = False
            if not manifest_stale:
                expected_views = template_manifest["track"].get("views") or []
                if expected_views:
                    vreg = await get_or_create_views_registry_for_content_profile(cand)
                    existing_views = await vreg.nodes(edge=[CATALOGS], node=["View"])
                    needs_node_heal = not existing_views
            if manifest_stale or needs_node_heal:
                cand.manifest = template_manifest
                cand.updated_at = now
                await cand.save()
                await _materialize_template_content_profile_nodes(cand)
            return cand

    template_cp = await ContentProfile.create(
        name=str(template_spec.get("name") or template_key),
        scope="track",
        manifest=template_manifest,
        workspace_id=getattr(app_node, "workspace_id", "") or "",
        app_id=app_node.id,
        library_package=False,
        created_at=now,
        updated_at=now,
    )
    # Catalog the template CP under the App so subsequent lookups find it.
    await app_node.connect(template_cp, edge=CONTAINS, added_at=now)
    # The manifest above is raw spec data only — nothing yet creates the real
    # EntryType/Tag/View graph nodes _list_track_views (app/api/views.py)
    # requires. Without this, every Track anchored from this template shares
    # a ContentProfile with zero cataloged Views, so its inline related_views
    # (table + action bar) silently render nothing. Materialize once here;
    # every subsequent anchored Track reuses the same by-reference CP.
    await _materialize_template_content_profile_nodes(template_cp)
    return template_cp


async def _materialize_template_content_profile_nodes(
    template_cp: ContentProfile,
) -> None:
    """Create real EntryType/Tag/View nodes on a template CP from its own manifest.

    Mirrors ``apply_space_track_spec_to_track``'s shim-merge pattern (content_profile_merge.py)
    but targets the template CP itself rather than a per-track CP, since
    anchored Tracks share this CP by reference (see
    ``_resolve_or_create_template_content_profile``).
    """
    from app.services.content_profile_merge import (
        _InMemoryLibraryManifest,
        merge_library_manifest_into_content_profile,
    )

    # _merge_track_tier_into_manifest replaces target_cp.manifest wholesale,
    # dropping any key it doesn't itself manage — including our
    # ``materialization`` discriminator (_resolve_or_create_template_content_profile's
    # by-reference reuse lookup keys off it). Preserve + restore it.
    materialization = dict((template_cp.manifest or {}).get("materialization") or {})
    shim = _InMemoryLibraryManifest(dict(template_cp.manifest or {}))
    await merge_library_manifest_into_content_profile(
        shim, template_cp, None, for_space=False
    )
    if materialization:
        template_cp.manifest = {
            **(template_cp.manifest or {}),
            "materialization": materialization,
        }
        await template_cp.save()


async def _sync_anchor_edges(
    *,
    source_entry: Entry,
    relation_refs: List[Dict[str, Any]],
) -> None:
    """Materialize ANCHORS edges (entry -> track) — Phase 3.1 ANC-01 path.

    Replace semantics: existing ANCHORS edges from ``source_entry`` are deleted
    (the underlying Track is NOT deleted — per CONTEXT Resolved Fork 1, the
    anchored Track persists as a top-level resource). Then connect each
    track-target ref via ``edge=ANCHORS`` carrying ``field_key`` + ``role="detail"``.

    Per CONTEXT decisions Conformance / invariants: this is the ONLY
    sanctioned write path for ANCHORS edges across ``backend/app/``.
    """
    ctx = await source_entry.get_context()

    # 1) Delete existing ANCHORS edges from this source entry.
    #    (Preserve target Tracks — they remain discoverable top-level resources.)
    old_anchor_targets = await source_entry.nodes(
        edge=["ANCHORS"], direction="out", node=["Track"]
    )
    for target in old_anchor_targets:
        old_edges = await ctx.find_edges_between(
            source_entry.id, target.id, edge_class=Anchors
        )
        for edge in old_edges:
            try:
                await edge.delete()
            except Exception as exc:  # pragma: no cover - best-effort cleanup
                logger.warning("ANCHORS edge delete failed (continuing): %s", exc)

    # 2) Connect each track-target ref.
    for rel in relation_refs:
        field_key = str(rel.get("field_key") or "")
        for target_id in rel.get("targets") or []:
            track = await Track.get(str(target_id))
            if track is None:
                logger.warning(
                    "ANCHORS skip: target Track %r not found (field_key=%r)",
                    target_id,
                    field_key,
                )
                continue
            # Idempotency: skip if an ANCHORS edge already exists to this target
            # (defensive — step 1 deletes all anchors, so this is only a guard
            # against duplicates within a single relation_refs list).
            existing = await ctx.find_edges_between(
                source_entry.id, track.id, edge_class=Anchors
            )
            if existing:
                continue
            await source_entry.connect(
                track,
                edge=ANCHORS,
                field_key=field_key,
                role="detail",
            )


async def _sync_member_ref_edges(
    *,
    source_entry: Entry,
    relation_refs: List[Dict[str, Any]],
) -> None:
    """Materialize HAS_MEMBER_REF edges (entry -> user) — member-field path.

    Single-writer for ``HAS_MEMBER_REF`` across ``backend/app/`` (mirrors
    the ``_sync_anchor_edges`` discipline at INVARIANTS.md L101-116). The
    grep gate ``grep -rE 'edge=HAS_MEMBER_REF|edge=HasMemberRef\\b'
    backend/app/`` must return matches only inside this function.

    Replace semantics: existing HAS_MEMBER_REF edges from ``source_entry``
    whose ``field_key`` matches an incoming ref are deleted before being
    re-wired, so re-writes upsert cleanly (single edge per (entry,
    field_key) pair). Other HAS_MEMBER_REF edges (different field_keys)
    are preserved.

    Per the member-field Decision Record: both endpoints (Entry, User)
    are already rooted; HAS_MEMBER_REF is an ADDITIONAL pointer. No new
    ``Node.create(...)`` is performed here.
    """
    from app.models.nodes import User

    if not relation_refs:
        return
    ctx = await source_entry.get_context()
    incoming_field_keys = {str(r.get("field_key") or "") for r in relation_refs}

    existing_users = await source_entry.nodes(
        edge=["HAS_MEMBER_REF"], direction="out", node=["User"]
    )
    for target in existing_users:
        edges = await ctx.find_edges_between(
            source_entry.id, target.id, edge_class=HasMemberRef
        )
        for edge in edges:
            fk = getattr(edge, "field_key", "") or ""
            if str(fk) in incoming_field_keys:
                try:
                    await edge.delete()
                except Exception as exc:  # pragma: no cover - best-effort cleanup
                    logger.warning(
                        "HAS_MEMBER_REF edge delete failed (continuing): %s", exc
                    )

    for ref in relation_refs:
        field_key = str(ref.get("field_key") or "")
        # The runtime dispatch builds ``targets`` as a single-element list
        # (v1 member fields are single-link; many=true is future work — see
        # the member-field Decision Record §9).
        for user_id in ref.get("targets") or []:
            user = await User.get(str(user_id))
            if user is None:
                logger.warning(
                    "HAS_MEMBER_REF skip: User %r not found (field_key=%r)",
                    user_id,
                    field_key,
                )
                continue
            # Idempotency guard — replace semantics above should have purged
            # any prior edge for this field_key; this catches in-batch duplicates.
            existing = await ctx.find_edges_between(
                source_entry.id, user.id, edge_class=HasMemberRef
            )
            already_for_field = any(
                str(getattr(e, "field_key", "") or "") == field_key for e in existing
            )
            if already_for_field:
                continue
            await source_entry.connect(
                user,
                edge=HAS_MEMBER_REF,
                field_key=field_key,
            )


async def seed_taxonomy_for_track(
    *,
    track: Track,
    content_profile: ContentProfile,
    runtime_tier: Dict[str, Any],
) -> None:
    """Seed hierarchical taxonomy tag library into track scope."""
    taxonomy = _as_dict(runtime_tier.get("taxonomy"), where="taxonomy")
    groups = _as_list(taxonomy.get("tag_groups"), where="taxonomy.tag_groups")
    created: Dict[str, Tag] = {}
    for g in groups:
        gd = _as_dict(g, where="taxonomy.group")
        gkey = str(gd.get("key") or "default")
        for t in _as_list(gd.get("tags"), where=f"taxonomy.{gkey}.tags"):
            td = _as_dict(t, where="taxonomy.tag")
            tname = str(td.get("name") or "").strip()
            if not tname:
                continue
            existing = await Tag.find(
                {
                    "context.track_id": track.id,
                    "context.name": tname,
                    "context.group_key": gkey,
                }
            )
            if existing:
                created[f"{gkey}:{td.get('key') or _slug(tname)}"] = existing[0]
                continue
            tag = await Tag.create(
                name=tname,
                color=str(td.get("color") or "#6B7280"),
                track_id=track.id,
                group_key=gkey,
                aliases=[str(a) for a in _as_list(td.get("aliases"), where="")],
                applies_to_entry_types=[
                    str(a) for a in _as_list(td.get("applies_to"), where="")
                ],
                is_template=False,
            )
            await content_profile.connect(tag, edge=CONTAINS)
            created[f"{gkey}:{td.get('key') or _slug(tname)}"] = tag

    # second pass for parent links
    for g in groups:
        gd = _as_dict(g, where="taxonomy.group")
        gkey = str(gd.get("key") or "default")
        for t in _as_list(gd.get("tags"), where=f"taxonomy.{gkey}.tags"):
            td = _as_dict(t, where="taxonomy.tag")
            tkey = f"{gkey}:{td.get('key') or _slug(str(td.get('name') or ''))}"
            tag = created.get(tkey)
            if not tag:
                continue
            parent_key = td.get("parent_key")
            if parent_key:
                parent = created.get(f"{gkey}:{parent_key}")
                if parent:
                    tag.parent_tag_id = parent.id
                    await tag.save()


async def sync_attached_manifest(content_profile: ContentProfile) -> None:
    """Rebuild the manifest on an attached ContentProfile from its materialized nodes.

    The manifest is the *living specification* — always the source of truth for
    what the Track/App contains.  After any in-place customization (add/remove/
    modify EntryType, Tag, View), this function must be called so the manifest
    stays in sync with the persisted subgraph.

    For track-attached profiles, the manifest ``track`` tier is rebuilt from:
      - EntryType nodes connected via CONTAINS
      - Tag nodes connected via CONTAINS
      - View nodes cataloged under the Views registry

    For App-attached profiles, the manifest ``app_node`` tier is rebuilt from:
      - Track-template ContentProfile nodes connected via DEFINES_TRACK_PROFILE

    Library-package provenance (``libraryMergeSourceId``) is preserved — the
    ``package`` and ``migrations`` top-level keys are carried over from the
    existing manifest so that re-merge can still work.
    """
    existing = content_profile.manifest or {}
    scope = str(existing.get("scope") or content_profile.scope or "track")
    saved_package = existing.get("package", {})
    saved_migrations = existing.get("migrations", [])
    saved_field_types = existing.get("field_types") or []
    saved_view_types = existing.get("view_types") or []
    saved_plugins = existing.get("plugins") or []

    if scope == "track":
        # --- rebuild track tier from materialized nodes ---
        entry_types = await content_profile.nodes(edge=[CONTAINS], node=["EntryType"])
        seen_et_ids = {str(getattr(et, "id", "") or "") for et in entry_types}
        sync_now = datetime.now(timezone.utc).isoformat()
        attached_tracks = await content_profile.nodes(
            edge=[HAS_CONTENT_PROFILE], direction="in", node=["Track"]
        )
        for attached_track in attached_tracks:
            if not isinstance(attached_track, Track):
                continue
            orphans = await EntryType.find({"context.track_id": attached_track.id})
            for et in orphans or []:
                et_id = str(getattr(et, "id", "") or "")
                if not et_id or et_id in seen_et_ids:
                    continue
                ctx = await content_profile.get_context()
                if not await ctx.find_edges_between(
                    content_profile.id, et_id, edge_class=CONTAINS
                ):
                    await content_profile.connect(et, edge=CONTAINS, added_at=sync_now)
                entry_types.append(et)
                seen_et_ids.add(et_id)
        tags = await content_profile.nodes(edge=[CONTAINS], node=["Tag"])

        # Resolve Views registry → cataloged Views
        vregs = await content_profile.nodes(edge=[Edge], node=["Views"])
        views: List[View] = []
        if vregs:
            views = await vregs[0].nodes(edge=[CATALOGS], node=["View"])

        # Build entry_types list — one row per manifest key. Anchor template CPs
        # can accumulate template shells plus per-track materializations with
        # the same key; dedupe so compile does not reject duplicate keys.
        et_by_key: Dict[str, EntryType] = {}
        for et in entry_types:
            key = _entry_type_manifest_key(et)
            if not key:
                continue
            prev = et_by_key.get(key)
            if prev is None or _entry_type_sync_precedence(
                et
            ) > _entry_type_sync_precedence(prev):
                et_by_key[key] = et

        et_list = []
        for et in et_by_key.values():
            fs = et.form_schema or {}
            fields = list(fs.get("fields", []))
            base_fields = fs.get("base_fields", {})
            rtg = list(fs.get("required_tag_groups", []))
            et_list.append(
                {
                    "key": _entry_type_manifest_key(et),
                    "name": str(et.name or ""),
                    "icon": str(et.icon or "document"),
                    "fields": fields,
                    "base_fields": base_fields,
                    "required_tag_groups": rtg,
                }
            )
        et_list = _dedupe_specs_by_key(et_list, key_field="key", slug_keys=True)

        # Build views list — dedupe template vs per-track materializations.
        v_by_key: Dict[str, View] = {}
        for v in views:
            vk = _view_manifest_key(v)
            if not vk:
                continue
            prev = v_by_key.get(vk)
            if prev is None or _view_sync_precedence(v) > _view_sync_precedence(prev):
                v_by_key[vk] = v

        v_list = []
        for v in v_by_key.values():
            cfg = v.config or {}
            v_list.append(
                {
                    "key": str(
                        cfg.get("_manifest_view_key") or _slug(str(v.name or ""))
                    ),
                    "name": str(v.name or ""),
                    "view_type": str(v.type or "feed"),
                    "is_default": bool(v.is_default),
                    "entry_types": list(getattr(v, "entry_type_keys", None) or []),
                    "default_entry_type": str(
                        getattr(v, "default_entry_type_key", "") or ""
                    ),
                    "filters": list(cfg.get("filters", [])),
                    "sort": list(cfg.get("sort", [])),
                    "group_by": cfg.get("group_by"),
                    "layout": cfg.get("layout", {}),
                    "field_visibility": list(cfg.get("field_visibility", [])),
                    "kanban_columns": list(cfg.get("kanban_columns", [])),
                    "calendar_mapping": cfg.get("calendar_mapping", {}),
                    "config": {
                        k: v
                        for k, v in cfg.items()
                        if k
                        not in {
                            "_manifest_view_key",
                            "view_type",
                            "filters",
                            "sort",
                            "group_by",
                            "layout",
                            "field_visibility",
                            "kanban_columns",
                            "calendar_mapping",
                        }
                    },
                }
            )
        v_list = _dedupe_specs_by_key(v_list, key_field="key", slug_keys=True)

        # Build taxonomy from tags
        tag_groups_map: Dict[str, List[Dict[str, Any]]] = {}
        for t in tags:
            gkey = str(getattr(t, "group_key", None) or "default")
            tag_entry = {
                "key": _slug(str(t.name or "")),
                "name": str(t.name or ""),
                "color": str(t.color or "#6B7280"),
                "aliases": list(getattr(t, "aliases", None) or []),
                "applies_to": list(getattr(t, "applies_to_entry_types", None) or []),
            }
            parent_key = getattr(t, "parent_tag_id", None)
            if parent_key:
                # Resolve parent key by looking up parent tag
                parent_tag = await Tag.get(str(parent_key))
                if parent_tag:
                    tag_entry["parent_key"] = _slug(str(parent_tag.name or ""))
            tag_groups_map.setdefault(gkey, []).append(tag_entry)

        tag_groups = []
        for gkey, tags_list in tag_groups_map.items():
            tag_groups.append({"key": gkey, "tags": tags_list})

        # Reconcile defaults against rebuilt tier keys. Blindly preserving
        # ``old_defaults`` leaves dangling ``default_entry_type`` references
        # (e.g. ``content_piece``) when EntryType nodes are not CONTAINS-linked
        # to this CP — the next ``compile_canonical_manifest`` call then fails
        # and blocks entry create / runtime profile resolution.
        old_defaults = (existing.get("track") or {}).get("defaults", {})
        defaults = dict(old_defaults) if isinstance(old_defaults, dict) else {}
        entry_keys = {
            _slug(str(et.get("key") or ""))
            for et in et_list
            if str(et.get("key") or "").strip()
        }
        view_keys = {
            _slug(str(v.get("key") or ""))
            for v in v_list
            if str(v.get("key") or "").strip()
        }
        det = defaults.get("default_entry_type")
        if det is not None and str(det).strip():
            want = _slug(str(det))
            if want not in entry_keys:
                if entry_keys:
                    defaults["default_entry_type"] = sorted(entry_keys)[0]
                else:
                    defaults.pop("default_entry_type", None)
        dv = defaults.get("default_view")
        if dv is not None and str(dv).strip():
            want = _slug(str(dv))
            if want not in view_keys:
                if view_keys:
                    defaults["default_view"] = sorted(view_keys)[0]
                else:
                    defaults.pop("default_view", None)

        manifest = {
            "content_profile_schema_version": 2,
            "scope": "track",
            "track": {
                "entry_types": et_list,
                "views": v_list,
                "taxonomy": {"tag_groups": tag_groups},
                "defaults": defaults,
            },
            "package": saved_package,
            "migrations": saved_migrations,
        }
        if saved_field_types:
            manifest["field_types"] = list(saved_field_types)
        if saved_view_types:
            manifest["view_types"] = list(saved_view_types)
        if saved_plugins:
            manifest["plugins"] = list(saved_plugins)

    elif scope == "app":
        # --- rebuild app_node tier from track-template profiles ---
        template_cps = await content_profile.nodes(
            edge=["DEFINES_TRACK_PROFILE"], node=["ContentProfile"]
        )

        tracks_list = []
        for tcp in template_cps:
            tcp_manifest = tcp.manifest or {}
            tcp_tier = tcp_manifest.get("track", {})
            tracks_list.append(
                {
                    "key": str(
                        tcp_manifest.get("package", {}).get("name")
                        or _slug(str(tcp.name or ""))
                    ),
                    "name": str(tcp.name or ""),
                    "provision_on_create": bool(
                        (tcp_manifest.get("package") or {}).get(
                            "provision_on_create", False
                        )
                    ),
                    "entry_types": list(tcp_tier.get("entry_types", [])),
                    "views": list(tcp_tier.get("views", [])),
                    "taxonomy": dict(tcp_tier.get("taxonomy", {})),
                }
            )

        old_defaults = (existing.get("app") or {}).get("defaults", {})
        old_relations = (existing.get("app") or {}).get("relations", [])

        manifest = {
            "content_profile_schema_version": 2,
            "scope": "app_node",
            "app": {
                "tracks": tracks_list,
                "relations": old_relations,
                "defaults": old_defaults,
            },
            "package": saved_package,
            "migrations": saved_migrations,
        }
        if saved_field_types:
            manifest["field_types"] = list(saved_field_types)
        if saved_view_types:
            manifest["view_types"] = list(saved_view_types)
        if saved_plugins:
            manifest["plugins"] = list(saved_plugins)

    else:
        # Unknown scope — don't clobber
        return

    content_profile.manifest = manifest
    content_profile.updated_at = datetime.now(timezone.utc).isoformat()
    await content_profile.save()
