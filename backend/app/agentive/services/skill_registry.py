"""App-bundled Skill registry — Phase 10 / Plan 10-04 (APP-SKILLS-01).

Persists ``Skill`` Nodes connected to their owning ``App`` via ``CONTAINS``,
validates ``tools_required`` against the live MCP catalogue at registration
time, and enforces the resolver-time ``private`` flag (Architectural
Decision 5 — single source of truth in ``get_callable_skills``).

Public surface (called by Plan 10-05's install lifecycle service):

  async register_skill(app_id, workspace_id, skill_spec) -> Skill
      Materialize / update a Skill under the App. Validates the spec via
      ``SkillRegisterRequest`` and the ``tools_required`` list against the
      live MCP catalogue. Idempotent on (app_id, key): re-calling updates the
      existing Skill rather than creating a duplicate. Connects Skill to App
      via CONTAINS on first creation.

  async unregister_skills_for_app(app_id) -> int
      Delete every Skill owned by an App. Returns count deleted. Called from
      Plan 10-05's uninstall lifecycle.

  async get_callable_skills(caller_agent_id, workspace_id) -> list[Skill]
      Resolve the caller's AgentConfig → ``caller_app_id``, walk the
      workspace's Apps, collect bundled Skills, and apply the resolver-time
      private filter: keep ``not skill.private`` OR
      ``skill.app_id == caller_app_id``.

  async get_skill_by_key(app_id, key) -> Skill | None
      Utility / test introspection lookup.

Resolver-time ``private`` semantics (Architectural Decision 5):
  - Public skills (``private=False``) resolvable by any agent in the same
    Workspace.
  - Private skills (``private=True``) only resolvable when the caller's
    AgentConfig.app_id == skill.app_id (i.e. same-App context).
  - ``caller_app_id`` is read SERVER-SIDE from the AgentConfig — never
    client-supplied — closing T-10-04-07 (spoofing).

Implementation notes:
  - Walks ``Workspace → Apps → Skills`` via two single-hop queries (CLAUDE.md
    pragmatism — measure before optimizing; Walker not justified for v1).
  - MCP catalogue lookup is rebuilt per-call from ``build_tool_catalogue()``
    (already per-call internally per Plan 10-02 Q3 empirical finding).
  - Handler-ref path validation (defense against malicious manifests) — see
    ``_validate_handler_ref`` below.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from app.agentive.nodes import AgentConfig
from app.exceptions import (
    InvalidToolReferenceError,
    SkillRegistrationError,
)
from app.models.edges import CONTAINS
from app.models.nodes import App, Skill
from app.schemas.app_skills import SkillRegisterRequest
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


# Python module identifier regex: defends against path traversal / shell
# metacharacters in custom-skill handler_ref values (T-10-04-04 mitigation).
# Allows only dotted identifiers: ``my_pkg.sub_pkg.handler_fn``.
_HANDLER_REF_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")

_LIVE_MCP_TOOL_NAMES: Optional[set] = None


def invalidate_live_mcp_tool_names() -> None:
    """Drop cached MCP tool names (manifest reload / tests)."""
    global _LIVE_MCP_TOOL_NAMES
    _LIVE_MCP_TOOL_NAMES = None


def _validate_handler_ref(handler_ref: str, *, skill_key: str) -> None:
    """Reject malformed / suspicious handler_ref values.

    Custom-skill manifests declare a Python dotted path to the handler
    function. Validate it's a clean module identifier — no ``..``, no
    absolute paths, no shell metacharacters, no slashes — to prevent
    a malicious manifest from importing arbitrary modules.
    """
    if not handler_ref:
        raise SkillRegistrationError(
            message=(
                f"skill {skill_key!r} kind=custom requires handler_ref " "(empty value)"
            ),
            details={"skill_key": skill_key},
        )
    if not _HANDLER_REF_RE.match(handler_ref):
        raise SkillRegistrationError(
            message=(
                f"skill {skill_key!r} handler_ref must be a valid Python "
                f"dotted module path (got {handler_ref!r})"
            ),
            details={"skill_key": skill_key, "handler_ref": handler_ref},
        )


async def _live_mcp_tool_names() -> set:
    """Snapshot of dispatchable MCP tool names (manifest-driven M2a catalogue).

    Used at registration time to validate each entry in
    ``skill_spec.tools_required``. Rebuilt per call from
    ``app.agentive.tooling.build_tool_catalogue`` — the same surface the
    resident agent and OAuth MCP server advertise (not the legacy route-walk
    catalogue formerly in ``app.services.mcp_adapter_legacy``, now deleted).
    """
    global _LIVE_MCP_TOOL_NAMES
    if _LIVE_MCP_TOOL_NAMES is not None:
        return set(_LIVE_MCP_TOOL_NAMES)
    try:
        from app.agentive.tooling import build_tool_catalogue

        _LIVE_MCP_TOOL_NAMES = {entry["name"] for entry in build_tool_catalogue()}
        return set(_LIVE_MCP_TOOL_NAMES)
    except Exception as e:  # pragma: no cover — defensive
        logger.warning(
            "skill_registry: build_tool_catalogue lookup failed: %s — "
            "registration will reject every tools_required entry",
            e,
        )
        return set()


def _validate_skill_spec(spec_dict: Dict[str, Any]) -> SkillRegisterRequest:
    """Run the spec through the Pydantic boundary; convert to typed model."""
    try:
        return SkillRegisterRequest(**spec_dict)
    except Exception as e:
        raise SkillRegistrationError(
            message=f"skill spec failed validation: {e}",
            details={"spec_key": spec_dict.get("key", "")},
        )


async def register_skill(
    app_id: str,
    workspace_id: str,
    skill_spec: Dict[str, Any],
    *,
    bundle_tool_names: Optional[set[str]] = None,
) -> Skill:
    """Register or update an App-bundled Skill.

    Idempotent on (app_id, key): a second call with the same key updates the
    existing Skill row rather than creating a duplicate. The CONTAINS edge to
    the App is created on first creation only.

    Raises:
      SkillRegistrationError      — missing required fields, malformed
                                    handler_ref, App not found.
      InvalidToolReferenceError   — tools_required entry is neither a live
                                    MCP tool nor a same-App bundle tool.
    """
    if not app_id:
        raise SkillRegistrationError(
            message="register_skill: app_id is required",
            details={"workspace_id": workspace_id},
        )
    if not workspace_id:
        raise SkillRegistrationError(
            message="register_skill: workspace_id is required",
            details={"app_id": app_id},
        )

    parsed = _validate_skill_spec(skill_spec)

    if not parsed.key:
        raise SkillRegistrationError(
            message="register_skill: skill.key is required",
            details={"app_id": app_id},
        )

    # kind/handler integrity — mirrors the compile-time check so this surface
    # is robust even when callers bypass the manifest compiler.
    # ADR-002 Phase 1 (Full Sweep T1): untrusted bundles cannot ship custom code.
    if parsed.kind == "custom" and (parsed.trust_tier or "untrusted") == "untrusted":
        raise SkillRegistrationError(
            message=(
                f"skill {parsed.key!r}: kind=custom requires trust_tier="
                "trusted (ADR-002 Phase 1)"
            ),
            details={
                "skill_key": parsed.key,
                "kind": parsed.kind,
                "trust_tier": parsed.trust_tier,
            },
        )
    if parsed.kind == "custom":
        _validate_handler_ref(parsed.handler_ref or "", skill_key=parsed.key)
    if parsed.kind == "declarative" and not parsed.prompt_template_ref:
        raise SkillRegistrationError(
            message=(
                f"skill {parsed.key!r} kind=declarative requires " "prompt_template_ref"
            ),
            details={"skill_key": parsed.key},
        )

    # tools_required catalogue validation (registration-time).
    # App-bundled skills may reference either a live central MCP tool or a
    # bundle-local tool declared by this same App's canonical manifest.
    if parsed.tools_required:
        live_tools = await _live_mcp_tool_names()
        declared_bundle_tools = set(bundle_tool_names or ())
        valid_tools = live_tools | declared_bundle_tools
        unknown = [t for t in parsed.tools_required if t not in valid_tools]
        if unknown:
            raise InvalidToolReferenceError(
                message=(
                    f"skill {parsed.key!r} declares unknown tools: "
                    f"{sorted(unknown)}"
                ),
                details={
                    "skill_key": parsed.key,
                    "unknown_tools": sorted(unknown),
                    "live_catalogue_size": len(live_tools),
                    "bundle_tool_count": len(declared_bundle_tools),
                },
            )

    app_node = await App.get(app_id)
    if not app_node:
        raise SkillRegistrationError(
            message=f"register_skill: App {app_id!r} not found",
            details={"app_id": app_id},
        )

    # Idempotency: find existing Skill under this App with the same key.
    existing = await get_skill_by_key(app_id=app_id, key=parsed.key)
    now = utc_now_iso()
    payload = {
        "app_id": app_id,
        "workspace_id": workspace_id,
        "key": parsed.key,
        "name": parsed.name or parsed.key,
        "description": parsed.description or "",
        "kind": parsed.kind,
        "prompt_template_ref": parsed.prompt_template_ref,
        "handler_ref": parsed.handler_ref,
        "tools_required": list(parsed.tools_required or []),
        "parameters_schema": dict(parsed.parameters_schema or {}),
        "outputs": list(parsed.outputs or []),
        "private": bool(parsed.private),
        "trust_tier": parsed.trust_tier,
        "external_apis": list(parsed.external_apis or []),
    }

    if existing is not None:
        preserve_override = bool(getattr(existing, "body_override", None))
        preserved = {
            "body_override": getattr(existing, "body_override", None),
            "enabled": getattr(existing, "enabled", True),
            "customized_at": getattr(existing, "customized_at", None),
            "customized_by": getattr(existing, "customized_by", None),
        }
        if preserve_override:
            preserved["name"] = getattr(existing, "name", payload["name"])
            old_desc = str(getattr(existing, "description", "") or "").strip()
            if old_desc:
                preserved["description"] = old_desc

        for field, value in payload.items():
            if field in preserved:
                continue
            setattr(existing, field, value)

        for field, value in preserved.items():
            setattr(existing, field, value)

        from app.agentive.workspace_agent_profile import resolve_bundle_default_body

        _, new_digest = await resolve_bundle_default_body(existing, app=app_node)
        if new_digest:
            old_digest = getattr(existing, "bundle_default_digest", None)
            existing.bundle_default_digest = new_digest
            if preserve_override and old_digest and old_digest != new_digest:
                existing.stale_default = True

        existing.updated_at = now
        await existing.save()
        logger.info(
            "skill_registry: updated skill %s under App %s",
            parsed.key,
            app_id,
        )
        return existing

    skill = await Skill.create(
        **payload,
        origin="bundle",
        enabled=True,
        created_at=now,
        updated_at=now,
    )
    await app_node.connect(skill, edge=CONTAINS, added_at=now)

    from app.agentive.workspace_agent_profile import resolve_bundle_default_body

    _, digest = await resolve_bundle_default_body(skill, app=app_node)
    if digest:
        skill.bundle_default_digest = digest
        await skill.save()
    logger.info(
        "skill_registry: registered skill %s under App %s (kind=%s, private=%s)",
        parsed.key,
        app_id,
        parsed.kind,
        parsed.private,
    )
    return skill


async def unregister_skills_for_app(app_id: str) -> int:
    """Delete every Skill owned by an App. Returns count.

    Plan 10-05's uninstall lifecycle calls this as the inverse of install
    step 7. CONTAINS edges are removed implicitly when the Skill nodes are
    destroyed.
    """
    if not app_id:
        return 0
    app_node = await App.get(app_id)
    if not app_node:
        # Nothing to unregister — already gone or never existed.
        return 0
    skills = await app_node.nodes(edge=[CONTAINS], node=["Skill"])
    count = 0
    for sk in skills:
        # jvspatial 0.0.17 has no ``Node.destroy`` — ``delete(cascade=False)``
        # removes the Skill + its incoming CONTAINS edge only.
        try:
            await sk.delete(cascade=False)
        except Exception as e:  # pragma: no cover — defensive
            logger.warning(
                "skill_registry: failed to delete Skill %s: %s",
                getattr(sk, "id", "<unknown>"),
                e,
            )
            continue
        count += 1
    logger.info("skill_registry: unregistered %d skills under App %s", count, app_id)
    return count


async def get_skill_by_key(app_id: str, key: str) -> Optional[Skill]:
    """Lookup a Skill by (app_id, key). None if absent."""
    if not app_id or not key:
        return None
    app_node = await App.get(app_id)
    if not app_node:
        return None
    skills = await app_node.nodes(edge=[CONTAINS], node=["Skill"])
    for sk in skills:
        if getattr(sk, "key", "") == key:
            return sk
    return None


async def get_callable_skills(
    caller_agent_id: str,
    workspace_id: str,
    *,
    user_id: str | None = None,
    active_apps_only: bool = False,
    include_disabled: bool = False,
) -> List[Skill]:
    """Resolve the set of Skills callable by ``caller_agent_id``.

    Single-source-of-truth gate for resolver-time ``private:`` enforcement
    per Architectural Decision 5, plus user App-access filtering
    (I-SKILL-SCOPE-01). Walks ``Workspace → Apps → Skills``, then filters:

        keep skill iff (not skill.private) OR (skill.app_id == caller_app_id)

    When ``user_id`` is supplied, skills whose owning App is not in
    ``accessible_apps_for_scope(user_id, workspace_id)`` are excluded.

    ``caller_app_id`` is read server-side from the caller's AgentConfig (NOT
    from any client-supplied value) — closes T-10-04-07.

    Returns the filtered list. Empty list when the agent is unknown or the
    workspace has no installed Apps.
    """
    if not workspace_id:
        return []

    caller_app_id: Optional[str] = None
    if caller_agent_id:
        try:
            cfg = await AgentConfig.get(caller_agent_id)
            if cfg is not None:
                caller_app_id = getattr(cfg, "app_id", None)
        except Exception as e:  # pragma: no cover — defensive
            logger.warning(
                "skill_registry: failed to load AgentConfig %s: %s",
                caller_agent_id,
                e,
            )

    accessible_app_ids: Optional[set] = None
    if user_id:
        from app.services.agent_scope import accessible_apps_for_scope

        accessible = await accessible_apps_for_scope(user_id, workspace_id=workspace_id)
        accessible_app_ids = {a.id for a in accessible}

    apps = await App.find({"workspace_id": workspace_id})
    eligible_apps: List[App] = []
    for app_node in apps:
        if (
            active_apps_only
            and getattr(app_node, "lifecycle_state", "active") != "active"
        ):
            continue
        if accessible_app_ids is not None and app_node.id not in accessible_app_ids:
            continue
        eligible_apps.append(app_node)

    if not eligible_apps:
        return []

    app_ids = [a.id for a in eligible_apps]
    try:
        skills_by_app = await App.nodes_bulk(
            app_ids,
            direction="out",
            edge=["CONTAINS"],
            node=["Skill"],
        )
    except Exception:
        skills_by_app = {}
        for app_node in eligible_apps:
            skills_by_app[app_node.id] = await app_node.nodes(
                edge=[CONTAINS], node=["Skill"]
            )

    out: List[Skill] = []
    for app_node in eligible_apps:
        skills = skills_by_app.get(app_node.id) or []
        for sk in skills:
            if not include_disabled and not getattr(sk, "enabled", True):
                continue
            if not getattr(sk, "private", False):
                out.append(sk)
                continue
            if caller_app_id and getattr(sk, "app_id", "") == caller_app_id:
                out.append(sk)
    return out
