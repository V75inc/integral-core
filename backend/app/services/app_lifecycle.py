"""App install / settings / uninstall lifecycle state machine.

Phase 10 Plan 10-05 (APP-LIFECYCLE-01 / APP-SETTINGS-01 / APP-SEEDS-01).

This module owns the atomic install transaction per ``app_bundles_v1.md §9``
and Architectural Decision 3 (hybrid pre-flight + compensating actions).

**Pipeline (install transaction — 12 steps per §9.1):**

  1. Validate manifest via ``compile_canonical_manifest`` (Plan 10-03).
  2. Check ``requires_apps[]`` against the target Workspace.
  3. Cross-App resolution check (Plan 10-06 owns the full impl; this plan
     stubs the call — passes if no cross-App relations declared).
  4. Create App node with ``lifecycle_state="installing"``.
  5. Materialize Tracks per ``app.tracks[]`` (provision_on_create=True).
  6. Apply taxonomy + views via ``merge_library_manifest_into_operational_model``.
  7. Register skills (``skill_registry.register_skill`` per skill — Plan 10-04).
  8. Register agents (``uplink_registry.register_app_agent`` per agent — Plan 10-04).
  9. **Pause point** — if ``settings_schema`` declared and caller did not
     pre-supply settings: set ``lifecycle_state="awaiting_settings"``, mint
     install_token, return 202.
 10. Persist user-submitted settings (validated via ``jsonschema``).
 11. Plant seeds (idempotent by deterministic ``seed.id``).
 12. Mark ``lifecycle_state="active"``, ``installed_at=now``, emit
     ``app.installed`` ChangeEvent (single emission per D-05).

**Compensation on failure** — ``InstallTransaction.compensate()`` runs
recorded undo functions in reverse order. Each compensation is wrapped in
try/except so a failed compensation does NOT prevent earlier-step
compensations from running. Compensations are also idempotent — if a step
never ran, its compensation is a no-op (the compensation isn't recorded
until the corresponding step succeeds).

**Single-emission D-05 invariant** — ``app.installed`` is emitted ONLY at
step 12 (final transition to active). ``app.uninstalled`` is emitted ONLY
on the uninstall happy path. Dependents and blocking cross-App references
always reject uninstall (no force bypass). Reaper-driven uninstalls of
abandoned ``awaiting_settings`` Apps emit ``app.uninstalled`` with
``details.reason="settings_pause_timeout"``.

See ``backend/tests/test_app_lifecycle.py`` for the regression suite.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.api.utils import export_node
from app.exceptions import (
    AppInstallError,
    AppLifecycleStateError,
    AppUninstallBlockedError,
    BadRequestError,
)
from app.models.nodes import (
    App,
    Entry,
    OperationalModel,
    Track,
    Workspace,
)
from app.services import app_install
from app.services.app_graph import (
    catalog_app,
    get_app_attached_operational_model,
    wire_app_owner,
)
from app.services.app_install_token import (
    issue_install_token,
    verify_install_token,
)
from app.services.application_definitions import (
    compile_application_definition,
    get_active_application_definition,
    verify_definition_materialization,
)
from app.services.change_event import emit_change_event
from app.services.operational_model_merge import (
    merge_library_manifest_into_operational_model,
)
from app.services.operational_model_runtime import (
    compile_canonical_manifest,
    slug_manifest_key,
)
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


async def enqueue_install_work(
    *,
    workspace_id: str,
    library_cp_id: str,
    actor_id: str,
    settings: Optional[Dict[str, Any]] = None,
    include_seed_data: bool = True,
) -> Dict[str, Any]:
    """Queue an idempotent package install for the leased lifecycle worker."""
    from app.agentive.services.work_items import enqueue_work_item

    work = await enqueue_work_item(
        kind="app_lifecycle",
        origin="app_lifecycle",
        principal_id=actor_id,
        workspace_id=workspace_id,
        idempotency_key=f"install:{library_cp_id}",
        input_payload={
            "action": "install",
            "library_cp_id": library_cp_id,
            "settings": dict(settings or {}),
            "include_seed_data": include_seed_data,
        },
        plan={"action": "install", "library_cp_id": library_cp_id},
        remaining_obligations=[{"kind": "lifecycle_completion", "action": "install"}],
    )
    return {"status": work.status, "work_item_id": work.work_item_id}


async def enqueue_upgrade_work(
    *, app_node: App, actor_id: str, version: Optional[str] = None
) -> Dict[str, Any]:
    """Queue an idempotent package upgrade bound to the active definition."""
    from app.agentive.services.work_items import enqueue_work_item

    work = await enqueue_work_item(
        kind="app_lifecycle",
        origin="app_lifecycle",
        principal_id=actor_id,
        workspace_id=app_node.workspace_id,
        app_id=app_node.id,
        definition_id=app_node.active_definition_id or None,
        idempotency_key=f"upgrade:{app_node.id}:{app_node.active_definition_revision}",
        input_payload={"action": "upgrade", "version": version or ""},
        plan={"action": "upgrade", "app_id": app_node.id},
        remaining_obligations=[{"kind": "lifecycle_completion", "action": "upgrade"}],
    )
    return {"status": work.status, "work_item_id": work.work_item_id}


async def enqueue_app_lifecycle_work(
    *,
    app_node: App,
    actor_id: str,
    action: str,
    archive: bool = True,
) -> Dict[str, Any]:
    """Queue an App-bound lifecycle transition under its active revision."""
    from app.agentive.services.work_items import enqueue_work_item

    work = await enqueue_work_item(
        kind="app_lifecycle",
        origin="app_lifecycle",
        principal_id=actor_id,
        workspace_id=app_node.workspace_id,
        app_id=app_node.id,
        definition_id=app_node.active_definition_id or None,
        idempotency_key=f"{action}:{app_node.id}:{app_node.active_definition_revision}",
        input_payload={"action": action, "archive": archive},
        plan={"action": action, "app_id": app_node.id},
        remaining_obligations=[{"kind": "lifecycle_completion", "action": action}],
    )
    return {"status": work.status, "work_item_id": work.work_item_id}


async def enqueue_finalize_install_work(
    *, app_node: App, actor_id: str, install_token: str, settings: Dict[str, Any]
) -> Dict[str, Any]:
    """Queue the settings-gated final installation transition."""
    from app.agentive.services.work_items import enqueue_work_item

    work = await enqueue_work_item(
        kind="app_lifecycle",
        origin="app_lifecycle",
        principal_id=actor_id,
        workspace_id=app_node.workspace_id,
        app_id=app_node.id,
        definition_id=app_node.active_definition_id or None,
        idempotency_key=f"finalize_install:{app_node.id}:{app_node.active_definition_revision}",
        input_payload={
            "action": "finalize_install",
            "install_token": install_token,
            "settings": dict(settings),
        },
        plan={"action": "finalize_install", "app_id": app_node.id},
        remaining_obligations=[
            {"kind": "lifecycle_completion", "action": "finalize_install"}
        ],
    )
    return {"status": work.status, "work_item_id": work.work_item_id}


# ---------------------------------------------------------------------------
# InstallTransaction context manager
# ---------------------------------------------------------------------------


class InstallTransaction:
    """Records install steps + compensation functions; reverses on failure.

    Per Architectural Decision 3 (Option C — hybrid pre-flight + compensating
    actions). The transaction is NOT a true database transaction (jvspatial
    has no transaction primitive); it's a saga-style record of recorded undo
    operations.

    F0: also writes ``InstallAttempt`` Object checkpoints (I-GRAPH-02) so
    failed installs leave an admin-visible reason without DB diving.
    """

    def __init__(
        self,
        workspace_id: str,
        actor_id: str,
        *,
        package_slug: str = "",
        app_id: str = "",
    ) -> None:
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.package_slug = package_slug
        self.app_id = app_id
        self._steps: List[Tuple[str, Callable[[], Awaitable[None]]]] = []

    def record(
        self,
        step_name: str,
        compensation: Callable[[], Awaitable[None]],
    ) -> None:
        """Record a step + its compensation. Call AFTER the step succeeds."""
        self._steps.append((step_name, compensation))

    async def checkpoint(
        self,
        step: str,
        *,
        status: str = "ok",
        error: str = "",
        app_id: Optional[str] = None,
    ) -> None:
        """Persist an InstallAttempt row for this step (best-effort)."""
        if app_id:
            self.app_id = app_id
        try:
            from app.models.install_attempt import InstallAttempt
            from app.utils.time import utc_now_iso

            await InstallAttempt.create(
                workspace_id=self.workspace_id,
                app_id=self.app_id or "",
                actor_id=self.actor_id,
                package_slug=self.package_slug or "",
                step=step,
                status=status,
                error=(error or "")[:2000],
                created_at=utc_now_iso(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("InstallAttempt checkpoint failed: %s", exc)

    async def compensate(self) -> int:
        """Run all recorded compensations in reverse order. Returns count run.

        Best-effort: each compensation is wrapped in try/except so a single
        failure logs + continues to the next. Never raises.
        """
        count = 0
        for step_name, fn in reversed(self._steps):
            try:
                await fn()
                count += 1
                logger.info("InstallTransaction.compensate: ran step=%s", step_name)
            except Exception as e:  # noqa: BLE001 — best-effort cleanup
                logger.warning(
                    "InstallTransaction.compensate: step=%s failed: %s",
                    step_name,
                    e,
                )
        await self.checkpoint("compensate", status="rolled_back")
        return count


# ---------------------------------------------------------------------------
# Bundle install helpers — canonical implementation in app_install.py
# ---------------------------------------------------------------------------

_parse_version_tuple = app_install.parse_version_tuple
_version_satisfies_min = app_install.version_satisfies_min
_app_dependency_index_keys = app_install.app_dependency_index_keys
_bundle_install_rank = app_install.bundle_install_rank
_bundle_identity_key = app_install.bundle_identity_key
_app_bundle_identity_keys = app_install.app_bundle_identity_keys
_app_matches_bundle_identity = app_install.app_matches_bundle_identity
_find_all_bundle_installs = app_install.find_all_bundle_installs
_purge_duplicate_bundle_installs = app_install.purge_duplicate_bundle_installs
_resolve_canonical_bundle_install = app_install.resolve_canonical_bundle_install
_find_existing_bundle_install = app_install.find_existing_bundle_install
_effective_app_version = app_install.effective_app_version
_check_requires_apps = app_install.check_requires_apps
_check_cross_app_resolution = app_install.check_cross_app_resolution
_validate_settings_against_schema = app_install.validate_settings_against_schema
_apply_schema_defaults = app_install.apply_schema_defaults
_seed_deterministic_id = app_install.seed_deterministic_id
_plant_seeds = app_install.plant_seeds
_plant_seeds_for_install = app_install.plant_seeds_for_install
_unplant_seeds = app_install.unplant_seeds
_resolve_include_seed_data = app_install.resolve_include_seed_data
_stash_install_include_seed_data = app_install.stash_install_include_seed_data
_materialize_tracks_for_app = app_install.materialize_tracks_for_app
_delete_tracks = app_install.delete_tracks

# ---------------------------------------------------------------------------
# Install entry points
# ---------------------------------------------------------------------------


async def install_app(
    *,
    workspace_id: str,
    library_cp_id: str,
    actor_id: str,
    settings: Optional[Dict[str, Any]] = None,
    name_override: Optional[str] = None,
    description_override: Optional[str] = None,
    include_seed_data: Optional[bool] = None,
) -> Dict[str, Any]:
    """Atomic install transaction — 12 steps per app_bundles_v1.md §9.1.

    Returns either:
      - ``{status: "active", app_id, installed_at, version}`` on full success.
      - ``{status: "awaiting_settings", app_id, install_token, settings_schema}``
        when the manifest declares ``settings_schema`` AND ``settings`` arg
        was not pre-supplied. Caller resumes via ``finalize_install``.

    Raises:
      - ``BadRequestError`` family on validation failures (manifest invalid,
        deps missing, settings invalid).
      - ``AppInstallError`` on transaction-level orchestration failure.

    Single-emission D-05: emits ``app.installed`` ChangeEvent exactly once,
    at step 12, only on the final transition to ``active``. Never emits on
    rollback / compensation paths.
    """
    if not workspace_id:
        raise BadRequestError(message="install_app: workspace_id is required")
    if not library_cp_id:
        raise BadRequestError(message="install_app: library_cp_id is required")

    # ---- Pre-flight (Step 1-3) — no compensations recorded yet ----
    library_cp = await OperationalModel.get(library_cp_id)
    if not library_cp:
        raise BadRequestError(
            message=f"Library OperationalModel {library_cp_id!r} not found",
            details={"library_cp_id": library_cp_id},
        )
    if not getattr(library_cp, "library_package", False):
        raise BadRequestError(
            message=f"OperationalModel {library_cp_id!r} is not a library package",
            details={"library_cp_id": library_cp_id},
        )
    from app.services.package_trust import assert_library_artifact_trusted

    assert_library_artifact_trusted(library_cp)

    manifest = library_cp.manifest or {}
    # Step 1: compile (raises OperationalModelV1RejectedError on v1).
    canonical = compile_canonical_manifest(manifest=manifest)

    # Step 2: requires_apps dependency check.
    await _check_requires_apps(canonical, workspace_id)

    # Step 3: cross-App resolution (Plan 10-06).
    await _check_cross_app_resolution(canonical, workspace_id)

    workspace = await Workspace.get(workspace_id)
    if not workspace:
        raise BadRequestError(
            message=f"Workspace {workspace_id!r} not found",
            details={"workspace_id": workspace_id},
        )

    package_meta = canonical.get("package") or {}
    lib_md = dict(getattr(library_cp, "metadata", None) or {})
    source_operational_model_slug = (
        str(lib_md.get("slug") or package_meta.get("slug") or "").strip() or None
    )
    # F3: commercial packages need an active workspace entitlement before install.
    entitlement_meta = {
        **package_meta,
        "slug": source_operational_model_slug or package_meta.get("slug") or "",
        "class": lib_md.get("package_class") or package_meta.get("class"),
    }
    from app.services.entitlements import require_active_entitlement

    await require_active_entitlement(
        workspace_id=workspace_id,
        package_meta=entitlement_meta,
    )

    library_display_name = str(getattr(library_cp, "name", "") or "").strip()
    existing_install = await _find_existing_bundle_install(
        workspace_id,
        source_operational_model_slug,
        library_cp_id,
        actor_id=actor_id,
        library_display_name=library_display_name,
        package_meta=package_meta,
    )
    if existing_install is not None:
        seed_pref = _resolve_include_seed_data(
            include_seed_data,
            app_node=existing_install,
            canonical=canonical,
        )
        if existing_install.lifecycle_state == "active":
            await wire_app_owner(
                existing_install,
                actor_id,
                workspace_id=workspace_id,
            )
            # Re-materialize before replanting: heals legacy tracks whose
            # EntryTypes pre-date manifest-key embedding and creates any
            # tracks a newer bundle version added — otherwise seed planting
            # fails against the stale subgraph (June 30 review R1).
            await _materialize_tracks_for_app(existing_install, actor_id)
            # Re-sync the operational layer too. Tracks were already healed
            # against a newer manifest above; skills, agents and hook bindings
            # were not, so a bundle edit reached a fresh install and never an
            # existing one. That asymmetry is silent and expensive to find:
            # editing a SKILL.md `allowed-tools`, restarting, and even bumping
            # the manifest version all left the persisted Skill node carrying
            # its original tool grant, so the running agent kept obeying an SOP
            # it no longer had the tools to satisfy.
            #
            # This is the function's documented job ("call after install,
            # library update, or merge when the App is (or will be) active")
            # and it upserts idempotently, so the reuse path is exactly where
            # it belongs.
            await sync_operational_layer_from_manifest(
                existing_install, canonical, actor_id=actor_id
            )
            await _plant_seeds_for_install(
                existing_install,
                canonical,
                actor_id,
                include_seed_data=seed_pref,
            )
            logger.info(
                "install_app: reusing active bundle install %s (slug=%r)",
                existing_install.id,
                source_operational_model_slug,
            )
            return {
                "status": "active",
                "app_id": existing_install.id,
                "installed_at": existing_install.installed_at,
                "version": existing_install.version,
            }
        if existing_install.lifecycle_state == "awaiting_settings":
            if include_seed_data is not None:
                _stash_install_include_seed_data(existing_install, seed_pref)
                existing_install.updated_at = utc_now_iso()
                await existing_install.save()
            if settings is not None:
                token = issue_install_token(existing_install.id)
                return await finalize_install(
                    app_id=existing_install.id,
                    install_token=token,
                    settings=settings,
                    actor_id=actor_id,
                )
            return {
                "status": "awaiting_settings",
                "app_id": existing_install.id,
                "install_token": issue_install_token(existing_install.id),
                "settings_schema": dict(
                    getattr(existing_install, "settings_schema", None) or {}
                ),
            }

    # ---- Transaction begins ----
    txn = InstallTransaction(
        workspace_id=workspace_id,
        actor_id=actor_id,
        package_slug=source_operational_model_slug or "",
    )
    try:
        # Step 4: Create App node with lifecycle_state="installing".
        canonical_app = canonical.get("app") or {}
        # Phase 32 — caller-supplied overrides take precedence over manifest
        # defaults; otherwise fall back to the library bundle's HUMAN display
        # name (OperationalModel.name, set from the YAML's package.name at
        # load time) — not manifest.package.name, which the loader has
        # rewritten to the slug. Falls through to package_meta as a last
        # resort for in-process manifests that bypass the library loader.
        library_display_name = str(getattr(library_cp, "name", "") or "").strip()
        default_name = library_display_name or str(
            package_meta.get("name") or package_meta.get("key") or "App"
        )
        app_name = (
            name_override.strip()
            if name_override and name_override.strip()
            else default_name
        )
        # Library bundles carry their display description on
        # OperationalModel.description (library-sync copies from
        # package.description; the loader strips it from manifest.package).
        # Fall back to canonical_app.description / package_meta.description
        # for in-process manifests that bypass the library loader.
        library_display_description = str(
            getattr(library_cp, "description", "") or ""
        ).strip()
        default_description = library_display_description or str(
            canonical_app.get("description") or package_meta.get("description") or ""
        )
        app_description = (
            description_override.strip()
            if description_override and description_override.strip()
            else default_description
        )
        app_version = package_meta.get("version") or getattr(
            library_cp, "version", None
        )
        settings_schema = dict(canonical_app.get("settings_schema") or {})
        lib_md = dict(getattr(library_cp, "metadata", None) or {})
        if not source_operational_model_slug:
            source_operational_model_slug = (
                str(lib_md.get("slug") or package_meta.get("slug") or "").strip()
                or None
            )
        now = utc_now_iso()
        app_node = await App.create(
            name=app_name,
            name_fold=app_name.casefold(),
            owner_user_id=actor_id,
            description=app_description,
            visibility="private",
            workspace_id=workspace_id,
            lifecycle_state="installing",
            settings_schema=settings_schema,
            installed_from_library_id=library_cp_id,
            source_operational_model_slug=source_operational_model_slug,
            version=str(app_version) if app_version is not None else None,
            installed_package_slug=source_operational_model_slug,
            installed_package_version=(
                str(app_version) if app_version is not None else None
            ),
            installed_artifact_fingerprint=str(lib_md.get("bundle_fingerprint") or "")
            or None,
            created_at=now,
            updated_at=now,
        )
        txn.app_id = app_node.id
        await txn.checkpoint("app_create", app_id=app_node.id)
        txn.record(
            "app_create",
            lambda app=app_node: _safe_destroy(app),  # type: ignore[misc, arg-type]
        )

        # OWNS edge + catalog registration. Unconditional: skipping the wire
        # when the actor did not resolve produced Apps nobody could administer.
        # `wire_app_owner` now falls back to the workspace owner and raises if
        # even that is impossible — the install rolls back via `txn` rather
        # than committing a permanently locked App.
        from app.services.permissions import get_user_node

        # Kept only to classify the emitted change event below. Ownership no
        # longer depends on it — the wire runs either way.
        actor_user = await get_user_node(actor_id) if actor_id else None
        await wire_app_owner(app_node, actor_id, workspace_id=workspace_id)
        await catalog_app(app_node)

        # WP-04: bind the install to an immutable, compiler-validated App
        # contract before anything is materialized from its mutable profile.
        definition = await compile_application_definition(
            app_node=app_node,
            manifest=canonical,
            source_operational_model_id=library_cp_id,
            base_package_manifest=canonical,
        )
        await txn.checkpoint("definition_compile")

        async def _delete_definition_on_rollback() -> None:
            await _safe_destroy(definition)

        txn.record(
            "definition_compile",
            _delete_definition_on_rollback,
        )

        # Step 5-6: Merge library manifest (creates EntryTypes, Tags, Views,
        # tracks materialization is included via provision_prescribed_tracks).
        attached_cp = await get_app_attached_operational_model(app_node)
        if not attached_cp:
            raise AppInstallError(
                message="App attached OperationalModel missing after create",
                details={"app_id": app_node.id},
            )
        await merge_library_manifest_into_operational_model(
            library_cp, attached_cp, track=None, for_space=True
        )

        # Materialize Tracks (uses the merged manifest).
        created_tracks = await _materialize_tracks_for_app(app_node, actor_id)
        txn.record(
            "tracks_create",
            lambda tracks=created_tracks: _delete_tracks(tracks),  # type: ignore[misc, arg-type]
        )

        # Step 7: Register skills (Plan 10-04).
        registered_skills = await _register_skills_from_manifest(app_node, canonical)
        txn.record(
            "skills_register",
            lambda app_id=app_node.id: _safe_unregister_skills(app_id),  # type: ignore[misc]
        )

        # Step 8: Register agents (Plan 10-04).
        registered_agents = await _register_agents_from_manifest(app_node, canonical)
        txn.record(
            "agents_register",
            lambda app_id=app_node.id: _safe_unregister_agents(app_id),  # type: ignore[misc]
        )

        # Step 9: Pause point — if settings_schema declared and caller did
        # NOT pre-supply settings, transition to awaiting_settings and
        # return 202 + install_token. D-05: no ChangeEvent yet — the
        # final settled state emits at step 12.
        if settings_schema and settings is None:
            app_node.lifecycle_state = "awaiting_settings"
            _stash_install_include_seed_data(
                app_node,
                _resolve_include_seed_data(
                    include_seed_data,
                    canonical=canonical,
                ),
            )
            app_node.updated_at = utc_now_iso()
            await app_node.save()
            token = issue_install_token(app_node.id)
            logger.info(
                "install_app: app %s paused at awaiting_settings (token issued)",
                app_node.id,
            )
            # Note: transaction stays "open" — but we return without
            # raising, so compensations don't fire. The reaper takes over
            # if the caller abandons the install past TTL.
            return {
                "status": "awaiting_settings",
                "app_id": app_node.id,
                "install_token": token,
                "settings_schema": settings_schema,
            }

        # Step 10: Apply settings (fill schema defaults, then validate).
        effective_settings = dict(settings or {})
        if settings_schema:
            effective_settings = _apply_schema_defaults(
                effective_settings, settings_schema
            )
            _validate_settings_against_schema(effective_settings, settings_schema)
        app_node.settings = effective_settings

        # Step 11: Plant seeds (optional per install preference).
        seed_pref = _resolve_include_seed_data(
            include_seed_data,
            app_node=app_node,
            canonical=canonical,
        )
        await _plant_seeds_for_install(
            app_node,
            canonical,
            actor_id,
            include_seed_data=seed_pref,
        )
        if seed_pref:
            txn.record(
                "seeds_plant",
                lambda app_id=app_node.id: _unplant_seeds(app_id),  # type: ignore[misc, arg-type]
            )

        # Phase 30 (DR-30-01 + DR-30-02) — register bundle tools + hooks
        # in the per-workspace registry. Best-effort: failure must NOT
        # block install (logged + swallowed).
        try:
            from app.services.hooks.install_hook import register_bundle_on_install

            bundle_dir = (
                str(
                    (getattr(library_cp, "metadata", None) or {}).get("bundle_dir_path")
                    or ""
                )
                or None
            )
            await register_bundle_on_install(
                workspace_id=app_node.workspace_id,
                canonical=canonical,
                bundle_dir=bundle_dir,
                app_id=app_node.id,
            )
        except Exception:
            logger.exception("hook framework registration failed; continuing")

        # Step 12: Mark active + emit single ChangeEvent.
        app_node.lifecycle_state = "active"
        app_node.installed_at = utc_now_iso()
        app_node.updated_at = app_node.installed_at
        await app_node.save()
        await verify_definition_materialization(
            app_node=app_node,
            definition=definition,
        )

        # ADR-012 — recompile catalogue now that lifecycle is active (bundle
        # register ran while state was still ``installing``).
        try:
            from app.services.capability_catalogue import compile_workspace_catalogue

            await compile_workspace_catalogue(app_node.workspace_id, activate=True)
        except Exception:
            logger.exception(
                "catalogue compile after activate failed for app %s", app_node.id
            )

        from app.agentive.workspace_agent_profile import invalidate_workspace_profile

        invalidate_workspace_profile(workspace_id)

        await emit_change_event(
            actor_kind="human" if actor_user else "system",
            actor_id=actor_id or "system",
            action="app.installed",
            resource_type="App",
            resource_id=app_node.id,
            before=None,
            after=await export_node(app_node),
            scope=f"app:{app_node.id}",
            details={
                "library_source_id": library_cp_id,
                "version": app_node.version,
                "skills_registered": len(registered_skills),
                "agents_registered": len(registered_agents),
            },
        )
        logger.info(
            "install_app: app %s installed (workspace=%s, library=%s, "
            "skills=%d, agents=%d)",
            app_node.id,
            workspace_id,
            library_cp_id,
            len(registered_skills),
            len(registered_agents),
        )
        return {
            "status": "active",
            "app_id": app_node.id,
            "installed_at": app_node.installed_at,
            "version": app_node.version,
        }

    except Exception as exc:
        # Mark App failed when it still exists, then compensate.
        try:
            if txn.app_id:
                failed = await App.get(txn.app_id)
                if failed is not None and failed.lifecycle_state == "installing":
                    failed.lifecycle_state = "failed"
                    failed.updated_at = utc_now_iso()
                    md = dict(getattr(failed, "metadata", None) or {})
                    md["install_error"] = str(exc)[:2000]
                    failed.metadata = md
                    await failed.save()
        except Exception:  # noqa: BLE001
            pass
        await txn.checkpoint(
            "install_failed",
            status="failed",
            error=str(exc),
        )
        # Compensation pass — best-effort cleanup of recorded steps.
        n = await txn.compensate()
        logger.warning(
            "install_app: transaction failed (%s); compensated %d step(s)",
            type(exc).__name__,
            n,
        )
        raise


def _skill_spec_for_registry(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Translate a compiled manifest skill spec → ``SkillRegisterRequest`` shape.

    Phase 10 Plan 10-07 fix: the manifest compiler emits keys per
    ``app_bundles_v1.md §4.2`` shape (``prompt_template``, ``parameters``);
    the Pydantic ``SkillRegisterRequest`` schema expects
    ``prompt_template_ref`` + ``parameters_schema``. Without this shim
    every install with a declarative skill 500s at step 7 (registration).

    Latent bug uncovered by Content Factory integration test (Plan 10-07).
    Plan 10-05's lifecycle tests dodged the gap by using ``_minimal_app_manifest``
    with no skills declared.
    """
    out = dict(spec)
    if "prompt_template" in out and "prompt_template_ref" not in out:
        out["prompt_template_ref"] = out.pop("prompt_template")
    if "parameters" in out and "parameters_schema" not in out:
        params = out.pop("parameters")
        if isinstance(params, dict):
            out["parameters_schema"] = params
    return out


