"""Per-workspace Agent Configuration Profile — workspace overlay for jvagent.

Composes public declarative skills from installed Apps in a workspace into
jvagent-ready SOP documents. The global base tier (integral_* filesystem
skills + full tool manifest) is loaded by jvagent independently; this module
only materializes the **workspace overlay**.
"""

from __future__ import annotations

import contextvars
import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.models.nodes import App, OperationalModel, Skill
from app.services.package_paths import default_packages_root, resolve_package_paths
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

_PROFILES_ROOT = default_packages_root()
_INTEGRAL_REPO_ROOT = Path(__file__).resolve().parents[3]
_INTEGRAL_AGENT_APP_ROOT = _INTEGRAL_REPO_ROOT / "agent"
_RESIDENT_AGENT_NAMESPACE = "integral"
_RESIDENT_AGENT_NAME = "integral_agent"

_turn_profile: contextvars.ContextVar[Optional["WorkspaceAgentProfile"]] = (
    contextvars.ContextVar("integral_turn_workspace_agent_profile", default=None)
)

# In-process cache: (workspace_id, user_id) -> (profile_version_hint, profile, monotonic_ts)
_profile_cache: Dict[Tuple[str, str], Tuple[str, "WorkspaceAgentProfile", float]] = {}
_PROFILE_CACHE_TTL_SECONDS = 60.0


@dataclass(frozen=True)
class WorkspaceAppRef:
    """Installed app metadata included in the workspace overlay."""

    app_id: str
    name: str
    slug: str
    settings: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OverlaySkillDoc:
    """jvagent-compatible SOP overlay (converted to SkillDoc at the provider)."""

    name: str
    description: str
    body: str
    requires_tools: Tuple[str, ...] = ()
    requires_actions: Tuple[str, ...] = ("EmbeddedIntegralAction",)
    source: str = "workspace"
    spec: str = "jv"
    always_active: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkspaceAgentProfile:
    workspace_id: str
    apps: Tuple[WorkspaceAppRef, ...]
    overlay_skill_docs: Tuple[OverlaySkillDoc, ...]
    composed_at: str
    profile_version: str


async def _resolve_bundle_dir_async(app: App) -> Optional[Path]:
    from app.services.operational_model_loader import load_library_operational_models

    slug = str(getattr(app, "source_operational_model_slug", None) or "").strip()
    if slug:
        spec = next(
            (s for s in load_library_operational_models() if s.slug == slug), None
        )
        if spec and spec.bundle_dir and spec.bundle_dir.is_dir():
            return spec.bundle_dir
        candidate = _PROFILES_ROOT / slug
        if candidate.is_dir():
            return candidate

    lib_id = getattr(app, "installed_from_library_id", None)
    if lib_id:
        cp = await OperationalModel.get(lib_id)
        if cp is not None:
            md = dict(getattr(cp, "metadata", None) or {})
            bdp = md.get("bundle_dir_path")
            if bdp:
                p = Path(str(bdp))
                if p.is_dir():
                    return p
            cp_slug = str(md.get("slug") or "").strip()
            if cp_slug:
                spec = next(
                    (s for s in load_library_operational_models() if s.slug == cp_slug),
                    None,
                )
                if spec and spec.bundle_dir and spec.bundle_dir.is_dir():
                    return spec.bundle_dir

    md = dict(getattr(app, "metadata", None) or {})
    bdp = md.get("bundle_dir_path")
    if bdp:
        p = Path(str(bdp))
        if p.is_dir():
            return p

    return None


