"""Workspace skills editor — list, resolve, and mutate workspace skill surface."""

from __future__ import annotations

import glob
import hashlib
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.agentive.services.skill_registry import (
    _live_mcp_tool_names,
    get_callable_skills,
)
from app.agentive.workspace_agent_profile import (
    _app_slug,
    _resolve_bundle_dir_async,
    _resolve_prompt_body,
    invalidate_workspace_profile,
    resolve_bundle_default_body,
    resolve_skill_domain_body,
)
from app.exceptions import InvalidToolReferenceError
from app.models.edges import CONTAINS
from app.models.nodes import App, Skill, Workspace
from app.utils.time import utc_now_iso

_INTEGRAL_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
_EMBEDDED_INTEGRAL_SKILLS_GLOB = os.path.join(
    _INTEGRAL_REPO_ROOT,
    "agent",
    "agents",
    "integral",
    "integral_agent",
    "actions",
    "integral",
    "embedded_integral_action",
    "skills",
    "integral_*",
    "SKILL.md",
)
_INTEGRAL_AGENT_APP_ROOT = os.path.join(_INTEGRAL_REPO_ROOT, "agent")
_RESIDENT_AGENT_NAMESPACE = "integral"
_RESIDENT_AGENT_NAME = "integral_agent"

_RESERVED_CORE_NAMES: Set[str] = set()
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_UNTRUSTED_OVERLAY_START = "<!-- UNTRUSTED_WORKSPACE_SKILL_OVERLAY -->"
_UNTRUSTED_OVERLAY_END = "<!-- END_UNTRUSTED_WORKSPACE_SKILL_OVERLAY -->"

_BODY_INJECTION_PATTERNS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"ignore\s+staging", re.IGNORECASE), "ignore_staging"),
    (re.compile(r"\byou\s+are\s+now\b", re.IGNORECASE), "role_override"),
    (re.compile(r"(?:^|\n)\s*SYSTEM\s*:", re.IGNORECASE), "system_marker"),
    (re.compile(r"\[SYSTEM:", re.IGNORECASE), "system_marker"),
)


def _body_digest(body: str) -> str:
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


def _friendly_tool_label(name: str) -> str:
    parts = name.replace("integral_", "").split("_")
    return " ".join(p.capitalize() for p in parts if p)


def _param_summary(input_schema: Dict[str, Any]) -> str:
    props = (input_schema or {}).get("properties") or {}
    keys = list(props.keys())
    if not keys:
        return "No parameters"
    if len(keys) <= 4:
        return ", ".join(keys)
    return ", ".join(keys[:4]) + f" +{len(keys) - 4} more"


async def build_tool_catalogue_for_editor() -> List[Dict[str, Any]]:
    """Flatten the live MCP tool catalogue into editor-friendly rows (name, label, param summary)."""
    from app.agentive.tooling import build_tool_catalogue

    out: List[Dict[str, Any]] = []
    for entry in build_tool_catalogue():
        schema = entry.get("input_schema") or {}
        out.append(
            {
                "name": entry["name"],
                "friendly_label": _friendly_tool_label(entry["name"]),
                "description": entry.get("description") or "",
                "param_summary": _param_summary(schema),
            }
        )
    return out


async def validate_tools_required(tools: List[str]) -> None:
    """Raise if any tool name isn't a live MCP tool the workspace agent can call."""
    if not tools:
        return
    live = await _live_mcp_tool_names()
    unknown = [t for t in tools if t not in live]
    if unknown:
        raise InvalidToolReferenceError(
            message=f"unknown MCP tools: {sorted(unknown)}",
            details={"unknown_tools": sorted(unknown)},
        )


def validate_skill_description(description: str) -> None:
    """Workspace skills require a non-empty one-line description."""
    if not str(description or "").strip():
        from app.exceptions import SkillRegistrationError

        raise SkillRegistrationError(
            message="skill description is required",
            details={"field": "description"},
        )


