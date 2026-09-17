"""Agent uplink registry — in-memory tracking of connected agents (AGT-01).

Backed by AgentConfig graph nodes for persistence; the in-memory dict tracks live
state (last_heartbeat, connected) which does not need to survive restarts.

Public API (signatures + behavior contracts):

  uplink_registry: AgentUplinkRegistry              # module-level singleton
  AgentConnection                                   # value object (see below)

  AgentUplinkRegistry methods:
    async register(config) -> str
        Register or update an agent connection. `config` may be an AgentConfig
        graph node OR a plain dict; reads via _cfg_val helper. Returns config_id.

    async unregister(agent_config_id: str) -> None
        Mark agent as disconnected and remove from the live dict.

    async heartbeat(agent_config_id: str) -> None
        Refresh last_heartbeat to utc_now() and persist last_connected_at to
        the AgentConfig graph node. Failure to persist is logged at WARNING
        (post-Plan-01-02; was silent except: pass pre-Plan-01-02).

    async get_agent_for_user(user_id: str) -> Optional[AgentConnection]
        Return the active personal agent for a user (scope='personal', connected=True).

    async get_system_agent() -> Optional[AgentConnection]
        Return the deployment-wide agent (scope='system'). Marks connections
        disconnected if last_heartbeat is older than 120 seconds.

    async get_org_agent_for_user(org_id, channel_user_id='') -> Optional[AgentConnection]
        Return the org-facing agent for an organization.

    async is_agent_connected(agent_config_id: str) -> bool
        Return True iff the agent is live (heartbeat within 120s).

    get_status() -> Dict[str, Any]
        Return a JSON-serializable summary of connected_agents count + per-agent details.
"""

import logging
from datetime import (  # noqa: F401 (kept for type hints / ad-hoc utc compares)
    datetime,
    timezone,
)
from typing import Any, Dict, List, Optional

from app.utils.time import utc_now, utc_now_iso

logger = logging.getLogger(__name__)


def _cfg_val(config: Any, attr: str, default: Any = "") -> Any:
    """Read a field from an AgentConfig instance or plain dict."""
    if isinstance(config, dict):
        return config.get(attr, default)
    return getattr(config, attr, default)


class AgentConnection:
    """Tracks a connected agent's live state (in-memory; not persisted).

    Fields (all set at __init__):
        config_id (str):         AgentConfig graph node id this connection mirrors.
        agent_type (str):        AgentType value (jvagent | mcp | skill_bundle | custom)
                                 per D-09; default "jvagent" if upstream omits.
        uplink_url (str):        Optional URL the host calls back to for proactive pushes.
        capabilities (list):     Capability tokens advertised at registration (D-10);
                                 stored as List[str], NOT enforced in Phase 1.
        scope (str):             "personal" | "system" | "org_facing".
        user_id (str):           User.id when scope='personal'; "" otherwise.
        workspace_id (str):   Workspace.id when scope='org_facing'; "" otherwise.
        preferences (dict):      Vendor-specific config (e.g., jvagent_base_url for
                                 scope='system' jvagent connections).
        last_heartbeat (datetime): tz-aware utc datetime of last register/heartbeat
                                   call. Compared against utc_now() to compute
                                   liveness in is_agent_connected and get_system_agent.
        connected (bool):        Live flag. Set False by unregister() and by
                                 timeout-based liveness checks.
    """

    def __init__(
        self,
        config_id: str,
        agent_type: str,
        uplink_url: str,
        capabilities: list,
        scope: str,
        user_id: str = "",
        workspace_id: str = "",
        preferences: Optional[Dict[str, Any]] = None,
    ):
        self.config_id = config_id
        self.agent_type = agent_type
        self.uplink_url = uplink_url
        self.capabilities = capabilities
        self.scope = scope
        self.user_id = user_id
        self.workspace_id = workspace_id
        self.preferences: Dict[str, Any] = dict(preferences or {})
        self.last_heartbeat = utc_now()
        self.connected = True


