"""Blueprint revisions and structural plan fidelity for App designs (W1.3)."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any, Dict, Iterator, List, Optional, Tuple

from pydantic import ValidationError

from app.schemas.design_blueprint import DesignBlueprint
from app.services.operational_model_compile import slug_manifest_key

_TRACK_REF = re.compile(r"^\{\{track\.id:(.+)\}\}$")
_TAG_REF = re.compile(r"^\{\{tag[._]id:(.+)\}\}$")


def tag_ref_name(ref: str) -> str:
    """The tag name inside ``{{tag.id:Name}}``, or ``ref`` itself."""
    match = _TAG_REF.match(ref.strip())
    return (match.group(1) if match else ref).strip()


def _group_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")


def validate_blueprint(raw: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return ``(canonical_dict, None)`` or ``(None, readable_error)``."""
    try:
        blueprint = DesignBlueprint.model_validate(raw)
    except ValidationError as exc:
        problems = [
            f"{'.'.join(str(p) for p in err['loc']) or 'blueprint'}: {err['msg']}"
            for err in exc.errors()[:8]
        ]
        return None, "; ".join(problems)
    return blueprint.model_dump(mode="json"), None


def blueprint_digest(blueprint: Dict[str, Any]) -> str:
    """Content digest of a canonical blueprint dict."""
    canonical = json.dumps(blueprint, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_child(value: Any) -> bool:
    if isinstance(value, dict):
        return "id" in value
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(x, dict) and "id" in x for x in value)
    )


def _item_contents(value: Any) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """Each identified item's own fields, excluding its identified children."""
    if isinstance(value, dict):
        if "id" in value:
            yield str(value["id"]), {k: v for k, v in value.items() if not _is_child(v)}
        for nested in value.values():
            yield from _item_contents(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _item_contents(nested)


def blueprint_diff(
    prior: Optional[Dict[str, Any]], current: Dict[str, Any]
) -> Dict[str, List[str]]:
    """Item-ID diff between two blueprint revisions."""
    before = dict(_item_contents(prior or {}))
    after = dict(_item_contents(current))
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "changed": sorted(
            item_id
            for item_id in set(before) & set(after)
            if before[item_id] != after[item_id]
        ),
    }


def _plan_fields(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        field
        for entry_type in params.get("entry_types") or []
        if isinstance(entry_type, dict)
        for field in entry_type.get("fields") or []
        if isinstance(field, dict)
    ]


def _track_name(ref: Any) -> str:
    match = _TRACK_REF.match(str(ref or ""))
    return (match.group(1) if match else str(ref or "")).strip().casefold()