def validate_body_override(body: str) -> None:
    """Reject workspace skill bodies that carry prompt-injection patterns."""
    text = str(body or "")
    for pattern, code in _BODY_INJECTION_PATTERNS:
        if pattern.search(text):
            from app.exceptions import SkillRegistrationError

            raise SkillRegistrationError(
                message=f"body_override rejected: forbidden pattern ({code})",
                details={"pattern": code, "field": "body_override"},
            )


def wrap_untrusted_overlay_body(body: str) -> str:
    """Wrap an untrusted workspace overlay body with explicit delimiters."""
    stripped = str(body or "").strip()
    if not stripped:
        return stripped
    return f"{_UNTRUSTED_OVERLAY_START}\n{stripped}\n{_UNTRUSTED_OVERLAY_END}"


async def skill_body_warnings(body: str) -> List[Dict[str, str]]:
    """Non-blocking compliance warnings for workspace skill bodies."""
    from app.services.skill_compliance import check_skill_body

    known = await _live_mcp_tool_names()
    return [
        {"code": i.code, "message": i.message}
        for i in check_skill_body(
            body or "",
            tier="bundle_public",
            known_tool_names=known,
        )
        if i.severity == "warning"
    ]


def _load_core_skill_names() -> Set[str]:
    global _RESERVED_CORE_NAMES
    if _RESERVED_CORE_NAMES:
        return _RESERVED_CORE_NAMES
    paths = glob.glob(_EMBEDDED_INTEGRAL_SKILLS_GLOB)
    names = {os.path.basename(os.path.dirname(p)) for p in paths}
    _RESERVED_CORE_NAMES = names
    return names


def _core_skill_dir(skill_name: str) -> str:
    return os.path.join(
        _INTEGRAL_REPO_ROOT,
        "agent",
        "agents",
        "integral",
        "integral_agent",
        "actions",
        "integral",
        "embedded_integral_action",
        "skills",
        skill_name,
    )


def _parse_core_skill_disk(skill_name: str) -> Tuple[str, str, List[str]]:
    """Return (description, domain_body, allowed_tools) from on-disk SKILL.md."""
    from jvagent.scaffold.skill_resolve import parse_skill_bundle

    skill_dir = _core_skill_dir(skill_name)
    parsed = parse_skill_bundle(Path(skill_dir), source="builtin")
    if not parsed:
        return "", "", []
    return (
        str(parsed.get("description") or "").strip(),
        str(parsed.get("content") or "").strip(),
        list(parsed.get("allowed_tools") or []),
    )


def _resolve_core_skill_body(skill_name: str) -> Tuple[str, str, List[str]]:
    """Return (resolved_body, description, tools) for a core integral_* skill."""
    description, domain_body, tools = _parse_core_skill_disk(skill_name)
    try:
        from jvagent.scaffold.skill_resolve import resolve_merged_skill_bundles

        bundles = resolve_merged_skill_bundles(
            _INTEGRAL_AGENT_APP_ROOT,
            _RESIDENT_AGENT_NAMESPACE,
            _RESIDENT_AGENT_NAME,
            include_builtin=False,
        )
        bundle = bundles.get(skill_name) or {}
        resolved = str(bundle.get("content") or domain_body).strip()
        tools = list(bundle.get("allowed_tools") or tools)
        return resolved, description, tools
    except Exception:
        skill_path = os.path.join(_core_skill_dir(skill_name), "SKILL.md")
        if os.path.isfile(skill_path):
            with open(skill_path, encoding="utf-8") as fh:
                return fh.read().strip(), description, tools
        return domain_body, description, tools


def _resolve_core_skill_domain_body(skill_name: str) -> str:
    """Domain SOP only (post-frontmatter, pre-extends merge)."""
    _, domain_body, _ = _parse_core_skill_disk(skill_name)
    return domain_body