class AgentUplinkRegistry:
    """In-memory registry of connected agents.

    Backed by AgentConfig nodes for persistence. After a server restart,
    agents must re-register via the uplink endpoint.
    """

    _agents: Dict[str, AgentConnection] = {}  # key: agent_config_id

    async def register(self, config) -> str:
        """Register or update an agent connection. Returns agent_config_id."""
        config_id = _cfg_val(config, "id", "")
        org_id = _cfg_val(config, "workspace_id", "") or ""
        prefs = _cfg_val(config, "preferences", None)
        if not isinstance(prefs, dict):
            prefs = {}
        conn = AgentConnection(
            config_id=config_id,
            agent_type=_cfg_val(config, "agent_type", "jvagent") or "jvagent",
            uplink_url=_cfg_val(config, "uplink_url", "") or "",
            capabilities=_cfg_val(config, "capabilities", []) or [],
            scope=_cfg_val(config, "scope", "personal") or "personal",
            user_id=_cfg_val(config, "user_id", "") or "",
            workspace_id=org_id,
            preferences=prefs,
        )
        self._agents[config_id] = conn
        logger.info(
            f"Agent registered: {config_id} ({conn.agent_type}, scope={conn.scope})"
        )
        return config_id

    async def unregister(self, agent_config_id: str) -> None:
        """Mark agent as disconnected."""
        if agent_config_id in self._agents:
            self._agents[agent_config_id].connected = False
            del self._agents[agent_config_id]
            logger.info(f"Agent unregistered: {agent_config_id}")

    async def heartbeat(self, agent_config_id: str) -> None:
        """Update last_connected_at for an agent."""
        if agent_config_id in self._agents:
            self._agents[agent_config_id].last_heartbeat = utc_now()
            self._agents[agent_config_id].connected = True
            # Also update the persistent AgentConfig
            try:
                from app.agentive.nodes import AgentConfig

                config = await AgentConfig.get(agent_config_id)
                if config:
                    config.last_connected_at = utc_now_iso()
                    await config.save()
            except Exception as e:
                logger.warning(
                    "uplink registry: failed to persist heartbeat for %s: %s",
                    agent_config_id,
                    e,
                )

    async def get_agent_for_user(self, user_id: str) -> Optional[AgentConnection]:
        """Find the active personal agent for a user."""
        for conn in self._agents.values():
            if conn.user_id == user_id and conn.scope == "personal" and conn.connected:
                return conn
        return None

    async def get_system_agent(self) -> Optional[AgentConnection]:
        """Deployment-wide agent (one connected jvagent / connector backend)."""
        for conn in self._agents.values():
            if conn.scope != "system" or not conn.connected:
                continue
            elapsed = (utc_now() - conn.last_heartbeat).total_seconds()
            if elapsed > 120:
                conn.connected = False
                continue
            return conn
        return None

    async def get_org_agent_for_user(
        self, workspace_id: str, channel_user_id: str = ""
    ) -> Optional[AgentConnection]:
        """Find the workspace-facing agent for an organization workspace."""
        for conn in self._agents.values():
            if (
                conn.workspace_id == workspace_id
                and conn.scope == "org_facing"
                and conn.connected
            ):
                return conn
        return None

    async def is_agent_connected(self, agent_config_id: str) -> bool:
        """Check if an agent is live (heartbeat within threshold)."""
        conn = self._agents.get(agent_config_id)
        if not conn or not conn.connected:
            return False
        # Consider disconnected if no heartbeat for 2 minutes
        elapsed = (utc_now() - conn.last_heartbeat).total_seconds()
        if elapsed > 120:
            conn.connected = False
            return False
        return True

    def get_status(self) -> Dict[str, Any]:
        """Get a summary of all connected agents."""
        return {
            "connected_agents": len([a for a in self._agents.values() if a.connected]),
            "agents": [
                {
                    "config_id": a.config_id,
                    "agent_type": a.agent_type,
                    "scope": a.scope,
                    "connected": a.connected,
                    "last_heartbeat": a.last_heartbeat.isoformat(),
                }
                for a in self._agents.values()
            ],
        }


# Global registry instance
uplink_registry = AgentUplinkRegistry()


# ---------------------------------------------------------------------------
# Phase 10 Plan 10-04 — App-bundled agent registration (APP-AGENTS-01)
# ---------------------------------------------------------------------------
#
# These helpers persist an AgentConfig Node with ``app_id`` set, bind declared
# skills (validated against ``skill_registry``), capture ``scope`` /
# ``staging`` / ``default_schedules`` from the manifest, and gracefully
# degrade schedules to ``"manual"`` when the integral native scheduler is
# unavailable (app_bundles_v1.md §6.4).
#
# Plan 10-05's install lifecycle calls ``register_app_agent`` once per
# ``app.agents[]`` manifest entry on install. The agent surface for
# non-install operations stays in the existing ``agentive/api/agent_skills.py``.


