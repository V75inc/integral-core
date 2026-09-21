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
from app.models.nodes import App, ApplicationDefinition, ContentProfile, Skill, Track
from app.services.content_profile_diff import compute_manifest_diff
from app.services.content_profile_runtime import compile_canonical_manifest
from app.utils.time import utc_now_iso


def definition_fingerprint(canonical_manifest: Dict[str, Any]) -> str:
    """Return the stable identity of a compiler-validated contract."""
    encoded = json.dumps(
        canonical_manifest, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
    fingerprint = definition_fingerprint(canonical)
    current = await get_active_application_definition(app_node)
    if current is not None and current.manifest_fingerprint == fingerprint:
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
    skills = await app_node.nodes(
        edge=["CONTAINS"], direction="out", node=["Skill"], limit=500
    )
    skill_by_key = {
        str(getattr(skill, "key", "") or ""): skill
        for skill in skills
        if isinstance(skill, Skill)
    }
    source_profile_id = str(getattr(definition, "source_profile_id", "") or "")
    source_profile = (
        await ContentProfile.get(source_profile_id) if source_profile_id else None
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
        if row["status"] == "verified":
            verified_count += 1
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
