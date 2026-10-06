"""Compatibility boundary for the third-party Pydantic AI Harness APIs.

Integral's tenant, policy, billing, tool-catalogue, and persistence contracts
must not depend on Pydantic AI implementation details. Imports and narrowly
version-sensitive construction live here so a library upgrade can be handled
at this seam and qualified by adapter contract tests.
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    CancellationToken,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelRetry,
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
    ModelMessagesTypeAdapter,
    ModelRequest,
    ToolReturn,
    UserPromptPart,
)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.litellm import LiteLLMProvider
from pydantic_ai_harness import Planning, Skills, StepPersistence
from pydantic_ai_harness.compaction import ClearToolResults
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


class IntegralToolDisclosure(ToolSearch):
    """Use unified catalog search with framework-owned deferred disclosure.

    Search returns ``ToolReturn.tools``; Pydantic AI records and replays that
    availability itself. Supported ToolSearch extension points and toolset
    filtering remove the duplicate search entrypoint, without altering skill
    loading, history or library code. Used only with search_capabilities.
    """

    def get_native_tools(self):
        return []

    def get_wrapper_toolset(self, toolset):
        return (
            super()
            .get_wrapper_toolset(toolset)
            .filtered(
                lambda _ctx, definition: definition.name != self.function_tool_name
            )
        )


def build_integral_json_schema_tool(
    *,
    function: Callable[..., Any],
    name: str,
    description: str,
    json_schema: dict[str, Any],
    prepare: Callable[..., Any] | None,
    defer_loading: bool,
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
        name=name,
        description=description,
        json_schema=dict(json_schema),
    )
    tool.prepare = prepare
    tool.defer_loading = defer_loading
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
        if not active_ids:
            return base_instructions
        active_list = ", ".join(active_ids)
        return (
            f"{base_instructions}\n\n"
            f"Integral runtime state: these capabilities are already loaded and "
            f"their instructions are active: {active_list}. Do not call "
            "load_capability for them again. Continue the current workflow using "
            "the loaded instructions and their tools."
        )

    return instructions


def build_integral_context_compaction() -> ClearToolResults:
    """Construct the adapter-owned context policy for Integral tool turns.

    Preserve the newest tool result so the model can act on its outcome. Older
    call arguments and results are recoverable from Integral's durable audit
    records and should not be replayed into every subsequent provider request.
    Keep capability-load parts intact because the Harness derives active skill
    state from them.
    """
    return ClearToolResults(
        max_tokens=12_000,
        keep_pairs=1,
        exclude_tools=frozenset({"load_capability"}),
        clear_tool_inputs=True,
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
    "ContinuableSnapshot",
    "ConversationSearch",
    "FunctionToolCallEvent",
    "FunctionToolResultEvent",
    "Instrumentation",
    "LiteLLMProvider",
    "ModelMessagesTypeAdapter",
    "ModelRequest",
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
    "TaskStatus",
    "TextPart",
    "TextPartDelta",
    "ThinkingPart",
    "Tool",
    "ToolEffectRecord",
    "ToolReturn",
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
