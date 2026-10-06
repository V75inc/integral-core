"""Adapt Integral's current capability broker to Pydantic AI function tools."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Collection, Sequence
from urllib.parse import quote

from app.agentive.harness.capability_search import (
    build_search_capabilities_tool,
)
from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.pydantic_ai_compat import (
    RunContext,
    Tool,
    build_integral_json_schema_tool,
)
from app.agentive.harness.tool_argument_adapter import normalize_tool_arguments
from app.schemas.agentive.work import WorkExecutionContext

# Keep workspace orientation and the small set of resident workflow lifecycle
# capabilities directly callable. The broader catalogue is deferred and
# exposed through unified capability search. In particular, skills can name the
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
# App setup is one declared proposal/build contract. Its implementation still
# invokes these authorized primitives through Core, but exposing them as
# separate model tools bypasses the saved blueprint and opens redundant
# staging dialogues. This is API composition, not user-text intent routing.
_BUILD_IMPLEMENTATION_TOOLS = frozenset(
    {
        "integral_create_app",
        "integral_create_app_track",
        "integral_register_track_template",
    }
)
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


def _resource_links_for_model(payload: dict[str, Any]) -> dict[str, Any]:
    """Supply canonical navigation URLs on Core resource read results.

    Node identifiers are opaque. The adapter renders the route rather than
    asking each model to infer how a graph identifier becomes a product URL.
    This is presentation only; the original ID and broker receipt are retained.
    """
    paths = {
        "n.WorkspaceApp.": "apps",
        "n.Track.": "tracks",
        "n.Entry.": "entries",
    }

    def resource(item):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            return item
        identifier = item["id"]
        for prefix, path in paths.items():
            if identifier.startswith(prefix):
                return {**item, "url": f"/{path}/{quote(identifier, safe='')}"}
        return item

    enriched = resource(payload)
    for collection in ("apps", "tracks", "entries"):
        items = payload.get(collection)
        if isinstance(items, list):
            enriched = {**enriched, collection: [resource(item) for item in items]}
    return enriched


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
    input_schema: dict[str, Any],
    skill_allowlist: tuple[str, ...],
    invoke_declared: Callable[..., Any],
    work_execution_context: WorkExecutionContext | None,
    call_state: dict[str, Any],
    no_workspace_writes: bool,
    design_only: bool,
    workflow_skills: frozenset[str] = frozenset(),
):
    """Freeze capability identity and host scope into one function tool."""

    build_attempted = False

    async def invoke_tool(ctx: RunContext[Any], **arguments: Any) -> dict[str, Any]:
        """Invoke the declared capability with server-bound identity."""

        nonlocal build_attempted
        call_state["attempted"] += 1
        arguments = normalize_tool_arguments(arguments, input_schema)
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
        if (
            call_state.get("pending_design")
            and capability_op_class != "read"
            and capability_name
            not in {"integral_propose_design", "integral_build_approved_design"}
        ):
            return {
                "error": True,
                "error_code": "design_pending_action",
                "message": (
                    "A saved design is awaiting the user's response. Use the "
                    "approved-build tool only when the latest reply authorizes "
                    "this design, or propose a revision when the user changes "
                    "its requirements. Do not perform unrelated writes."
                ),
                "retryable": False,
            }
        if capability_name == "integral_propose_design":
            if call_state.get("approved_design_ready"):
                return {
                    "error": True,
                    "error_code": "approved_design_immutable",
                    "message": (
                        "This design is approved for this turn. Build the saved "
                        "design; do not replace it with another proposal."
                    ),
                    "retryable": False,
                }
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
        if workflow_skills and not workflow_skills.intersection(
            ctx.active_capability_ids
        ):
            return {
                "error": True,
                "error_code": "required_skill_not_loaded",
                "message": (
                    "Load the relevant authorized workflow with load_capability "
                    "before proposing this change, then follow its procedure. "
                    "The skills declaring this tool are: "
                    + ", ".join(sorted(workflow_skills))
                    + ". Choose the one that fits the user's request."
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
            if not call_state.get("approved_design_ready"):
                if call_state.get("pending_design"):
                    from app.agentive.harness.design_approval import (
                        authorize_pending_design_build,
                    )

                    try:
                        call_state["approved_design_ready"] = (
                            await authorize_pending_design_build(
                                scope=scope,
                                expected_user_turn=call_state["active_user_turn"],
                                expected_utterance=call_state["active_user_text"],
                                work_context=work_execution_context,
                            )
                        )
                    except Exception:
                        return {
                            "error": True,
                            "error_code": "design_approval_conflict",
                            "message": (
                                "Core could not persist approval for the current "
                                "design and run. Do not execute or replace the "
                                "design. Resolve by reading current state."
                            ),
                            "retryable": False,
                        }
                if not call_state.get("approved_design_ready"):
                    return {
                        "error": True,
                        "error_code": "design_approval_required",
                        "message": (
                            "No current approved design is ready. Do not build; "
                            "respond to the user or save a requested revision."
                        ),
                        "retryable": False,
                    }
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
        model_result = _resource_links_for_model(result.for_model())
        if capability_name == "integral_build_approved_design":
            call_state["build_succeeded"] = bool(result.ok)
        if capability_name == "integral_verify_build":
            call_state["verification_status"] = model_result.get("status")
            call_state["verification_succeeded"] = bool(
                result.ok and model_result.get("status") == "verified"
            )
        if capability_name == "integral_propose_design" and result.ok:
            call_state["proposal_succeeded"] = True
            call_state["approved_design_ready"] = bool(model_result.get("approved"))
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
    pending_approval_tokens: dict[str, str] | None = None,
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
    call_state.setdefault("scaffold_coverage_attempted", False)
    call_state.setdefault("proposal_attempted", False)
    call_state.setdefault("proposal_succeeded", False)
    call_state.setdefault("approved_design_ready", False)
    call_state.setdefault("build_succeeded", False)
    call_state.setdefault("verification_succeeded", False)
    call_state.setdefault("capability_search_completed", False)
    call_state.setdefault("pending_staged_tokens", {})
    immediately_available = (
        _ALWAYS_AVAILABLE_TOOLS
        if always_available_tools is None
        else frozenset(always_available_tools)
    )
    if pending_approval_tokens:
        # A semantic decision by the primary model; Core maps the opaque item
        # reference back to a token from this exact conversation at execution.
        token_map = dict(pending_approval_tokens)
        call_state["pending_staged_tokens"] = token_map
        if token_map:

            async def resolve_pending_write(
                ctx: RunContext[Any], item_reference: str, decision: str
            ) -> dict[str, Any]:
                if decision not in {"approve", "reject"}:
                    return {"ok": False, "error_code": "invalid_decision"}
                token = call_state["pending_staged_tokens"].get(item_reference)
                if token is None:
                    return {"ok": False, "error_code": "pending_item_not_found"}
                from app.agentive.services.staging_apply import bless_and_execute
                from app.agentive.staging import (
                    StagingError,
                    list_unresolved_for_session,
                    revoke_token,
                )
                from app.models.nodes import ChatThread
                from app.services.prompt_queue import mark_write_item

                current = await list_unresolved_for_session(
                    scope.principal_id, scope.session_id
                )
                staged = next((item for item in current if item.token == token), None)
                if (
                    staged is None
                    or staged.workspace_id != scope.workspace_id
                    or staged.state != "pending"
                ):
                    return {"ok": False, "error_code": "pending_item_changed"}
                try:
                    if decision == "approve":
                        result = await bless_and_execute(
                            user_id=scope.principal_id, token=token
                        )
                        success = bool(result.get("consumed"))
                        state = (result.get("staged_change") or {}).get("state")
                    else:
                        result = await revoke_token(
                            user_id=scope.principal_id, token=token
                        )
                        success, state = True, result.state
                    if success:
                        thread = await ChatThread.get(scope.thread_id)
                        if thread is not None:
                            await mark_write_item(
                                user_id=scope.principal_id,
                                thread=thread,
                                token=token,
                                status=(
                                    "approved" if decision == "approve" else "rejected"
                                ),
                            )
                    return {"ok": success, "decision": decision, "state": state}
                except StagingError as exc:
                    return {"ok": False, "error_code": exc.code}

            tools.append(
                build_integral_json_schema_tool(
                    function=resolve_pending_write,
                    name="integral_resolve_pending_write",
                    description=(
                        "Resolve one staged write in this conversation after the "
                        "user clearly approves or rejects it. Use its item_reference "
                        "from pending approval context."
                    ),
                    json_schema={
                        "type": "object",
                        "properties": {
                            "item_reference": {"type": "string"},
                            "decision": {
                                "type": "string",
                                "enum": ["approve", "reject"],
                            },
                        },
                        "required": ["item_reference", "decision"],
                        "additionalProperties": False,
                    },
                    prepare=None,
                    defer_loading=False,
                )
            )
    skill_owners: dict[str, set[str]] = {}
    if skill_library is not None:
        from app.agentive.workspace_agent_profile import allowed_tools_from_skill_path

        for path in skill_library.glob("*/SKILL.md"):
            for tool_name in allowed_tools_from_skill_path(path):
                skill_owners.setdefault(tool_name, set()).add(path.parent.name)
    for item in catalogue:
        name = str(item.get("name") or "").strip()
        schema = item.get("input_schema")
        if (
            not name
            or name == "search_capabilities"
            or name in _BUILD_IMPLEMENTATION_TOOLS
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
            input_schema=schema,
            skill_allowlist=immutable_skill_allowlist,
            invoke_declared=invoke_declared_capability,
            work_execution_context=work_execution_context,
            call_state=call_state,
            no_workspace_writes=no_workspace_writes,
            design_only=design_only,
            # Standard skill/tool relationships, never user-text intent, bind
            # protected Core writes to their procedure. Reads remain directly
            # discoverable; loading a skill does not grant write authority.
            workflow_skills=(
                frozenset(skill_owners.get(name, ()))
                if source == "core" and op_class != "read"
                else frozenset()
            ),
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
                    name,
                    _REQUIRED_SKILLS_BY_TOOL.get(name),
                    frozenset(skill_owners.get(name, ())),
                ),
                defer_loading=name not in immediately_available,
                # Public Pydantic barriers preserve model-emitted order for
                # stateful Core writes, including batch start/append/commit.
                # Independent reads remain eligible for parallel execution.
                sequential=op_class != "read",
            )
        )
    if skill_library is not None:
        tools.append(
            build_search_capabilities_tool(
                skill_library=skill_library,
                catalogue=[
                    item for item in catalogue if item.get("name") in seen_names
                ],
                immediately_available_tools=tuple(
                    name for name in immediately_available if name in seen_names
                ),
                run_state=call_state,
            )
        )
    return tools


def _prepare_capability_tool(
    name: str,
    required_skill: str | None,
    skill_owners: frozenset[str] = frozenset(),
):
    """Disclose skill tools while preserving the build authority boundary."""

    def prepare(ctx: RunContext[Any], tool_def):
        if required_skill and required_skill not in ctx.active_capability_ids:
            return None
        # Core validates the typed blueprint and live coverage on proposal
        # dispatch. Hiding the proposal here would break amendments because
        # coverage state is local to a run, while skills survive in history.
        # Likewise, build authority is enforced by the handler and broker;
        # keeping the schema disclosed lets a stale call receive that precise
        # rejection instead of consuming unknown-tool validation retries.
        # Standard allowed-tools metadata binds a skill to its Core tools.
        # Loading that skill reveals their schemas via the public preparation
        # API; it does not widen authority or bypass the broker. Framework
        # capability state is restored from its own conversation history.
        if skill_owners.intersection(ctx.active_capability_ids):
            return replace(tool_def, defer_loading=False)
        return tool_def

    return prepare
