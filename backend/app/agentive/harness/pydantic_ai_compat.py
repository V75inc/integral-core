"""Compatibility boundary for the third-party Pydantic AI Harness APIs.

Integral's tenant, policy, billing, tool-catalogue, and persistence contracts
must not depend on Pydantic AI implementation details. Imports and narrowly
version-sensitive construction live here so a library upgrade can be handled
at this seam and qualified by adapter contract tests.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    BinaryContent,
    CancellationToken,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelRetry,
    ModelSettings,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RetryPromptPart,
    RunContext,
    TextOutput,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    Tool,
    ToolOutput,
    UsageLimitExceeded,
    UsageLimits,
)
from pydantic_ai.capabilities import AbstractCapability, Instrumentation, ToolSearch
from pydantic_ai.messages import (
    LoadCapabilityCallPart,
    LoadCapabilityReturnPart,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ToolAvailabilityDeltaPart,
    ToolCallPart,
    ToolReturn,
    ToolReturnPart,
    UserPromptPart,
    post_compaction_window,
)
from pydantic_ai.models import InstructionPart
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.litellm import LiteLLMProvider
from pydantic_ai_harness import Planning, Skills, StepPersistence
from pydantic_ai_harness.compaction import (
    ClearToolResults,
    SummarizingCompaction,
    WarnNearLimits,
)
from pydantic_ai_harness.conversation_search import (
    ConversationSearch,
    SnapshotHistorySource,
)
from pydantic_ai_harness.planning import PlanItem, PlanStore, TaskStatus
from pydantic_ai_harness.step_persistence import (
    ContinuableSnapshot,
    RunRecord,
    StepEvent,
    StepStore,
    ToolEffectRecord,
    continue_run,
)
from pydantic_core import SchemaValidator, core_schema, to_jsonable_python

from app.agentive.harness.turn_progress import FINAL_RESPONSE_FALLBACK, TurnProgress


def final_response_reserved(ctx: RunContext[Any]) -> bool:
    """Reserve the final permitted model request for a tool-free handoff.

    Read the framework's live allowance, including adaptive-request deductions;
    never increase the budget or infer intent from the user's words.
    """
    limits = getattr(ctx, "usage_limits", None)
    limit = getattr(limits, "request_limit", None)
    usage = getattr(ctx, "usage", None)
    if (
        isinstance(limit, int)
        and limit > 1
        and getattr(usage, "requests", 0) >= limit - 1
    ):
        return True
    tool_limit = getattr(limits, "tool_calls_limit", None)
    if isinstance(tool_limit, int) and tool_limit > 1:
        if getattr(usage, "tool_calls", 0) >= max(
            1, tool_limit - min(4, tool_limit // 4)
        ):
            return True
    token_limit = getattr(limits, "total_tokens_limit", None)
    if isinstance(token_limit, int):
        used = getattr(usage, "total_tokens", 0)
        # Budget for the next prompt grows with history. Reserve the cost of
        # another response before the framework rejects it after generation.
        last_input = next(
            (
                m.usage.input_tokens
                for m in reversed(getattr(ctx, "messages", ()))
                if isinstance(m, ModelResponse)
            ),
            0,
        )
        if used and used + max(last_input * 2, 2048) >= token_limit:
            return True
    return False


class IntegralLimitWarnings(AbstractCapability):
    """Use upstream usage warnings without fabricating a human conversation turn.

    WarnNearLimits normally injects UserPromptPart and strips marker-bearing
    messages. Generate its usage-only warning on a disposable empty projection,
    then attach it as a dynamic instruction. Real user messages remain exact;
    neither warning prose nor model output can establish a new turn boundary.
    """

    async def before_model_request(self, ctx, request_context):
        limits = ctx.usage_limits
        requests = getattr(limits, "request_limit", None)
        tokens = getattr(limits, "total_tokens_limit", None)
        requests = requests if isinstance(requests, int) and requests > 0 else None
        tokens = tokens if isinstance(tokens, int) and tokens > 0 else None
        if requests is None and tokens is None:
            return request_context
        upstream = WarnNearLimits(
            max_iterations=requests, max_total_tokens=tokens, warning_threshold=0.6
        )
        projected = await upstream.before_model_request(
            ctx, replace(request_context, messages=[])
        )
        warning = "\n".join(
            part.content
            for message in projected.messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart) and isinstance(part.content, str)
        )
        if warning:
            parameters = request_context.model_request_parameters
            request_context.model_request_parameters = replace(
                parameters,
                instruction_parts=[
                    *(parameters.instruction_parts or []),
                    InstructionPart(
                        warning, dynamic=True, name="integral-limit-warning"
                    ),
                ],
            )
        return request_context


@dataclass
class IntegralToolDisclosure(ToolSearch):
    """Use unified catalog search with framework-owned deferred disclosure.

    Search returns ``ToolReturn.tools``; Pydantic AI records and replays that
    availability itself. Supported ToolSearch extension points and toolset
    filtering remove the duplicate search entrypoint, without altering skill
    loading, history or library code. Used only with search_capabilities.
    """

    turn_progress: TurnProgress | None = None
    require_initial_search: bool = True
    pending_decision_tool: str | None = None
    continuation_tools: frozenset[str] = frozenset()
    workflow_skill_ids: frozenset[str] = frozenset()
    recovery_read_tools: frozenset[str] = frozenset({"integral_describe_capabilities"})

    def get_native_tools(self):
        return []

    def get_model_settings(self):
        """Require initial discovery through the library's dynamic tool choice.

        Reuse framework-restored availability under the current Core catalogue
        and session revision. Fresh runs discover first; warm runs can use their
        loaded workflow or search for another. No user-text classifier participates.
        """
        inherited = super().get_model_settings()

        def settings(ctx: RunContext[Any]) -> ModelSettings:
            baseline = inherited(ctx) if callable(inherited) else inherited
            resolved = ModelSettings(**(baseline or {}))
            self._observe_discovery(ctx)
            if final_response_reserved(ctx) or (
                self.turn_progress and self.turn_progress.should_finalize
            ):
                resolved["tool_choice"] = "none"
                return resolved
            # A scoped pending proposal already supplies its decision tool.
            # Do not force rediscovery of a different workflow before the
            # model can interpret the reply; Core still validates that decision.
            profile = ctx.model.profile
            # Respect public model capabilities. Conservatively leave models
            # with thinking-specific restrictions on normal choice rather than
            # duplicating the library's provider/thinking resolution rules.
            if not profile.get("supports_forced_tool_choice", True) or not profile.get(
                "supports_forced_tool_choice_with_thinking", True
            ):
                return resolved
            # Public message boundaries separate the current request from
            # restored discovery. This is structural turn state, not intent
            # classification or routing based on the user's wording.
            turn_start = next(
                (
                    index
                    for index in range(len(ctx.messages) - 1, -1, -1)
                    if isinstance(ctx.messages[index], ModelRequest)
                    and any(
                        isinstance(part, UserPromptPart)
                        for part in ctx.messages[index].parts
                    )
                ),
                0,
            )
            # A Core refusal can name its read-only recovery contract. Enforce
            # that contract before another broad read; availability and live
            # broker checks still apply. Never force an effect or replay a write.
            recovery = None
            for message in ctx.messages[turn_start:]:
                if not isinstance(message, ModelRequest):
                    continue
                for part in message.parts:
                    if not isinstance(part, ToolReturnPart):
                        continue
                    content = part.content
                    if isinstance(content, dict) and content.get("error"):
                        next_tool = content.get("next_tool")
                        if next_tool in self.recovery_read_tools:
                            recovery = next_tool
                    elif part.tool_name == recovery:
                        recovery = None
            if recovery:
                recovery_available: frozenset[str] = getattr(
                    ctx, "available_tool_names", frozenset()
                )
                resolved["tool_choice"] = [
                    (
                        recovery
                        if recovery in recovery_available
                        else "search_capabilities"
                    )
                ]
                return resolved
            if self.pending_decision_tool:
                decided = any(
                    isinstance(part, ToolReturnPart)
                    and part.tool_name == self.pending_decision_tool
                    for message in ctx.messages[turn_start:]
                    if isinstance(message, ModelRequest)
                    for part in message.parts
                )
                if not decided:
                    # The primary model interprets approve/reject/no-decision
                    # against a concrete Core proposal. Questions and ambiguous
                    # replies select no-decision; no text classifier participates.
                    resolved["tool_choice"] = [self.pending_decision_tool]
                    return resolved
            if not self.require_initial_search:
                return resolved
            searched = any(
                isinstance(part, ToolReturnPart)
                and part.tool_name == "search_capabilities"
                and not (isinstance(part.content, dict) and part.content.get("error"))
                for message in ctx.messages[turn_start:]
                if isinstance(message, ModelRequest)
                for part in message.parts
            )
            if not searched:
                available: frozenset[str] = getattr(
                    ctx, "available_tool_names", frozenset()
                )
                if "integral_get_page_context" in available:
                    # Let the model select the current-screen read on demand.
                    # The response hook below still requires discovery for
                    # arbitrary operational answers before a workflow is loaded.
                    resolved["tool_choice"] = "auto"
                elif not any(name.startswith("integral_") for name in available):
                    resolved["tool_choice"] = ["search_capabilities"]
            else:
                candidates = {
                    item.get("load_with", {}).get("id")
                    for message in ctx.messages[turn_start:]
                    if isinstance(message, ModelRequest)
                    for part in message.parts
                    if isinstance(part, ToolReturnPart)
                    and part.tool_name == "search_capabilities"
                    and isinstance(part.content, dict)
                    and not part.content.get("error")
                    for item in part.content.get("results", ())
                    if isinstance(item, dict)
                    and item.get("kind") == "skill"
                    and isinstance(item.get("load_with"), dict)
                    and item["load_with"].get("tool") == "load_capability"
                }
                candidates.discard(None)
                if (
                    candidates
                    and not candidates.intersection(ctx.active_capability_ids)
                    and not self.workflow_skill_ids.intersection(
                        ctx.active_capability_ids
                    )
                ):
                    # The model chooses the fitting returned procedure. Core
                    # metadata requires an initial procedure to be loaded, without
                    # selecting it from user wording or assigning authority.
                    # Once a workflow is active, discovering a missing read
                    # must not force loading every skill that also owns it.
                    # Write handlers still enforce their applicable procedure.
                    resolved["tool_choice"] = ["load_capability"]
            return resolved

        return settings

    def _observe_discovery(self, ctx):
        if self.turn_progress is None:
            return
        # Inspect only this invocation, never restored public history.
        start = _active_turn_start(ctx.messages)
        if start is None:
            return
        available = frozenset(getattr(ctx, "available_tool_names", ())) | frozenset(
            ctx.active_capability_ids
        )
        for message in ctx.messages[start:]:
            if isinstance(message, ModelRequest):
                for part in message.parts:
                    if isinstance(part, ToolReturnPart) and part.tool_name in {
                        "search_capabilities",
                        "load_capability",
                    }:
                        self.turn_progress.observe_discovery(
                            part.tool_call_id, available
                        )

    async def after_model_request(self, ctx, *, request_context, response):
        """Enforce discovery even when a provider cannot force tool choice.

        This validates structural turn state, never the user's wording. A
        pending decision, authorized continuation or already disclosed Integral
        workflow retains its existing path. No capability is executed here.
        """
        response = await super().after_model_request(
            ctx, request_context=request_context, response=response
        )
        from app.agentive.harness.disclosure import is_public_support_text

        calls = [p for p in response.parts if p.part_kind == "tool-call"]
        finalizing = final_response_reserved(ctx) or bool(
            self.turn_progress and self.turn_progress.should_finalize
        )
        if (
            finalizing
            and (request_context.model_settings or {}).get("tool_choice") == "none"
        ):
            if calls:
                # Provider noncompliance must not produce another effect or
                # replace the conversation with a framework exception.
                return replace(response, parts=[TextPart(FINAL_RESPONSE_FALLBACK)])
            return response
        tool_limit = getattr(ctx.usage_limits, "tool_calls_limit", None)
        if (
            isinstance(tool_limit, int)
            and ctx.usage.tool_calls + len(calls) > tool_limit
        ):
            request_limit = getattr(ctx.usage_limits, "request_limit", None)
            if request_limit is None or ctx.usage.requests < request_limit:
                if self.turn_progress is None:
                    self.turn_progress = TurnProgress()
                self.turn_progress.reason = (
                    "The proposed tool batch exceeds the remaining allowance."
                )
                # Use the framework's corrective-request lifecycle once. No
                # member of the rejected batch has executed. The next request
                # is tool-free; provider noncompliance takes the fallback above.
                raise ModelRetry(
                    "This tool batch was not executed. Give a final answer now "
                    "using only earlier verified results. Identify any missing "
                    "evidence or capability precisely; do not call more tools "
                    "or claim that the rejected actions happened."
                )
            return replace(response, parts=[TextPart(FINAL_RESPONSE_FALLBACK)])

        # A fixed host support response carries no current-state assertions.
        # This permits a model-recognized disclosure request to stop without
        # forcing workspace retrieval. Arbitrary prose still requires discovery.
        if not any(
            part.part_kind == "tool-call" for part in response.parts
        ) and is_public_support_text(
            "".join(
                part.content for part in response.parts if isinstance(part, TextPart)
            )
        ):
            return response
        if (
            not self.require_initial_search
            or self.pending_decision_tool
            or self.continuation_tools
            or any(part.part_kind == "tool-call" for part in response.parts)
        ):
            return response
        turn_start = next(
            (
                index
                for index in range(len(ctx.messages) - 1, -1, -1)
                if isinstance(ctx.messages[index], ModelRequest)
                and any(
                    isinstance(part, UserPromptPart)
                    for part in ctx.messages[index].parts
                )
            ),
            0,
        )
        outcomes = [
            part
            for message in ctx.messages[turn_start:]
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        searches = [
            part
            for part in outcomes
            if part.tool_name == "search_capabilities"
            and not (isinstance(part.content, dict) and part.content.get("error"))
        ]
        # Page orientation identifies the target; it is not workflow discovery.
        # Merely exposing other schemas also does not prove they were used.
        # Keep restored active procedures and actual operational reads usable.
        if (
            not searches
            and not self.workflow_skill_ids.intersection(ctx.active_capability_ids)
            and not any(
                part.tool_name.startswith("integral_")
                and part.tool_name != "integral_get_page_context"
                for part in outcomes
            )
        ):
            raise ModelRetry(
                "This turn has not searched Integral's authorized capabilities. "
                "Call search_capabilities now with the user's requested outcome. "
                "A promise to search or request for workspace context does not "
                "complete discovery. Use the returned skill/tool schemas; only "
                "then answer or explain an observed limitation."
            )
        candidates = {
            item.get("load_with", {}).get("id")
            for part in searches
            if isinstance(part.content, dict)
            for item in part.content.get("results", ())
            if isinstance(item, dict)
            and item.get("kind") == "skill"
            and isinstance(item.get("load_with"), dict)
            and item["load_with"].get("tool") == "load_capability"
        }
        candidates.discard(None)
        if (
            candidates
            and not candidates.intersection(ctx.active_capability_ids)
            and not self.workflow_skill_ids.intersection(ctx.active_capability_ids)
            and not any(part.tool_name.startswith("integral_") for part in outcomes)
        ):
            raise ModelRetry(
                "Discovery returned available workflow skills, but none has been "
                "loaded. Call the load_capability tool now. Its id argument must "
                f"be one of these authorized ids: {', '.join(sorted(candidates))}. "
                "Choose the id whose returned description fits the user's question. "
                "Loading reads the skill instructions; it does not grant access or "
                "modify workspace records. Follow its procedure and "
                "perform the required authorized reads before answering. Do not "
                "ask the user to supply records you have not attempted to read."
            )
        return response

    def get_wrapper_toolset(self, toolset):
        def should_disclose(ctx: RunContext[Any], definition) -> bool:
            if definition.name == self.function_tool_name:
                return False
            if definition.name == "search_capabilities":
                return True
            if (
                definition.name == self.pending_decision_tool
                or definition.name in self.continuation_tools
            ):
                # This exact decision capability was supplied from current
                # Core authority, not the searchable skill catalog.
                return ctx.is_tool_available(definition)
            # Pydantic AI owns deferred skill activation through this tool.
            # Keep it available; the model-settings policy still requires the
            # catalog search first on providers that support forced choice.
            # Hiding it based on serialized tool-result shapes can strand a
            # skill recommendation and make the model retry discovery forever.
            if definition.name == "load_capability":
                return True
            if not definition.name.startswith("integral_"):
                return True
            # Restored discovery is not permission to disclose the full
            # Integral tool catalogue. Ask the framework whether this concrete
            # definition is available: it accounts for both search discovery and
            # deferred Skills ownership, including the required load-before-call
            # boundary.
            if definition.name.startswith("integral_"):
                return ctx.is_tool_available(definition)
            return True

        return super().get_wrapper_toolset(toolset).filtered(should_disclose)


def build_integral_json_schema_tool(
    *,
    function: Callable[..., Any],
    name: str,
    description: str,
    json_schema: dict[str, Any],
    prepare: Callable[..., Any] | None,
    defer_loading: bool,
    sequential: bool = False,
    max_retries: int | None = None,
) -> Tool[Any, Any]:
    """Adapt an Integral JSON Schema tool to Pydantic AI's callable tool API.

    Integral validates the authoritative, complete JSON Schema again at the
    capability broker. Pydantic calls this function with keyword arguments;
    construction uses public Tool.from_schema rather than a second schema
    engine or a private library import.
    """

    tool = Tool.from_schema(
        function=function,
        takes_ctx=True,
        sequential=sequential,
        name=name,
        description=description,
        json_schema=dict(json_schema),
    )
    tool.prepare = prepare
    tool.defer_loading = defer_loading
    tool.max_retries = max_retries
    return tool


def build_integral_run_instructions(
    base_instructions: str,
    turn_progress: TurnProgress | None = None,
) -> Callable[[RunContext[Any]], str]:
    """Add current framework-owned capability state to each model request.

    Pydantic AI Harness intentionally keeps loaded capabilities in its discovery
    catalog for prompt-cache stability. Some models repeatedly reload an active
    skill after tool results. Make the authoritative active set explicit at the
    adapter boundary so they can continue with the already-loaded procedure.
    """

    # Capture once for this invocation. Resumed/new runs build fresh instructions;
    # model requests within the run share a stable, server-owned date.
    run_started_at = datetime.now(timezone.utc)

    def instructions(ctx: RunContext[Any]) -> str:
        active_ids = sorted(ctx.active_capability_ids)
        blocks = [
            base_instructions,
            "Integral conversation style: write for everyday people. Explain what "
            "happened, what is finished or still needs approval, and one useful "
            "next step. Say 'I couldn't finish' rather than describing tool calls, "
            "turns, receipts, contracts, scope, tokens or generation limits. "
            "Keep technical details in diagnostics unless the user asks for them. "
            "Do not blame the user for a generated request that failed validation. "
            "Never promise background work, saved changes or success without "
            "evidence. If only part is done, name that part plainly. Complete "
            "requested actions in this response using the authorized procedure; "
            "a final reply saying you will prepare a proposal later does not "
            "do the work. Present a saved approval card, the verified result, "
            "a necessary question, or a concrete reason you couldn't finish. "
            "After an approval, rejection, or simple status recheck, keep the reply to one or two short "
            "sentences: confirm only the verified outcome and any unresolved "
            "problem. Do not repeat the proposal, list completed steps, recap "
            "the workflow, or add unsolicited offers. Give more detail only "
            "when the user asks or needs it to resolve a problem.",
            "Integral callable-tool contract: skill identifiers name instructions, "
            "not functions. Never invoke a skill name as a tool or convert its "
            "hyphens to underscores. Activate a chosen skill with load_capability "
            "using its exact id. Invoke only a tool whose exact name and argument "
            "schema are disclosed for this request. If a needed tool is absent, "
            "call search_capabilities to discover it before invoking it.",
            "Integral trusted clock: this invocation started at "
            f"{run_started_at.isoformat()}; current UTC date is "
            f"{run_started_at.date().isoformat()}. Use this date for today's "
            "recorded actions unless the user specifies an event date or a "
            "verified timezone requires another local date. Historical record "
            "dates and model memory do not establish today's date. Do not "
            "invent a user timezone or shift the date without a verified basis.",
            "Integral navigation: resource IDs are opaque. When linking saved "
            "Apps, Tracks or Entries, copy the canonical url returned by Core "
            "tools exactly. Do not split an ID, replace its dots with slashes, "
            "or invent a route. If no verified url is available, use a plain "
            "record label instead of a guessed link.",
            "Integral read recovery: when Core refuses a read and returns "
            "next_tool, search for that exact canonical tool name and follow "
            "its permitted contract. A refusal is not an empty result or zero "
            "count. Do not try other broad queries, schema dumps, or archive "
            "searches to bypass it. Use only a verified matching declared "
            "query or operation with read effects. If none exists, explain "
            "that the current App exposes no permitted read for this question.",
        ]
        blocks.append(
            "Integral evidence discipline: use the smallest scoped read that answers "
            "the question. Distinguish a record's modification time from evidence "
            "of who changed it. Do not infer an actor from recency. If the required "
            "capability is missing or refused, explain the precise evidence gap; "
            "do not enumerate unrelated records or keep rediscovering the same tools. "
            "Use a corrected argument or a genuinely different authorized approach "
            "after failure. When results suffice, answer immediately."
        )
        if final_response_reserved(ctx) or (
            turn_progress and turn_progress.should_finalize
        ):
            if turn_progress and turn_progress.reason:
                blocks.append("Integral observed progress: " + turn_progress.reason)
            blocks.append(
                "Integral turn budget: this is the final available response. "
                "Do not call tools or promise further work in this turn. Give a "
                "concise handoff grounded only in observed receipts: what was "
                "verified, what is pending approval, and what remains unresolved. "
                "A staged proposal is not an applied change. Do not claim success "
                "without readback. Invite the user to continue in this chat; "
                "never instruct them to restart or repeat already applied work."
            )
        available = sorted(getattr(ctx, "available_tool_names", ()))
        if available:
            blocks.append(
                "Callable tool names for this request: " + ", ".join(available) + "."
            )
        if active_ids:
            blocks.append(
                "Integral runtime state: these capabilities are already loaded and "
                f"their instructions are active: {', '.join(active_ids)}. Do not call "
                "load_capability for them again. Continue the current workflow using "
                "the loaded instructions and their tools."
            )
        outcomes = {}
        for message in getattr(ctx, "messages", ()):
            if not isinstance(message, ModelRequest):
                continue
            for part in message.parts:
                if not isinstance(part, ToolReturnPart) or not isinstance(
                    part.content, dict
                ):
                    continue
                content = part.content
                if content.get("state_source") == "current_core_staging":
                    state = content.get("state")
                    if state in {"consumed", "revoked", "expired"}:
                        outcomes[content["token"]] = state
        if outcomes:
            blocks.append(
                "Current Core approval outcomes (authoritative, superseding old "
                "proposal text and skill instructions to wait): "
                + "; ".join(f"{token}: {state}" for token, state in outcomes.items())
                + ". Consumed changes have already been applied: read back their "
                "saved records and report the result. Revoked changes were rejected: "
                "acknowledge that they were not applied. Expired changes cannot be "
                "applied. None of these changes is awaiting approval. Do not replay "
                "them or ask for approval again."
            )
        return "\n\n".join(blocks)

    return instructions


_HOST_TURN_METADATA_KEY = "integral_host_turn_v1"


def build_integral_host_turn(content: str, *, run_id: str) -> ModelRequest:
    """Mark a Core-generated event boundary without fabricating a user turn."""
    if not run_id or run_id != run_id.strip():
        raise ValueError("host turn requires a canonical Core run ID")
    return ModelRequest(
        parts=[SystemPromptPart(content=content)],
        metadata={_HOST_TURN_METADATA_KEY: run_id},
    )


def _active_turn_start(messages) -> int | None:
    """Find a human turn or a typed Core event; prompt prose is never a marker."""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, ModelRequest):
            continue
        if any(isinstance(part, UserPromptPart) for part in message.parts):
            return index
        marker = (message.metadata or {}).get(_HOST_TURN_METADATA_KEY)
        if isinstance(marker, str) and marker and marker == marker.strip():
            return index
    return None


@dataclass
class IntegralToolResultCompaction(ClearToolResults):
    """Clear completed-turn payloads without erasing the active task's inputs.

    A five-pair window cannot retain a task that needs several record reads
    plus destination schemas before staging. Clearing those reads mid-turn
    causes the model to repeat them instead of completing the task. Keep the
    active user turn exact; the native run's usage and tool limits still bound
    it. Completed turns retain only the library's bounded recent window.
    """

    async def before_model_request(self, ctx, request_context):
        # Request-only projection must also become the checkpoint working
        # history. Otherwise later runs resurrect the completed-turn payloads.
        before = list(request_context.messages)
        processed = await super().before_model_request(ctx, request_context)
        if processed.messages != before:
            ctx.messages[:] = processed.messages
        return processed

    async def compact(self, messages, ctx):
        turn_start = _active_turn_start(messages)
        if turn_start is None:
            return await super().compact(messages, ctx)
        history = await super().compact(messages[:turn_start], ctx)
        return [*history, *messages[turn_start:]]


def build_integral_context_compaction() -> ClearToolResults:
    """Bound completed-turn payloads while preserving active task grounding."""
    return IntegralToolResultCompaction(
        max_tokens=16_384,
        keep_pairs=5,
        exclude_tools=frozenset({"load_capability"}),
        clear_tool_inputs=True,
    )


@dataclass
class IntegralHistorySummary(SummarizingCompaction):
    """Keep typed disclosure evidence when the library summarizes prose.

    Capability activation is derived from completed typed load pairs, not from
    a summary saying a skill was loaded. Preserve one pair per current skill
    and a bounded tool-availability delta using public message types. The
    library still owns summarization, pairing, execution and checkpointing;
    current registration and the broker remain the authorization boundary.
    """

    max_history_bytes: int = 96_000
    _compacted_history_digest: str | None = None

    async def before_model_request(self, ctx, request_context):
        # Pydantic AI 2.10 makes request_context.messages request-only.
        # Persist compaction through the documented RunContext.messages seam
        # so StepPersistence and later turns receive the same working history.
        before = list(request_context.messages)
        turn_start = _active_turn_start(before)
        history = before[:turn_start] if turn_start is not None else []
        history_bytes = ModelMessagesTypeAdapter.dump_json(history)
        digest = hashlib.sha256(history_bytes).hexdigest()
        # Only completed-turn history is eligible for summarization. A large
        # active build must not repeatedly summarize the same older history,
        # spend the closing-response allowance, or duplicate its receipts.
        if final_response_reserved(ctx) or digest == self._compacted_history_digest:
            return request_context
        if len(history_bytes) > self.max_history_bytes:
            request_context.messages = await self.compact(before, ctx)
            processed = request_context
        else:
            processed = await super().before_model_request(ctx, request_context)
        if processed.messages != before:
            ctx.messages[:] = processed.messages
        processed_start = _active_turn_start(processed.messages)
        retained_history = (
            processed.messages[:processed_start] if processed_start is not None else []
        )
        self._compacted_history_digest = hashlib.sha256(
            ModelMessagesTypeAdapter.dump_json(retained_history)
        ).hexdigest()
        return processed

    async def compact(self, messages, ctx):
        # Keep the active user request and its tool sequence exact. Summarizing
        # it mid-batch can erase a queued effect receipt and provoke restaging.
        # ClearToolResults still bounds older payloads; the library usage limits
        # bound a long active turn. Summarization owns completed-turn history.
        turn_start = _active_turn_start(messages) or 0
        history, active_turn = messages[:turn_start], messages[turn_start:]
        if (
            len(history) <= self.keep_messages
            and len(ModelMessagesTypeAdapter.dump_json(history))
            <= self.max_history_bytes
        ):
            return messages
        # A summary request must not resend large completed-turn tool payloads.
        # Durable scoped checkpoints keep the full evidence searchable.
        history = await ClearToolResults(
            max_tokens=1,
            keep_pairs=0,
            exclude_tools=frozenset(),
            clear_tool_inputs=True,
        ).compact(history, ctx)
        compacted = await super().compact(history, ctx)
        if compacted == history:
            return [*history, *active_turn]
        calls = {}
        pairs = {}
        names = set()
        for message in post_compaction_window(messages):
            for part in message.parts:
                if isinstance(part, LoadCapabilityCallPart):
                    calls[part.tool_call_id] = part
                elif isinstance(part, LoadCapabilityReturnPart):
                    call = calls.get(part.tool_call_id)
                    if call and call.capability_id in ctx.active_capability_ids:
                        pairs[call.capability_id] = (call, part)
                elif isinstance(part, ToolAvailabilityDeltaPart):
                    names.update(name for name in part.tools_added if name in ctx.tools)
        retained = {
            part.tool_call_id
            for message in compacted
            for part in message.parts
            if isinstance(part, LoadCapabilityReturnPart)
        }
        evidence = []
        for call, result in pairs.values():
            if result.tool_call_id not in retained:
                evidence.extend(
                    [ModelResponse(parts=[call]), ModelRequest(parts=[result])]
                )
        if names:
            evidence.append(
                ModelRequest(
                    parts=[ToolAvailabilityDeltaPart(tools_added=sorted(names))]
                )
            )
        # A summary is a leading system request; attach state after it and
        # before the retained current task. No execution is replayed.
        return [compacted[0], *evidence, *compacted[1:], *active_turn]


def build_integral_history_compaction() -> IntegralHistorySummary:
    """Summarize old prose after the separate cheap result-clearing pass.

    The populated browser chat retained 112k conversation characters after
    clearing. Provider usage anchors and heuristic reclaim can understate the
    remaining context when reasoning was not sent back to the provider. A
    library message-count trigger supplies an independent bound, alongside
    the token trigger. Preserve recent user constraints and task data; scoped
    checkpoints remain searchable. Summary requests share model accounting.
    """
    return IntegralHistorySummary(
        max_messages=40,
        max_tokens=24_576,
        keep_messages=20,
        keep_tokens=8_192,
        keep_user_messages=True,
        receipts=True,
        model_settings={"max_tokens": 2048},
    )


def classify_integral_harness_exception(exc: BaseException) -> str | None:
    """Translate Pydantic AI failures to Integral's stable error categories."""
    from app.agentive.harness.model_errors import classified_model_error

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, UsageLimitExceeded):
            return "harness_usage_limit"
        current = current.__cause__ or current.__context__
    model_code = classified_model_error(exc)
    if model_code:
        return model_code
    if isinstance(exc, UsageLimitExceeded):
        return "harness_usage_limit"
    exception_text = str(getattr(exc, "message", exc)).lower()
    if "token limit" in exception_text or "context length" in exception_text:
        return "model_context_limit"
    return None