def _scheduler_available() -> bool:
    """Probe whether the integral native scheduler is currently wired up.

    Phase 10 v1 does NOT yet ship a native agent scheduler — schedules are
    captured on AgentConfig.scheduled_runs for future hookup. This probe
    returns False by default; tests may monkeypatch it to True to exercise
    the "scheduled" branch.

    The probe is module-level so test code can patch it via
    ``monkeypatch.setattr(uplink_registry_module, "_scheduler_available", lambda: True)``.
    """
    try:
        # Forward-compat: when the native scheduler ships under
        # ``app.services.scheduler``, it should expose a ``scheduler_available``
        # callable. Until then, the import will fail and we report False.
        from app.services.scheduler import scheduler_available  # type: ignore

        return bool(scheduler_available())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# default_schedules → RoutineTask materialization
#
# ``AgentConfig.scheduled_runs`` used to be the ONLY record of a bundle's
# ``default_schedules`` and nothing ever read it back to dispatch — the
# ``scheduled`` label was a lie. Each schedule is now materialized as a
# ``RoutineTask`` owned by the installing user so the existing
# ``routine_task_scheduler`` loop runs it like any user-created routine.
# ---------------------------------------------------------------------------

_SCHEDULE_THREAD_PROVIDER = "jvagent"


def _schedule_key(agent_key: str, index: int) -> str:
    """Stable per-(agent, slot) idempotency key for reinstall / library sync."""
    return f"{agent_key}:{index}"


def _schedule_instruction(
    *, slug: str, agent_key: str, skills: List[str], description: str
) -> str:
    refs = [f"`{slug}__{k}`" for k in skills if k]
    if len(refs) == 1:
        head = f"Run skill {refs[0]}"
    elif refs:
        head = f"Run skills {', '.join(refs)}"
    else:
        head = f"Run the `{agent_key}` agent's scheduled task"
    desc = (description or "").strip()
    return f"{head}: {desc}" if desc else f"{head}."


async def _resolve_installing_principal(app_node: Any) -> Optional[str]:
    """Principal (AuthUser id) the materialized routine runs as.

    ``App.owner_user_id`` holds the graph User id (see ``wire_app_owner``);
    routines and chat threads are keyed by the AuthUser principal, so map
    through the User node. Falls back to the OWNS edge, then the workspace
    owner — the same chain ``wire_app_owner`` uses.
    """
    from app.services.permissions import get_user_node

    owner_ref = str(getattr(app_node, "owner_user_id", "") or "").strip()
    user = await get_user_node(owner_ref) if owner_ref else None
    if user is None:
        try:
            owners = await app_node.nodes(edge=["OWNS"], node=["User"], direction="in")
        except Exception:  # noqa: BLE001
            owners = []
        user = owners[0] if owners else None
    if user is None:
        from app.services.workspace_permissions import get_workspace_owner_user_id

        ws_id = str(getattr(app_node, "workspace_id", "") or "").strip()
        fallback = await get_workspace_owner_user_id(ws_id) if ws_id else None
        user = await get_user_node(fallback) if fallback else None
    if user is None:
        return None
    return str(getattr(user, "user_id", "") or "").strip() or str(user.id)


async def _resolve_schedule_agent_id(principal: str, workspace_id: str) -> str:
    """Sticky agent binding for the routine's thread (same chain as thread create)."""
    try:
        from app.api.ai_chat import _resolve_agent_for_new_thread
        from app.services.chat_providers.registry import get_registry

        provider = get_registry().get(_SCHEDULE_THREAD_PROVIDER)
        if provider is None:
            return ""
        return await _resolve_agent_for_new_thread(
            user_id=principal,
            workspace_id=workspace_id,
            provider=provider,
            body_agent_id="",
        )
    except Exception:  # noqa: BLE001 — empty binding degrades at run time only
        return ""


