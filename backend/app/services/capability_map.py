"""Generated capability map (W0.4).

A deterministic projection of what the resident and MCP clients can reach:
intent (skill) → tool → dispatch target → REST route / MCP scope, plus the
ADR-012 capability descriptors and an App-skill dependency report.

Nobody edits the output by hand. ``backend/scripts/generate_capability_map.py``
writes it; ``tests/test_capability_map.py`` fails when the committed copy is
stale. Sources:

* ``tooling.catalogue.build_tool_catalogue`` — advertised, dispatchable tools
* ``agent_skills.build_tool_catalogue_for_editor`` — the Settings → Skills rows
* ``capability_catalogue.compile`` — Core descriptors plus the queries and
  operations an installed App declares (``describe_capabilities`` serves the
  per-workspace compilation of the same)
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FIXTURE_ROOTS = (REPO_ROOT / "examples",)
MAP_JSON_PATH = REPO_ROOT / "docs" / "generated" / "capability-map.json"
MAP_MD_PATH = REPO_ROOT / "docs" / "generated" / "capability-map.md"

_MCP_SCOPES = ("integral:read", "integral:propose", "integral:execute")
_BACKTICK_RE = re.compile(r"`([a-z][a-z0-9_]*)`")
_CALL_RE = re.compile(r"\b([a-z][a-z0-9_]*)\(")


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _ref_targets(ref: Any) -> List[str]:
    """Dotted targets behind a binding ref, read without importing them."""
    if ref is None:
        return []
    nonlocals = inspect.getclosurevars(ref).nonlocals
    if "modpath" in nonlocals and "name" in nonlocals:
        return [f"{nonlocals['modpath']}.{nonlocals['name']}"]
    if "routes" in nonlocals:
        return sorted({f"{mod}.{fn}" for mod, fn, *_ in nonlocals["routes"].values()})
    return [f"{ref.__module__}.{ref.__qualname__}"]


def _dispatch(name: str, binding: Any) -> Dict[str, Any]:
    from app.agentive.tooling.catalogue import _INTERCEPTED_EPHEMERAL_TOOLS
    from app.agentive.tooling.dispatch import _BATCH_CONTROL_TOOLS

    if name in _BATCH_CONTROL_TOOLS or name in _INTERCEPTED_EPHEMERAL_TOOLS:
        return {"seam": "intercept", "targets": ["app.agentive.tooling.dispatch"]}
    if binding is None:
        return {"seam": "unbound", "targets": []}
    for seam in ("handler_ref", "service_ref", "stager", "direct_ref"):
        ref = getattr(binding, seam)
        if ref is not None:
            return {"seam": seam.replace("_ref", ""), "targets": _ref_targets(ref)}
    return {"seam": "unbound", "targets": []}


def _min_mcp_scope(op_class: str) -> Optional[str]:
    from app.agentive.mcp.server import allowed_op_classes_for_scopes

    for scope in _MCP_SCOPES:
        if op_class in (allowed_op_classes_for_scopes([scope]) or ()):
            return scope
    return None


def _frontmatter(path: Path) -> tuple[Dict[str, Any], str]:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        return {}, raw
    _, head, body = raw.split("---", 2)
    return (yaml.safe_load(head) or {}), body


def _core_skills(tool_names: set[str]) -> List[Dict[str, Any]]:
    from app.services.skill_compliance import (
        CORE_INTEGRAL_SKILL_NAMES,
        _normalize_allowed_tools,
        iter_core_skill_paths,
    )

    core_names = set(CORE_INTEGRAL_SKILL_NAMES)
    skills = []
    for path in iter_core_skill_paths():
        meta, body = _frontmatter(path)
        refs = set(_BACKTICK_RE.findall(body)) | set(_CALL_RE.findall(body))
        integral_refs = {r for r in refs if r.startswith("integral_")}
        skills.append(
            {
                "name": path.parent.name,
                "intent": " ".join(str(meta.get("description") or "").split()),
                "source": _rel(path),
                "allowed_tools": sorted(
                    _normalize_allowed_tools(meta.get("allowed-tools"))
                ),
                "body_tools": sorted(integral_refs & tool_names),
                "delegates_to": sorted(
                    (integral_refs & core_names) - {path.parent.name}
                ),
                "unresolved_refs": sorted(integral_refs - tool_names - core_names),
            }
        )
    return skills


def _core_descriptors() -> List[Dict[str, Any]]:
    from app.services.capability_catalogue.compile import _core_descriptors

    return [
        {
            "id": f"{d.namespace}.{d.key}",
            "kind": d.kind,
            "effects": d.effects,
            "policy_action": d.policy_action,
        }
        for d in _core_descriptors()
    ]


def _keys(items: Iterable[Any]) -> List[str]:
    out = []
    for item in items or []:
        key = item if isinstance(item, str) else (item or {}).get("key")
        if key:
            out.append(str(key))
    return out


def _app_report(
    bundle_dir: Path, catalogue: Dict[str, Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    from app.services.operational_model_loader import _assemble_manifest
    from app.services.skill_compliance import CORE_INTEGRAL_SKILL_NAMES

    core_skill_names = set(CORE_INTEGRAL_SKILL_NAMES)
    model_path = bundle_dir / "operational-model.yaml"
    if not model_path.is_file():
        return None
    raw = yaml.safe_load(model_path.read_text(encoding="utf-8")) or {}
    package = raw.get("package") or {}
    app = _assemble_manifest(raw).get("app") or {}
    operations = {str(o["key"]): o for o in app.get("operations") or [] if o.get("key")}
    queries = {str(q["key"]): q for q in app.get("queries") or [] if q.get("key")}
    app_capabilities = set(operations) | set(queries) | set(_keys(app.get("tools")))
    tracks = [
        (str(t["key"]), str(t.get("name") or t["key"]))
        for t in app.get("tracks") or []
        if t.get("key")
    ]
    skill_meta = {
        (s if isinstance(s, str) else s.get("key")): ({} if isinstance(s, str) else s)
        for s in app.get("skills") or []
    }

    skills = []
    for path in sorted((bundle_dir / "skills").glob("*/SKILL.md")):
        key = path.parent.name
        meta, body = _frontmatter(path)
        declared = skill_meta.get(key) or {}
        backticks = set(_BACKTICK_RE.findall(body))
        calls = set(_CALL_RE.findall(body))
        named = (backticks | calls | set(declared.get("tools_required") or [])) | set(
            meta.get("allowed-tools") or []
        )
        core_used = sorted(n for n in named if n in catalogue)
        lowered = body.lower()
        skills.append(
            {
                "key": key,
                "source": _rel(path),
                "private": bool(declared.get("private", False)),
                "same_app_focus": (
                    "required" if declared.get("private") else "not_required"
                ),
                "app_capabilities": sorted(named & app_capabilities),
                "core_generic_reads": [
                    n for n in core_used if catalogue[n]["op_class"] == "read"
                ],
                "core_writes": [
                    n for n in core_used if catalogue[n]["op_class"] != "read"
                ],
                "target_tracks": [
                    k
                    for k, label in tracks
                    if re.search(rf"\b{re.escape(k)}\b", lowered)
                    or re.search(rf"\b{re.escape(label.lower())}\b", lowered)
                ],
                "delegates_to": sorted(named & core_skill_names),
                "unresolved_calls": sorted(
                    n
                    for n in calls | {n for n in named if n.startswith("integral_")}
                    if n not in app_capabilities
                    and n not in catalogue
                    and n not in core_skill_names
                ),
            }
        )

    return {
        "slug": str(package.get("slug") or bundle_dir.name),
        "version": str(package.get("version") or ""),
        "class": str(package.get("class") or ""),
        "trust_tier": str(package.get("trust_tier") or ""),
        "source": _rel(bundle_dir),
        "tracks": [k for k, _ in tracks],
        "operations": [
            {
                "key": k,
                "kind": str(o.get("kind") or ""),
                "policy_action": str(o.get("policy_action") or ""),
            }
            for k, o in sorted(operations.items())
        ],
        "queries": [
            {"key": k, "policy_action": str(q.get("policy_action") or "")}
            for k, q in sorted(queries.items())
        ],
        "skills": skills,
    }


def build_capability_map(
    fixture_roots: Sequence[Path] = DEFAULT_FIXTURE_ROOTS,
) -> Dict[str, Any]:
    """Project the tool catalogue, skills, and App fixtures into one map."""
    from app.agentive.services.agent_skills import build_tool_catalogue_for_editor
    from app.agentive.tooling.bindings import TOOL_BINDINGS
    from app.agentive.tooling.catalogue import build_tool_catalogue
    from app.agentive.tooling.manifest import DEFAULT_MANIFEST_PATH, load_manifest

    manifest = load_manifest()
    raw_domains = yaml.safe_load(DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8"))[
        "domains"
    ]
    domain_of = {
        str(entry["name"]): key
        for key, domain in raw_domains.items()
        for entry in (domain or {}).get("tools") or []
    }
    advertised = {t["name"]: t for t in build_tool_catalogue()}
    editor = {r["name"] for r in asyncio.run(build_tool_catalogue_for_editor())}
    skills = _core_skills(set(advertised))

    allowed_by: Dict[str, List[str]] = {}
    for skill in skills:
        for tool in skill["allowed_tools"]:
            allowed_by.setdefault(tool, []).append(skill["name"])

    tools = []
    for name, spec in manifest.items():
        tools.append(
            {
                "name": name,
                "domain": domain_of.get(name, ""),
                "status": spec.status,
                "op_class": spec.op_class,
                "policy_action": spec.policy_action,
                "advertised": name in advertised,
                "dispatch": _dispatch(name, TOOL_BINDINGS.get(name)),
                "http": (
                    None
                    if spec.http.method == "SERVICE"
                    else f"{spec.http.method} {spec.http.path}"
                ),
                "mcp_min_scope": (
                    _min_mcp_scope(spec.op_class) if name in advertised else None
                ),
                "skills": sorted(allowed_by.get(name, [])),
            }
        )

    apps = []
    for root in fixture_roots:
        for bundle_dir in sorted(p for p in Path(root).iterdir() if p.is_dir()):
            report = _app_report(bundle_dir, advertised)
            if report is not None:
                apps.append(report)

    skill_names = {s["name"] for s in skills}
    diagnostics = {
        "skill_tools_not_advertised": sorted(
            f"{s['name']}: {t}"
            for s in skills
            for t in s["allowed_tools"]
            if t not in advertised
        ),
        "skill_unresolved_refs": sorted(
            f"{s['name']}: {r}" for s in skills for r in s["unresolved_refs"]
        ),
        "existing_tools_not_dispatchable": sorted(
            t["name"]
            for t in tools
            if t["status"] == "existing" and not t["advertised"]
        ),
        "gap_tools": sorted(t["name"] for t in tools if t["status"] != "existing"),
        "advertised_tools_without_skill": sorted(
            t["name"] for t in tools if t["advertised"] and not t["skills"]
        ),
        "editor_catalogue_mismatch": sorted(editor ^ set(advertised)),
        "app_skill_unresolved_calls": sorted(
            f"{a['slug']}/{s['key']}: {c}"
            for a in apps
            for s in a["skills"]
            for c in s["unresolved_calls"]
        ),
        "delegation_to_unknown_skill": sorted(
            f"{s['name']}: {d}"
            for s in skills
            for d in s["delegates_to"]
            if d not in skill_names
        ),
    }

    return {
        "generator": "backend/scripts/generate_capability_map.py",
        "counts": {
            "tools": len(tools),
            "advertised": len(advertised),
            "by_op_class": {
                op: sum(1 for t in tools if t["advertised"] and t["op_class"] == op)
                for op in ("read", "propose", "execute")
            },
            "core_skills": len(skills),
            "apps": len(apps),
        },
        "tools": tools,
        "core_skills": skills,
        "capability_descriptors": _core_descriptors(),
        "apps": apps,
        "diagnostics": diagnostics,
    }


def render_json(cap_map: Dict[str, Any]) -> str:
    """Serialize the map with stable key order."""
    return json.dumps(cap_map, indent=2, sort_keys=True) + "\n"


def render_markdown(cap_map: Dict[str, Any]) -> str:
    """Render the human-readable view of the map."""
    counts = cap_map["counts"]
    lines = [
        "# Capability map",
        "",
        "Generated by `backend/scripts/generate_capability_map.py` from the tool",
        "manifest, bindings, core skills, and App fixtures under `examples/`. Do",
        "not edit; `tests/test_capability_map.py` fails when this file is stale.",
        "The JSON beside it carries every field.",
        "",
        f"{counts['advertised']} of {counts['tools']} manifest tools are advertised "
        f"({counts['by_op_class']['read']} read, {counts['by_op_class']['propose']} "
        f"propose, {counts['by_op_class']['execute']} execute) across "
        f"{counts['core_skills']} core skills.",
        "",
        "## Skills → tools",
        "",
        "| Skill | Intent | Allowed tools | Delegates to |",
        "| --- | --- | --- | --- |",
    ]
    for s in cap_map["core_skills"]:
        intent = s["intent"].replace("|", "\\|")
        lines.append(
            f"| `{s['name']}` | {intent} | {len(s['allowed_tools'])} | "
            f"{', '.join(f'`{d}`' for d in s['delegates_to']) or '—'} |"
        )
    lines += [
        "",
        "## Tools → dispatch → surfaces",
        "",
        "| Tool | Op class | Dispatch | REST route | Min MCP scope | Skills |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for t in cap_map["tools"]:
        if not t["advertised"]:
            continue
        target = ", ".join(t["dispatch"]["targets"]) or "—"
        lines.append(
            f"| `{t['name']}` | {t['op_class']} | {t['dispatch']['seam']}: `{target}` | "
            f"{t['http'] or '—'} | `{t['mcp_min_scope']}` | "
            f"{', '.join(t['skills']) or '—'} |"
        )
    lines += [
        "",
        "## App skill dependencies",
        "",
        "Calls are read from each SKILL.md (backticked names, call forms,",
        "`allowed-tools`) and its manifest `tools_required`. Target Tracks are",
        "textual matches on a Track key or name. Private skills resolve only",
        "in the same App's context; public ones resolve workspace-wide.",
        "",
    ]
    for a in cap_map["apps"]:
        lines += [
            f"### {a['slug']} {a['version']} (`{a['source']}`)",
            "",
            "| Skill | Same-App focus | App capabilities | Core generic reads | "
            "Core writes | Target Tracks | Unresolved |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for s in a["skills"]:
            cells = [
                ", ".join(f"`{x}`" for x in s[k]) or "—"
                for k in (
                    "app_capabilities",
                    "core_generic_reads",
                    "core_writes",
                    "target_tracks",
                    "unresolved_calls",
                )
            ]
            lines.append(
                f"| `{s['key']}` | {s['same_app_focus']} | " + " | ".join(cells) + " |"
            )
        lines.append("")
    lines += ["## Diagnostics", ""]
    for key, values in cap_map["diagnostics"].items():
        lines.append(f"- **{key}** ({len(values)}): " + (", ".join(values) or "none"))
    return "\n".join(lines) + "\n"
