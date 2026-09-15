"""Agentive edge definitions.

Conditionally registered when AGENTIVE_ENABLED=True.
Extends the core graph with agent-specific relationships.
"""

from typing import Optional

from jvspatial.core import Edge


class HasChannelIdentity(Edge):
    """User → ChannelIdentity (external channel linkages)."""

    is_primary: bool = False
    created_at: Optional[str] = None
    bidirectional: bool = False


class HasIntegralContext(Edge):
    """ConversationContext → Track|App|Entry (entity focus in conversation)."""

    context_type: str = ""  # "focus" | "referenced" | "triggered_by"
    confidence: float = 1.0
    mentioned_at: Optional[str] = None
    bidirectional: bool = False


class HasAgentConfig(Edge):
    """User → AgentConfig (personal agent binding)."""

    is_default: bool = True
    activated_at: Optional[str] = None
    bidirectional: bool = False


class HasOrgAgent(Edge):
    """Workspace → AgentConfig (workspace-facing agent binding).

    .. deprecated::
        ADR-003 facet-collapse pending. Retained so boot / org-facing uplink
        paths keep working. The collapse plan will route org-facing facet
        wiring through ``HasAgentConfig`` (or a renamed successor) keyed by
        ``AgentConfig.facet`` / ``.scope`` instead of a separate edge type.
        Do not add new call sites; prefer reading ``AgentConfig.facet`` (fall
        back to ``scope``) on the singular resident.
    """

    configured_by: str = ""  # user_id of workspace admin who set it up
    configured_at: Optional[str] = None
    bidirectional: bool = False


class HasSystemAgent(Edge):
    """IntegralApp → AgentConfig (system-scope agent binding).

    .. deprecated::
        ADR-003 facet-collapse pending. Retained so system-registration and
        I-GRAPH-01 reachability keep working. The collapse plan will hang the
        system facet off the same harness config edge family as personal /
        org-facing, discriminated by ``AgentConfig.facet`` (migrated from
        ``scope``). Do not add new call sites.

    Phase 10.5 Plan 10.5-06 (I-GRAPH-01). System-scope AgentConfig rows
    (``scope="system"``) have no concrete User or Workspace owner and
    must hang off the singleton ``IntegralApp`` to remain reachable
    from Root. Created by the system-registration path in
    ``uplink.py:register_system_agent`` (uplink.py:304).
    """

    registered_at: Optional[str] = None
    bidirectional: bool = False


class ServesUser(Edge):
    """Org AgentConfig → User (external user authorized to interact with org agent).

    External users are authenticated via channel identity (WhatsApp, email)
    or OTP — no Integral account required.
    """

    authorized_by: str = ""  # admin user_id
    authorized_at: Optional[str] = None
    scope_level: str = "limited"  # "full" | "limited" | "readonly"
    channel: str = ""  # "whatsapp" | "email" | "sms" | "web"
    channel_user_id: str = ""  # Phone, email, etc.
    verified: bool = False
    bidirectional: bool = False


class HasRoutineTask(Edge):
    """User → RoutineTask (personal recurring-instruction binding)."""

    activated_at: Optional[str] = None
    bidirectional: bool = False


class Owns(Edge):
    """User → Connector (canonical owner relationship per D-07).

    Per CONTEXT D-07: ``Connector.owner`` scalar field is preserved for compatibility
    but the canonical relationship is THIS edge. ``IS_CONNECTED_TO`` (Connector →
    Track/App) is deferred to Phase 5 when sync semantics land. ``ORG_OWNS`` is
    deferred entirely (no per-org connectors in v1).

    NOTE: This is the agentive-layer ``Owns`` edge for User → Connector, distinct
    from the core ``OWNS`` edge (User → Track | Workspace | App) defined in
    ``app/models/edges.py``. Both follow the project convention: PascalCase class
    + ALL_CAPS alias (RESEARCH Pitfall 5 explicitly forbids defining
    ``class OWNS(Edge):`` directly here — alias only).
    """

    granted_at: Optional[str] = None
    bidirectional: bool = False


# Backward-compatible aliases for existing imports/usages.
HAS_CHANNEL_IDENTITY = HasChannelIdentity
HAS_INTEGRAL_CONTEXT = HasIntegralContext
HAS_AGENT_CONFIG = HasAgentConfig
HAS_ORG_AGENT = HasOrgAgent
HAS_SYSTEM_AGENT = HasSystemAgent
HAS_ROUTINE_TASK = HasRoutineTask
SERVES_USER = ServesUser
# D-07: PascalCase class + ALL_CAPS alias for the agentive User → Connector ownership edge.
OWNS = Owns
