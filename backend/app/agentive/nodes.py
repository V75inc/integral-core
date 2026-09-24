"""Agentive node definitions.

Part of the always-on agentive ops layer. Nodes extend the core graph with
agent/harness entities (AgentConfig facets, ChannelIdentity, RoutineTask, …).
"""

from typing import Any, Dict, List, Optional

from jvspatial.core import Node
from pydantic import Field

from app.agentive.types import AgentType  # D-09: shared canonical Literal


class ChannelIdentity(Node):
    """External channel identity linked to a User.

    Supports WhatsApp, Slack, Telegram, email, SMS, and in-app channels.
    For org-facing agents, external users are authenticated via channel
    identity or OTP — no Integral account required.
    """

    user_id: str = ""
    channel: str = ""  # "whatsapp" | "slack" | "telegram" | "email" | "sms" | "in_app"
    channel_user_id: str = ""  # Phone number, Slack user ID, email, etc.
    verified: bool = False
    verified_at: Optional[str] = None
    preferences: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None


class AgentConfig(Node):
    """Per-user or per-workspace agent configuration.

    Bound to User via HAS_AGENT_CONFIG edge (personal agent)
    or to a Workspace via HAS_ORG_AGENT edge (workspace-facing agent).
    """

    # Integral auth user id (JWT subject) for personal scope; used by uplink registry
    user_id: str = ""
    scope: str = (
        "personal"  # "personal" | "org_facing" | "system"  # ADR-003 facet-collapse pending
    )
    # ADR-003 scaffolding (Wave 3): preferred facet discriminator for new code.
    # When set, mirrors ``scope`` (``personal`` | ``org_facing`` | ``system``).
    # Use ``app.agentive.facet.effective_facet`` in readers. Legacy rows leave
    # this ``None`` — fall back to ``scope`` until full collapse. Dual-write
    # ``facet = scope`` on create/update paths (Full Sweep F1).
    facet: Optional[str] = None
    # D-09: shared AgentType Literal in app/agentive/types.py
    agent_type: AgentType = "jvagent"
    persona: str = ""  # System prompt / personality override
    # Soft-tombstone (ADR-003 / Full Sweep F3): write-ignored descriptive
    # metadata. Readers MUST NOT enforce A2A discovery, delegation, or
    # capability gates against this field — A2A is retired. Persisted only so
    # legacy rows / uplink payloads keep shape until facet collapse drops it.
    capabilities: List[str] = Field(
        default_factory=list
    )  # write-ignored; do not enforce A2A
    preferences: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    workspace_id: Optional[str] = None  # Set only for scope="org_facing"
    uplink_url: str = ""  # Where the agent connects from (MCP/Skills endpoint)
    last_connected_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    # Soft-tombstone (ADR-003 / Full Sweep F3): historically List[Policy.id]
    # plus retired A2A grants. Distinct from the ``scope: str`` facet
    # discriminator at L41 (personal|org_facing|system). The per-agent baseline
    # Policy is still materialized by ``materialize_policies_for_agent``
    # (``operational_model.author``); this list field itself is write-ignored for A2A —
    # readers MUST NOT enforce agent-to-agent grants from it.
    policy_scope: List[str] = Field(default_factory=list)  # write-ignored A2A
    # Phase 10 Plan 10-04 (APP-AGENTS-01) — additive field.
    # ``None`` for legacy / non-App-bundled agents (preserves existing rows).
    # Set to the parent App.id when this AgentConfig was registered as part of
    # an App install (``uplink_registry.register_app_agent``). Used by
    # ``skill_registry.get_callable_skills`` for resolver-time private-flag
    # enforcement (Architectural Decision 5): only an agent whose ``app_id``
    # matches a private skill's owning ``app_id`` may resolve that skill.
    app_id: Optional[str] = None
    # Phase 10 Plan 10-04 (APP-AGENTS-01) — schedule definitions captured at
    # registration time. Each entry mirrors the manifest spec
    # (``cron``, ``description``, ``status``: "scheduled" | "manual" — set to
    # ``manual`` when the native scheduler is unavailable per §6.4 degradation).
    scheduled_runs: List[Dict[str, Any]] = Field(default_factory=list)
    # Phase 10 Plan 10-04 (APP-AGENTS-01) — staging policy for App-bundled
    # agent writes ("required" | "optional" | "none"). Defaults to "required"
    # for v1 per app_bundles_v1.md §6.5.
    staging: str = "required"


