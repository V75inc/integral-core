"""Agent registry helpers — per-agent baseline Policy materialization.

Without an attached Policy, ``policy_engine.evaluate(Subject(kind="agent", ...))``
returns ``fail_closed_no_policy``, so the Operational Model-author endpoint would
fail-close for every agent caller. This helper mirrors Phase 5's per-Connector
Policy materialization (I-CON-04) and runs at AgentConfig.create time inside
``register_agent`` / ``register_system_agent``.

The Policy grants the agent its baseline action on its own agent scope:

- ``operational_model.author``  — the gate on POST /api/operational-models/author.

Every agent can author Operational Models from day one without needing an
explicit Policy attachment; a finer-grained additive Policy can layer on top
without conflicting with this baseline.

(The former A2A baseline actions ``agent.discover`` / ``a2a.delegate`` were
retired with the agent-to-agent fabric — ADR-003.)
"""

import logging
from typing import Any

from app.agentive.nodes import AgentConfig

logger = logging.getLogger(__name__)

# Baseline action list granted to every registered agent on its own scope.
BASELINE_AGENT_ACTIONS = [
    "operational_model.author",
]


async def materialize_policies_for_agent(
    *,
    agent: AgentConfig,
    actor_id: str,
) -> "Any":
    """Write the default per-agent Policy.

    Mirrors Phase 5 ``materialize_policies_for_connector`` (I-CON-04). Without
    an attached Policy the agent fail-closes on day one for profile authoring
    (``policy_engine.evaluate(subject=Subject(kind="agent"), action="operational_model.author")``
    returns ``fail_closed_no_policy``).

    Idempotency is enforced by the caller (register_agent only calls this on
    the CREATE branch, never on the existing-AgentConfig update branch).
    """
    from app.services.policy_registry import create_policy

    policy = await create_policy(
        subject_kind="agent",
        subject_id=agent.id,
        scope=f"agent:{agent.id}",
        actions=list(BASELINE_AGENT_ACTIONS),
        entry_types=[],  # not entry-scoped; per-result evaluate covers visibility
        tags=[],
        requires_human_approval=False,
        is_active=True,
        created_by=actor_id,
    )
    return policy


async def wire_agent_config_attachment_edge(config: AgentConfig) -> bool:
    """Wire the canonical structural edge for an ``AgentConfig``.

    Idempotent. Returns True if a new edge was materialized; False if it
    already existed OR no anchor resolved. Call at register/create time
    so read paths do not need repair.
    """
    try:
        from datetime import datetime, timezone

        from app.agentive.edges import (
            HAS_AGENT_CONFIG,
            HAS_ORG_AGENT,
            HAS_SYSTEM_AGENT,
        )
        from app.models.edges import CONTAINS
        from app.models.nodes import APP_NODE_ID, App, IntegralApp, Workspace
        from app.services.permissions import get_user_node

        now = datetime.now(timezone.utc).isoformat()

        # App-bundled — App-Node anchor takes precedence (CONTEXT.md
        # "extends from the App-Node directly" requirement).
        if getattr(config, "app_id", None):
            app = await App.get(config.app_id)
            if app is not None:
                ctx = await app.get_context()
                existing = await ctx.find_edges_between(
                    app.id, config.id, edge_class=CONTAINS
                )
                if existing:
                    return False
                await app.connect(config, edge=CONTAINS, added_at=now)
                return True
            logger.warning(
                "wire_agent_config_attachment_edge: app_id=%s missing for "
                "AgentConfig %s — leaving unwired",
                config.app_id,
                config.id,
            )
            return False

        scope = getattr(config, "scope", "") or ""

        if scope == "personal" and getattr(config, "user_id", ""):
            user = await get_user_node(config.user_id)
            if user is None:
                logger.warning(
                    "wire_agent_config_attachment_edge: user_id=%s unresolvable "
                    "for personal AgentConfig %s",
                    config.user_id,
                    config.id,
                )
                return False
            ctx = await user.get_context()
            existing = await ctx.find_edges_between(
                user.id, config.id, edge_class=HAS_AGENT_CONFIG
            )
            if existing:
                return False
            await user.connect(config, edge=HAS_AGENT_CONFIG, activated_at=now)
            return True

        if scope == "org_facing" and getattr(config, "workspace_id", None):
            workspace = await Workspace.get(config.workspace_id)
            if workspace is None:
                logger.warning(
                    "wire_agent_config_attachment_edge: workspace_id=%s "
                    "missing for org_facing AgentConfig %s",
                    config.workspace_id,
                    config.id,
                )
                return False
            ctx = await workspace.get_context()
            existing = await ctx.find_edges_between(
                workspace.id, config.id, edge_class=HAS_ORG_AGENT
            )
            if existing:
                return False
            await workspace.connect(config, edge=HAS_ORG_AGENT, configured_at=now)
            return True

        if scope == "system":
            integral_app = await IntegralApp.get(APP_NODE_ID)
            if integral_app is None:
                logger.warning(
                    "wire_agent_config_attachment_edge: IntegralApp singleton "
                    "missing — system AgentConfig %s left unwired",
                    config.id,
                )
                return False
            ctx = await integral_app.get_context()
            existing = await ctx.find_edges_between(
                integral_app.id, config.id, edge_class=HAS_SYSTEM_AGENT
            )
            if existing:
                return False
            await integral_app.connect(config, edge=HAS_SYSTEM_AGENT, registered_at=now)
            return True

        logger.warning(
            "wire_agent_config_attachment_edge: no anchor for AgentConfig %s "
            "(scope=%r, user_id=%r, workspace_id=%r, app_id=%r)",
            config.id,
            scope,
            getattr(config, "user_id", ""),
            getattr(config, "workspace_id", None),
            getattr(config, "app_id", None),
        )
        return False
    except Exception:
        logger.exception(
            "wire_agent_config_attachment_edge raised for AgentConfig %s",
            getattr(config, "id", "?"),
        )
        return False


async def register_agent_config(
    *,
    actor_id: str,
    **fields: Any,
) -> AgentConfig:
    """Create AgentConfig and wire its structural edge before returning."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    create_fields = dict(fields)
    create_fields.setdefault("created_at", now)
    create_fields.setdefault("updated_at", now)
    # Dual-write facet ↔ scope until ADR-003 facet collapse (Full Sweep F1).
    scope_val = create_fields.get("scope") or "personal"
    create_fields["scope"] = scope_val
    create_fields["facet"] = scope_val
    config = await AgentConfig.create(**create_fields)
    await wire_agent_config_attachment_edge(config)
    await materialize_policies_for_agent(agent=config, actor_id=actor_id)
    return config
