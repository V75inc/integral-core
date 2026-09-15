"""EmbeddedIntegralAction — in-process tool provider for the Integral agent.

Drop-in replacement for ``IntegralApiAction`` when jvagent runs embedded
inside the Integral process (``jvagent.embed.bootstrap`` was called from
``backend/app/main.py:_startup`` with ``AGENTIVE_ENABLED=1``).

Why this exists
---------------

``IntegralApiAction`` makes localhost HTTP calls to the same FastAPI app
the agent is running inside of. Two costs:

* a real socket round-trip per tool call (latency + serialization tax)
* the service-auth bridge (HMAC headers + JWT handoff) that exists only
  to translate "I am the agent acting for user X" across a process
  boundary that no longer exists.

This action shortcuts both. :meth:`get_tools` GENERATES the agent's tool
surface from the shared manifest catalogue
(:func:`app.agentive.tooling.build_tool_catalogue`) plus the trusted
bundle-local tools registered for the conversation's bound workspace, and
routes every tool call through the single dispatch seam
(:func:`app.agentive.tooling.dispatch_tool`). ``dispatch_tool`` invokes the
backing ``@endpoint`` handler / service function or workspace-local bundle tool
in-process under the acting principal + bound workspace scope, so permission
checks, content-profile validation, change events, and audit logging all run
without a localhost HTTP bridge.

Identity + scope model
----------------------

Per-call identity and scope are READ FROM CONTEXT, never from tool args:

* identity — the host's stable Integral User node id flows from the chat
  router via the jvagent dispatch context
  (:func:`jvagent.tooling.tool_executor.get_dispatch_context`); the adapter
  passes it to ``dispatch_tool`` as ``principal_id`` (PC-1: a tool arg can
  never act-as).

* scope — the active workspace the UI is showing flows from the per-turn
  ``app.services.agent_scope.current_scope_workspace_id`` ContextVar (set by
  the chat router from the frontend's ``X-Integral-Scope`` header); the adapter
  passes it as ``scope`` (PC-2: no tool arg can widen it).

Bundle-local tools are looked up only from that same bound workspace. A tool
registered by an App in one workspace is therefore not advertised in another.
MCP-mounted tools share that same per-workspace registry (``mcp__*`` keys) and
are additive — they never replace substrate catalogue names (PC-9).

When this action is unsuitable
------------------------------

* Out-of-process jvagent deployments (``AGENTIVE_ENABLED=0`` or
  separate jvagent server) — keep using ``IntegralApiAction`` there.
* Hosts other than integral — the imports below are integral-specific.
"""

from __future__ import annotations

from typing import Any, Dict, List

from jvagent.action.base import Action


def _to_resident_dict(res: Any) -> Any:
    """Adapt a :class:`app.agentive.tooling.dispatch.ToolResult` to the resident
    return shape skill scripts and the chat surface already expect.

    On error: the historical ``{error, error_code, message}`` envelope (skill
    scripts check ``"error" in result``). On success: the handler/service
    payload verbatim.
    """
    if res.is_error:
        return {
            "error": True,
            "error_code": res.error_code,
            "message": res.message,
        }
    return res.data


def _bundle_tool_schema(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Return a resident-safe JSON Schema for one registered workspace tool."""
    schema = spec.get("parameters_schema") or spec.get("input_schema")
    if isinstance(schema, dict) and schema:
        return schema
    return {"type": "object", "properties": {}}


class EmbeddedIntegralAction(Action):
    """In-process Integral tool provider (manifest + workspace bundle driven)."""

    async def get_tools(self) -> List[Any]:
        """Generate the Integral tool surface for the current workspace.

        Central tools come from the global manifest catalogue. Trusted
        bundle-local tools come from the per-workspace registry populated by
        App lifecycle installation/rehydration. Both execute through
        ``dispatch_tool`` so identity and workspace scope remain server-bound.

        Central tool names are authoritative: a bundle registration with the
        same key is not allowed to shadow a central Integral tool. MCP mounts
        use ``mcp__*`` keys and ride the same workspace registry.
        """
        from jvagent.tooling.tool import Tool
        from jvagent.tooling.tool_executor import get_dispatch_context

        from app.agentive.tooling import build_tool_catalogue, dispatch_tool
        from app.services.agent_scope import current_scope_workspace_id
        from app.services.hooks.registry import get_workspace_tools

        tools: List[Any] = []

        async def _execute_tool(_name: str, args: Dict[str, Any]) -> Any:
            ctx = get_dispatch_context()
            uid = getattr(ctx, "user_id", None) if ctx else None
            sid = getattr(ctx, "session_id", None) if ctx else None
            iid = getattr(ctx, "interaction_id", None) if ctx else None
            res = await dispatch_tool(
                _name,
                args,
                principal_id=uid,
                scope=current_scope_workspace_id.get(),
                session_id=sid,
                interaction_id=iid,
            )
            return _to_resident_dict(res)

        central_entries = build_tool_catalogue()
        central_names = {str(entry.get("name") or "") for entry in central_entries}

        for entry in central_entries:
            name = entry["name"]

            # ``_name=name`` binds the loop variable per closure — without it
            # every closure would capture the final loop value.
            async def _exec(_name: str = name, **args: Any) -> Any:
                return await _execute_tool(_name, args)

            tools.append(
                Tool(
                    name=name,
                    description=entry["description"],
                    parameters_schema=entry["input_schema"],
                    execute=_exec,
                )
            )

        workspace_id = current_scope_workspace_id.get()
        if not workspace_id:
            return tools

        for key, spec in get_workspace_tools(workspace_id).items():
            name = str(spec.get("key") or key or "").strip()
            if not name or name in central_names:
                # Central names retain their established manifest/binding
                # semantics; a bundle cannot shadow them. MCP mounts use
                # mcp__* keys and pass this check.
                continue

            async def _exec_bundle(_name: str = name, **args: Any) -> Any:
                return await _execute_tool(_name, args)

            description = str(
                spec.get("description")
                or spec.get("name")
                or name
            ).strip()

            tools.append(
                Tool(
                    name=name,
                    description=description,
                    parameters_schema=_bundle_tool_schema(spec),
                    execute=_exec_bundle,
                )
            )

        return tools