def plan_fidelity_errors(
    blueprint: Dict[str, Any],
    operations: List[Tuple[str, Dict[str, Any]]],
    *,
    new_app: bool,
) -> List[str]:
    """Compare a compiled scaffold plan with the approved blueprint.

    ``operations`` are the expanded ``(tool, args)`` pairs about to be staged.
    Every approved constituent the builder can create must appear exactly;
    anything the plan adds beyond the blueprint is drift, not initiative.
    """
    errors: List[str] = []
    if blueprint.get("open_decisions"):
        open_ids = ", ".join(d["id"] for d in blueprint["open_decisions"])
        return [
            f"The design still has open decisions ({open_ids}); resolve them first."
        ]
    templates = blueprint.get("track_templates") or []
    tagged_templates = [t["id"] for t in templates if t.get("tag_groups")]
    if tagged_templates:
        errors.append(
            "The build cannot yet create tags on track templates: "
            + ", ".join(sorted(tagged_templates))
            + ". Amend the design to drop them or add them after the build."
        )
    template_keys = {t["id"]: slug_manifest_key(t["name"]) for t in templates}

    by_tool: Dict[str, List[Dict[str, Any]]] = {}
    for tool, params in operations:
        by_tool.setdefault(tool, []).append(params)

    def compare_fields(
        label: str, approved: List[Dict[str, Any]], params: Dict[str, Any]
    ) -> None:
        planned = _plan_fields(params)
        planned_by_key = {
            str(f.get("key") or "").casefold(): f for f in planned if f.get("key")
        }
        planned_by_name = {str(f.get("name") or "").casefold(): f for f in planned}
        matched: set[int] = set()
        for spec in approved:
            hit = planned_by_key.get(spec["key"]) or planned_by_name.get(
                spec["name"].casefold()
            )
            if hit is None:
                errors.append(
                    f"{label} omits approved field {spec['key']} ({spec['name']})."
                )
                continue
            matched.add(id(hit))
            if hit.get("type") and hit["type"] != spec["type"]:
                errors.append(
                    f"Field {spec['key']} must be {spec['type']}, not {hit['type']}."
                )
            relation = spec.get("relation") or {}
            if relation.get("target") == "track":
                want = template_keys.get(relation["target_track_template"])
                got = hit.get("relation") or {}
                if (
                    got.get("target") != "track"
                    or got.get("target_track_template") != want
                ):
                    errors.append(
                        f"Field {spec['key']} must anchor to track template {want!r} "
                        "with relation.target='track'."
                    )
        for extra in (f for f in planned if id(f) not in matched):
            errors.append(
                f"{label} adds field {extra.get('key') or extra.get('name')!r}, "
                "which is not in the design."
            )

    approved_templates = {t["name"].casefold(): t for t in templates}
    planned_templates = {
        str(p.get("name") or "").casefold(): p
        for p in by_tool.get("integral_register_track_template", [])
    }
    for name in sorted(set(approved_templates) - set(planned_templates)):
        errors.append(
            f"The plan omits approved track template {approved_templates[name]['name']!r}."
        )
    for name in sorted(set(planned_templates) - set(approved_templates)):
        errors.append(f"The plan adds track template {name!r}, not in the design.")
    for name in sorted(set(approved_templates) & set(planned_templates)):
        compare_fields(
            f"Track template {approved_templates[name]['name']!r}",
            [f for et in approved_templates[name]["entry_types"] for f in et["fields"]],
            planned_templates[name],
        )

    app_name = blueprint["app"]["name"].casefold()
    if new_app:
        planned_apps = [
            str(p.get("name") or "").casefold()
            for p in by_tool.get("integral_create_app", [])
        ]
        if planned_apps != [app_name]:
            errors.append(
                f"The plan must create exactly the approved App {blueprint['app']['name']!r}."
            )

    tracks = {t["id"]: t for t in blueprint["tracks"]}
    track_names = {t["name"].casefold(): t for t in blueprint["tracks"]}
    planned_tracks = {
        str(p.get("name") or "").casefold(): p
        for p in by_tool.get("integral_create_app_track", [])
    }
    for name in sorted(set(track_names) - set(planned_tracks)):
        errors.append(f"The plan omits approved Track {track_names[name]['name']!r}.")
    for name in sorted(set(planned_tracks) - set(track_names)):
        errors.append(f"The plan adds Track {name!r}, which is not in the design.")

    for name in sorted(set(track_names) & set(planned_tracks)):
        label = track_names[name]["name"]
        compare_fields(
            f"Track {label!r}",
            [f for et in track_names[name]["entry_types"] for f in et["fields"]],
            planned_tracks[name],
        )
        approved_tags = {
            tag.casefold(): (_group_key(group["name"]), tag, group["name"])
            for group in track_names[name].get("tag_groups") or []
            for tag in group["tags"]
        }
        planned_tags: Dict[str, Optional[str]] = {
            str(tag["name"]).casefold(): _group_key(str(group.get("key") or ""))
            for group in (planned_tracks[name].get("taxonomy") or {}).get("tag_groups")
            or []
            for tag in group.get("tags") or []
        }
        for p in by_tool.get("integral_create_tag", []):
            if _track_name(p.get("track_id")) == name:
                planned_tags[str(p.get("name") or "").casefold()] = (
                    _group_key(str(p["group_key"])) if p.get("group_key") else None
                )
        for tag_fold, (group_key, tag, group_name) in sorted(approved_tags.items()):
            if tag_fold not in planned_tags:
                errors.append(
                    f"Track {label!r} omits approved tag {tag!r} ({group_name})."
                )
            elif planned_tags[tag_fold] not in (None, group_key):
                errors.append(
                    f"Tag {tag!r} on {label!r} belongs to group {group_name!r}."
                )
        for tag_fold in sorted(set(planned_tags) - set(approved_tags)):
            errors.append(
                f"Track {label!r} adds tag {tag_fold!r}, which is not in the design."
            )

    display = {}
    approved_views = set()
    for v in blueprint.get("views") or []:
        key = (tracks[v["track"]]["name"].casefold(), v["name"].casefold(), v["type"])
        approved_views.add(key)
        display[key] = (tracks[v["track"]]["name"], v["name"])
    planned_views = {
        (
            _track_name(p.get("track_id")),
            str(p.get("name") or "").casefold(),
            str(p.get("view_type") or ""),
        )
        for p in by_tool.get("integral_save_view", [])
    }
    for key in sorted(approved_views - planned_views):
        track, view = display[key]
        errors.append(f"The plan omits approved {key[2]} view {view!r} on {track!r}.")
    for track, view, kind in sorted(planned_views - approved_views):
        errors.append(
            f"The plan adds {kind} view {view!r} on {track!r}, not in the design."
        )

    approved_seeds = set()
    for s in blueprint.get("seeds") or []:
        key = (tracks[s["track"]]["name"].casefold(), s["title"].casefold())
        approved_seeds.add(key)
        display[key] = (tracks[s["track"]]["name"], s["title"])
    planned_seeds = {
        (_track_name(p.get("track_id")), str(p.get("title") or "").casefold())
        for p in by_tool.get("integral_create_entry", [])
    }
    for key in sorted(approved_seeds - planned_seeds):
        track, title = display[key]
        errors.append(f"The plan omits approved seed {title!r} on {track!r}.")
    for track, title in sorted(planned_seeds - approved_seeds):
        errors.append(
            f"The plan adds seed {title!r} on {track!r}; the design has none such."
        )
    planned_seed_tags = {
        (_track_name(p.get("track_id")), str(p.get("title") or "").casefold()): {
            tag_ref_name(str(tag)).casefold() for tag in p.get("tags") or []
        }
        for p in by_tool.get("integral_create_entry", [])
    }
    for seed in blueprint.get("seeds") or []:
        identity = (tracks[seed["track"]]["name"].casefold(), seed["title"].casefold())
        want = {tag.casefold() for tag in seed.get("tags") or []}
        if identity in planned_seed_tags and planned_seed_tags[identity] != want:
            errors.append(
                f"Seed {seed['title']!r} must carry tags {sorted(want)}, "
                f"not {sorted(planned_seed_tags[identity])}."
            )

    has_dashboard = bool(by_tool.get("integral_create_dashboard"))
    if bool(blueprint.get("dashboard")) != has_dashboard:
        errors.append(
            "The approved dashboard is missing from the plan."
            if blueprint.get("dashboard")
            else "The plan adds a dashboard the design does not include."
        )
    approved_skills = {s["name"].casefold() for s in blueprint.get("skills") or []}
    planned_skills = {
        str(p.get("name") or "").casefold()
        for p in by_tool.get("integral_author_skill", [])
    }
    if approved_skills != planned_skills:
        errors.append(
            "Authored skills must match the design exactly: "
            f"missing {sorted(approved_skills - planned_skills)}, "
            f"extra {sorted(planned_skills - approved_skills)}."
        )
    approved_routines = Counter(
        r.get("cron") or r.get("run_at") for r in blueprint.get("routines") or []
    )
    planned_routines = Counter(
        p.get("cron") or p.get("run_at")
        for p in by_tool.get("integral_schedule_task", [])
    )
    if approved_routines != planned_routines:
        errors.append("Scheduled routines must match the design's schedules exactly.")
    return errors