def locate_skill_disk_path(
    skill: Skill,
    *,
    bundle_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Resolve on-disk SKILL.md for a bundle skill (bundle dir or profiles glob)."""
    ref = str(getattr(skill, "prompt_template_ref", "") or "").strip()
    if bundle_dir is not None and ref:
        candidate = bundle_dir / ref.lstrip("./")
        if candidate.is_file():
            return candidate
    key = str(getattr(skill, "key", "") or "").strip()
    if key:
        matches = sorted(
            path
            for root in resolve_package_paths()
            for path in root.glob(f"*/skills/{key}/SKILL.md")
        )
        if matches:
            return matches[0]
    return None


def _parse_skill_disk_bundle(skill_path: Path) -> Optional[Dict[str, Any]]:
    try:
        return _load_parsed_bundle(skill_path)
    except Exception:
        return None


def _body_from_skill_path(
    skill_path: Path,
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Domain markdown (no frontmatter) + optional parsed bundle meta."""
    bundle = _parse_skill_disk_bundle(skill_path)
    if bundle:
        body = str(bundle.get("content") or "").strip()
        if body:
            return body, bundle
    try:
        raw = skill_path.read_text(encoding="utf-8")
    except Exception:
        return None, None
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            body = parts[2].strip()
            return (body or None), bundle
    stripped = raw.strip()
    return (stripped or None), bundle


def allowed_tools_from_skill_path(skill_path: Path) -> List[str]:
    """allowed-tools from SKILL.md frontmatter (jvagent parse or YAML fallback)."""
    bundle = _parse_skill_disk_bundle(skill_path)
    if bundle:
        tools = bundle.get("allowed_tools") or bundle.get("allowed-tools") or []
        if isinstance(tools, list):
            return [str(t) for t in tools if str(t).strip()]
    try:
        raw = skill_path.read_text(encoding="utf-8")
        if raw.startswith("---"):
            import yaml

            fm = yaml.safe_load(raw.split("---", 2)[1]) or {}
            tools = fm.get("allowed-tools") or fm.get("allowed_tools") or []
            if isinstance(tools, list):
                return [str(t) for t in tools if str(t).strip()]
    except Exception:
        pass
    return []


def _compose_bundle_body_with_extends(bundle: Dict[str, Any], raw_body: str) -> str:
    """Apply ADR-0020 ``extends`` (action base SOP) to an app-bundled skill body."""
    extends_raw = bundle.get("extends")
    if not extends_raw:
        return raw_body
    try:
        from jvagent.scaffold.sop_extend import (
            compose_skill_body,
            load_action_base_sop_body,
            parse_extends_ref,
        )

        parsed = parse_extends_ref(extends_raw)
        if not parsed:
            return raw_body
        kind, target = parsed
        if kind != "action":
            return raw_body
        base = load_action_base_sop_body(
            target,
            app_root=str(_INTEGRAL_AGENT_APP_ROOT),
            agent_namespace=_RESIDENT_AGENT_NAMESPACE,
            agent_name=_RESIDENT_AGENT_NAME,
        )
        return compose_skill_body(base, raw_body)
    except Exception as exc:
        logger.warning(
            "workspace_agent_profile: extends compose failed: %s",
            exc,
        )
        return raw_body


def _app_slug(app: App) -> str:
    slug = str(getattr(app, "source_operational_model_slug", None) or "").strip()
    if slug:
        return slug
    fold = str(getattr(app, "name_fold", "") or "").strip()
    if fold:
        return fold.replace(" ", "-")
    return str(getattr(app, "id", "app") or "app")


def _load_parsed_bundle(skill_path: Path) -> Optional[Dict[str, Any]]:
    try:
        from jvagent.scaffold.skill_resolve import parse_skill_bundle

        return parse_skill_bundle(skill_path.parent, source="workspace")
    except Exception:
        return None


def _resolve_domain_body(
    skill: Skill,
    *,
    bundle_dir: Optional[Path],
    ignore_override: bool = False,
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Return domain markdown (no extends merge) and optional parsed bundle meta."""
    skill_path = locate_skill_disk_path(skill, bundle_dir=bundle_dir)
    bundle_meta: Optional[Dict[str, Any]] = None
    if skill_path is not None:
        _, bundle_meta = _body_from_skill_path(skill_path)

    if not ignore_override:
        override = str(getattr(skill, "body_override", None) or "").strip()
        if override:
            from app.agentive.services.agent_skills import wrap_untrusted_overlay_body

            return wrap_untrusted_overlay_body(override), bundle_meta

    if skill_path is None:
        return None, None

    return _body_from_skill_path(skill_path)


def _resolve_prompt_body(
    skill: Skill,
    *,
    bundle_dir: Optional[Path],
    tools_required: List[str],
    ignore_override: bool = False,
) -> Tuple[Optional[str], List[str]]:
    domain_body, bundle_meta = _resolve_domain_body(
        skill,
        bundle_dir=bundle_dir,
        ignore_override=ignore_override,
    )
    if not domain_body:
        return None, list(tools_required)

    resolved_tools = list(tools_required)
    if bundle_meta is not None:
        body = _compose_bundle_body_with_extends(bundle_meta, domain_body)
        return body, resolved_tools

    return domain_body, resolved_tools


def _skill_to_overlay_doc(
    skill: Skill,
    *,
    app_slug: str,
    bundle_dir: Optional[Path],
) -> Optional[OverlaySkillDoc]:
    if not getattr(skill, "enabled", True):
        return None
    if getattr(skill, "kind", "declarative") != "declarative":
        return None

    tools_required = list(getattr(skill, "tools_required", None) or [])
    _, bundle_meta = _resolve_domain_body(skill, bundle_dir=bundle_dir)
    body, resolved_tools = _resolve_prompt_body(
        skill, bundle_dir=bundle_dir, tools_required=tools_required
    )
    if not body:
        return None

    key = str(getattr(skill, "key", "") or "").strip()
    origin = str(getattr(skill, "origin", "bundle") or "bundle")
    if origin == "workspace":
        namespaced = f"workspace__{key}" if key else "workspace"
    else:
        namespaced = f"{app_slug}__{key}" if key else app_slug

    description = str(getattr(skill, "description", "") or "").strip()
    if not description and bundle_meta is not None:
        description = str(bundle_meta.get("description") or "").strip()
    if not description:
        description = str(getattr(skill, "name", "") or key or namespaced).strip()

    return OverlaySkillDoc(
        name=namespaced,
        description=description,
        body=body,
        requires_tools=tuple(resolved_tools),
        metadata={
            "skill_key": key,
            "app_id": getattr(skill, "app_id", ""),
            "app_slug": app_slug,
            "origin": origin,
        },
    )


def _profile_version_hint(
    apps: List[App],
    skills: List[Skill],
    accessible_app_ids: Optional[set] = None,
    connector_fingerprint: str = "",
) -> str:
    """Cache key for a composed profile.

    ``accessible_app_ids`` is part of the identity of the result: the Operational Model
    is per-(workspace, user) and its App/skill set is filtered by what this
    caller may see. Hashing only App/Skill ``updated_at`` left permission
    changes invisible — removing a collaborator or adding an ``EXCLUDED_FROM``
    mutates no App or Skill row, so a revoked user kept the cached overlay
    (including that App's skills) for the full TTL.

    ``connector_fingerprint`` covers mounted connectors + their advertised
    tool keys, so completing OAuth (which registers tools) invalidates the
    entry instead of hiding the new source for the full TTL.
    """
    parts = []
    for app in sorted(apps, key=lambda a: getattr(a, "id", "")):
        parts.append(f"app:{getattr(app, 'id', '')}:{getattr(app, 'updated_at', '')}")
    for sk in sorted(skills, key=lambda s: getattr(s, "id", "")):
        parts.append(
            f"skill:{getattr(sk, 'id', '')}:{getattr(sk, 'updated_at', '')}:"
            f"{getattr(sk, 'enabled', True)}:{bool(getattr(sk, 'body_override', None))}"
        )
    if accessible_app_ids is not None:
        parts.append("access:" + ",".join(sorted(accessible_app_ids)))
    if connector_fingerprint:
        parts.append(f"connectors:{connector_fingerprint}")
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
    return digest


def _connector_display_name(connector: Any) -> str:
    """Human label for a mounted connector — catalog display name, no secrets.

    Delegates to the resolution layer's label-first lookup (row label →
    catalog display name → prettified slug). ``auth_state`` credential
    material never leaves that function.
    """
    from app.agentive.connectors.connector_resolution import row_display_name

    return row_display_name(connector)


def _connected_sources_fingerprint(
    connectors: List[Any], tool_keys: List[str], user_id: Optional[str] = None
) -> str:
    parts = [
        f"{getattr(c, 'id', '')}:{getattr(c, 'updated_at', '')}:"
        f"{getattr(c, 'health_status', '') or ''}:"
        f"{getattr(c, 'connection_mode', '') or ''}:"
        f"{getattr(c, 'owner', '') or ''}"
        for c in sorted(connectors, key=lambda c: getattr(c, "id", ""))
    ]
    parts.append("tools:" + ",".join(sorted(tool_keys)))
    if user_id:
        # Annotations are per-user (your connection vs shared vs teammate).
        parts.append(f"user:{user_id}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


async def _workspace_connectors(workspace_id: str) -> List[Any]:
    """Connectors mounted in a workspace (empty on lookup failure)."""
    try:
        from app.agentive.nodes import Connector

        found = await Connector.find({"workspace_id": workspace_id})
    except Exception:  # noqa: BLE001 — announcement is advisory, never blocking
        logger.warning("_connected_sources_doc: connector lookup failed")
        return []
    return [c for c in (found or []) if c is not None]


async def _connectors_fingerprint(
    workspace_id: str, user_id: Optional[str] = None
) -> str:
    """Cache-identity fingerprint for mounted connectors + advertised tools."""
    from app.services.hooks.registry import get_workspace_tools

    connectors = await _workspace_connectors(workspace_id)
    tool_keys = [
        str(spec.get("key") or key or "")
        for key, spec in (get_workspace_tools(workspace_id) or {}).items()
        if isinstance(spec, dict)
        and (
            str(spec.get("_native_connector_id") or "").strip()
            or str(spec.get("_mcp_connector_id") or "").strip()
            or str(spec.get("_native_connector_slug") or "").strip()
            or str(spec.get("_mcp_connector_slug") or "").strip()
        )
    ]
    return _connected_sources_fingerprint(connectors, tool_keys, user_id)


async def _connected_sources_doc(
    workspace_id: str, user_id: Optional[str] = None
) -> Optional[OverlaySkillDoc]:
    """Announce mounted connectors + their callable tools to the resident.

    Lists the canonical slug keys (``drive_native__search_files``) — the
    keys to call — grouped per capability with availability annotations:

    - "your <label> connection" — the caller owns a personal row;
    - "shared <label>" — a shared row covers the caller;
    - "connected by a teammate" — only other users' rows exist; the caller
      must connect their own account (names the connector + where).

    Readiness comes from the row the caller would actually use. Generated
    from the same per-workspace registry ``get_tools`` advertises (no
    drift between announcement and callable surface).

    Privacy: display names + health + tool keys only. ``auth_state`` is
    never read — no tokens, secrets, or OAuth state can reach the prompt.
    """
    from app.agentive.connectors.connector_resolution import (
        is_shared_row,
        row_display_name,
        row_slug,
    )
    from app.services.hooks.registry import get_workspace_tools

    connectors = await _workspace_connectors(workspace_id)
    if not connectors:
        return None

    canonical_by_slug: Dict[str, List[Dict[str, Any]]] = {}
    for _key, spec in (get_workspace_tools(workspace_id) or {}).items():
        if not isinstance(spec, dict):
            continue
        if spec.get("_native_connector_id") or spec.get("_mcp_connector_id"):
            continue  # per-row legacy keys stay dispatchable, not announced
        slug = str(
            spec.get("_native_connector_slug") or spec.get("_mcp_connector_slug") or ""
        ).strip()
        if slug:
            canonical_by_slug.setdefault(slug, []).append(spec)
    if not canonical_by_slug:
        return None

    rows_by_slug: Dict[str, List[Any]] = {}
    for connector in connectors:
        rows_by_slug.setdefault(row_slug(connector), []).append(connector)

    lines = [
        "External data sources connected to this workspace. Call the "
        "listed `slug__tool` keys — they resolve to your own connection, "
        "else the shared one, automatically. Never tell the user you lack "
        "access to a source listed here as ready. A source needing "
        "attention requires re-authorization in Settings → Connectors "
        "first (say so explicitly)."
    ]
    announced = 0
    for slug in sorted(canonical_by_slug):
        specs = canonical_by_slug[slug]
        rows = rows_by_slug.get(slug, [])
        personal = next(
            (r for r in rows if user_id and getattr(r, "owner", "") == user_id),
            None,
        )
        shared = next((r for r in rows if is_shared_row(r)), None)
        subject = personal or shared
        if subject is None:
            label = row_display_name(rows[0]) if rows else slug.replace("_", " ")
            lines.append(
                f"- {label}: connected by a teammate — not usable by you. "
                "Connect your own account in Settings → Connectors, or ask "
                "an admin to share the connection."
            )
            announced += 1
            continue
        label = row_display_name(subject)
        status = str(getattr(subject, "health_status", "") or "").strip().lower()
        last_error = str(getattr(subject, "last_error", "") or "").strip()
        ready = status in ("ok", "") and not last_error
        ownership = "your connection" if subject is personal else "shared connection"
        if ready:
            state = f"ready ({ownership})"
        else:
            state = f"needs attention ({ownership}; {last_error[:160] or status})"
        lines.append(f"- {label} ({state}):")
        for spec in sorted(specs, key=lambda s: str(s.get("key") or "")):
            tkey = str(spec.get("key") or "").strip()
            desc = str(spec.get("description") or "").strip().split(".")[0].strip()
            lines.append(f"  - `{tkey}`" + (f" — {desc}" if desc else ""))
        announced += 1
    if not announced:
        return None
    body = "\n".join(lines)
    if len(body) > 4000:
        body = body[:3999] + "…"
    return OverlaySkillDoc(
        name="connected_data_sources",
        description="External data sources and tools connected to this workspace",
        body=body,
        requires_tools=(),
        metadata={"origin": "connectors", "workspace_id": workspace_id},
    )


async def compose_workspace_agent_profile(
    workspace_id: str,
    *,
    user_id: str | None = None,
    caller_agent_id: str | None = None,
) -> WorkspaceAgentProfile:
    """Build the workspace overlay profile from graph state."""
    from app.agentive.services.skill_registry import get_callable_skills

    if not workspace_id:
        return WorkspaceAgentProfile(
            workspace_id="",
            apps=(),
            overlay_skill_docs=(),
            composed_at=utc_now_iso(),
            profile_version="empty",
        )

    cache_key = (workspace_id, user_id or "")

    apps = await App.find({"workspace_id": workspace_id, "lifecycle_state": "active"})
    skills = await get_callable_skills(
        caller_agent_id or "",
        workspace_id,
        user_id=user_id,
        active_apps_only=True,
    )
    workspace_skills = await Skill.find(
        {"workspace_id": workspace_id, "origin": "workspace", "enabled": True}
    )
    all_skills = list(skills) + list(workspace_skills)

    # Resolve what this caller may see BEFORE consulting the cache — the
    # answer is part of the cache identity (see _profile_version_hint), so a
    # permission change invalidates the entry instead of being masked by the
    # TTL.
    accessible_app_ids: Optional[set] = None
    if user_id:
        from app.services.agent_scope import accessible_apps_for_scope

        accessible = await accessible_apps_for_scope(user_id, workspace_id=workspace_id)
        accessible_app_ids = {a.id for a in accessible}

    conn_fp = await _connectors_fingerprint(workspace_id, user_id)
    version_hint = _profile_version_hint(
        list(apps), all_skills, accessible_app_ids, connector_fingerprint=conn_fp
    )

    cache_key = (workspace_id, user_id or "")
    cached = _profile_cache.get(cache_key)
    if (
        cached
        and cached[0] == version_hint
        and (time.monotonic() - cached[2]) <= _PROFILE_CACHE_TTL_SECONDS
    ):
        return cached[1]

    app_refs: List[WorkspaceAppRef] = []
    app_by_id: Dict[str, App] = {}
    for app in apps:
        if accessible_app_ids is not None and app.id not in accessible_app_ids:
            continue
        slug = _app_slug(app)
        app_by_id[app.id] = app
        app_refs.append(
            WorkspaceAppRef(
                app_id=app.id,
                name=str(getattr(app, "name", "") or slug),
                slug=slug,
                settings=dict(getattr(app, "settings", None) or {}),
            )
        )

    overlay_docs: List[OverlaySkillDoc] = []
    bundle_dir_cache: Dict[str, Optional[Path]] = {}
    seen_ids: set = set()

    for skill in skills:
        if not getattr(skill, "enabled", True):
            continue
        origin = str(getattr(skill, "origin", "bundle") or "bundle")
        if origin == "workspace":
            continue
        app_id = str(getattr(skill, "app_id", "") or "")
        app = app_by_id.get(app_id)
        if app is None and app_id:
            app = await App.get(app_id)
        if app is None:
            continue

        app_slug = _app_slug(app)
        if app_id not in bundle_dir_cache:
            bundle_dir_cache[app_id] = await _resolve_bundle_dir_async(app)

        doc = _skill_to_overlay_doc(
            skill,
            app_slug=app_slug,
            bundle_dir=bundle_dir_cache[app_id],
        )
        if doc is not None:
            overlay_docs.append(doc)
            seen_ids.add(skill.id)

    for skill in workspace_skills:
        if skill.id in seen_ids or not getattr(skill, "enabled", True):
            continue
        # Access gate (Architectural Decision 5): a workspace-authored skill
        # scoped to an App must not overlay for a caller who cannot access that
        # App, and a ``private`` App-scoped skill only surfaces inside its own
        # App's focused context — which this general compose path does not
        # establish, so it is excluded here. Without this, an App-private skill
        # (e.g. HR salary bands) leaked to every workspace member. The
        # resolver-time gate in ``get_callable_skills`` covers the callable
        # surface; this covers the discovery/overlay surface.
        skill_app_id = str(getattr(skill, "app_id", "") or "")
        if skill_app_id:
            if (
                accessible_app_ids is not None
                and skill_app_id not in accessible_app_ids
            ):
                continue
            if getattr(skill, "private", False):
                continue
        doc = _skill_to_overlay_doc(skill, app_slug="workspace", bundle_dir=None)
        if doc is not None:
            overlay_docs.append(doc)

    sources_doc = await _connected_sources_doc(workspace_id, user_id)
    if sources_doc is not None:
        overlay_docs.append(sources_doc)

    profile = WorkspaceAgentProfile(
        workspace_id=workspace_id,
        apps=tuple(app_refs),
        overlay_skill_docs=tuple(overlay_docs),
        composed_at=utc_now_iso(),
        profile_version=version_hint,
    )
    _profile_cache[cache_key] = (version_hint, profile, time.monotonic())
    return profile


def invalidate_workspace_profile(workspace_id: str) -> None:
    """Drop cached profile after install/uninstall/settings change."""
    if workspace_id:
        stale = [k for k in _profile_cache if k[0] == workspace_id]
        for key in stale:
            _profile_cache.pop(key, None)


async def materialize_profile_for_turn(
    workspace_id: str,
    *,
    user_id: str | None = None,
) -> None:
    """Async compose + bind profile to the current turn ContextVar."""
    profile = await compose_workspace_agent_profile(workspace_id, user_id=user_id)
    _turn_profile.set(profile)


def get_turn_workspace_profile() -> Optional[WorkspaceAgentProfile]:
    """Return the Operational Model bound to the current request turn, if any."""
    return _turn_profile.get()


def clear_turn_workspace_profile() -> None:
    """Reset the turn ContextVar after request handling completes."""
    _turn_profile.set(None)


async def resolve_bundle_default_body(
    skill: Skill,
    *,
    app: Optional[App] = None,
) -> Tuple[Optional[str], str]:
    """Merged default body + sha256 digest of domain body only (ignores body_override)."""
    bundle_dir: Optional[Path] = None
    if app is not None:
        bundle_dir = await _resolve_bundle_dir_async(app)
    domain_body, bundle_meta = _resolve_domain_body(
        skill,
        bundle_dir=bundle_dir,
        ignore_override=True,
    )
    if not domain_body:
        return None, ""
    if bundle_meta is not None:
        merged = _compose_bundle_body_with_extends(bundle_meta, domain_body)
    else:
        merged = domain_body
    digest = hashlib.sha256(domain_body.encode("utf-8")).hexdigest()
    return merged, digest


async def resolve_skill_domain_body(
    skill: Skill,
    *,
    app: Optional[App] = None,
) -> Optional[str]:
    """Domain body: override if set, else disk content (never extends-merged)."""
    bundle_dir: Optional[Path] = None
    if app is not None:
        bundle_dir = await _resolve_bundle_dir_async(app)
    domain_body, _ = _resolve_domain_body(skill, bundle_dir=bundle_dir)
    return domain_body


__all__ = [
    "OverlaySkillDoc",
    "WorkspaceAgentProfile",
    "WorkspaceAppRef",
    "clear_turn_workspace_profile",
    "compose_workspace_agent_profile",
    "get_turn_workspace_profile",
    "invalidate_workspace_profile",
    "materialize_profile_for_turn",
    "allowed_tools_from_skill_path",
    "locate_skill_disk_path",
    "resolve_bundle_default_body",
    "resolve_skill_domain_body",
]
