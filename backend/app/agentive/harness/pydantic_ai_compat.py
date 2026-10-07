"""Compatibility boundary for the third-party Pydantic AI Harness APIs.

Integral's tenant, policy, billing, tool-catalogue, and persistence contracts
must not depend on Pydantic AI implementation details. Imports and narrowly
version-sensitive construction live here so a library upgrade can be handled
at this seam and qualified by adapter contract tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
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
    TextPart,
    TextPartDelta,
    ThinkingPart,
    Tool,
    UsageLimitExceeded,
    UsageLimits,
)
from pydantic_ai.capabilities import Instrumentation, ToolSearch
from pydantic_ai.messages import (
    LoadCapabilityCallPart,
    LoadCapabilityReturnPart,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ToolAvailabilityDeltaPart,
    ToolReturn,
    ToolReturnPart,
    UserPromptPart,
    post_compaction_window,
)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.litellm import LiteLLMProvider
from pydantic_ai_harness import Planning, Skills, StepPersistence
from pydantic_ai_harness.compaction import (
    ClearToolResults,
    SummarizingCompaction,
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


@dataclass
class IntegralToolDisclosure(ToolSearch):
    """Use unified catalog search with framework-owned deferred disclosure.

    Search returns ``ToolReturn.tools``; Pydantic AI records and replays that
    availability itself. Supported ToolSearch extension points and toolset
    filtering remove the duplicate search entrypoint, without altering skill
    loading, history or library code. Used only with search_capabilities.
    """

    require_initial_search: bool = True
    pending_decision_tool: str | None = None
    continuation_tools: frozenset[str] = frozenset()

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
                if not any(name.startswith("integral_") for name in available):
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
                if candidates and not candidates.intersection(
                    ctx.active_capability_ids
                ):
                    # The model chooses the fitting returned procedure. Core
                    # metadata requires a procedure to be loaded, without
                    # selecting it from user wording or assigning authority.
                    resolved["tool_choice"] = ["load_capability"]
            return resolved

        return settings

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
) -> Callable[[RunContext[Any]], str]:
    """Add current framework-owned capability state to each model request.

    Pydantic AI Harness intentionally keeps loaded capabilities in its discovery
    catalog for prompt-cache stability. Some models repeatedly reload an active
    skill after tool results. Make the authoritative active set explicit at the
    adapter boundary so they can continue with the already-loaded procedure.
    """

    def instructions(ctx: RunContext[Any]) -> str:
        active_ids = sorted(ctx.active_capability_ids)
        blocks = [base_instructions]
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


def build_integral_context_compaction() -> ClearToolResults:
    """Construct the adapter-owned context policy for Integral tool turns.

    Preserve the recent working set: filing needs the source, destination,
    schema and duplicate check together before proposing its writes. A fixed
    12k trigger retaining one result discarded those inputs during ordinary
    receipt filing. The observed DeepSeek turn spent nearly 596k input tokens
    across nine requests, with a 79k peak input, while generating only 12.6k
    output tokens. A large provider context window delayed fractional
    compaction until beyond a useful working set. The follow-up Notes rename
    measured 501,113 input tokens over 17 requests, with a 43,929-token peak;
    repeated tool results reached 83k characters alongside 48k of tool schemas.
    Trigger at 16k estimated conversation tokens while retaining the five most recent tool pairs and
    keeping capability-load parts intact because the Harness derives active
    skill state from them.
    """
    return ClearToolResults(
        max_tokens=16_384,
        # Keep the active decision window while releasing old tool payloads early.
        # Five pairs preserve the full filing context and recent scaffold
        # receipts without replaying every older payload into each model request.
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

    async def before_model_request(self, ctx, request_context):
        # Pydantic AI 2.10 makes request_context.messages request-only.
        # Persist compaction through the documented RunContext.messages seam
        # so StepPersistence and later turns receive the same working history.
        before = list(request_context.messages)
        processed = await super().before_model_request(ctx, request_context)
        if processed.messages != before:
            ctx.messages[:] = processed.messages
        return processed

    async def compact(self, messages, ctx):
        # Keep the active user request and its tool sequence exact. Summarizing
        # it mid-batch can erase a queued effect receipt and provoke restaging.
        # ClearToolResults still bounds older payloads; the library usage limits
        # bound a long active turn. Summarization owns completed-turn history.
        turn_start = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if isinstance(messages[index], ModelRequest)
                and any(
                    isinstance(part, UserPromptPart) for part in messages[index].parts
                )
            ),
            0,
        )
        history, active_turn = messages[:turn_start], messages[turn_start:]
        if (
            len(history) <= self.keep_messages
            and len(ModelMessagesTypeAdapter.dump_json(history))
            <= self.max_history_bytes
        ):
            return messages
        compacted = await super().compact(history, ctx)
        if compacted == history:
            return messages
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
    if isinstance(exc, UsageLimitExceeded):
        return "harness_usage_limit"
    exception_text = str(getattr(exc, "message", exc)).lower()
    if "token limit" in exception_text or "context length" in exception_text:
        return "model_context_limit"
    return None


__all__ = [
    "Agent",
    "AgentRunResultEvent",
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
    "ToolReturnPart",
    "ToolSearch",
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
