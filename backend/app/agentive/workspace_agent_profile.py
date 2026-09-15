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

from app.models.nodes import App, ContentProfile, Skill
from app.services.package_paths import default_profiles_root
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

_PROFILES_ROOT = default_profiles_root()
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
    from app.services.content_profile_loader import load_library_profiles

    slug = str(getattr(app, "source_profile_slug", None) or "").strip()
    if slug:
        spec = next((s for s in load_library_profiles() if s.slug == slug), None)
        if spec and spec.bundle_dir and spec.bundle_dir.is_dir():
            return spec.bundle_dir
        candidate = _PROFILES_ROOT / slug
        if candidate.is_dir():
            return candidate

    lib_id = getattr(app, "installed_from_library_id", None)
    if lib_id:
        cp = await ContentProfile.get(lib_id)
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
                    (s for s in load_library_profiles() if s.slug == cp_slug), None
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
        matches = sorted(_PROFILES_ROOT.glob(f"*/skills/{key}/SKILL.md"))
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
    slug = str(getattr(app, "source_profile_slug", None) or "").strip()
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
) -> str:
    """Cache key for a composed profile.

    ``accessible_app_ids`` is part of the identity of the result: the profile
    is per-(workspace, user) and its App/skill set is filtered by what this
    caller may see. Hashing only App/Skill ``updated_at`` left permission
    changes invisible — removing a collaborator or adding an ``EXCLUDED_FROM``
    mutates no App or Skill row, so a revoked user kept the cached overlay
    (including that App's skills) for the full TTL.
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
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
    return digest


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

    version_hint = _profile_version_hint(list(apps), all_skills, accessible_app_ids)

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
    """Return the profile bound to the current request turn, if any."""
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