async def _materialize_default_schedule(
    *,
    app_id: str,
    workspace_id: str,
    agent_key: str,
    skills: List[str],
    schedule_index: int,
    cron: str,
    description: str,
) -> Optional[str]:
    """Create (once) the ``RoutineTask`` backing one ``default_schedules[]`` entry.

    Returns the live routine's id, or ``None`` when nothing will dispatch it
    (no resolvable installing user, creation failed, or the user already
    cancelled it — reinstall / library sync never resurrects a cancelled
    routine or creates a duplicate).
    """
    from app.agentive.nodes import RoutineTask
    from app.agentive.services.routine_tasks import create_routine_task
    from app.agentive.workspace_agent_profile import _app_slug
    from app.models.edges import CONTAINS
    from app.models.nodes import App
    from app.services.chat_threads import create_thread

    key = _schedule_key(agent_key, schedule_index)
    try:
        existing = await RoutineTask.find(
            {"source_app_id": app_id, "source_schedule_key": key}
        )
    except Exception:  # noqa: BLE001
        existing = []
    if existing:
        live = [r for r in existing if r.status in ("active", "paused")]
        return live[0].id if live else None

    app_node = await App.get(app_id)
    if app_node is None:
        return None
    principal = await _resolve_installing_principal(app_node)
    if not principal:
        logger.warning(
            "register_app_agent: no installing user resolvable for App %s — "
            "schedule %s (cron=%r) registered with status='manual'",
            app_id,
            key,
            cron,
        )
        return None

    try:
        tracks = await app_node.nodes(edge=[CONTAINS], node=["Track"])
    except Exception:  # noqa: BLE001
        tracks = []
    write_scope = [
        {"resource_type": "track", "resource_id": t.id}
        for t in tracks
        if getattr(t, "id", "")
    ]
    slug = _app_slug(app_node)
    instruction = _schedule_instruction(
        slug=slug, agent_key=agent_key, skills=skills, description=description
    )
    app_name = str(getattr(app_node, "name", "") or slug)
    try:
        agent_id = await _resolve_schedule_agent_id(principal, workspace_id)
        thread = await create_thread(
            user_id=principal,
            provider_id=_SCHEDULE_THREAD_PROVIDER,
            title=f"{app_name}: {description or agent_key}",
            workspace_id=workspace_id,
            agent_id=agent_id,
        )
        routine = await create_routine_task(
            user_id=principal,
            workspace_id=workspace_id,
            thread_id=thread.id,
            agent_id=agent_id,
            instruction=instruction,
            cron=cron,
            timezone="UTC",
            write_scope=write_scope,
            source_app_id=app_id,
            source_schedule_key=key,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "register_app_agent: failed to materialize schedule %s for App %s "
            "(cron=%r): %s — registered with status='manual'",
            key,
            app_id,
            cron,
            e,
        )
        return None
    logger.info(
        "register_app_agent: materialized schedule %s for App %s as RoutineTask %s",
        key,
        app_id,
        routine.id,
    )
    return routine.id


async def _remove_materialized_schedules(app_id: str) -> int:
    """Drop every RoutineTask materialized from ``app_id``'s bundle schedules."""
    from app.agentive.nodes import RoutineTask

    if not app_id:
        return 0
    try:
        routines = await RoutineTask.find({"source_app_id": app_id})
    except Exception:  # noqa: BLE001
        return 0
    count = 0
    for r in routines:
        try:
            await r.delete(cascade=False)
            count += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "unregister_app_agents_for_app: failed to delete RoutineTask %s: %s",
                getattr(r, "id", "<unknown>"),
                e,
            )
    return count


async def pause_materialized_schedules(app_id: str) -> int:
    """Pause bundle-owned routines when the parent App is paused (AC-08)."""
    from app.agentive.nodes import RoutineTask
    from app.utils.time import utc_now_iso

    if not app_id:
        return 0
    try:
        routines = await RoutineTask.find({"source_app_id": app_id})
    except Exception:  # noqa: BLE001
        return 0
    count = 0
    for routine in routines:
        if routine.status != "active":
            continue
        routine.status = "paused"
        routine.updated_at = utc_now_iso()
        try:
            await routine.save()
            count += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "pause_materialized_schedules: failed for RoutineTask %s: %s",
                getattr(routine, "id", "<unknown>"),
                e,
            )
    return count