def list_core_skills() -> List[Dict[str, Any]]:
    """Read-only summaries of every filesystem-defined core `integral_*` skill."""
    names = sorted(_load_core_skill_names())
    out: List[Dict[str, Any]] = []
    for name in names:
        resolved, description, tools = _resolve_core_skill_body(name)
        domain_body = _resolve_core_skill_domain_body(name)
        out.append(
            {
                "id": f"core:{name}",
                "source": "core",
                "read_only": True,
                "key": name,
                "name": name.replace("integral_", "").replace("_", " ").title(),
                "description": description,
                "kind": "declarative",
                "enabled": True,
                "customized": False,
                "stale_default": False,
                "tools_required": tools,
                "resolved_body": resolved,
                "domain_body": domain_body or resolved,
            }
        )
    return out


def is_core_skill_id(skill_id: str) -> bool:
    """True for the synthetic `core:<name>` id scheme used by filesystem skills (never a graph node id)."""
    return str(skill_id or "").startswith("core:")


def parse_core_skill_id(skill_id: str) -> Optional[str]:
    """Extract the bare skill name from a `core:<name>` id, or None if not a core id."""
    if not is_core_skill_id(skill_id):
        return None
    return skill_id.split(":", 1)[1]


def validate_skill_key(key: str) -> None:
    """Raise unless `key` is lowercase snake_case and not reserved (integral_* prefix or a core skill name)."""
    from app.exceptions import SkillRegistrationError

    k = (key or "").strip()
    if not k or not _KEY_RE.match(k):
        raise SkillRegistrationError(
            message="skill key must be lowercase snake_case",
            details={"key": key},
        )
    if k.startswith("integral_") or k in _load_core_skill_names():
        raise SkillRegistrationError(
            message=f"skill key {k!r} is reserved",
            details={"key": k},
        )


async def resolve_bundle_default_body_for_skill(
    skill: Skill,
    *,
    app: Optional[App] = None,
) -> Tuple[Optional[str], str]:
    return await resolve_bundle_default_body(skill, app=app)


