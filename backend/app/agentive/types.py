"""Agentive type aliases — single source of truth for cross-module enums.

Per CONTEXT D-09: AgentType Literal lives here ONCE, imported by AgentConfig,
Connector, every Pydantic body model, and every connector's @register_connector arg.

`"claude_code"` and `"open_claw"` (already strings in `ConversationContext.agent_type`)
are NOT first-class v1 vendors — grep on 2026-05-06 found them only as comment
documentation, not as v1 ROADMAP commitments. They map to `"custom"` if needed.
`ConversationContext.agent_type` stays `str` (default `""`) until tightened in a
later phase (see Pitfall 7 in 01-RESEARCH.md).
"""

from typing import Literal

AgentType = Literal["jvagent", "mcp", "skill_bundle", "custom"]