async def resume_materialized_schedules(app_id: str) -> int:
    """Re-activate bundle-owned routines when the parent App resumes."""
    from app.agentive.nodes import RoutineTask
    from app.utils.time import utc_now_iso

    if not app_id:
        return 0
    try:
        routines = await RoutineTask.find({"source_app_id": app_id})
    except Exception:  # noqa: BLE001
        return 0
    count = 0
    for routine in routines:
        if routine.status != "paused":
            continue
        routine.status = "active"
        routine.updated_at = utc_now_iso()
        try:
            await routine.save()
            count += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "resume_materialized_schedules: failed for RoutineTask %s: %s",
                getattr(routine, "id", "<unknown>"),
                e,
            )
    return count


async def register_app_agent(
    app_id: str,
    workspace_id: str,
    agent_spec: Dict[str, Any],
) -> Any:
    """Register an App-bundled agent.

    Materializes an AgentConfig Node with:
      - ``app_id`` set to the parent App.id (additive field — Plan 10-04).
      - ``workspace_id`` set to the install target workspace.
      - ``scope``: "app" | "workspace" (from agent_spec.scope; default "app").
      - ``persona``: loaded inline OR via ``persona_ref`` path resolution.
        For first-party Apps the persona_ref resolves against the seeded
        package directory; for marketplace Apps (Plan 13) bundle-resolution
        is TODO — left to that phase.
      - ``capabilities``: raw MCP tool names declared on the manifest entry.
      - ``staging``: agent-write staging mode (default "required" per
        app_bundles_v1.md §6.5).
      - ``scheduled_runs``: each entry gets ``status = "scheduled"`` if the
        native scheduler is available, else ``status = "manual"`` (graceful
        degradation per §6.4 — install does NOT fail).

    Skill binding: every entry in agent_spec.skills must already be a
    registered Skill under the same App (skill_registry must run first).
    Unknown skill keys raise AgentRegistrationError.

    Returns the persisted AgentConfig.
    """
    # Local imports to avoid pulling agentive nodes at module-import time.
    from app.agentive.nodes import AgentConfig
    from app.agentive.services.skill_registry import get_skill_by_key
    from app.exceptions import AgentRegistrationError
    from app.schemas.app_agents import AppBundledAgentRegisterRequest

    if not app_id:
        raise AgentRegistrationError(
            message="register_app_agent: app_id is required",
            details={"workspace_id": workspace_id},
        )
    if not workspace_id:
        raise AgentRegistrationError(
            message="register_app_agent: workspace_id is required",
            details={"app_id": app_id},
        )

    try:
        parsed = AppBundledAgentRegisterRequest(**agent_spec)
    except Exception as e:
        raise AgentRegistrationError(
            message=f"register_app_agent: spec failed validation: {e}",
            details={"app_id": app_id, "spec_key": agent_spec.get("key", "")},
        )

    # Skill-reference validation — Plan 10-04 hard rule: agents may only
    # reference skills already registered under the SAME App.
    for skill_key in parsed.skills:
        bound = await get_skill_by_key(app_id=app_id, key=skill_key)
        if bound is None:
            raise AgentRegistrationError(
                message=(
                    f"register_app_agent: agent {parsed.key!r} references "
                    f"unknown skill {skill_key!r} (not registered under "
                    f"App {app_id})"
                ),
                details={
                    "app_id": app_id,
                    "agent_key": parsed.key,
                    "unknown_skill_key": skill_key,
                },
            )

    # Persona resolution: inline ``persona`` wins; ``persona_ref`` is a
    # placeholder for the install-lifecycle file resolver (Plan 10-05 owns
    # the bundle-side IO). For now we store both — runtime can read inline,
    # callers can resolve the ref on demand.
    persona = parsed.persona or ""

    # Schedule registration with graceful degradation per §6.4. A schedule
    # is only labelled ``scheduled`` when something will actually dispatch
    # it: the routine-task loop is up AND a ``RoutineTask`` was materialized
    # for it (see ``_materialize_default_schedule``). Anything else is
    # ``manual`` — honest, not aspirational.
    scheduler_on = _scheduler_available()
    scheduled_runs: List[Dict[str, Any]] = []
    for idx, sched in enumerate(parsed.default_schedules or []):
        routine_id: Optional[str] = None
        if scheduler_on:
            routine_id = await _materialize_default_schedule(
                app_id=app_id,
                workspace_id=workspace_id,
                agent_key=parsed.key,
                skills=list(parsed.skills or []),
                schedule_index=idx,
                cron=sched.cron,
                description=sched.description,
            )
            status_label = "scheduled" if routine_id else "manual"
        else:
            status_label = "manual"
            logger.warning(
                "register_app_agent: scheduler unavailable — schedule for "
                "agent %s (cron=%r) registered with status='manual'",
                parsed.key,
                sched.cron,
            )
        entry: Dict[str, Any] = {
            "cron": sched.cron,
            "description": sched.description,
            "status": status_label,
        }
        if routine_id:
            entry["routine_task_id"] = routine_id
        scheduled_runs.append(entry)

    now = utc_now_iso()
    # AgentConfig.scope is the EXISTING field (personal | org_facing | system).
    # The App-bundled "scope: app | workspace" semantic maps onto:
    #   - "app"       → scope="personal" (per-installation scope; runs as the
    #                    user that installed the App)
    #   - "workspace" → scope="org_facing"
    # The raw manifest scope is recoverable from ``preferences["app_scope"]``
    # so introspection round-trips correctly.
    legacy_scope = "org_facing" if parsed.scope == "workspace" else "personal"
    from app.agentive.services.agent_registry_node import (
        wire_agent_config_attachment_edge,
    )

    cfg = await AgentConfig.create(
        user_id="",
        scope=legacy_scope,
        facet=legacy_scope,  # Full Sweep F1 — dual-write until facet collapse
        agent_type="jvagent",
        persona=persona,
        capabilities=list(parsed.capabilities or []),
        preferences={
            "app_scope": parsed.scope,  # raw "app" | "workspace"
            "persona_ref": parsed.persona_ref or "",
            "agent_key": parsed.key,
            "skills": list(parsed.skills or []),
        },
        is_active=True,
        workspace_id=workspace_id,
        app_id=app_id,
        scheduled_runs=scheduled_runs,
        staging=parsed.staging,
        created_at=now,
        updated_at=now,
    )
    try:
        await wire_agent_config_attachment_edge(cfg)
    except Exception:
        logger.warning(
            "register_app_agent: wire_agent_config_attachment_edge failed for %s",
            cfg.id,
        )
    logger.info(
        "register_app_agent: agent %s registered under App %s "
        "(scope=%s, skills=%d, schedules=%d, scheduler_on=%s)",
        parsed.key,
        app_id,
        parsed.scope,
        len(parsed.skills or []),
        len(scheduled_runs),
        scheduler_on,
    )
    return cfg