async def resolve_skill_bodies(
    skill: Skill,
    *,
    app: Optional[App] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (resolved_body, bundle_default_body, domain_body)."""
    bundle_dir = None
    if app is not None:
        bundle_dir = await _resolve_bundle_dir_async(app)

    domain_body = await resolve_skill_domain_body(skill, app=app)
    default_merged, _ = await resolve_bundle_default_body(skill, app=app)
    resolved, _ = _resolve_prompt_body(
        skill,
        bundle_dir=bundle_dir,  # type: ignore[arg-type]
        tools_required=list(getattr(skill, "tools_required", None) or []),
    )
    return resolved, default_merged, domain_body


async def resolve_skill_description(
    skill: Skill,
    *,
    app: Optional[App] = None,
) -> str:
    """Discovery description: Skill node, else SKILL.md frontmatter on disk."""
    desc = str(getattr(skill, "description", "") or "").strip()
    if desc:
        return desc
    if app is None:
        app_id = str(getattr(skill, "app_id", "") or "")
        if app_id:
            app = await App.get(app_id)
    if app is None:
        return ""
    bundle_dir = await _resolve_bundle_dir_async(app)
    from app.agentive.workspace_agent_profile import locate_skill_disk_path

    skill_path = locate_skill_disk_path(skill, bundle_dir=bundle_dir)
    if skill_path is None:
        return ""
    try:
        from jvagent.scaffold.skill_resolve import parse_skill_bundle

        parsed = parse_skill_bundle(skill_path.parent, source="app")
        if parsed:
            return str(parsed.get("description") or "").strip()
    except Exception:
        pass
    return ""


async def resolve_skill_tools_from_disk(
    skill: Skill,
    *,
    app: Optional[App] = None,
) -> List[str]:
    """tools_required from SKILL.md allowed-tools when disk is reachable."""
    if app is None:
        app_id = str(getattr(skill, "app_id", "") or "")
        if app_id:
            app = await App.get(app_id)
    if app is None:
        return list(getattr(skill, "tools_required", None) or [])
    from app.agentive.workspace_agent_profile import (
        _resolve_bundle_dir_async,
        allowed_tools_from_skill_path,
        locate_skill_disk_path,
    )

    bundle_dir = await _resolve_bundle_dir_async(app)
    skill_path = locate_skill_disk_path(skill, bundle_dir=bundle_dir)
    if skill_path is None:
        return list(getattr(skill, "tools_required", None) or [])
    disk_tools = allowed_tools_from_skill_path(skill_path)
    if disk_tools:
        return disk_tools
    return list(getattr(skill, "tools_required", None) or [])


def skill_summary(
    skill: Skill,
    *,
    app: Optional[App] = None,
    source: str = "app",
) -> Dict[str, Any]:
    """Editor-list row for one Skill node — identity, origin, customization/staleness flags."""
    app_slug = _app_slug(app) if app else ""
    key = str(getattr(skill, "key", "") or "")
    origin = str(getattr(skill, "origin", "bundle") or "bundle")
    if origin == "workspace":
        namespaced = f"workspace__{key}"
    else:
        namespaced = f"{app_slug}__{key}" if key else app_slug
    customized = bool(getattr(skill, "body_override", None))
    return {
        "id": skill.id,
        "source": source,
        "read_only": getattr(skill, "kind", "declarative") != "declarative"
        or source == "core",
        "key": key,
        "name": str(getattr(skill, "name", "") or key),
        "description": str(getattr(skill, "description", "") or ""),
        "kind": getattr(skill, "kind", "declarative"),
        "app_id": getattr(skill, "app_id", ""),
        "app_slug": app_slug,
        "namespaced_name": namespaced,
        "origin": origin,
        "enabled": bool(getattr(skill, "enabled", True)),
        "customized": customized,
        "stale_default": bool(getattr(skill, "stale_default", False)),
        "private": bool(getattr(skill, "private", False)),
        "trust_tier": getattr(skill, "trust_tier", "untrusted"),
        "tools_required": list(getattr(skill, "tools_required", None) or []),
        "customized_at": getattr(skill, "customized_at", None),
        "customized_by": getattr(skill, "customized_by", None),
    }


async def list_workspace_skills(
    workspace_id: str,
    *,
    user_id: str,
) -> List[Dict[str, Any]]:
    """Every app-bundle and workspace-authored skill visible to the editor in this workspace."""
    # include_disabled=True so the editor can surface (and re-enable) a
    # disabled bundle skill — the runtime resolver still hides it from the agent.
    skills = await get_callable_skills(
        "",
        workspace_id,
        user_id=user_id,
        active_apps_only=True,
        include_disabled=True,
    )
    workspace_authored = await Skill.find(
        {"workspace_id": workspace_id, "origin": "workspace"}
    )
    apps = await App.find({"workspace_id": workspace_id, "lifecycle_state": "active"})
    app_by_id = {a.id: a for a in apps}

    # Apps this caller may actually see — used to gate App-scoped
    # workspace-authored skills so a private/App skill (e.g. HR salary bands)
    # is not enumerated to a workspace member without access to that App.
    from app.services.agent_scope import accessible_apps_for_scope

    accessible = await accessible_apps_for_scope(user_id, workspace_id=workspace_id)
    accessible_app_ids = {a.id for a in accessible}

    out: List[Dict[str, Any]] = []
    seen: set = set()
    for sk in skills:
        origin = str(getattr(sk, "origin", "bundle") or "bundle")
        if origin == "workspace":
            owning_app = app_by_id.get(str(getattr(sk, "app_id", "") or ""))
            row = skill_summary(sk, app=owning_app, source="workspace")
            disk_desc = await resolve_skill_description(sk)
            if disk_desc:
                row["description"] = disk_desc
            out.append(row)
            seen.add(sk.id)
            continue
        app = app_by_id.get(str(getattr(sk, "app_id", "") or ""))
        if app is None:
            continue
        row = skill_summary(sk, app=app, source="app")
        disk_desc = await resolve_skill_description(sk, app=app)
        if disk_desc:
            row["description"] = disk_desc
        disk_tools = await resolve_skill_tools_from_disk(sk, app=app)
        if disk_tools:
            row["tools_required"] = disk_tools
        out.append(row)
        seen.add(sk.id)

    for sk in workspace_authored:
        if sk.id in seen:
            continue
        sk_app_id = str(getattr(sk, "app_id", "") or "")
        if sk_app_id and sk_app_id not in accessible_app_ids:
            # App-scoped skill for an App this caller cannot access — hide it
            # from the editor list (private or not).
            continue
        owning_app = app_by_id.get(sk_app_id)
        row = skill_summary(sk, app=owning_app, source="workspace")
        disk_desc = await resolve_skill_description(sk)
        if disk_desc:
            row["description"] = disk_desc
        out.append(row)
    return out


async def get_skill_detail(
    skill_id: str,
    *,
    workspace_id: str,
    user_id: str,
) -> Dict[str, Any]:
    """Full editor detail for one skill (core, app-bundle, or workspace) — resolved body + bundle default."""
    if is_core_skill_id(skill_id):
        name = parse_core_skill_id(skill_id)
        if not name:
            from app.api.errors import ResourceNotFoundError

            raise ResourceNotFoundError(message="skill not found")
        for row in list_core_skills():
            if row["id"] == skill_id:
                return row
        from app.api.errors import ResourceNotFoundError

        raise ResourceNotFoundError(message="skill not found")

    skill = await Skill.get(skill_id)
    if skill is None or getattr(skill, "workspace_id", "") != workspace_id:
        from app.api.errors import ResourceNotFoundError

        raise ResourceNotFoundError(message="skill not found")

    origin = str(getattr(skill, "origin", "bundle") or "bundle")
    if origin != "workspace":
        callable_ids = {
            s.id
            for s in await get_callable_skills(
                "",
                workspace_id,
                user_id=user_id,
                active_apps_only=True,
                include_disabled=True,
            )
        }
        if skill.id not in callable_ids:
            from jvspatial.api.exceptions import InsufficientPermissionsError

            raise InsufficientPermissionsError(
                message="skill not accessible in this workspace"
            )
    else:
        # NOTE: ``can_access_workspace`` returns the string "none" (truthy) for
        # no access, so ``if not can_access_workspace(...)`` never fires — use
        # the boolean helper. Additionally gate App-scoped workspace skills on
        # access to their owning App so a private/App skill is not readable by
        # a workspace member without App access.
        from jvspatial.api.exceptions import InsufficientPermissionsError

        from app.services.workspace_permissions import user_in_workspace_member_pool

        if not await user_in_workspace_member_pool(user_id, workspace_id):
            raise InsufficientPermissionsError(
                message="skill not accessible in this workspace"
            )
        skill_app_id = str(getattr(skill, "app_id", "") or "")
        if skill_app_id:
            from app.services.agent_scope import accessible_apps_for_scope

            accessible = await accessible_apps_for_scope(
                user_id, workspace_id=workspace_id
            )
            if skill_app_id not in {a.id for a in accessible}:
                raise InsufficientPermissionsError(
                    message="skill not accessible in this workspace"
                )

    origin = str(getattr(skill, "origin", "bundle") or "bundle")
    app: Optional[App] = None
    if origin != "workspace":
        app = await App.get(str(getattr(skill, "app_id", "") or ""))
    summary = skill_summary(
        skill,
        app=app,
        source="workspace" if origin == "workspace" else "app",
    )
    disk_desc = await resolve_skill_description(skill, app=app)
    if disk_desc:
        summary["description"] = disk_desc
    disk_tools = await resolve_skill_tools_from_disk(skill, app=app)
    if disk_tools:
        summary["tools_required"] = disk_tools
    resolved, default_body, domain_body = await resolve_skill_bodies(skill, app=app)
    summary["resolved_body"] = resolved or ""
    summary["bundle_default_body"] = default_body or ""
    summary["domain_body"] = domain_body or ""
    summary["prompt_template_ref"] = getattr(skill, "prompt_template_ref", None)
    summary["handler_ref"] = getattr(skill, "handler_ref", None)
    warn_body = domain_body or summary.get("body_override") or ""
    if origin == "workspace" and warn_body:
        summary["warnings"] = await skill_body_warnings(warn_body)
    else:
        summary["warnings"] = []
    return summary


async def create_workspace_skill(
    *,
    workspace_id: str,
    user_id: str,
    key: str,
    name: str,
    description: str,
    body_override: str,
    tools_required: List[str],
    app_id: str = "",
    private: Optional[bool] = None,
) -> Skill:
    """Create a new `origin="workspace"` Skill node.

    When ``app_id`` is given, the skill is scoped to that App: wired
    ``App -CONTAINS-> Skill`` instead of ``Workspace -CONTAINS-> Skill``, and
    ``private`` defaults to ``True`` (only usable when the agent's active
    context is that App — see ``skill_registry.get_callable_skills``) unless
    explicitly overridden. Without ``app_id``, behavior is unchanged: wired
    to the Workspace, ``private`` defaults ``False``.
    """
    validate_skill_key(key)
    validate_skill_description(description)
    validate_body_override(body_override)
    await validate_tools_required(tools_required)

    existing = await Skill.find({"workspace_id": workspace_id, "key": key})
    if existing:
        from app.exceptions import SkillRegistrationError

        raise SkillRegistrationError(
            message=f"skill key {key!r} already exists in workspace",
            details={"key": key},
        )

    ws = await Workspace.get(workspace_id)
    if ws is None:
        from app.api.errors import ResourceNotFoundError

        raise ResourceNotFoundError(message="workspace not found")

    app_node = None
    if app_id:
        app_node = await App.get(app_id)
        if app_node is None or getattr(app_node, "workspace_id", "") != workspace_id:
            from app.api.errors import ResourceNotFoundError

            raise ResourceNotFoundError(message="app not found")

    resolved_private = private if private is not None else bool(app_id)

    now = utc_now_iso()
    skill = await Skill.create(
        app_id=app_id,
        workspace_id=workspace_id,
        key=key,
        name=name or key,
        description=description or "",
        kind="declarative",
        origin="workspace",
        trust_tier="untrusted",
        body_override=body_override,
        tools_required=list(tools_required or []),
        private=resolved_private,
        enabled=True,
        customized_at=now,
        customized_by=user_id,
        created_at=now,
        updated_at=now,
    )
    if app_node is not None:
        await app_node.connect(skill, edge=CONTAINS, added_at=now)
    else:
        await ws.connect(skill, edge=CONTAINS, added_at=now)
    invalidate_workspace_profile(workspace_id)
    return skill


async def update_workspace_skill(
    skill: Skill,
    *,
    workspace_id: str,
    user_id: str,
    patch: Dict[str, Any],
) -> Skill:
    """Apply an editor patch to a declarative skill, stamping customization metadata."""
    if getattr(skill, "kind", "declarative") != "declarative":
        from jvspatial.api.exceptions import InsufficientPermissionsError

        raise InsufficientPermissionsError(
            message="only declarative skills are editable"
        )

    if patch.get("tools_required") is not None:
        await validate_tools_required(list(patch["tools_required"] or []))

    if "description" in patch and patch["description"] is not None:
        validate_skill_description(str(patch["description"]))

    if patch.get("body_override") is not None:
        validate_body_override(str(patch["body_override"]))

    now = utc_now_iso()
    for field in (
        "name",
        "description",
        "body_override",
        "tools_required",
        "enabled",
        "private",
    ):
        if field in patch and patch[field] is not None:
            setattr(skill, field, patch[field])

    # private intentionally excluded — a visibility toggle isn't content
    # customization, so it should not stamp customized_at/customized_by.
    if any(
        f in patch for f in ("name", "description", "body_override", "tools_required")
    ):
        skill.customized_at = now
        skill.customized_by = user_id
        if patch.get("body_override") is not None:
            skill.stale_default = False

    skill.updated_at = now
    await skill.save()
    invalidate_workspace_profile(workspace_id)
    return skill


async def _manifest_skill_defaults(app: App, key: str) -> Tuple[str, str]:
    from pathlib import Path

    from app.services.operational_model_compile import compile_canonical_manifest
    from app.services.operational_model_loader import (
        load_library_operational_models_with_issues,
    )

    slug = str(getattr(app, "source_operational_model_slug", None) or "").strip()
    if not slug:
        return "", ""
    specs, _ = load_library_operational_models_with_issues(
        packages_root=Path("app/packages")
    )
    spec = next((s for s in specs if s.slug == slug), None)
    if spec is None:
        return "", ""
    canonical = compile_canonical_manifest(manifest=spec.manifest, scope_hint="app")
    for sk in canonical.get("app", {}).get("skills") or []:
        if sk.get("key") == key:
            return str(sk.get("name") or key), str(sk.get("description") or "")
    return "", ""


async def reset_workspace_skill(skill: Skill, *, workspace_id: str) -> Skill:
    """Clear a bundle skill's workspace override, restoring the manifest-declared name/description/body."""
    if str(getattr(skill, "origin", "bundle") or "bundle") != "bundle":
        from jvspatial.api.exceptions import InsufficientPermissionsError

        raise InsufficientPermissionsError(message="only bundle skills can be reset")

    app = await App.get(str(getattr(skill, "app_id", "") or ""))
    if app is None:
        from app.api.errors import ResourceNotFoundError

        raise ResourceNotFoundError(message="owning app not found")

    manifest_name, manifest_desc = await _manifest_skill_defaults(app, skill.key)
    now = utc_now_iso()
    skill.body_override = None
    skill.stale_default = False
    skill.customized_at = None
    skill.customized_by = None
    if manifest_name:
        skill.name = manifest_name
    if manifest_desc is not None:
        skill.description = manifest_desc
    skill.updated_at = now
    await skill.save()
    invalidate_workspace_profile(workspace_id)
    return skill


async def delete_workspace_skill(skill: Skill, *, workspace_id: str) -> None:
    """Permanently remove a workspace-authored skill node (bundle-origin skills cannot be deleted, only reset)."""
    if str(getattr(skill, "origin", "bundle") or "bundle") != "workspace":
        from jvspatial.api.exceptions import InsufficientPermissionsError

        raise InsufficientPermissionsError(
            message="only workspace-authored skills can be deleted"
        )

    # jvspatial 0.0.17 has no ``Node.destroy``; cascade=False drops only the
    # Skill and its incoming CONTAINS edge.
    await skill.delete(cascade=False)
    invalidate_workspace_profile(workspace_id)


__all__ = [
    "build_tool_catalogue_for_editor",
    "create_workspace_skill",
    "delete_workspace_skill",
    "get_skill_detail",
    "is_core_skill_id",
    "list_core_skills",
    "list_workspace_skills",
    "reset_workspace_skill",
    "resolve_bundle_default_body",
    "resolve_skill_bodies",
    "skill_body_warnings",
    "skill_summary",
    "update_workspace_skill",
    "validate_body_override",
    "validate_skill_description",
    "validate_skill_key",
    "validate_tools_required",
    "wrap_untrusted_overlay_body",
]
