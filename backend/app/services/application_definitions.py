"""Versioned application-definition authority (WP-04).

The Content Profile compiler validates an App's schema and composition. This
module makes that result durable and App-bound: a compiled definition captures
the effective contract and its promised materialization obligations without
executing arbitrary generated code.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional

from app.models.edges import HAS_APPLICATION_DEFINITION
from app.models.nodes import (
    App,
    ApplicationDefinition,
    ContentProfile,
    EntryType,
    Skill,
    Track,
    View,
)
from app.services.content_profile_diff import compute_manifest_diff
from app.services.content_profile_runtime import compile_canonical_manifest
from app.utils.time import utc_now_iso


def definition_fingerprint(canonical_manifest: Dict[str, Any]) -> str:
    """Return the stable identity of a compiler-validated contract."""
    encoded = json.dumps(
        canonical_manifest, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def derive_local_overrides(
    *, base_package_manifest: Dict[str, Any], effective_manifest: Dict[str, Any]
) -> Dict[str, Any]:
    """Record effective divergence from an immutable package base.

    This is intentionally descriptive, not a conflict resolver. It gives a
    future three-way upgrader durable base/effective inputs and a bounded
    structural diff without pretending that Core has already chosen how to
    resolve a conflicting package and tenant change.
    """
    base = compile_canonical_manifest(manifest=dict(base_package_manifest or {}))
    effective = compile_canonical_manifest(manifest=dict(effective_manifest or {}))
    base_fingerprint = definition_fingerprint(base)
    effective_fingerprint = definition_fingerprint(effective)
    if base_fingerprint == effective_fingerprint:
        return {}
    return {
        "base_manifest_fingerprint": base_fingerprint,
        "effective_manifest_fingerprint": effective_fingerprint,
        "structural_diff": compute_manifest_diff(before=base, after=effective),
    }


def preview_three_way_package_upgrade(
    *,
    base_package_manifest: Dict[str, Any],
    effective_manifest: Dict[str, Any],
    incoming_package_manifest: Dict[str, Any],
) -> Dict[str, Any]:
    """Conservatively identify package/local conflicts without resolving them."""
    base = compile_canonical_manifest(manifest=dict(base_package_manifest or {}))
    effective = compile_canonical_manifest(manifest=dict(effective_manifest or {}))
    incoming = compile_canonical_manifest(
        manifest=dict(incoming_package_manifest or {})
    )
    conflicts: List[Dict[str, Any]] = []
    counts = {"upstream_only": 0, "local_only": 0, "converged": 0}

    def walk(base_value: Any, local_value: Any, incoming_value: Any, path: str) -> None:
        if local_value == incoming_value:
            counts["converged"] += 1
            return
        if local_value == base_value:
            counts["upstream_only"] += 1
            return
        if incoming_value == base_value:
            counts["local_only"] += 1
            return
        if all(
            isinstance(value, dict)
            for value in (base_value, local_value, incoming_value)
        ):
            for key in sorted(set(base_value) | set(local_value) | set(incoming_value)):
                walk(
                    base_value.get(key),
                    local_value.get(key),
                    incoming_value.get(key),
                    f"{path}.{key}",
                )
            return
        conflicts.append(
            {
                "path": path,
                "base": base_value,
                "local": local_value,
                "incoming": incoming_value,
            }
        )

    walk(base, effective, incoming, "$")
    return {
        "status": "conflicts" if conflicts else "ready",
        "base_fingerprint": definition_fingerprint(base),
        "effective_fingerprint": definition_fingerprint(effective),
        "incoming_fingerprint": definition_fingerprint(incoming),
        "conflicts": conflicts,
        "counts": counts,
        "limitations": [
            "This is a conservative manifest-level assessment; it does not apply or resolve conflicts."
        ],
    }


def assert_package_upgrade_conflict_free(
    definition: ApplicationDefinition, incoming_package_manifest: Dict[str, Any]
) -> None:
    """Reject an upgrade that needs an explicit tenant/package resolution."""
    base = dict(getattr(definition, "base_package_manifest", None) or {})
    if not base:
        return
    preview = preview_three_way_package_upgrade(
        base_package_manifest=base,
        effective_manifest=dict(getattr(definition, "canonical_manifest", None) or {}),
        incoming_package_manifest=incoming_package_manifest,
    )
    if preview["status"] == "conflicts":
        from app.exceptions import ApplicationDefinitionUpgradeConflictError

        raise ApplicationDefinitionUpgradeConflictError(
            message="Package upgrade requires conflict resolution before it can apply.",
            details={"conflicts": preview["conflicts"], "counts": preview["counts"]},
        )


def build_requirement_ledger(
    canonical_manifest: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Extract declarative materialization obligations from a contract."""
    app = dict(canonical_manifest.get("app") or {})
    package = dict(canonical_manifest.get("package") or {})
    ledger: List[Dict[str, Any]] = []

    for dependency in app.get("requires_apps") or []:
        if not isinstance(dependency, dict):
            continue
        key = str(dependency.get("key") or "").strip()
        if key:
            ledger.append(
                {
                    "id": f"dependency:{key}",
                    "kind": "app_dependency",
                    "label": dependency.get("reason") or key,
                    "required": not bool(dependency.get("optional", False)),
                    "minimum_version": str(dependency.get("min_version") or "0.0.0"),
                }
            )

    def add_collection(items: Iterable[Any], kind: str, prefix: str) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or item.get("name") or "").strip()
            if key:
                ledger.append(
                    {
                        "id": f"{prefix}:{key}",
                        "kind": kind,
                        "label": str(item.get("name") or key),
                        "required": True,
                    }
                )

    add_collection(app.get("tracks") or [], "track", "track")
    add_collection(app.get("track_templates") or [], "track_template", "track_template")
    for collection_name, collection in (
        ("track", app.get("tracks") or []),
        ("track_template", app.get("track_templates") or []),
    ):
        for track in collection:
            if not isinstance(track, dict):
                continue
            track_key = str(track.get("key") or track.get("name") or "").strip()
            if not track_key:
                continue
            for entry_type in track.get("entry_types") or []:
                if not isinstance(entry_type, dict):
                    continue
                key = str(entry_type.get("key") or entry_type.get("name") or "").strip()
                if key:
                    ledger.append(
                        {
                            "id": f"entry_type:{track_key}:{key}",
                            "kind": "entry_type",
                            "label": str(entry_type.get("name") or key),
                            "required": True,
                        }
                    )
            for view in track.get("views") or []:
                if not isinstance(view, dict):
                    continue
                key = str(view.get("key") or view.get("name") or "").strip()
                if key:
                    ledger.append(
                        {
                            "id": f"view:{track_key}:{key}",
                            "kind": "view",
                            "label": str(view.get("name") or key),
                            "required": True,
                            "scope": collection_name,
                        }
                    )
    add_collection(app.get("extension_views") or [], "extension_view", "extension_view")
    add_collection(app.get("operations") or [], "command", "command")
    add_collection(app.get("queries") or [], "query", "query")
    add_collection(app.get("skills") or [], "skill", "skill")
    add_collection(app.get("agents") or [], "agent", "agent")

    package_slug = str(package.get("slug") or package.get("name") or "").strip()
    if package_slug:
        ledger.append(
            {
                "id": f"package:{package_slug}",
                "kind": "package",
                "label": package_slug,
                "required": True,
            }
        )
    return ledger