async def unregister_app_agents_for_app(app_id: str) -> int:
    """Delete every AgentConfig with ``app_id == <App.id>``. Returns count.

    Plan 10-05's uninstall lifecycle calls this as the inverse of install
    step 8. Also drops the in-memory uplink connection for each agent so
    a stale row doesn't continue to advertise as connected.
    """
    from app.agentive.nodes import AgentConfig

    if not app_id:
        return 0
    matches = await AgentConfig.find({"app_id": app_id})
    count = 0
    for cfg in matches:
        cfg_id = getattr(cfg, "id", "")
        try:
            await uplink_registry.unregister(cfg_id)
        except Exception as e:  # pragma: no cover — defensive
            logger.warning(
                "unregister_app_agents_for_app: in-memory unregister failed "
                "for %s: %s",
                cfg_id,
                e,
            )
        # jvspatial 0.0.17 has no ``Node.destroy``; cascade=False drops only
        # the AgentConfig and its incoming attachment edge.
        try:
            await cfg.delete(cascade=False)
        except Exception as e:  # pragma: no cover — defensive
            logger.warning(
                "unregister_app_agents_for_app: failed to delete " "AgentConfig %s: %s",
                cfg_id,
                e,
            )
            continue
        count += 1
    removed_routines = await _remove_materialized_schedules(app_id)
    if removed_routines:
        logger.info(
            "unregister_app_agents_for_app: removed %d materialized schedule "
            "routine(s) under App %s",
            removed_routines,
            app_id,
        )
    logger.info(
        "unregister_app_agents_for_app: removed %d App-bundled agents under App %s",
        count,
        app_id,
    )
    return count