async def _register_skills_from_manifest(
    app_node: App, canonical: Dict[str, Any]
) -> List[Any]:
    """Register every skill in the manifest under the App."""
    from app.agentive.services.skill_registry import register_skill

    app_spec = canonical.get("app") or {}
    bundle_tool_names = {
        str(tool.get("key") or "").strip()
        for tool in app_spec.get("tools") or []
        if isinstance(tool, dict) and str(tool.get("key") or "").strip()
    }

    skills_specs = app_spec.get("skills") or []
    if not skills_specs:
        # Track-scope manifests may declare skills under track.skills too.
        skills_specs = (canonical.get("track") or {}).get("skills") or []
    out: List[Any] = []
    for spec in skills_specs:
        if not isinstance(spec, dict):
            continue
        try:
            sk = await register_skill(
                app_id=app_node.id,
                workspace_id=app_node.workspace_id,
                skill_spec=_skill_spec_for_registry(spec),
                bundle_tool_names=bundle_tool_names,
            )
            out.append(sk)
        except Exception as e:
            logger.warning(
                "install: skill %r registration failed: %s",
                spec.get("key", "<unknown>"),
                e,
            )
            raise
    return out


def _manifest_skill_keys(canonical: Dict[str, Any]) -> set:
    """Return skill keys declared in a compiled manifest."""
    skills_specs = (canonical.get("app") or {}).get("skills") or []
    if not skills_specs:
        skills_specs = (canonical.get("track") or {}).get("skills") or []
    keys: set = set()
    for spec in skills_specs:
        if not isinstance(spec, dict):
            continue
        key = str(spec.get("key") or "").strip()
        if key:
            keys.add(key)
    return keys