def preview_application_definition(
    *,
    before_manifest: Optional[Dict[str, Any]],
    candidate_manifest: Dict[str, Any],
) -> Dict[str, Any]:
    """Produce an author-facing, non-authorizing contract preview.

    The structural diff remains machine-readable for clients. The labels and
    effects let a reviewer understand the proposal without mistaking preview
    for application. Record impact stays explicitly unknown until the target
    App runs migration planning; zero would be a misleading claim.
    """
    before = compile_canonical_manifest(manifest=dict(before_manifest or {}))
    candidate = compile_canonical_manifest(manifest=dict(candidate_manifest or {}))
    structural_diff = compute_manifest_diff(before=before, after=candidate)
    before_ledger = {item["id"]: item for item in build_requirement_ledger(before)}
    candidate_ledger = {
        item["id"]: item for item in build_requirement_ledger(candidate)
    }
    changes: List[Dict[str, Any]] = []
    for item_id in sorted(candidate_ledger.keys() - before_ledger.keys()):
        item = candidate_ledger[item_id]
        changes.append(
            {
                "kind": "add",
                "subject_kind": item["kind"],
                "subject_id": item_id,
                "label": item["label"],
            }
        )
    for item_id in sorted(before_ledger.keys() - candidate_ledger.keys()):
        item = before_ledger[item_id]
        changes.append(
            {
                "kind": "remove",
                "subject_kind": item["kind"],
                "subject_id": item_id,
                "label": item["label"],
            }
        )

    effects = [
        {
            "effect": "materialize",
            "subject_kind": item["kind"],
            "subject_id": item["id"],
            "label": item["label"],
        }
        for item in candidate_ledger.values()
        if item["id"] not in before_ledger
    ]
    limitations = [
        "Preview validates supported manifest capabilities only; it does not authorize or apply changes."
    ]
    if any(change["kind"] == "remove" for change in changes):
        limitations.append(
            "Removed capabilities may require a migration or maintenance window before activation."
        )
    return {
        "candidate_fingerprint": definition_fingerprint(candidate),
        "structural_diff": structural_diff,
        "changes": changes,
        "effects": effects,
        "affected_records": {
            "status": "not_evaluated",
            "count": None,
            "explanation": (
                "Record impact is evaluated against an installed App during "
                "migration planning; this definition-only preview does not "
                "claim that no records are affected."
            ),
        },
        "limitations": limitations,
    }