__all__ = [
    "build_integral_host_turn",
    "Agent",
    "AgentRunResultEvent",
    "BinaryContent",
    "CancellationToken",
    "ClearToolResults",
    "build_integral_history_compaction",
    "ContinuableSnapshot",
    "ConversationSearch",
    "FunctionToolCallEvent",
    "FunctionToolResultEvent",
    "Instrumentation",
    "LiteLLMProvider",
    "ModelMessagesTypeAdapter",
    "ModelRequest",
    "ModelResponse",
    "ModelRetry",
    "OpenAIChatModel",
    "PartDeltaEvent",
    "PartEndEvent",
    "PartStartEvent",
    "PlanItem",
    "PlanStore",
    "Planning",
    "IntegralToolDisclosure",
    "RetryPromptPart",
    "RunContext",
    "RunRecord",
    "SchemaValidator",
    "Skills",
    "SnapshotHistorySource",
    "StepEvent",
    "StepPersistence",
    "StepStore",
    "SystemPromptPart",
    "TaskStatus",
    "TextPart",
    "TextPartDelta",
    "ThinkingPart",
    "Tool",
    "ToolEffectRecord",
    "ToolReturn",
    "ToolCallPart",
    "ToolReturnPart",
    "ToolSearch",
    "TextOutput",
    "ToolOutput",
    "UsageLimitExceeded",
    "UsageLimits",
    "UserPromptPart",
    "build_integral_json_schema_tool",
    "build_integral_context_compaction",
    "build_integral_run_instructions",
    "classify_integral_harness_exception",
    "continue_run",
    "core_schema",
    "to_jsonable_python",
]