async def _remove_stale_skills_from_manifest(
    app_node: App, canonical: Dict[str, Any]
) -> int:
    """Remove Skill nodes whose keys disappeared from the manifest."""
    from app.models.edges import CONTAINS

    manifest_keys = _manifest_skill_keys(canonical)
    skills = await app_node.nodes(edge=[CONTAINS], node=["Skill"])
    removed = 0
    for sk in skills:
        key = str(getattr(sk, "key", "") or "").strip()
        if key and key not in manifest_keys:
            try:
                await sk.delete(cascade=False)
            except Exception:
                logger.exception(
                    "sync_operational_layer: stale skill delete failed for %s",
                    getattr(sk, "id", ""),
                )
                continue
            removed += 1
    if removed:
        logger.info(
            "sync_operational_layer: removed %d stale skill(s) from App %s",
            removed,
            app_node.id,
        )
    return removed


async def sync_operational_layer_from_manifest(
    app_node: App,
    canonical: Dict[str, Any],
    *,
    actor_id: str,
) -> Dict[str, Any]:
    """Sync Skill nodes, agents, and hook registry from a compiled manifest.

    Idempotent upsert for skills/agents; replaces bundle hook registration
    for the App's workspace. Call after install, library update, or merge
    when the App is (or will be) active.
    """
    _ = actor_id  # reserved for audit hooks
    await _remove_stale_skills_from_manifest(app_node, canonical)
    registered_skills = await _register_skills_from_manifest(app_node, canonical)
    registered_agents = await _register_agents_from_manifest(app_node, canonical)
    try:
        from app.services.hooks.install_hook import register_bundle_on_install

        await register_bundle_on_install(
            workspace_id=app_node.workspace_id,
            canonical=canonical,
            app_id=app_node.id,
        )
    except Exception:
        logger.exception("hook framework registration failed during operational sync")
    from app.agentive.workspace_agent_profile import invalidate_workspace_profile

    invalidate_workspace_profile(app_node.workspace_id)
    return {
        "skills_registered": len(registered_skills),
        "agents_registered": len(registered_agents),
    }