async def get_active_application_definition(
    app_node: App,
) -> Optional[ApplicationDefinition]:
    """Resolve the active revision and repair an absent legacy scalar pointer."""
    active_id = str(getattr(app_node, "active_definition_id", "") or "")
    if active_id:
        active = await ApplicationDefinition.get(active_id)
        if active is not None:
            return active
    # Snapshot callers may provide a lightweight App-shaped record. It has no
    # graph traversal API, so only its explicit active_definition_id can be
    # resolved; falling through would turn an optional ledger lookup into an
    # unrelated AttributeError.
    if not hasattr(app_node, "nodes"):
        return None
    definitions = await app_node.nodes(
        edge=[HAS_APPLICATION_DEFINITION],
        direction="out",
        node=["ApplicationDefinition"],
    )
    active = [item for item in definitions if getattr(item, "status", "") == "active"]
    if not active:
        return None
    chosen = max(active, key=lambda item: int(getattr(item, "revision", 0) or 0))
    app_node.active_definition_id = chosen.id
    app_node.active_definition_revision = int(getattr(chosen, "revision", 0) or 0)
    await app_node.save()
    return chosen  # type: ignore[return-value]


async def compile_application_definition(
    *,
    app_node: App,
    manifest: Dict[str, Any],
    source_profile_id: str = "",
    source_kind: str = "package",
    base_package_manifest: Optional[Dict[str, Any]] = None,
    local_overrides: Optional[Dict[str, Any]] = None,
    activate: bool = True,
) -> ApplicationDefinition:
    """Append a compiler-validated App definition, reusing equal revisions.

    The App → definition structural edge satisfies I-GRAPH-01. A changed
    contract supersedes its predecessor and updates the App's scalar fast-path
    in the same service operation; the old contract remains inspectable for a
    later three-way upgrade merge.
    """
    canonical = compile_canonical_manifest(manifest=dict(manifest or {}))
    package_base = (
        compile_canonical_manifest(manifest=dict(base_package_manifest or {}))
        if base_package_manifest
        else {}
    )
    if local_overrides is None and package_base:
        local_overrides = derive_local_overrides(
            base_package_manifest=package_base,
            effective_manifest=canonical,
        )
    fingerprint = definition_fingerprint(canonical)
    package_base_fingerprint = (
        definition_fingerprint(package_base) if package_base else ""
    )
    current = await get_active_application_definition(app_node)
    if current is not None and current.manifest_fingerprint == fingerprint:
        current_base = dict(getattr(current, "base_package_manifest", None) or {})
        current_base_fingerprint = (
            definition_fingerprint(current_base) if current_base else ""
        )
        if current_base_fingerprint == package_base_fingerprint:
            return current

    prior_definitions = await app_node.nodes(
        edge=[HAS_APPLICATION_DEFINITION],
        direction="out",
        node=["ApplicationDefinition"],
    )
    revision = (
        max(
            (int(getattr(item, "revision", 0) or 0) for item in prior_definitions),
            default=0,
        )
        + 1
    )
    now = utc_now_iso()
    definition = await ApplicationDefinition.create(
        app_id=app_node.id,
        workspace_id=str(getattr(app_node, "workspace_id", "") or ""),
        revision=revision,
        status="active" if activate else "compiled",
        source_kind=source_kind,
        source_profile_id=source_profile_id,
        base_definition_id=current.id if current is not None else None,
        base_package_revision=str(
            getattr(app_node, "installed_package_version", "") or ""
        )
        or None,
        base_artifact_fingerprint=str(
            getattr(app_node, "installed_artifact_fingerprint", "") or ""
        )
        or None,
        base_package_manifest=package_base,
        manifest_fingerprint=fingerprint,
        canonical_manifest=canonical,
        requirement_ledger=build_requirement_ledger(canonical),
        local_overrides=dict(local_overrides or {}),
        compiled_at=now,
        activated_at=now if activate else None,
    )
    await app_node.connect(
        definition,
        edge=HAS_APPLICATION_DEFINITION,
        revision=revision,
        activated_at=now if activate else None,
    )
    if activate:
        if current is not None:
            current.status = "superseded"
            await current.save()
        app_node.active_definition_id = definition.id
        app_node.active_definition_revision = revision
        await app_node.save()
    return definition


