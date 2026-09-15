"""Skill definitions for the agentive layer.

Declares the ``SKILLS`` catalogue (SOP-style prompt bundles surfaced via
``GET /api/agentive/skills``) and ``resolve_callable_units`` — the merge of an
agent's dispatchable tool names (from the manifest-driven
``build_tool_catalogue``) with its App-bundled Skills.

The legacy external MCP tool surface (the ``MCP_TOOLS`` route-derived catalogue
and the ``execute_tool`` dispatcher) was retired in M2a Task 7; the canonical
tool surface is now ``app.agentive.tooling`` (``dispatch_tool`` +
``build_tool_catalogue``).
"""

from typing import Any, Dict

# ---------------------------------------------------------------------------
# Skills Definitions
# ---------------------------------------------------------------------------


SKILLS = {
    "integral_query": {
        "name": "integral_query",
        "description": "Answer questions about entries, tasks, deadlines, and activity across tracks.",
        "examples": [
            "What's due this week?",
            "How many tasks are in Marketing?",
            "Show me overdue items",
        ],
        "success_criteria": "Accurate, permission-scoped response with relevant entry data.",
    },
    "integral_collaborate": {
        "name": "integral_collaborate",
        "description": "Manage sharing, assignments, and team coordination.",
        "examples": [
            "Share Marketing track with Alex",
            "Assign the budget task to Sarah",
        ],
        "success_criteria": "Collaborator added or role updated with confirmation.",
    },
    "integral_digest": {
        "name": "integral_digest",
        "description": "Generate activity summaries, deadline reminders, and proactive alerts.",
        "examples": [
            "Good morning digest",
            "What's overdue?",
        ],
        "success_criteria": "Accurate summary of recent activity and upcoming deadlines.",
    },
    "integral_update": {
        "name": "integral_update",
        "description": "Modify existing entries — change status, update fields, assign tags.",
        "examples": [
            "Mark the budget task as done",
            "Change priority to high on the login bug",
            "Update the deadline to next Monday",
        ],
        "success_criteria": "Entry updated with the requested field changes confirmed.",
    },
    "integral_comment": {
        "name": "integral_comment",
        "description": "Add comments to entries for discussion and notes.",
        "examples": [
            "Add a comment on the design task: looks good to me",
            "Reply that we need more info on the budget item",
        ],
        "success_criteria": "Comment created on the referenced entry.",
    },
    "integral_create_app_track": {
        "name": "integral_create_app_track",
        "description": "Create new tracks or apps to organize work.",
        "examples": [
            "Create a track for my new startup",
            "Make a CRM app with contacts and opportunities",
            "Add a track called Bug Reports",
        ],
        "success_criteria": "Track or app created with the requested name and configuration.",
    },
    "integral_resolve": {
        "name": "integral_resolve",
        "description": "Resolve references to entries from conversation context or natural language.",
        "examples": [
            "Which task did you just create?",
            "Show me the budget entry",
            "What was that bug about the login?",
        ],
        "success_criteria": "Correct entry identified and returned with full context.",
    },
}


# ---------------------------------------------------------------------------
# App-bundled Skill resolution (Phase 10 Plan 10-04 — APP-SKILLS-01)
# ---------------------------------------------------------------------------
#
# ``resolve_callable_units(agent_id, workspace_id)`` merges the agent's
# dispatchable tool names (from the manifest-driven ``build_tool_catalogue``)
# with App-bundled Skills surfaced by ``skill_registry.get_callable_skills``
# (resolver-time ``private`` gate enforced there — Architectural Decision 5).


async def resolve_callable_units(
    agent_id: str,
    workspace_id: str,
    *,
    user_id: str | None = None,
) -> Dict[str, Any]:
    """Return the merged set of callable units for an agent.

    Output shape:
      {
        "mcp_tools": [tool_name, ...],         # from existing MCP catalogue
        "app_bundled_skills": [                # NEW — Plan 10-04
          {
            "id": "...",
            "app_id": "...",
            "key": "...",
            "name": "...",
            "kind": "declarative" | "custom",
            "prompt_template_ref": "..." | None,
            "handler_ref": "..." | None,
            "tools_required": [...],
            "private": bool,
          },
          ...
        ],
      }

    The MCP path is unchanged (existing AgentConfig.capabilities flow), while
    the App-bundled skill path delegates to
    ``skill_registry.get_callable_skills`` so the resolver-time private
    enforcement is a single source of truth.
    """
    from app.agentive.services.skill_registry import get_callable_skills
    from app.agentive.tooling.catalogue import build_tool_catalogue

    mcp_tools = [t["name"] for t in build_tool_catalogue()]
    skills = await get_callable_skills(agent_id, workspace_id, user_id=user_id)
    return {
        "mcp_tools": mcp_tools,
        "app_bundled_skills": [
            {
                "id": getattr(sk, "id", ""),
                "app_id": getattr(sk, "app_id", ""),
                "key": getattr(sk, "key", ""),
                "name": getattr(sk, "name", ""),
                "kind": getattr(sk, "kind", ""),
                "prompt_template_ref": getattr(sk, "prompt_template_ref", None),
                "handler_ref": getattr(sk, "handler_ref", None),
                "tools_required": list(getattr(sk, "tools_required", []) or []),
                "private": bool(getattr(sk, "private", False)),
            }
            for sk in skills
        ],
    }