class ConversationContext(Node):
    """Bridges an agent's conversation to Integral's productivity entities.

    Agent-agnostic: works with jvagent, Claude Code, Open Claw, or any agent.
    Supports hierarchical contexts (workspace-facing agent conversations scoped
    to a specific external user's access level).
    """

    # ConversationContext.agent_type stays `str` until tightened in a later phase
    # (current default `""` is not a valid AgentType Literal value; legacy strings
    # like "claude_code" / "open_claw" map to "custom" once tightened).
    agent_type: str = ""  # "jvagent" | "claude_code" | "open_claw" | "custom"
    agent_conversation_id: str = ""  # Foreign key to agent's conversation
    user_id: str = ""  # The Integral user this conversation serves
    agent_config_id: Optional[str] = None  # Links to AgentConfig for capability lookup
    workspace_id: Optional[str] = (
        None  # Set when this is a workspace-facing conversation
    )
    scope: str = "personal"  # "personal" | "org_facing"
    persona: str = ""  # Active persona for this conversation
    focused_track_id: Optional[str] = None
    focused_space_id: Optional[str] = None
    focused_operational_model_id: str = ""
    parent_context_id: Optional[str] = (
        None  # For org agent: links to org's master context
    )
    entities_referenced: List[Dict[str, Any]] = Field(default_factory=list)
    user_patterns: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class Connector(Node):
    """Per-user connector instance — actor-of-record for downstream audit/policy phases.

    Phase 1: stored only; sync semantics + IS_CONNECTED_TO edge land in Phase 5 (D-07).
    Per D-08, registered conditionally only when AGENTIVE_ENABLED=1 (in main.py).

    Seven canonical fields (CON-01 + D-10):
    - kind: AgentType Literal — same enum as AgentConfig.agent_type (D-09)
    - auth_state: connector-specific credential / handshake state
    - sync_cursor: last-sync marker for pull-based connectors (Phase 5)
    - mapping_profile: optional OperationalModel id for projecting connector data
    - owner: User.id — preserved for compat per D-07; canonical relationship is OWNS edge
    - permissions: capability-scoped permission strings
    - capabilities: D-10 — Phase 1 stores; Phase 6 enforces capability-scoped dispatch
    """

    kind: AgentType = "jvagent"  # D-09 Literal — same enum as AgentConfig.agent_type
    auth_state: Dict[str, Any] = Field(default_factory=dict)
    sync_cursor: Optional[str] = None
    mapping_profile: Optional[str] = None
    owner: str = (
        ""  # User.id — preserved per D-07 (canonical relationship is OWNS edge)
    )
    permissions: List[str] = Field(default_factory=list)
    capabilities: List[str] = Field(default_factory=list)  # D-10
    # Phase 5 Plan 05-01 — additive scalar fields (locked decision §A4).
    # Sync scheduler (Plan 05-03) consumes both. Defaults preserve legacy
    # Phase 1 connectors that have no sync semantics.
    # ``subclass_slug`` is the registry key for SyncConnector dispatch
    # (replaces overload of ``mapping_profile``); read by 05-03 sync_runtime
    # + 05-05 fixture. ``mapping_profile`` is deprecated for binding (see
    # I-CON-02); use the IS_CONNECTED_TO edge instead.
    subclass_slug: str = ""
    sync_interval_seconds: int = 300  # per-connector tick interval (seconds)
    last_synced_at: Optional[str] = (
        None  # ISO timestamp updated by sync_runtime on successful pull
    )
    # ADR-009 — MCP client mount (additive; empty for sync connectors).
    # workspace_id is a denormalized cache for per-workspace tool registry
    # keys (I-GRAPH-01); structural ownership remains User —OWNS→ Connector.
    workspace_id: str = ""
    health_status: str = "unknown"  # unknown | healthy | error
    last_error: Optional[str] = None
    last_health_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    # Connector scoping — who may invoke through this row:
    # - "per_user" (default): only the owning user; each user installs and
    #   authorizes their own row. Pre-scoping rows read as per_user.
    # - "shared": any member of the mounted workspace invokes through this
    #   one row (admin-installed); re-auth stays owner-or-admin-only.
    connection_mode: str = "per_user"
    # Operator-chosen display label (install sheet + rename); falls back to
    # the catalog display_name when blank. Kept off auth_state so renames
    # never rewrite credential material.
    label: str = ""