async def _register_agents_from_manifest(
    app_node: App, canonical: Dict[str, Any]
) -> List[Any]:
    """Register every agent in the manifest under the App."""
    from app.agentive.services.uplink_registry import register_app_agent

    agents_specs = (canonical.get("app") or {}).get("agents") or []
    out: List[Any] = []
    for spec in agents_specs:
        if not isinstance(spec, dict):
            continue
        try:
            ag = await register_app_agent(
                app_id=app_node.id,
                workspace_id=app_node.workspace_id,
                agent_spec=spec,
            )
            out.append(ag)
        except Exception as e:
            logger.warning(
                "install: agent %r registration failed: %s",
                spec.get("key", "<unknown>"),
                e,
            )
            raise
    return out


# ---------------------------------------------------------------------------
# Compensation safe wrappers — never raise from inside compensation
# ---------------------------------------------------------------------------


async def _safe_destroy(node) -> None:
    """Best-effort node removal wrapped to absorb errors.

    An ``App`` goes through ``delete_app_cascade`` so its attached
    OperationalModel subtree, side-cars and any Tracks that survived the
    ``tracks_create`` compensation are removed with it; any other node is
    dropped with ``cascade=False`` (edges only — never jvspatial's
    graph-walking ``cascade=True``).
    """
    try:
        if isinstance(node, App):
            from app.services.app_deletion import delete_app_cascade

            await delete_app_cascade(node)
        else:
            await node.delete(cascade=False)
    except Exception:
        logger.exception(
            "install compensation: delete failed for %s", getattr(node, "id", "")
        )


async def _safe_unregister_skills(app_id: str) -> None:
    """Compensation for skills_register. Best-effort."""
    from app.agentive.services.skill_registry import unregister_skills_for_app

    try:
        await unregister_skills_for_app(app_id)
    except Exception:
        pass


async def _safe_unregister_agents(app_id: str) -> None:
    """Compensation for agents_register. Best-effort."""
    from app.agentive.services.uplink_registry import (
        unregister_app_agents_for_app,
    )

    try:
        await unregister_app_agents_for_app(app_id)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Finalize install (resume from awaiting_settings)
# ---------------------------------------------------------------------------


