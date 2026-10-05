"""Adapt Integral's current capability broker to Pydantic AI function tools."""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Collection, Sequence

from pydantic_ai import RunContext, Tool
from pydantic_ai._function_schema import FunctionSchema
from pydantic_core import SchemaValidator, core_schema

from app.agentive.harness.contracts import HarnessExecutionScope
from app.schemas.agentive.work import WorkExecutionContext

# Keep the common workspace-orientation path directly callable without a
# discovery round. The remaining catalogue is large (currently 100+ tools),
# so it is deferred and exposed through the Harness ToolSearch capability.
# This controls model-visible schemas only; every invocation still crosses
# the same live Integral capability broker.
_ALWAYS_AVAILABLE_TOOLS = frozenset(
    {
        "integral_get_scope",
        "integral_list_workspaces",
        "integral_list_apps",
        "integral_list_tracks",
        "integral_query_entries",
    }
)


def _idempotency_key(run_id: str, tool_call_id: str | None, tool_name: str) -> str:
    identity = "\0".join((run_id, tool_call_id or "", tool_name))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"native:{digest}"


def _make_handler(
    *,
    scope: HarnessExecutionScope,
    capability_name: str,
    capability_source: str,
    capability_op_class: str,
    skill_allowlist: tuple[str, ...],
    invoke_declared: Callable[..., Any],
    work_execution_context: WorkExecutionContext | None,
):
    """Freeze capability identity and host scope into one function tool."""

    build_attempted = False

    async def invoke_tool(ctx: RunContext[Any], **arguments: Any) -> dict[str, Any]:
        """Invoke the declared capability with server-bound identity."""

        nonlocal build_attempted
        if capability_name == "integral_build_approved_design":
            if build_attempted:
                return {
                    "error": True,
                    "error_code": "build_already_attempted",
                    "message": (
                        "The approved build was already attempted in this turn. "
                        "Do not call the build tool again or claim success. "
                        "Report the first build result to the user."
                    ),
                    "retryable": False,
                }
            # This macro may create a complete multi-node App. Permit one
            # dispatch per model turn so a rejected result cannot trigger a
            # repeated batch (and repeated full-plan context) in the same run.
            build_attempted = True

        async def dispatch() -> Any:
            return await invoke_declared(
                principal_id=scope.principal_id,
                workspace_id=scope.workspace_id,
                capability_key=capability_name,
                origin="chat",
                source=capability_source,
                op_class=capability_op_class,
                arguments=arguments,
                run_id=scope.run_id,
                idempotency_key=_idempotency_key(
                    scope.run_id, ctx.tool_call_id, capability_name
                ),
                session_id=scope.session_id,
                skill_tools_required=(
                    list(skill_allowlist) if skill_allowlist else None
                ),
            )

        if work_execution_context is None:
            result = await dispatch()
        else:
            from app.agentive.services.work_items import authorized_work_item_effect

            async with authorized_work_item_effect(work_execution_context):
                result = await dispatch()
        return result.for_model()

    return invoke_tool


def build_brokered_tools(
    *,
    scope: HarnessExecutionScope,
    skill_tools_required: Sequence[str] = (),
    catalogue: Sequence[dict[str, Any]] | None = None,
    always_available_tools: Collection[str] | None = None,
    work_execution_context: WorkExecutionContext | None = None,
) -> list[Tool[Any, Any]]:
    """Build Pydantic function tools backed by Core's live authorization gate.

    The supplied catalogue controls only which names and schemas the model can
    see. Every invocation is sent through ``invoke_declared_capability``,
    which verifies the durable run, principal, workspace, original capability
    snapshot, current declaration, policy, and effect receipt before dispatch.
    Model arguments never supply tenant, principal, session, or run identity.
    """
    if catalogue is None:
        from app.agentive.tooling.catalogue import build_tool_catalogue

        catalogue = build_tool_catalogue()
    if work_execution_context is not None and (
        work_execution_context.principal_id != scope.principal_id
        or work_execution_context.workspace_id != scope.workspace_id
        or work_execution_context.run_id != scope.run_id
    ):
        raise ValueError("WorkItem authority does not match Harness execution scope")
    from app.agentive.services.capability_broker import (
        infer_source_and_op_class,
        invoke_declared_capability,
    )

    tools: list[Tool[Any, Any]] = []
    seen_names: set[str] = set()
    immediately_available = (
        _ALWAYS_AVAILABLE_TOOLS
        if always_available_tools is None
        else frozenset(always_available_tools)
    )
    for item in catalogue:
        name = str(item.get("name") or "").strip()
        schema = item.get("input_schema")
        if not name or name in seen_names or not isinstance(schema, dict):
            continue
        if schema.get("type") != "object":
            continue
        seen_names.add(name)
        description = str(item.get("description") or "")
        source, op_class = infer_source_and_op_class(name)
        immutable_skill_allowlist = tuple(skill_tools_required)

        invoke_tool = _make_handler(
            scope=scope,
            capability_name=name,
            capability_source=source,
            capability_op_class=op_class,
            skill_allowlist=immutable_skill_allowlist,
            invoke_declared=invoke_declared_capability,
            work_execution_context=work_execution_context,
        )

        # Integral's capability broker validates the exact JSON schema from
        # its catalogue on dispatch. This framework validator enforces only
        # that the top-level arguments are an object; it deliberately avoids a
        # second, potentially divergent interpretation of nested JSON Schema.
        function_schema = FunctionSchema(
            function=invoke_tool,
            name=name,
            description=description,
            validator=SchemaValidator(
                core_schema.dict_schema(
                    keys_schema=core_schema.str_schema(),
                    values_schema=core_schema.any_schema(),
                    strict=True,
                )
            ),
            json_schema=dict(schema),
            takes_ctx=True,
            is_async=True,
        )
        tools.append(
            Tool(
                invoke_tool,
                takes_ctx=True,
                name=name,
                description=description,
                function_schema=function_schema,
                defer_loading=name not in immediately_available,
            )
        )
    return tools
