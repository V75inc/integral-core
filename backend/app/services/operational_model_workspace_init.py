"""Strict-init multi-app provisioning from a scope=workspace bundle (Phase D3).

Single transactional unit per I-WSINIT-02. Pre-flight runs all validation
before any write; mid-write failure rolls back every node created in this
init via cascade-delete on the Apps that were materialized.

Pipeline:

    1. Pre-flight (no writes)
        - Bundle exists in ``load_library_operational_models()``.
        - ``manifest.scope == "workspace"``.
        - Slug not already present in ``workspace.applied_profiles``.
        - Bundle signature verified (matches loader's gate).
        - ``workspace.apps[]`` is non-empty.

    2. Writes (single unit)
        - For each App in declaration order:
            * Create ``App`` node with ``source_operational_model_slug = bundle_slug``.
            * Wire the workspace branch via ``catalog_app`` (I-GRAPH-01 known
              wiring helper — attaches the App under the workspace's ``Apps``
              registry and ensures the attached OperationalModel).
            * Apply the sub-manifest to the App's attached OperationalModel
              via ``merge_library_manifest_into_operational_model`` and
              ``provision_prescribed_tracks_from_app_manifest`` — same
              merge path ``install_app`` uses (G1).
        - Append ``{slug, version, applied_at}`` to
          ``workspace.applied_profiles`` and save.

    3. On mid-write failure
        - Cascade-delete every App created in this init (best-effort).
        - Raise ``WorkspaceInitFailed`` wrapping the underlying exception.

D4 wires this service into ``POST /workspaces?operational_model_slug=<slug>``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class WorkspaceInitValidationError(Exception):
    """Bundle metadata or shape failed pre-flight validation."""


class WorkspaceInitConflict(Exception):
    """Bundle already applied to this workspace."""


class WorkspaceInitFailed(Exception):
    """Mid-write failure during provisioning; rollback was attempted."""


@dataclass
class WorkspaceInitResult:
    """Summary of nodes created during a successful strict-init pass."""

    workspace_id: str
    apps_created: List[str] = field(default_factory=list)
    tracks_created: List[str] = field(default_factory=list)
    relations_created: List[str] = field(default_factory=list)


async def init_workspace_from_profile(
    workspace: Any,
    operational_model_slug: str,
    actor_id: str,
) -> WorkspaceInitResult:
    """Provision all apps declared by a scope=workspace bundle.

    Args:
        workspace: A live ``Workspace`` node — must already exist and be
            saved (this is the resource the bundle is being applied to).
        operational_model_slug: Library bundle slug to provision from.
        actor_id: User id recorded as ``App.owner_user_id`` for each
            created App.

    Returns:
        ``WorkspaceInitResult`` with the ids of nodes created.

    Raises:
        WorkspaceInitValidationError: Bundle missing, wrong scope, empty
            apps declaration, or signature failed.
        WorkspaceInitConflict: Workspace already has an
            ``applied_profiles`` entry for ``operational_model_slug``.
        WorkspaceInitFailed: Mid-write failure; rollback was attempted.
    """
    # Import inside the function so test monkey-patches on the loader
    # module land on the resolved symbol (Step 9 in the D3 plan).
    from app.services.operational_model_loader import load_library_operational_models

    spec = next(
        (
            s
            for s in load_library_operational_models()
            if s.slug == operational_model_slug
        ),
        None,
    )
    if spec is None:
        raise WorkspaceInitValidationError(
            f"bundle '{operational_model_slug}' not found"
        )

    manifest = spec.manifest or {}
    if manifest.get("scope") != "workspace":
        raise WorkspaceInitValidationError(
            f"bundle '{operational_model_slug}' is not scope=workspace"
        )

    if any(
        (e or {}).get("slug") == operational_model_slug
        for e in (workspace.applied_profiles or [])
    ):
        raise WorkspaceInitConflict(
            f"workspace already provisioned from '{operational_model_slug}'"
        )

    if not spec.signature_verified:
        raise WorkspaceInitValidationError(
            f"bundle '{operational_model_slug}' signature failed "
            f"({spec.signature_reason})"
        )

    ws_block = manifest.get("workspace") or {}
    apps_decl = ws_block.get("apps") or []
    if not apps_decl:
        raise WorkspaceInitValidationError(
            f"bundle '{operational_model_slug}' workspace.apps[] is empty"
        )

    result = WorkspaceInitResult(workspace_id=workspace.id)
    apps_created_nodes: List[Any] = []
    # G4: track bundle-slug → App node so cross_app_relations[] can resolve
    # source / target app by the slug used in the manifest.
    slug_to_app: Dict[str, Any] = {}

    try:
        for app_entry in apps_decl:
            inline = _resolve_sub_manifest(app_entry, spec)
            app_name = str(app_entry.get("name") or app_entry.get("slug") or "App")
            app_source_slug = str(app_entry.get("slug") or "").strip()
            operational_model_ref = app_entry.get("operational_model_ref")
            if (
                not app_source_slug
                and isinstance(operational_model_ref, str)
                and not operational_model_ref.startswith("./")
            ):
                app_source_slug = operational_model_ref.strip()
            app = await _create_workspace_app(
                workspace=workspace,
                name=app_name,
                actor_id=actor_id,
                source_operational_model_slug=app_source_slug or operational_model_slug,
            )
            apps_created_nodes.append(app)
            result.apps_created.append(app.id)
            app_slug = str(app_entry.get("slug") or "").strip()
            if app_slug:
                slug_to_app[app_slug] = app

            if inline:
                bundle_dir = spec.bundle_dir
                if app_source_slug and app_source_slug != operational_model_slug:
                    from app.services.operational_model_loader import (
                        load_library_operational_models,
                    )

                    sibling = next(
                        (
                            s
                            for s in load_library_operational_models()
                            if s.slug == app_source_slug
                        ),
                        None,
                    )
                    if sibling and sibling.bundle_dir:
                        bundle_dir = sibling.bundle_dir
                await _apply_app_submanifest(
                    app=app,
                    manifest=inline,
                    actor_id=actor_id,
                    bundle_dir=bundle_dir,
                )

        # G4: materialize cross_app_relations[] into relation fields on the
        # source EntryType's form_schema. This is the cross-App equivalent of
        # ``operational_model_merge.materialize_app_relations`` (which handles
        # same-App relations declared at ``app.relations[]``).
        cross_app_rels = ws_block.get("cross_app_relations") or []
        if cross_app_rels:
            relation_keys = await _materialize_cross_app_relations(
                cross_app_relations=cross_app_rels,
                slug_to_app=slug_to_app,
            )
            result.relations_created.extend(relation_keys)

        workspace.applied_profiles = list(workspace.applied_profiles or []) + [
            {
                "slug": operational_model_slug,
                "version": spec.version,
                "applied_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
        await workspace.save()

        return result
    except (WorkspaceInitValidationError, WorkspaceInitConflict):
        raise
    except Exception as exc:
        logger.exception(
            "workspace init failed for '%s' on workspace %s; rolling back",
            operational_model_slug,
            workspace.id,
        )
        for app in apps_created_nodes:
            try:
                await app.delete()
            except Exception:
                logger.exception(
                    "workspace init rollback: delete of app %s failed",
                    getattr(app, "id", "?"),
                )
        raise WorkspaceInitFailed(str(exc)) from exc


def _resolve_sub_manifest(
    app_entry: Dict[str, Any], spec: Any
) -> Dict[str, Any] | None:
    """Resolve an app entry's sub-manifest from inline / operational_model_ref.

    ``profile`` (inline) takes precedence. ``operational_model_ref`` may be:
      - ``"./relative/path.yaml"`` — read from disk under ``spec.bundle_dir``.
      - ``"sibling-slug"`` — resolved from ``load_library_operational_models()``.

    Returns the manifest dict or ``None`` when no sub-manifest is declared.
    """
    from app.services.operational_model_loader import load_library_operational_models

    inline = app_entry.get("profile")
    if inline:
        return inline

    operational_model_ref = app_entry.get("operational_model_ref")
    if not operational_model_ref:
        return None

    if isinstance(operational_model_ref, str) and operational_model_ref.startswith(
        "./"
    ):
        rel = operational_model_ref[2:]
        if spec.bundle_dir is None:
            raise WorkspaceInitValidationError(
                f"bundle '{spec.slug}' app '{app_entry.get('slug')}' "
                f"declares relative operational_model_ref but bundle has no bundle_dir"
            )
        import yaml as _yaml

        sub_path = spec.bundle_dir / rel
        return _yaml.safe_load(sub_path.read_text(encoding="utf-8"))

    sibling = next(
        (
            s
            for s in load_library_operational_models()
            if s.slug == operational_model_ref
        ),
        None,
    )
    if sibling is None:
        raise WorkspaceInitValidationError(
            f"app '{app_entry.get('slug')}' operational_model_ref "
            f"'{operational_model_ref}' not found"
        )
    return sibling.manifest


async def _create_workspace_app(
    *,
    workspace: Any,
    name: str,
    actor_id: str,
    source_operational_model_slug: str,
) -> Any:
    """Create an App node in the workspace and wire its structural edge.

    Uses ``catalog_app`` (a known I-GRAPH-01 wiring helper) which attaches
    the new App under the workspace's ``Apps`` registry via ``CATALOGS``
    and provisions the attached OperationalModel in the same call.

    Also wires the actor's ``OWNS`` edge so the App is visible through the
    standard access-resolution path (``resolve_role`` for resource_type
    ``"app"`` does NOT cascade from workspace membership — see permissions
    rule 2). Mirrors the pattern in ``app_lifecycle.py`` for non-bundle
    App creation.
    """
    from app.models.nodes import App
    from app.services.app_graph import catalog_app, wire_app_owner

    now = datetime.now(timezone.utc).isoformat()
    app = await App.create(
        name=name,
        name_fold=name.casefold(),
        owner_user_id=actor_id,
        workspace_id=workspace.id,
        source_operational_model_slug=source_operational_model_slug,
        visibility="private",
        created_at=now,
        updated_at=now,
    )
    await wire_app_owner(app, actor_id, workspace_id=workspace.id)
    await catalog_app(app)
    return app


async def _apply_app_submanifest(
    *,
    app: Any,
    manifest: Dict[str, Any],
    actor_id: str,
    bundle_dir: Any = None,
) -> None:
    """Apply a scope=app sub-manifest to a just-created App.

    Materializes tracks, entry types, tags, and views under the App's
    attached OperationalModel via the same merge-library + track
    provisioning path that ``install_app`` uses (canonical reference
    flow in ``app_lifecycle.install_app`` steps 5–6). On failure the
    exception propagates so ``init_workspace_from_profile`` rolls back
    the App (cascade-delete of materialized tracks via ``app.delete()``).

    Also snapshots the source manifest on ``App.metadata.source_manifest``
    for audit and potential re-apply.
    """
    if not manifest:
        return

    from app.services.app_graph import get_app_attached_operational_model
    from app.services.app_install import plant_seeds_for_install
    from app.services.operational_model_merge import (
        _InMemoryLibraryManifest,
        merge_library_manifest_into_operational_model,
        provision_prescribed_tracks_from_app_manifest,
    )
    from app.services.operational_model_runtime import SCHEMA_VERSION

    try:
        attached_cp = await get_app_attached_operational_model(app)
        if attached_cp is None:
            raise RuntimeError(
                f"app {app.id} has no attached operational model to merge into"
            )

        # Sub-manifests inside ``workspace.apps[].profile`` (inline) or in
        # sibling library bundles arrive without the explicit schema-version
        # stamp the canonical validator requires. The loader stamps it on
        # the top-level workspace bundle but does not recurse. Inject it
        # here so the merge path's ``compile_canonical_manifest`` accepts.
        normalized = dict(manifest)
        if "operational_model_schema_version" not in normalized:
            normalized["operational_model_schema_version"] = SCHEMA_VERSION

        shim = _InMemoryLibraryManifest(normalized)
        await merge_library_manifest_into_operational_model(
            shim, attached_cp, track=None, for_space=True
        )

        # Materialize Tracks declared by ``app.tracks[].provision_on_create``
        # (same call install_app uses via _materialize_tracks_for_app).
        await provision_prescribed_tracks_from_app_manifest(app, actor_id)

        # Snapshot the applied manifest for audit / re-apply.
        md = dict(getattr(app, "metadata", None) or {})
        md["source_manifest"] = manifest
        if bundle_dir is not None:
            md["bundle_dir_path"] = str(bundle_dir)
        app.metadata = md
        await app.save()

        from app.agentive.workspace_agent_profile import invalidate_workspace_profile
        from app.services.app_lifecycle import sync_operational_layer_from_manifest
        from app.services.operational_model_compile import compile_canonical_manifest

        canonical = compile_canonical_manifest(manifest=normalized)
        await sync_operational_layer_from_manifest(app, canonical, actor_id=actor_id)
        # Workspace bundles are the same install surface as the app picker:
        # their referenced apps must receive their declared demo/configuration
        # seeds too. Without this, a suite can look installed while its
        # Company Profile, calendar, and roster remain empty.
        await plant_seeds_for_install(
            app,
            canonical,
            actor_id,
            include_seed_data=True,
        )
        invalidate_workspace_profile(app.workspace_id)
    except Exception:
        logger.exception(
            "merge sub-manifest into app %s failed", getattr(app, "id", "?")
        )
        raise


async def _materialize_cross_app_relations(
    *,
    cross_app_relations: List[Dict[str, Any]],
    slug_to_app: Dict[str, Any],
) -> List[str]:
    """Inject relation fields on source EntryTypes for each cross-App relation.

    For each declared ``workspace.cross_app_relations[]`` entry:

      1. Resolve ``source.app`` / ``target.app`` via the slug → App map
         built during this workspace-init pass.
      2. Walk ``App —CONTAINS→ Track`` (filtered by ``Track.template_id``)
         to find source + target tracks declared in each App's
         sub-manifest.
      3. Walk ``Track —HAS_OPERATIONAL_MODEL→ OperationalModel —CONTAINS→
         EntryType`` (case-folded name lookup) to find the actual
         EntryType nodes.
      4. Append a ``relation`` field to the source EntryType's
         ``form_schema.fields`` carrying ``target_app=<target_app.id>``,
         ``allow_cross_app=True``, plus ``target_entry_types`` and
         ``target_track_types``. The cross-App runtime
         (``app/services/relation_runtime.py``) already understands these
         flags — no validator changes needed.

    Returns the list of relation keys actually materialized (skips ones
    that already exist on the source EntryType, mirroring
    ``materialize_app_relations`` idempotence).

    Mirrors the same-App contract in
    ``operational_model_merge.materialize_app_relations`` so future
    refactoring can collapse both into one resolver.
    """
    from app.models.edges import CONTAINS
    from app.services.app_graph import get_track_attached_operational_model
    from app.services.operational_model_runtime import (
        normalize_entry_type_form_schema,
        slug_manifest_key,
    )

    created: List[str] = []
    if not cross_app_relations:
        return created

    now = datetime.now(timezone.utc).isoformat()

    for rel in cross_app_relations:
        if not isinstance(rel, dict):
            continue
        src = rel.get("source") or {}
        tgt = rel.get("target") or {}
        if not isinstance(src, dict) or not isinstance(tgt, dict):
            continue

        src_app_slug = str(src.get("app") or "").strip()
        tgt_app_slug = str(tgt.get("app") or "").strip()
        src_app = slug_to_app.get(src_app_slug)
        tgt_app = slug_to_app.get(tgt_app_slug)
        if not src_app or not tgt_app:
            logger.warning(
                "cross_app_relations: could not resolve source/target app "
                "(src=%r, tgt=%r) — skipping relation %r",
                src_app_slug,
                tgt_app_slug,
                rel.get("key"),
            )
            continue

        src_track_key = slug_manifest_key(str(src.get("track") or ""))
        tgt_track_key = slug_manifest_key(str(tgt.get("track") or ""))
        if not src_track_key or not tgt_track_key:
            continue

        src_track = await _find_track_by_template(src_app, src_track_key)
        tgt_track = await _find_track_by_template(tgt_app, tgt_track_key)
        if not src_track or not tgt_track:
            logger.warning(
                "cross_app_relations: could not resolve track "
                "(src_track=%r, tgt_track=%r) — skipping relation %r",
                src_track_key,
                tgt_track_key,
                rel.get("key"),
            )
            continue

        src_tcp = await get_track_attached_operational_model(src_track)
        tgt_tcp = await get_track_attached_operational_model(tgt_track)
        if not src_tcp or not tgt_tcp:
            continue

        src_et_key = slug_manifest_key(str(src.get("entry_type") or ""))
        tgt_et_key = slug_manifest_key(str(tgt.get("entry_type") or ""))

        src_et = await _find_entry_type_by_key(src_tcp, src_et_key)
        if not src_et:
            logger.warning(
                "cross_app_relations: could not resolve source entry type "
                "%r under track %r — skipping relation %r",
                src_et_key,
                src_track_key,
                rel.get("key"),
            )
            continue

        # Target entry-type keys: prefer the explicit ``target.entry_type``
        # when supplied; otherwise expose every EntryType under the target
        # track so the relation can point at any of them.
        tgt_et_keys: List[str] = []
        if tgt_et_key:
            tgt_et_keys = [tgt_et_key]
        else:
            for tet in await tgt_tcp.nodes(edge=[CONTAINS], node=["EntryType"]):
                k = slug_manifest_key(str(getattr(tet, "name", "") or ""))
                if k:
                    tgt_et_keys.append(k)

        rel_key = str(rel.get("key") or "").strip()
        field_key = (
            str(src.get("field") or "").strip()
            or rel_key
            or (
                f"rel_{src_app_slug}_{src_et_key}_to_"
                f"{tgt_app_slug}_{tgt_et_key or 'any'}"
            )
        )
        field_name = str(rel.get("name") or field_key)

        schema = src_et.form_schema or {}
        fields = list(schema.get("fields") or [])
        if any(
            isinstance(f, dict)
            and str(f.get("key") or "") == field_key
            and str(f.get("type") or "") == "relation"
            for f in fields
        ):
            # Already materialized (e.g. the source sub-manifest declared
            # the relation field inline). Skip — manifest wins, mirrors
            # ``materialize_app_relations``.
            continue

        relation_field: Dict[str, Any] = {
            "key": field_key,
            "name": field_name,
            "type": "relation",
            "required": False,
            "readonly": False,
            "default": None,
            "enum": [],
            "relation": {
                "target_entry_types": tgt_et_keys,
                "target_track_types": [tgt_track_key],
                "allow_cross_track": True,
                "allow_cross_app": True,
                "target_app": tgt_app.id,
                "many": bool(rel.get("many", False)),
                "inverse_field": rel.get("inverse_field"),
            },
        }
        fields.append(relation_field)
        schema["fields"] = fields
        src_et.form_schema = normalize_entry_type_form_schema(schema)
        src_et.updated_at = now
        await src_et.save()
        if rel_key:
            created.append(rel_key)
        else:
            created.append(field_key)

    return created


async def _find_track_by_template(app: Any, template_key: str) -> Any:
    """Find an App-contained Track whose ``template_id`` slug matches ``template_key``."""
    from app.services.operational_model_runtime import slug_manifest_key

    tracks = await app.nodes(edge=["CONTAINS"], node=["Track"])
    for t in tracks:
        tmpl = str(getattr(t, "template_id", "") or "").strip()
        if tmpl and slug_manifest_key(tmpl) == template_key:
            return t
    return None


async def _find_entry_type_by_key(operational_model: Any, et_key: str) -> Any:
    """Find a OperationalModel-contained EntryType whose case-folded name matches ``et_key``."""
    from app.models.edges import CONTAINS
    from app.services.operational_model_runtime import slug_manifest_key

    if not et_key:
        return None
    entry_types = await operational_model.nodes(edge=[CONTAINS], node=["EntryType"])
    for et in entry_types:
        name = str(getattr(et, "name", "") or "")
        if name and slug_manifest_key(name) == et_key:
            return et
    return None