async def finalize_install(
    *,
    app_id: str,
    install_token: str,
    settings: Dict[str, Any],
    actor_id: str,
) -> Dict[str, Any]:
    """Resume a paused install — apply settings, plant seeds, emit ChangeEvent.

    Verifies the install_token (signature + expiry + app_id match), then
    enforces the state gate: ``App.lifecycle_state == "awaiting_settings"``.
    Token replay against an already-active App raises
    ``AppLifecycleStateError`` (T-10-05-02 mitigation).
    """
    # Verify token first — surfaces a precise error before doing any state
    # work. Verify raises AppInstallTokenInvalidError or
    # AppInstallTokenExpiredError on failure.
    verify_install_token(install_token, expected_app_id=app_id)

    # Plan 10-07 fix: bypass the jvspatial in-process cache before reading
    # lifecycle_state. The install_app() path used to import the (now-deleted)
    # mcp_adapter_legacy shim → app.main during skill registration (step 7),
    # and skill registration still reaches app.main, which triggers
    # DatabaseConfigurator.initialize_graph_context() → set_default_context()
    # with a FRESH GraphContext. The fresh context's cache is then populated
    # with a stale App snapshot during register_skill's App.get(), and step 9's
    # subsequent app_node.save() updates a cache entry that is NOT the one
    # finalize_install() reads here. Cache-bust this specific entry so the
    # post-install read reflects the on-disk awaiting_settings state.
    try:
        from jvspatial.core.context import get_default_context

        _ctx = get_default_context()
        if _ctx is not None and getattr(_ctx, "_cache", None) is not None:
            await _ctx._cache.delete(app_id)
    except Exception:
        pass

    app_node = await App.get(app_id)
    if not app_node:
        raise BadRequestError(
            message=f"App {app_id!r} not found",
            details={"app_id": app_id},
        )

    # Single-use enforcement via state check (T-10-05-02).
    if app_node.lifecycle_state != "awaiting_settings":
        raise AppLifecycleStateError(
            message=(
                f"App {app_id!r} is in lifecycle_state="
                f"{app_node.lifecycle_state!r}; finalize_install requires "
                "awaiting_settings"
            ),
            details={
                "app_id": app_id,
                "current_state": app_node.lifecycle_state,
            },
        )

    # Fetch the canonical manifest from the attached OperationalModel.
    attached_cp = await get_app_attached_operational_model(app_node)
    if not attached_cp:
        raise AppInstallError(
            message="App attached OperationalModel missing on finalize",
            details={"app_id": app_id},
        )
    canonical = compile_canonical_manifest(manifest=attached_cp.manifest or {})

    # Fill schema defaults, validate, then persist settings.
    schema = app_node.settings_schema or {}
    settings = _apply_schema_defaults(settings or {}, schema)
    _validate_settings_against_schema(settings, schema)
    app_node.settings = dict(settings or {})

    # Plant seeds (idempotent per APP-SEEDS-01) when install opted in.
    seed_pref = _resolve_include_seed_data(
        None,
        app_node=app_node,
        canonical=canonical,
    )
    await _plant_seeds_for_install(
        app_node,
        canonical,
        actor_id,
        include_seed_data=seed_pref,
    )

    # Register bundle tools + hooks in the per-workspace registry. The
    # deferred-settings finalize path MUST do this too: install_app performs it
    # (step 11.5) only on the inline-settings path, so a settings-gated bundle
    # (any manifest with a settings_schema) would otherwise install "active"
    # with its tools/hooks inert in the running process until the next restart
    # (rehydrate_all_installed_bundles). Same best-effort contract as
    # install_app — a registry failure must not block finalize.
    try:
        from app.services.hooks.install_hook import register_bundle_on_install

        await register_bundle_on_install(
            workspace_id=app_node.workspace_id,
            canonical=canonical,
            app_id=app_node.id,
        )
    except Exception:
        logger.exception(
            "finalize_install: hook framework registration failed; continuing"
        )
    from app.agentive.workspace_agent_profile import invalidate_workspace_profile

    invalidate_workspace_profile(app_node.workspace_id)

    # Transition to active + emit single ChangeEvent (D-05).
    app_node.lifecycle_state = "active"
    app_node.installed_at = utc_now_iso()
    app_node.updated_at = app_node.installed_at
    await app_node.save()

    try:
        from app.services.capability_catalogue import compile_workspace_catalogue

        await compile_workspace_catalogue(app_node.workspace_id, activate=True)
    except Exception:
        logger.exception(
            "finalize_install: catalogue compile after activate failed for %s",
            app_node.id,
        )

    await emit_change_event(
        actor_kind="human" if actor_id and actor_id != "system" else "system",
        actor_id=actor_id or "system",
        action="app.installed",
        resource_type="App",
        resource_id=app_node.id,
        before=None,
        after=await export_node(app_node),
        scope=f"app:{app_node.id}",
        details={
            "library_source_id": app_node.installed_from_library_id,
            "version": app_node.version,
            "via": "finalize_install",
        },
    )
    logger.info(
        "finalize_install: app %s transitioned awaiting_settings → active",
        app_id,
    )
    return {
        "status": "active",
        "app_id": app_node.id,
        "installed_at": app_node.installed_at,
        "version": app_node.version,
    }


# ---------------------------------------------------------------------------
# Settings post-install update
# ---------------------------------------------------------------------------


async def update_app_settings(
    *,
    app_id: str,
    settings: Dict[str, Any],
    actor_id: str,
) -> Dict[str, Any]:
    """Update an active App's settings (post-install Settings page edit).

    Settings_schema validation runs server-side. T-10-05-05 mitigation.
    Does NOT emit a ChangeEvent (settings edits are not lifecycle events;
    audit signal is the App's modified-at timestamp). A future plan may
    add ``app.settings_updated`` if the audit gap matters.
    """
    app_node = await App.get(app_id)
    if not app_node:
        raise BadRequestError(message=f"App {app_id!r} not found")
    if app_node.lifecycle_state not in ("active", "paused"):
        raise AppLifecycleStateError(
            message=(
                f"App {app_id!r} in lifecycle_state="
                f"{app_node.lifecycle_state!r}; settings can be updated only "
                "in active or paused state"
            ),
            details={
                "app_id": app_id,
                "current_state": app_node.lifecycle_state,
            },
        )
    settings = _apply_schema_defaults(settings or {}, app_node.settings_schema or {})
    _validate_settings_against_schema(settings, app_node.settings_schema or {})
    app_node.settings = dict(settings or {})
    app_node.updated_at = utc_now_iso()
    await app_node.save()
    return {
        "app_id": app_id,
        "settings": app_node.settings,
        "settings_schema": app_node.settings_schema,
        "lifecycle_state": app_node.lifecycle_state,
    }


# ---------------------------------------------------------------------------
# Pause / Resume
# ---------------------------------------------------------------------------


