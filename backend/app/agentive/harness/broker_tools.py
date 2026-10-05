"""Adapt Integral's current capability broker to Pydantic AI function tools."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Collection, Sequence

from app.agentive.harness.capability_search import (
    build_search_capabilities_tool,
)
from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.pydantic_ai_compat import (
    RunContext,
    Tool,
    build_integral_json_schema_tool,
)
from app.schemas.agentive.work import WorkExecutionContext

# Keep workspace orientation and the small set of resident workflow lifecycle
# capabilities directly callable. The broader catalogue is deferred and
# exposed through Harness ToolSearch. In particular, skills can name the
# proposal/approval/verification APIs without depending on a second model
# discovery round to reveal those APIs. This controls visible schemas only;
# every invocation still crosses the same live Integral capability broker.
_ALWAYS_AVAILABLE_TOOLS = frozenset(
    {
        "integral_get_scope",
        "integral_list_workspaces",
        "integral_list_apps",
        "integral_list_tracks",
        "integral_query_entries",
        "integral_check_design_coverage",
        "integral_propose_design",
        "integral_build_approved_design",
        "integral_verify_build",
    }
)
_MAX_BROKERED_CALLS_PER_RUN = 24
_MAX_TOOL_CALLS_PER_RUN = {
    "integral_check_design_coverage": 3,
    "integral_propose_design": 2,
    "integral_verify_build": 1,
}
_REQUIRED_SKILLS_BY_TOOL = {
    "integral_check_design_coverage": "integral-scaffold",
    "integral_propose_design": "integral-scaffold",
    "integral_build_approved_design": "integral-scaffold",
    "integral_verify_build": "integral-scaffold",
}


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
    call_state: dict[str, Any],
    no_workspace_writes: bool,
    design_only: bool,
):
    """Freeze capability identity and host scope into one function tool."""

    build_attempted = False

    async def invoke_tool(ctx: RunContext[Any], **arguments: Any) -> dict[str, Any]:
        """Invoke the declared capability with server-bound identity."""

        nonlocal build_attempted
        call_state["attempted"] += 1
        if call_state["attempted"] > _MAX_BROKERED_CALLS_PER_RUN:
            return {
                "error": True,
                "error_code": "harness_tool_call_limit",
                "message": (
                    "This turn reached Integral's brokered tool-call limit. "
                    "Stop calling tools and respond with the information already "
                    "verified, or explain what remains unknown."
                ),
                "retryable": False,
            }
        if no_workspace_writes and capability_op_class != "read":
            return {
                "error": True,
                "error_code": "user_no_workspace_writes",
                "message": (
                    "The user explicitly asked not to save or create anything. "
                    "Do not call write, batch, proposal, or direct-action tools "
                    "for this turn. Answer in chat only."
                ),
                "retryable": False,
            }
        if (
            design_only
            and capability_op_class != "read"
            and capability_name != "integral_propose_design"
        ):
            return {
                "error": True,
                "error_code": "design_proposal_only",
                "message": (
                    "This turn asked for an App design only. Read the substrate "
                    "and save the design with integral_propose_design; wait for "
                    "the user's affirmation before any staged or direct write."
                ),
                "retryable": False,
            }
        if not call_state["capability_search_completed"]:
            return {
                "error": True,
                "error_code": "capability_discovery_required",
                "message": (
                    "Search authorized skills and tools with search_capabilities "
                    "before calling Integral capabilities. Use its results to "
                    "select the appropriate skill and tool."
                ),
                "retryable": False,
            }
        if capability_name == "integral_propose_design":
            call_state["proposal_attempted"] = True
        if capability_name == "integral_check_design_coverage":
            call_state["scaffold_coverage_attempted"] = True
        required_skill = _REQUIRED_SKILLS_BY_TOOL.get(capability_name)
        if required_skill and required_skill not in ctx.active_capability_ids:
            return {
                "error": True,
                "error_code": "required_skill_not_loaded",
                "message": (
                    f"Load the {required_skill} skill with load_capability before "
                    f"calling {capability_name}. Use the initial capability "
                    "search results or search_capabilities to find it."
                ),
                "retryable": False,
            }
        if (
            capability_name == "integral_propose_design"
            and not call_state["scaffold_coverage_validated"]
        ):
            return {
                "error": True,
                "error_code": "design_coverage_required",
                "message": (
                    "Check the complete blueprint with integral_check_design_coverage "
                    "and resolve every unsupported item before proposing it."
                ),
                "retryable": False,
            }
        capability_calls = call_state["capability_calls"]
        capability_calls[capability_name] = capability_calls.get(capability_name, 0) + 1
        max_calls = _MAX_TOOL_CALLS_PER_RUN.get(capability_name)
        if max_calls is not None and capability_calls[capability_name] > max_calls:
            return {
                "error": True,
                "error_code": "capability_call_limit",
                "message": (
                    f"{capability_name} reached Integral's per-turn limit of "
                    f"{max_calls} calls. Use the results already returned and "
                    "finish the turn without repeating this capability."
                ),
                "retryable": False,
            }
        if capability_op_class == "read":
            signature = json.dumps(
                [capability_name, arguments],
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            seen_reads = call_state["read_signatures"]
            if signature in seen_reads:
                return {
                    "error": True,
                    "error_code": "repeated_read_suppressed",
                    "message": (
                        "This exact read was already performed in this turn. "
                        "Use the earlier result; do not repeat this call. If it "
                        "did not answer the request, search for a more relevant "
                        "capability once, then stop and explain the limitation."
                    ),
                    "retryable": False,
                }
            seen_reads.add(signature)
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
        model_result = result.for_model()
        if capability_name == "integral_check_design_coverage" and result.ok:
            if model_result.get("status") in {
                "buildable",
                "needs_trusted_package",
            } and not model_result.get("unsupported"):
                call_state["scaffold_coverage_validated"] = True
        if capability_name == "integral_propose_design" and result.ok:
            call_state["proposal_succeeded"] = True
            proposal_text = model_result.get("proposal")
            if isinstance(proposal_text, str) and proposal_text.strip():
                call_state["proposal_text"] = proposal_text
        return model_result

    return invoke_tool


def build_brokered_tools(
    *,
    scope: HarnessExecutionScope,
    skill_tools_required: Sequence[str] = (),
    catalogue: Sequence[dict[str, Any]] | None = None,
    always_available_tools: Collection[str] | None = None,
    work_execution_context: WorkExecutionContext | None = None,
    skill_library: Path | None = None,
    run_state: dict[str, Any] | None = None,
    no_workspace_writes: bool = False,
    design_only: bool = False,
) -> list[Tool[Any, Any]]:
    """Build Pydantic function tools backed by Core's live authorization gate.

    The supplied catalogue controls only which names and schemas the model can
    see. Every invocation is sent through ``invoke_declared_capability``,
    which verifies the durable run, principal, workspace, original capability
    snapshot, current declaration, policy, and effect receipt before dispatch.
    Model arguments never supply tenant, principal, session, or run identity.
    When ``skill_library`` is supplied, a read-only unified search tool covers
    the authorized projected skills and this same tool catalogue.
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
    call_state = run_state if run_state is not None else {}
    call_state.setdefault("attempted", 0)
    call_state.setdefault("capability_calls", {})
    call_state.setdefault("read_signatures", set())
    call_state.setdefault("scaffold_coverage_validated", False)
    call_state.setdefault("scaffold_coverage_attempted", False)
    call_state.setdefault("proposal_attempted", False)
    call_state.setdefault("proposal_succeeded", False)
    call_state.setdefault("capability_search_completed", False)
    immediately_available = (
        _ALWAYS_AVAILABLE_TOOLS
        if always_available_tools is None
        else frozenset(always_available_tools)
    )
    for item in catalogue:
        name = str(item.get("name") or "").strip()
        schema = item.get("input_schema")
        if (
            not name
            or name == "search_capabilities"
            or name in seen_names
            or not isinstance(schema, dict)
        ):
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
            call_state=call_state,
            no_workspace_writes=no_workspace_writes,
            design_only=design_only,
        )

        # Integral's capability broker validates the exact JSON schema from
        # its catalogue on dispatch. This framework validator enforces only
        # that the top-level arguments are an object; it deliberately avoids a
        # second, potentially divergent interpretation of nested JSON Schema.
        tools.append(
            build_integral_json_schema_tool(
                function=invoke_tool,
                name=name,
                description=description,
                json_schema=schema,
                prepare=_prepare_capability_tool(
                    call_state, name, _REQUIRED_SKILLS_BY_TOOL.get(name)
                ),
                defer_loading=name not in immediately_available,
            )
        )
    if skill_library is not None:
        tools.append(
            build_search_capabilities_tool(
                skill_library=skill_library,
                catalogue=catalogue,
                immediately_available_tools=tuple(
                    name for name in immediately_available if name in seen_names
                ),
                run_state=call_state,
            )
        )
    return tools


def _prepare_capability_tool(
    call_state: dict[str, Any], name: str, required_skill: str | None
):
    """Expose tools only after discovery, skill loading, and prior workflow steps."""

    def prepare(ctx: RunContext[Any], tool_def):
        if not call_state.get("capability_search_completed"):
            return None
        if required_skill and required_skill not in ctx.active_capability_ids:
            return None
        if name == "integral_propose_design" and not call_state.get(
            "scaffold_coverage_validated"
        ):
            return None
        return tool_def

    return prepare