async def verify_definition_materialization(
    *, app_node: App, definition: ApplicationDefinition
) -> Dict[str, int]:
    """Persist conservative evidence for requirements Core can inspect.

    This is deliberately not a blanket "installed" flag. A ledger item is
    marked ``verified`` only after its concrete Core artifact is found. Other
    declared capabilities remain ``not_evaluated`` until their owning runtime
    publishes a verifier. That makes the definition useful for operational
    diagnosis without turning an incomplete check into false assurance.
    """
    now = utc_now_iso()
    tracks = await app_node.nodes(
        edge=["CONTAINS"], direction="out", node=["Track"], limit=500
    )
    track_by_title = {
        str(getattr(track, "title", "") or ""): track
        for track in tracks
        if isinstance(track, Track)
    }
    definition_tracks: Dict[str, Track] = {}
    definition_app = dict((definition.canonical_manifest or {}).get("app") or {})
    for track_spec in list(definition_app.get("tracks") or []):
        if not isinstance(track_spec, dict):
            continue
        key = str(track_spec.get("key") or "").strip()
        name = str(track_spec.get("name") or key).strip()
        track = track_by_title.get(name)
        if key and track is not None:
            definition_tracks[key] = track
    skills = await app_node.nodes(
        edge=["CONTAINS"], direction="out", node=["Skill"], limit=500
    )
    skill_by_key = {
        str(getattr(skill, "key", "") or ""): skill
        for skill in skills
        if isinstance(skill, Skill)
    }
    agent_by_key: Dict[str, Any] = {}
    if any(
        str(item.get("kind") or "") == "agent"
        for item in list(getattr(definition, "requirement_ledger", []) or [])
    ):
        from app.agentive.nodes import AgentConfig

        for agent in await AgentConfig.find({"app_id": app_node.id}):
            preferences = dict(getattr(agent, "preferences", None) or {})
            key = str(preferences.get("agent_key") or "")
            if key:
                agent_by_key[key] = agent
    source_profile_id = str(getattr(definition, "source_profile_id", "") or "")
    source_profile = (
        await ContentProfile.get(source_profile_id) if source_profile_id else None
    )
    commands: Dict[str, Dict[str, Any]] = {}
    queries: Dict[str, Dict[str, Any]] = {}
    requirement_kinds = {
        str(item.get("kind") or "")
        for item in list(getattr(definition, "requirement_ledger", []) or [])
    }
    if "command" in requirement_kinds:
        from app.services.app_operations.registry import list_registered_operations

        commands = list_registered_operations(app_node.workspace_id, app_node.id)
    if "query" in requirement_kinds:
        from app.services.app_queries.registry import list_registered_queries

        queries = list_registered_queries(app_node.workspace_id, app_node.id)
    dependency_installs: Dict[str, List[tuple[App, str]]] = {}
    if "app_dependency" in requirement_kinds:
        from app.services.app_install import (
            app_dependency_index_keys,
            effective_app_version,
        )

        for installed_app in await App.find({"workspace_id": app_node.workspace_id}):
            if installed_app.id == app_node.id:
                continue
            if str(getattr(installed_app, "lifecycle_state", "") or "") != "active":
                continue
            installed_version = await effective_app_version(installed_app)
            for alias in app_dependency_index_keys(installed_app):
                dependency_installs.setdefault(alias, []).append(
                    (installed_app, installed_version)
                )
    evidence: List[Dict[str, Any]] = []
    verified_count = 0
    for requirement in list(getattr(definition, "requirement_ledger", []) or []):
        requirement_id = str(requirement.get("id") or "")
        kind = str(requirement.get("kind") or "")
        label = str(requirement.get("label") or "")
        row: Dict[str, Any] = {
            "requirement_id": requirement_id,
            "kind": kind,
            "checked_at": now,
            "status": "not_evaluated",
            "references": [],
        }
        if kind == "package":
            if source_profile is not None:
                row.update(status="verified", references=[source_profile.id])
        elif kind == "track":
            track = track_by_title.get(label)
            if track is not None:
                row.update(status="verified", references=[track.id])
        elif kind == "skill":
            skill = skill_by_key.get(str(requirement_id).removeprefix("skill:"))
            if skill is not None:
                row.update(status="verified", references=[skill.id])
        elif kind == "agent":
            agent = agent_by_key.get(str(requirement_id).removeprefix("agent:"))
            if agent is not None:
                row.update(status="verified", references=[agent.id])
        elif kind == "command":
            key = str(requirement_id).removeprefix("command:")
            if key in commands:
                row.update(status="verified", references=[f"operation:{key}"])
        elif kind == "query":
            key = str(requirement_id).removeprefix("query:")
            if key in queries:
                row.update(status="verified", references=[f"query:{key}"])
        elif kind == "app_dependency":
            from app.services.app_install import version_satisfies_min
            from app.services.content_profile_runtime import slug_manifest_key

            key = str(requirement_id).removeprefix("dependency:")
            candidates: List[tuple[App, str]] = []
            for alias in (key, key.casefold(), slug_manifest_key(key)):
                candidates.extend(dependency_installs.get(alias, []))
            seen_app_ids: set[str] = set()
            matching = []
            for candidate, version in candidates:
                if candidate.id in seen_app_ids:
                    continue
                seen_app_ids.add(candidate.id)
                if version_satisfies_min(
                    version, str(requirement.get("minimum_version") or "0.0.0")
                ):
                    matching.append(candidate)
            if matching:
                row.update(status="verified", references=[item.id for item in matching])
        elif kind == "entry_type":
            _, track_key, _ = requirement_id.split(":", 2)
            track = definition_tracks.get(track_key)
            if track is not None:
                found = await EntryType.find(
                    {"context.track_id": track.id, "context.name": label}
                )
                if found:
                    row.update(status="verified", references=[found[0].id])
        elif kind == "view":
            _, track_key, _ = requirement_id.split(":", 2)
            track = definition_tracks.get(track_key)
            if track is not None:
                found = await View.find(
                    {"context.track_id": track.id, "context.name": label}
                )
                if found:
                    row.update(status="verified", references=[found[0].id])
        if row["status"] == "verified":
            verified_count += 1
        else:
            if kind == "app_dependency":
                row["explanation"] = (
                    "No active App in this workspace satisfies the required dependency "
                    "identity and minimum version."
                )
            else:
                row["explanation"] = (
                    "No generic runtime verifier is registered for this requirement kind."
                )
        evidence.append(row)
    definition.materialization_evidence = evidence
    definition.verified_at = now
    await definition.save()
    return {
        "total": len(evidence),
        "verified": verified_count,
        "not_evaluated": len(evidence) - verified_count,
    }