async def _find_active_hard_dependents(
    app_node: App,
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Find active Apps whose hard requirement is satisfied by ``app_node``."""
    provider_keys = set(app_install.app_dependency_index_keys(app_node))
    blockers: List[Dict[str, Any]] = []
    manifests: Dict[str, Dict[str, Any]] = {}
    for other in await App.find({"workspace_id": app_node.workspace_id}):
        if other.id == app_node.id or other.lifecycle_state != "active":
            continue
        try:
            definition = await get_active_application_definition(other)
            if definition is not None and definition.canonical_manifest:
                canonical = dict(definition.canonical_manifest)
            else:
                attached = await get_app_attached_operational_model(other)
                if attached is None:
                    continue
                canonical = compile_canonical_manifest(manifest=attached.manifest or {})
        except Exception:  # noqa: BLE001
            logger.exception("dependency manifest unavailable for App %s", other.id)
            continue
        manifests[other.id] = canonical
        for dep in (canonical.get("app") or {}).get("requires_apps") or []:
            if not isinstance(dep, dict) or bool(dep.get("optional", False)):
                continue
            dep_key = str(dep.get("key") or "").strip()
            aliases = {dep_key, dep_key.casefold(), slug_manifest_key(dep_key)}
            if provider_keys.intersection(aliases):
                blockers.append(
                    {"app_id": other.id, "app_name": other.name, "dep_key": dep_key}
                )
    return blockers, manifests


async def _check_app_dependencies_active(app_node: App) -> None:
    """Require the App's hard dependencies to be active before it resumes."""
    definition = await get_active_application_definition(app_node)
    if definition is not None and definition.canonical_manifest:
        canonical = dict(definition.canonical_manifest)
    else:
        attached = await get_app_attached_operational_model(app_node)
        if attached is None:
            raise AppInstallError(
                message="App attached OperationalModel missing during dependency check",
                details={"app_id": app_node.id},
            )
        canonical = compile_canonical_manifest(manifest=attached.manifest or {})
    await _check_requires_apps(canonical, app_node.workspace_id)


async def pause_app(*, app_id: str, actor_id: str) -> Dict[str, Any]:
    """Set App.lifecycle_state to ``paused``.

    F0: also unregisters workspace hooks/tools so a paused App cannot
    execute. Data is retained (unlike uninstall).
    """
    app_node = await App.get(app_id)
    if not app_node:
        raise BadRequestError(message=f"App {app_id!r} not found")
    if app_node.lifecycle_state == "paused":
        return {"app_id": app_id, "status": "paused"}
    if app_node.lifecycle_state != "active":
        raise AppLifecycleStateError(
            message=(
                f"App {app_id!r} in lifecycle_state="
                f"{app_node.lifecycle_state!r}; pause requires active state"
            ),
            details={"app_id": app_id, "current_state": app_node.lifecycle_state},
        )
    dependents, _ = await _find_active_hard_dependents(app_node)
    if dependents:
        raise AppLifecycleStateError(
            message="App pause blocked because active dependent Apps require it. Pause dependents first.",
            details={"app_id": app_id, "blocking_dependents": dependents},
        )
    slug = str(getattr(app_node, "source_operational_model_slug", "") or "")
    if slug and app_node.workspace_id:
        from app.services.hooks.install_hook import unregister_bundle_on_uninstall

        await unregister_bundle_on_uninstall(
            app_node.workspace_id, slug, app_id=app_node.id
        )
    try:
        from app.agentive.services.uplink_registry import pause_materialized_schedules

        await pause_materialized_schedules(app_node.id)
    except Exception:
        logger.exception("pause_app: failed to pause materialized schedules")
    app_node.lifecycle_state = "paused"
    app_node.updated_at = utc_now_iso()
    await app_node.save()
    return {"app_id": app_id, "status": "paused"}


async def resume_app(*, app_id: str, actor_id: str) -> Dict[str, Any]:
    """Set App.lifecycle_state back to ``active`` and re-register hooks/tools."""
    app_node = await App.get(app_id)
    if not app_node:
        raise BadRequestError(message=f"App {app_id!r} not found")
    if app_node.lifecycle_state == "active":
        return {"app_id": app_id, "status": "active"}
    if app_node.lifecycle_state != "paused":
        raise AppLifecycleStateError(
            message=(
                f"App {app_id!r} in lifecycle_state="
                f"{app_node.lifecycle_state!r}; resume requires paused state"
            ),
            details={"app_id": app_id, "current_state": app_node.lifecycle_state},
        )
    await _check_app_dependencies_active(app_node)
    # F3: if an entitlement row exists for this App's package, it must be
    # active before resume (commercial revoke → pause cannot be undone
    # without a new grant). Community Apps have no entitlement row.
    slug = str(
        getattr(app_node, "source_operational_model_slug", None)
        or getattr(app_node, "installed_package_slug", None)
        or ""
    ).strip()
    if slug and app_node.workspace_id:
        from app.api.errors import InsufficientPermissionsError
        from app.services.entitlements import (
            entitlement_is_active,
            entitlement_key_for_package,
            find_entitlement,
        )

        key = entitlement_key_for_package(
            slug=slug, package_meta={"slug": slug, "entitlement_key": slug}
        )
        row = await find_entitlement(
            workspace_id=app_node.workspace_id, entitlement_key=key
        )
        if row is not None and not entitlement_is_active(row):
            raise InsufficientPermissionsError(
                message=(
                    f"cannot resume App {app_id!r}: entitlement {key!r} "
                    f"is {row.status!r}"
                ),
                details={
                    "app_id": app_id,
                    "entitlement_key": key,
                    "status": row.status,
                },
            )
    if app_node.workspace_id:
        from app.services.hooks.install_hook import register_bundle_on_install

        try:
            definition = await get_active_application_definition(app_node)
            if definition is not None and definition.canonical_manifest:
                canonical = dict(definition.canonical_manifest)
            else:
                attached = await get_app_attached_operational_model(app_node)
                if attached is None:
                    canonical = {}
                else:
                    canonical = compile_canonical_manifest(
                        manifest=attached.manifest or {}
                    )
            attached = await get_app_attached_operational_model(app_node)
            bundle_dir = (
                str(
                    (getattr(attached, "metadata", None) or {}).get("bundle_dir_path")
                    or ""
                )
                or None
            )
            if not bundle_dir:
                library_id = getattr(app_node, "installed_from_library_id", None)
                if library_id:
                    library_cp = await OperationalModel.get(library_id)
                    bundle_dir = (
                        str(
                            (getattr(library_cp, "metadata", None) or {}).get(
                                "bundle_dir_path"
                            )
                            or ""
                        )
                        or None
                    )
            await register_bundle_on_install(
                app_node.workspace_id,
                canonical,
                bundle_dir=bundle_dir,
                app_id=app_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "resume_app: hook re-register failed for %s: %s", app_id, exc
            )
    try:
        from app.agentive.services.uplink_registry import resume_materialized_schedules

        await resume_materialized_schedules(app_id)
    except Exception:
        logger.exception("resume_app: failed to resume materialized schedules")
    app_node.lifecycle_state = "active"
    app_node.updated_at = utc_now_iso()
    await app_node.save()
    return {"app_id": app_id, "status": "active"}


# ---------------------------------------------------------------------------
# Update from library
# ---------------------------------------------------------------------------


async def update_app_from_library(
    *,
    app_id: str,
    version: Optional[str] = None,
    actor_id: str,
) -> Dict[str, Any]:
    """Re-merge the originating library package into the App's attached CP.

    Plan 10-05 ships the basic "re-run merge_library_manifest_into_operational_model"
    path. Migrations / version-bump diff is Plan 10-06's concern.
    """
    app_node = await App.get(app_id)
    if not app_node:
        raise BadRequestError(message=f"App {app_id!r} not found")
    lib_id = app_node.installed_from_library_id
    if not lib_id:
        raise BadRequestError(
            message=f"App {app_id!r} has no installed_from_library_id",
            details={"app_id": app_id},
        )
    library_cp = await OperationalModel.get(lib_id)
    if not library_cp:
        raise BadRequestError(
            message=f"Library OperationalModel {lib_id!r} not found",
            details={"library_cp_id": lib_id},
        )
    from app.services.package_trust import assert_library_artifact_trusted

    assert_library_artifact_trusted(library_cp)
    attached_cp = await get_app_attached_operational_model(app_node)
    if not attached_cp:
        raise AppInstallError(
            message="App attached OperationalModel missing on update",
            details={"app_id": app_id},
        )
    canonical = compile_canonical_manifest(manifest=library_cp.manifest or {})
    current_definition = await get_active_application_definition(app_node)
    if current_definition is not None:
        from app.services.application_definitions import (
            assert_package_upgrade_conflict_free,
        )

        assert_package_upgrade_conflict_free(current_definition, canonical)
    from app.services.application_upgrade_safety import (
        assert_package_upgrade_migration_safe,
    )

    migration_safety = await assert_package_upgrade_migration_safe(
        attached_profile=attached_cp,
        library_profile=library_cp,
    )
    version_before = app_node.version
    fingerprint_before = getattr(app_node, "installed_artifact_fingerprint", None)
    manifest_snapshot = dict(getattr(attached_cp, "manifest", None) or {})
    try:
        await merge_library_manifest_into_operational_model(
            library_cp, attached_cp, track=None, for_space=True
        )
    except Exception as exc:
        # F0 rollback: restore prior attached manifest + fingerprint.
        try:
            attached_cp.manifest = manifest_snapshot
            attached_cp.updated_at = utc_now_iso()
            await attached_cp.save()
            app_node.version = version_before
            app_node.installed_artifact_fingerprint = fingerprint_before
            app_node.updated_at = utc_now_iso()
            await app_node.save()
            if app_node.workspace_id:
                from app.services.hooks.install_hook import register_bundle_on_install

                await register_bundle_on_install(
                    app_node.workspace_id,
                    compile_canonical_manifest(manifest=manifest_snapshot),
                    app_id=app_id,
                )
        except Exception as rollback_exc:  # noqa: BLE001
            logger.error(
                "update_app_from_library rollback failed for %s: %s",
                app_id,
                rollback_exc,
            )
        raise AppInstallError(
            message=f"App update failed; prior state restored: {exc}",
            details={"app_id": app_id, "error": str(exc)},
        ) from exc
    from app.services.bundle_post_seed import run_bundle_post_seed
    from app.services.operational_model_merge import (
        provision_prescribed_tracks_from_app_manifest,
        refresh_all_app_track_template_materializations,
    )
    from app.services.operational_model_runtime import (
        synchronize_track_view_default_flags,
    )

    await provision_prescribed_tracks_from_app_manifest(app_node, actor_id)
    await refresh_all_app_track_template_materializations(app_node)
    # Anchor / prescribed tracks inherit views + EntryTypes from template CPs.
    # Rematerialize per-track EntryType nodes so new fields (Task.sprint, …)
    # land on already-provisioned details tracks — template CP refresh alone
    # does not update clones the UI lists by track_id.
    from app.services.entry_type_service import materialize_entry_types_from_tier

    app_tracks = await app_node.nodes(
        edge=["CONTAINS"], direction="out", node=["Track"]
    )
    for t in app_tracks:
        if not isinstance(t, Track):
            continue
        if not str(getattr(t, "template_id", "") or "").strip():
            continue
        try:
            await materialize_entry_types_from_tier(t)
        except Exception:  # noqa: BLE001
            logger.exception(
                "update_app_from_library: rematerialize EntryTypes failed for %s",
                t.id,
            )
        await synchronize_track_view_default_flags(t)
    await run_bundle_post_seed(app_node, actor_id)
    # Operational layer follows the library package (source of truth on update).
    await sync_operational_layer_from_manifest(app_node, canonical, actor_id=actor_id)
    # Update version + display identity on App row from the new library
    # package. name/description previously only synced at first install —
    # a package rename (e.g. "Payroll" -> "Guyana Payroll") on update never
    # reached the already-installed App node, so every workspace that
    # installed before the rename kept showing the old name/description
    # indefinitely, even after every other part of an update ran cleanly.
    #
    # Deliberately reads library_cp.name/.description (the top-level
    # OperationalModel scalars), NOT canonical["package"]["name"] —
    # _assemble_manifest (operational_model_loader.py) always stores the
    # SLUG under manifest.package.name by design ("the human display name
    # lives on LibraryProfileSpec.name instead"); library_cp.name/
    # .description are where that real display name/description actually
    # land when the library catalog is synced from disk.
    new_version = (canonical.get("package") or {}).get("version")
    if new_version is not None:
        app_node.version = str(new_version)
        app_node.installed_package_version = str(new_version)
    lib_md = dict(getattr(library_cp, "metadata", None) or {})
    fp = str(lib_md.get("bundle_fingerprint") or "")
    if fp:
        app_node.installed_artifact_fingerprint = fp
    slug = str(
        lib_md.get("slug")
        or getattr(app_node, "source_operational_model_slug", "")
        or ""
    )
    if slug:
        app_node.installed_package_slug = slug
    if library_cp.name:
        app_node.name = str(library_cp.name)
    if library_cp.description is not None:
        app_node.description = str(library_cp.description)
    app_node.updated_at = utc_now_iso()
    await app_node.save()
    definition = await compile_application_definition(
        app_node=app_node,
        # The attached operational model holds the three-way effective result: upstream
        # additions plus tenant-local customizations preserved by merge.
        # Binding raw library input here would make the active definition
        # disagree with the materialized App.
        manifest=dict(attached_cp.manifest or {}),
        source_operational_model_id=library_cp.id,
        base_package_manifest=canonical,
    )
    await verify_definition_materialization(
        app_node=app_node,
        definition=definition,
    )
    from app.services.application_upgrade_safety import start_package_upgrade_migrations

    migration_tracker = await start_package_upgrade_migrations(
        attached_profile=attached_cp,
        safety=migration_safety,
        actor_id=actor_id,
    )
    return {
        "app_id": app_id,
        "added_sections": [],  # Plan 10-06 fills in
        "version_before": version_before,
        "version_after": app_node.version,
        "definition_revision": definition.revision,
        "migration_tracker": migration_tracker,
    }


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------


async def _collect_uninstall_blockers(
    app_node: App,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return ``(blocking_dependents, blocking_references)`` without raising."""
    blocking_dependents, other_app_manifests = await _find_active_hard_dependents(
        app_node
    )
    from app.services.walkers.cross_app_resolver import find_inbound_references

    raw_refs = await find_inbound_references(
        target_app_id=app_node.id, workspace_id=app_node.workspace_id
    )
    blocking_references: List[Dict[str, Any]] = []
    for ref in raw_refs:
        source_app_id = ref.get("source_app_id", "")
        field_key = ref.get("relation_field_key", "")
        policy = _lookup_on_target_uninstall(
            manifest=other_app_manifests.get(source_app_id),
            field_key=field_key,
        )
        if policy == "block":
            blocking_references.append(
                {
                    "source_app_id": ref["source_app_id"],
                    "source_app_name": ref["source_app_name"],
                    "source_entry_id": ref["source_entry_id"],
                    "source_track_id": ref["source_track_id"],
                    "relation_field_key": field_key,
                }
            )
    return blocking_dependents, blocking_references


async def count_app_entries(app_node: App) -> int:
    """Sum Entry counts across Tracks contained by ``app_node``."""
    from app.models.edges import CONTAINS

    tracks = await app_node.nodes(
        edge=[CONTAINS], node=["Track"], direction="out", limit=500
    )
    total = 0
    for track in tracks or []:
        cached = getattr(track, "entry_count", None)
        if cached is not None:
            try:
                total += int(cached or 0)
                continue
            except (TypeError, ValueError):
                pass
        try:
            total += int(
                await track.count_nodes(
                    edge=[CONTAINS], node=["Entry"], direction="out"
                )
            )
        except Exception:
            logger.debug(
                "count_app_entries: count_nodes failed for track %s",
                getattr(track, "id", "?"),
                exc_info=True,
            )
    return total


async def get_uninstall_preflight(app_id: str) -> Dict[str, Any]:
    """Structural uninstall readiness for UI gating + confirmation."""
    app_node = await App.get(app_id)
    if not app_node:
        raise BadRequestError(message=f"App {app_id!r} not found")
    blocking_dependents, blocking_references = await _collect_uninstall_blockers(
        app_node
    )
    entry_count = await count_app_entries(app_node)
    can_uninstall = not blocking_dependents and not blocking_references
    return {
        "app_id": app_id,
        "can_uninstall": can_uninstall,
        "blocking_dependents": blocking_dependents,
        "blocking_references": blocking_references,
        "entry_count": entry_count,
        "requires_data_confirmation": entry_count > 0,
    }


async def _check_uninstall_blockers(
    app_node: App,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Two-walk uninstall pre-flight check (Pitfall 6).

    Raises ``AppUninstallBlockedError`` if either walk produces
    block-class blockers.
    """
    blocking_dependents, blocking_references = await _collect_uninstall_blockers(
        app_node
    )
    if blocking_dependents or blocking_references:
        n_deps = len(blocking_dependents)
        n_refs = len(blocking_references)
        raise AppUninstallBlockedError(
            message=(
                f"App uninstall blocked — {n_deps} dependent App(s) and "
                f"{n_refs} cross-App reference(s) point at this App. "
                f"Uninstall dependents first."
            ),
            details={
                "blocking_dependents": blocking_dependents,
                "blocking_references": blocking_references,
            },
        )
    return blocking_dependents, blocking_references



def _lookup_on_target_uninstall(
    *,
    manifest: Optional[Dict[str, Any]],
    field_key: str,
) -> str:
    """Look up a relation field's ``on_target_uninstall`` policy.

    Returns the declared policy (``block`` | ``null`` | ``archive_self``) or
    ``"block"`` (default) if the manifest is missing or the field cannot be
    located. Defaults to block so a missing manifest never silently allows
    silent data destruction.
    """
    if not manifest or not field_key:
        return "block"
    app_section = manifest.get("app") or {}
    for track in app_section.get("tracks") or []:
        if not isinstance(track, dict):
            continue
        for et in track.get("entry_types") or []:
            if not isinstance(et, dict):
                continue
            for field in et.get("fields") or []:
                if not isinstance(field, dict):
                    continue
                if str(field.get("key") or "") != field_key:
                    continue
                rel = field.get("relation") or {}
                if not isinstance(rel, dict):
                    return "block"
                return str(rel.get("on_target_uninstall") or "block")
    return "block"


async def uninstall_app(
    *,
    app_id: str,
    actor_id: str,
    archive: bool = True,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Uninstall an App. Archive-by-default; dependents always hard-block.

    Single-emission per D-05: emits ``app.uninstalled`` on success.

    Always runs the two-walk pre-flight (manifest-level + edge-level
    REFERENCES). Cross-App refs with ``on_target_uninstall`` of ``null`` /
    ``archive_self`` cascade before the App is destroyed. There is no
    force bypass — uninstall leaves first.

    Args:
        app_id: target App id.
        actor_id: caller principal (User.id or "system" for reaper).
        archive: if True (default), set lifecycle_state="uninstalled" and
            leave Tracks/Entries in place. If False (purge), cascade-delete
            via existing ``delete_app_cascade``.
        reason: optional details payload (used by reaper).
    """
    app_node = await App.get(app_id)
    if not app_node:
        raise BadRequestError(message=f"App {app_id!r} not found")

    pre_state = app_node.lifecycle_state
    workspace_id_for_invalidation = app_node.workspace_id
    bundle_slug_for_unregister = await _resolve_bundle_slug(app_node)

    # ---- Pre-flight (always) ----
    _ = await _check_uninstall_blockers(app_node)
    cascade_null_refs, cascade_archive_refs = await _collect_cascade_refs(app_node)

    await _cascade_null_inbound_refs(cascade_null_refs, actor_id)
    await _cascade_archive_inbound_entries(cascade_archive_refs, actor_id)

    await _safe_unregister_agents(app_id)
    await _safe_unregister_skills(app_id)

    from app.agentive.workspace_agent_profile import invalidate_workspace_profile

    invalidate_workspace_profile(getattr(app_node, "workspace_id", "") or "")

    if not archive:
        try:
            await purge_app_with_bundle_teardown(
                app_node, bundle_slug=bundle_slug_for_unregister
            )
        except Exception as e:
            logger.warning(
                "uninstall_app: delete_app_cascade(%s) raised: %s",
                app_id,
                e,
            )
    else:
        app_node.lifecycle_state = "uninstalled"
        app_node.updated_at = utc_now_iso()
        await app_node.save()

    details_payload: Dict[str, Any] = {
        "archived": archive,
        "nulled_references": len(cascade_null_refs),
        "archived_source_entries": len(cascade_archive_refs),
    }
    if reason:
        details_payload["reason"] = reason

    await emit_change_event(
        actor_kind="human" if actor_id and actor_id != "system" else "system",
        actor_id=actor_id or "system",
        action="app.uninstalled",  # type: ignore[arg-type]
        resource_type="App",
        resource_id=app_id,
        before={"lifecycle_state": pre_state},
        after={"lifecycle_state": "uninstalled" if archive else "deleted"},
        scope=f"app:{app_id}",
        details=details_payload,
    )
    logger.info(
        "uninstall_app: app %s uninstalled (archive=%s, pre_state=%s, "
        "nulled_refs=%d, archived_sources=%d)",
        app_id,
        archive,
        pre_state,
        len(cascade_null_refs),
        len(cascade_archive_refs),
    )
    await unregister_app_bundle(
        workspace_id=workspace_id_for_invalidation,
        bundle_slug=bundle_slug_for_unregister,
    )

    return {
        "app_id": app_id,
        "status": "uninstalled",
        "archived": archive,
    }



async def _resolve_bundle_slug(app_node: App) -> str:
    """Return the registry key ``register_bundle_on_install`` used for this App.

    Mirrors the install-side resolution (``package.slug`` falling back to
    ``package.name``) from the attached OperationalModel's manifest, then
    falls back to ``App.source_operational_model_slug``. Returns "" when neither is
    available. Never raises.
    """
    try:
        definition = await get_active_application_definition(app_node)
        canonical = {}
        if definition is not None and definition.canonical_manifest:
            canonical = dict(definition.canonical_manifest)
        package = canonical.get("package") or {}
        slug = str(package.get("slug") or package.get("name") or "")
        if slug:
            return slug

        # Apps created before the definition ledger can have an active
        # definition that does not carry bundle metadata. Their live hooks
        # still came from the attached operational model, so do not let that incomplete
        # definition prevent teardown from resolving the registered slug.
        if not slug:
            cp = await get_app_attached_operational_model(app_node)
            canonical = (
                compile_canonical_manifest(manifest=cp.manifest or {})
                if cp and cp.manifest
                else {}
            )
            package = canonical.get("package") or {}
            slug = str(package.get("slug") or package.get("name") or "")
            if slug:
                return slug
    except Exception:
        logger.exception(
            "hook framework unregister: failed resolving bundle slug for app %s",
            app_node.id,
        )
    return str(getattr(app_node, "source_operational_model_slug", "") or "")


async def unregister_app_bundle(*, workspace_id: str, bundle_slug: str) -> None:
    """Drop the in-process hook/tool registrations a bundle contributed.

    Best-effort and idempotent — every App-teardown path calls this, and a
    failure here must never abort the teardown. Also invalidates the
    workspace's resident skill-overlay cache, which mirrors the same
    registrations.
    """
    try:
        from app.services.hooks.install_hook import unregister_bundle_on_uninstall

        await unregister_bundle_on_uninstall(
            workspace_id=workspace_id,
            bundle_slug=bundle_slug,
        )
    except Exception:
        logger.exception("hook framework unregister failed; continuing")
    try:
        from app.agentive.workspace_agent_profile import invalidate_workspace_profile

        invalidate_workspace_profile(workspace_id)
    except Exception:
        logger.exception("workspace profile invalidation failed; continuing")


async def purge_app_with_bundle_teardown(
    app_node: App, *, bundle_slug: Optional[str] = None
) -> Tuple[int, int]:
    """Cascade-delete an App AND tear down its bundle registrations.

    The single entry point every hard-delete path must use (``uninstall_app``
    purge branch, ``DELETE /apps/{id}`` non-library branch,
    ``delete_workspace_cascade``). Calling ``delete_app_cascade`` directly
    leaves the in-process hook + tool registry dispatching for a deleted App
    until the process restarts — reachable for Apps that carry no
    ``installed_from_library_id`` too, because
    ``sync_operational_layer_from_manifest`` registers their bundles as well.

    Ordering matters: the bundle slug and workspace id are resolved BEFORE
    the cascade. Once ``delete_app_cascade`` has run, the attached
    OperationalModel is gone, the slug resolves to "" and the unregister
    silently no-ops.

    Returns ``delete_app_cascade``'s ``(deleted_tracks, unlinked_tracks)``.
    """
    from app.services.app_deletion import delete_app_cascade

    workspace_id = str(getattr(app_node, "workspace_id", "") or "")
    slug = (
        bundle_slug if bundle_slug is not None else await _resolve_bundle_slug(app_node)
    )
    try:
        return await delete_app_cascade(app_node)
    finally:
        await unregister_app_bundle(workspace_id=workspace_id, bundle_slug=slug)


async def _collect_cascade_refs(
    app_node: App,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Enumerate inbound REFERENCES with ``on_target_uninstall`` in (null, archive_self).

    Returns ``(null_refs, archive_refs)``. Used by the normal-uninstall path
    to cascade the policy declared by each source App's manifest.
    """
    from app.services.walkers.cross_app_resolver import find_inbound_references

    raw_refs = await find_inbound_references(
        target_app_id=app_node.id, workspace_id=app_node.workspace_id
    )
    if not raw_refs:
        return [], []
    # Build the source-app manifest cache once.
    other_apps = await App.find({"workspace_id": app_node.workspace_id})
    manifests: Dict[str, Dict[str, Any]] = {}
    for other in other_apps:
        if other.id == app_node.id:
            continue
        if other.lifecycle_state != "active":
            continue
        try:
            definition = await get_active_application_definition(other)
            if definition is not None and definition.canonical_manifest:
                manifests[other.id] = dict(definition.canonical_manifest)
                continue
            cp = await get_app_attached_operational_model(other)
            if cp:
                manifests[other.id] = compile_canonical_manifest(
                    manifest=cp.manifest or {}
                )
        except Exception:
            continue
    null_refs: List[Dict[str, Any]] = []
    archive_refs: List[Dict[str, Any]] = []
    for ref in raw_refs:
        policy = _lookup_on_target_uninstall(
            manifest=manifests.get(ref.get("source_app_id", "")),
            field_key=ref.get("relation_field_key", ""),
        )
        if policy == "null":
            null_refs.append(ref)
        elif policy == "archive_self":
            archive_refs.append(ref)
    return null_refs, archive_refs


async def _count_bypassed_blockers(app_node: App) -> Tuple[int, int]:
    """Force-uninstall side — count what would have been a blocker (for audit)."""
    try:
        # _check_uninstall_blockers raises on blockers; catch and count.
        await _check_uninstall_blockers(app_node)
        return 0, 0
    except AppUninstallBlockedError as e:
        details = getattr(e, "details", {}) or {}
        deps = details.get("blocking_dependents") or []
        refs = details.get("blocking_references") or []
        return len(deps), len(refs)
    except Exception:
        return 0, 0


async def _cascade_null_inbound_refs(refs: List[Dict[str, Any]], actor_id: str) -> None:
    """For each null-policy inbound ref, clear the source field + emit entry.update.

    D-05 single-emission per affected source entry. Multiple ref slots on the
    same field get coalesced (one update per field, not per ref).
    """
    # Coalesce by (source_entry_id, field_key) so a single field with N
    # cleared targets emits ONE entry.update.
    by_entry_field: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for ref in refs:
        key = (ref["source_entry_id"], ref["relation_field_key"])
        by_entry_field.setdefault(key, []).append(ref)

    for (entry_id, field_key), entry_refs in by_entry_field.items():
        try:
            entry = await Entry.get(entry_id)
            if not entry:
                continue
            before_cf = dict(entry.custom_fields or {})
            cf = dict(entry.custom_fields or {})
            existing = cf.get(field_key)
            # Many-relations are stored as lists; single relations as scalars.
            if isinstance(existing, list):
                cf[field_key] = []
            else:
                cf[field_key] = None
            entry.custom_fields = cf
            entry.updated_at = utc_now_iso()
            await entry.save()
            await emit_change_event(
                actor_kind="system",
                actor_id=actor_id or "system",
                action="entry.update",
                resource_type="Entry",
                resource_id=entry_id,
                before={"custom_fields": before_cf},
                after={"custom_fields": cf},
                scope=f"track:{entry.track_id}",
                details={
                    "reason": "cross_app_target_uninstalled",
                    "relation_field_key": field_key,
                    "nulled_refs": len(entry_refs),
                },
            )
        except Exception as e:
            logger.warning(
                "_cascade_null_inbound_refs: entry %s field %s raised: %s",
                entry_id,
                field_key,
                e,
            )


async def _cascade_archive_inbound_entries(
    refs: List[Dict[str, Any]], actor_id: str
) -> None:
    """For each archive_self-policy inbound ref, archive the source entry.

    D-05 single-emission per affected source entry — multiple refs on the
    same entry coalesce to a single entry.archived emission.
    """
    seen: set[str] = set()
    for ref in refs:
        entry_id = ref["source_entry_id"]
        if entry_id in seen:
            continue
        seen.add(entry_id)
        try:
            entry = await Entry.get(entry_id)
            if not entry:
                continue
            before_status = entry.status
            entry.status = "archived"
            entry.updated_at = utc_now_iso()
            await entry.save()
            await emit_change_event(
                actor_kind="system",
                actor_id=actor_id or "system",
                action="entry.archived",
                resource_type="Entry",
                resource_id=entry_id,
                before={"status": before_status},
                after={"status": "archived"},
                scope=f"track:{entry.track_id}",
                details={
                    "reason": "cross_app_target_uninstalled",
                    "source_app_id": ref.get("source_app_id", ""),
                },
            )
        except Exception as e:
            logger.warning(
                "_cascade_archive_inbound_entries: entry %s raised: %s",
                entry_id,
                e,
            )