class RoutineTask(Node):
    """A user-created recurring instruction, replayed as an agent turn on a cadence.

    Bound to User via HAS_ROUTINE_TASK. Distinct from both jvspatial's own
    (unpersisted, runtime-only) scheduler ``ScheduledTask`` model and
    ``AgentConfig.scheduled_runs`` (bundle-declared default schedules for
    App-bundled agents — a different actor, different layer). This node is
    the user-issued, chat-created equivalent: personal-scope only in v1.

    ``write_scope`` is the resource allowlist the user pre-approved at
    creation time — a run may auto-apply a staged write ONLY when its
    target matches an entry in this list; every other write always falls
    back to a normal staged card for manual review (fail-closed).

    ``max_runs`` / ``run_count`` are the run-count dimension: ``max_runs``
    is ``None`` for an open-ended routine (the original v1 default), or a
    positive int capping the number of SUCCESSFUL runs. ``run_count``
    increments only on ``last_run_status == "success"`` (a failed attempt
    doesn't consume the budget) — when it reaches ``max_runs`` the
    scheduler transitions ``status`` to ``"completed"``, a terminal state
    distinct from user-initiated ``"cancelled"``.
    """

    user_id: str = ""
    workspace_id: str = ""
    thread_id: str = ""  # ChatThread this routine posts into
    agent_id: str = ""  # sticky agent binding, mirrors ChatThread.agent_id
    instruction: str = ""  # canonical natural-language prompt replayed each run
    cron: str = ""  # 5-field cron, computed by the agent from conversational phrasing
    timezone: str = "UTC"  # IANA tz
    status: str = "active"  # active | paused | completed | cancelled
    write_scope: List[Dict[str, str]] = Field(default_factory=list)
    next_run_at: Optional[str] = None
    last_run_at: Optional[str] = None
    last_run_status: Optional[str] = None  # success | error | skipped
    last_run_error: Optional[str] = (
        None  # short human reason when last_run_status=error
    )
    consecutive_failures: int = 0
    max_runs: Optional[int] = (
        None  # None = unlimited; else stop after this many successes
    )
    run_count: int = 0  # successful-run counter; only meaningful when max_runs is set
    # Provenance for App-bundled scheduled_runs materialization (uplink_registry).
    # Empty for user-created chat routines.
    source_app_id: str = ""
    source_schedule_key: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def get_indexes(cls) -> List[Dict[str, Any]]:
        """Compound index backing ``routine_task_scheduler.run_scheduler_pass``'s
        every-tick due-task query (``{"status": "active", "next_run_at": {"$lte": ...}}``).

        Without this, the shared ``node`` table's generic ``entity`` index
        narrows to RoutineTask rows but ``context.status`` / ``context.next_run_at``
        get evaluated via unindexed JSONB extraction — a full scan of the
        RoutineTask subset every scheduler tick (measured ~300ms on a
        2.4k-row dev table via ``EXPLAIN ANALYZE``; see routine_task_scheduler.py).
        """
        indexes = super().get_indexes()
        indexes.append(
            {
                "fields": [("context.status", 1), ("context.next_run_at", 1)],
                "unique": False,
                "name": "idx_routine_task_status_next_run_at",
            }
        )
        return indexes