@dataclass
class InstructionDisclosureGuard(AbstractCapability[Any]):
    """Remove literal instruction copies before processing or checkpointing.

    Append last: the pinned framework runs after-model hooks in reverse order.
    Stream deltas remain private until the provider's settled-output buffer
    publishes the modified result. This does not detect semantic paraphrases.
    """

    protected_instructions: str

    async def after_model_request(self, ctx, *, request_context, response):
        """Replace unsafe text or tool arguments before any tool dispatch."""
        from app.agentive.harness.disclosure import instruction_output_validator

        inspect_output = instruction_output_validator(self.protected_instructions)
        text = "".join(
            part.content for part in response.parts if isinstance(part, TextPart)
        )
        candidates = [
            text,
            *(
                str(getattr(part, "args", ""))
                for part in response.parts
                if part.part_kind == "tool-call"
            ),
        ]
        for candidate in candidates:
            safe = inspect_output(candidate)
            if safe != candidate:
                return replace(response, parts=[TextPart(safe)])
        return response


class PublicConversationHistorySource:
    """Project scoped checkpoints to conversation search without host context.

    Private instructions/reasoning remain in the encrypted restore store; the
    searchable tool corpus contains human turns, safe answers and tool results.
    """

    def __init__(self, source: Any, *, protected_instructions: str):
        self._source = source
        self._protected = protected_instructions

    async def list_runs(self):
        """Retain the underlying scoped run inventory."""
        return await self._source.list_runs()

    async def run_history(self, *, run_id: str):
        """Remove host-only parts before the search tool can render them."""
        guard = InstructionDisclosureGuard(protected_instructions=self._protected)
        public = []
        for message in await self._source.run_history(run_id=run_id):
            if isinstance(message, ModelRequest):
                parts = [
                    part
                    for part in message.parts
                    if isinstance(part, (UserPromptPart, ToolReturnPart))
                ]
                if parts:
                    public.append(replace(message, parts=parts, instructions=None))
            elif isinstance(message, ModelResponse):
                response = await guard.after_model_request(
                    None, request_context=None, response=message
                )
                parts = [
                    part
                    for part in response.parts
                    if isinstance(part, TextPart) or part.part_kind == "tool-call"
                ]
                if parts:
                    public.append(replace(response, parts=parts))
        return public
