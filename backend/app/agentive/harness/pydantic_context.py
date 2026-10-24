"""Bound the native harness working set through public Pydantic lifecycle APIs.

This is a context projection, never an authority or effect ledger. Only settled
disclosure evidence expires; the current task and unresolved effects stay exact.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace

from pydantic_ai import BinaryContent, ModelRequestNode, UsageLimitExceeded
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import (
    CapabilityInstructionSource,
    InstructionPart,
    LoadCapabilityCallPart,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    NativeToolSearchReturnPart,
    RetryPromptPart,
    ToolAvailabilityDeltaPart,
    ToolCallPart,
    ToolReturnPart,
    ToolSearchReturnPart,
    UserPromptPart,
)
from pydantic_ai_harness.compaction import (
    estimate_context_tokens,
    estimate_token_count,
    resolve_context_window,
)

logger = logging.getLogger(__name__)


class IntegralContextOverflow(UsageLimitExceeded):
    """The protected task cannot fit safely without losing execution evidence."""


@dataclass(frozen=True)
class IntegralContextPolicy:
    """Working-set ceilings, independent of conversation age and catalogue size."""

    skill_bytes: int = 32_768
    revealed_tools: int = 16
    trigger_tokens: int = 24_576
    target_tokens: int = 16_384
    input_tokens: int = 65_536
    fallback_window: int = 32_768

    def input_limit(self, model, settings) -> int:
        window = resolve_context_window(model) or self.fallback_window
        output = (settings or {}).get("max_tokens") or 4096
        return max(0, min(self.input_tokens, window - output - max(1024, window // 10)))


def active_turn_start(messages) -> int | None:
    """Only accepted human input or a typed Core host event starts a task."""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, ModelRequest):
            continue
        if any(isinstance(part, UserPromptPart) for part in message.parts):
            return index
        marker = (message.metadata or {}).get("integral_host_turn_v1")
        if isinstance(marker, str) and marker and marker == marker.strip():
            return index
    return None


def protected_work_start(messages) -> int:
    """Pending proposals retain their source task until Core reconciles them."""
    boundary = active_turn_start(messages) or 0
    task_start = 0
    for index, message in enumerate(messages):
        if active_turn_start([message]) is not None:
            task_start = index
        for part in message.parts:
            if isinstance(part, ToolReturnPart) and isinstance(part.content, dict):
                content = part.content
                if content.get("_kind") == "staged_change" and content.get(
                    "state"
                ) not in {"consumed", "revoked", "expired"}:
                    boundary = min(boundary, task_start)
    return boundary


def _discovered(part) -> list[str]:
    if isinstance(part, ToolAvailabilityDeltaPart):
        return list(part.tools_added)
    if isinstance(part, (ToolSearchReturnPart, NativeToolSearchReturnPart)):
        return [item["name"] for item in part.discovered_tools]
    if isinstance(part, ToolReturnPart) and part.tool_name == "search_tools":
        names = (part.metadata or {}).get("discovered_tools", ())
        if not isinstance(names, (list, tuple)):
            return []
        return [name for name in names if isinstance(name, str)]
    return []


def project_disclosures(messages, policy: IntegralContextPolicy):
    """Expire complete old loads and discovery, preserving pair structure.

    Keep the active task and its preceding task for follow-up continuity. Older
    pending staging or incomplete tool exchanges protect their entire task. If
    protected work alone is too large, admission fails instead of deleting it.
    No procedure body is shortened and no successful load is fabricated.
    """
    if not messages or (
        isinstance(messages[-1], ModelResponse) and messages[-1].state == "suspended"
    ):
        return messages
    starts = []
    for index, message in enumerate(messages):
        if active_turn_start([message]) is not None:
            starts.append(index)
    protected_start = starts[-2] if len(starts) > 1 else 0
    calls, pairs, discoveries = {}, {}, {}
    unresolved_start = protected_start
    task_start = 0
    for index, message in enumerate(messages):
        if index in starts:
            task_start = index
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                calls[part.tool_call_id] = (part, task_start)
                if not isinstance(part, LoadCapabilityCallPart):
                    discoveries[part.tool_name] = index
            elif isinstance(part, RetryPromptPart):
                # Validation/retry feedback settles this model tool attempt;
                # it is not an unknown external effect awaiting completion.
                calls.pop(part.tool_call_id, None)
            elif isinstance(part, ToolReturnPart):
                call = calls.pop(part.tool_call_id, None)
                if call and isinstance(call[0], LoadCapabilityCallPart):
                    pairs[part.tool_call_id] = (call[0], part, index)
                content = part.content
                if (
                    isinstance(content, dict)
                    and content.get("_kind") == "staged_change"
                    and content.get("state") not in {"consumed", "revoked", "expired"}
                ):
                    unresolved_start = min(unresolved_start, task_start)
            for name in _discovered(part):
                discoveries[name] = index
    for _call, start in calls.values():
        unresolved_start = min(unresolved_start, start)
    protected_start = min(protected_start, unresolved_start)

    # Most recent successful load wins. Historical duplicate activations are
    # unnecessary but must be removed as a complete pair, never body-only.
    retained, used_bytes, skills = set(), 0, set()
    for call_id, (call, result, index) in reversed(list(pairs.items())):
        size = len(ModelMessagesTypeAdapter.dump_json([ModelRequest(parts=[result])]))
        if index >= protected_start:
            retained.add(call_id)
            skills.add(call.capability_id)
        elif (
            call.capability_id not in skills and used_bytes + size <= policy.skill_bytes
        ):
            retained.add(call_id)
            skills.add(call.capability_id)
            used_bytes += size
    expired = set(pairs) - retained
    recent = sorted(discoveries, key=discoveries.get, reverse=True)
    tools = set(recent[: policy.revealed_tools]) | {
        name for name, index in discoveries.items() if index >= protected_start
    }
    projected = []
    for index, message in enumerate(messages):
        parts = []
        for part in message.parts:
            if (
                isinstance(part, (ToolCallPart, ToolReturnPart))
                and part.tool_call_id in expired
            ):
                continue
            if index < protected_start:
                if isinstance(part, ToolAvailabilityDeltaPart):
                    names = [name for name in part.tools_added if name in tools]
                    if not names:
                        continue
                    part = replace(part, tools_added=names)
                elif isinstance(
                    part, (ToolSearchReturnPart, NativeToolSearchReturnPart)
                ):
                    part = replace(
                        part,
                        content={
                            **part.content,
                            "discovered_tools": [
                                item
                                for item in part.discovered_tools
                                if item["name"] in tools
                            ],
                        },
                    )
                elif (
                    isinstance(part, ToolReturnPart)
                    and part.tool_name == "search_tools"
                ):
                    metadata = part.metadata or {}
                    if "discovered_tools" in metadata:
                        part = replace(
                            part,
                            metadata={
                                **metadata,
                                "discovered_tools": [
                                    n for n in _discovered(part) if n in tools
                                ],
                            },
                        )
            parts.append(part)
        if parts or not message.parts:
            projected.append(
                replace(message, parts=parts) if parts != message.parts else message
            )
    return projected


@dataclass
class IntegralContextLifecycle(AbstractCapability):
    """Normalize before instruction/tool preparation, not after availability resolves."""

    policy: IntegralContextPolicy = IntegralContextPolicy()

    async def wrap_node_run(self, ctx, *, node, handler):
        suspended = bool(
            ctx.messages
            and isinstance(ctx.messages[-1], ModelResponse)
            and ctx.messages[-1].state == "suspended"
        )
        if isinstance(node, ModelRequestNode) and not suspended:
            # Include the upcoming user/host boundary, but leave the native node
            # responsible for appending its request and dispatching tools.
            combined = [*ctx.messages, node.request]
            normalized = project_disclosures(combined, self.policy)
            if normalized != combined:
                # Reported usage measures the earlier, larger physical prompt.
                # Record reclaim against that anchor without editing usage or
                # its accounting ledger. A new provider response supersedes it.
                reclaimed = max(
                    0, estimate_token_count(combined) - estimate_token_count(normalized)
                )
                for index in range(len(normalized) - 1, -1, -1):
                    message = normalized[index]
                    if (
                        isinstance(message, ModelResponse)
                        and message.usage.input_tokens
                    ):
                        metadata = message.metadata or {}
                        normalized[index] = replace(
                            message,
                            metadata={
                                **metadata,
                                "integral_context_reclaimed_tokens_v1": metadata.get(
                                    "integral_context_reclaimed_tokens_v1", 0
                                )
                                + reclaimed,
                            },
                        )
                        break
                ctx.messages[:] = normalized[:-1]
        return await handler(node)

    async def before_model_request(self, ctx, request_context):
        # Native Skills renders the whole deferred description catalogue in an
        # addressed dynamic instruction. Core already exposes authorized
        # paged search, so listing every installed App is redundant. This exact
        # typed source is pinned by integration tests; no user text or
        # procedure body is interpreted, and no capability is registered here.
        if "search_capabilities" in ctx.tools:
            parameters = request_context.model_request_parameters
            parts = [
                (
                    replace(
                        part,
                        content=(
                            "Discover authorized workflows with search_capabilities. "
                            "Load the selected workflow using load_capability before using its tools. "
                            "Previously expired workflows can be searched for and loaded again."
                        ),
                    )
                    if part.dynamic
                    and part.id is not None
                    and isinstance(part.id.source, CapabilityInstructionSource)
                    and part.id.source.id == "deferred_capabilities"
                    else part
                )
                for part in parameters.instruction_parts or ()
            ]
            request_context.model_request_parameters = replace(
                parameters, instruction_parts=parts
            )
            # Public estimation reads ModelRequest.instructions, whereas the
            # provider reads instruction_parts. Reflect the same projection in
            # working history now, before compaction estimates it; Pydantic
            # also records this rendering after the before-request chain.
            messages = list(request_context.messages)
            for index in range(len(messages) - 1, -1, -1):
                if isinstance(messages[index], ModelRequest):
                    messages[index] = replace(
                        messages[index], instructions=InstructionPart.join(parts)
                    )
                    break
            request_context.messages = messages
            ctx.messages[:] = messages
        return request_context


def request_estimate(request_context) -> int:
    """Count current instructions/schemas once; never checkpoint JSON overhead."""
    estimated = estimate_context_tokens(
        request_context.messages,
        model_request_parameters=request_context.model_request_parameters,
    )
    for message in reversed(request_context.messages):
        if isinstance(message, ModelResponse) and message.usage.input_tokens:
            reclaimed = (message.metadata or {}).get(
                "integral_context_reclaimed_tokens_v1", 0
            )
            if isinstance(reclaimed, int) and reclaimed > 0:
                estimated = max(0, estimated - reclaimed)
            break
    return estimated


def _part_size(part) -> int:
    """Text dimensions; image byte/base64 length is not a token measurement."""
    content = getattr(part, "content", getattr(part, "args", ""))
    if isinstance(part, UserPromptPart) and isinstance(content, (list, tuple)):
        # Core already validates media bytes/pixels and permission at dispatch.
        # Reserve 4096 tokens/image independently of compressed file size. This
        # is a conservative calibration, not provider-reported vision usage.
        return sum(
            16_384 if isinstance(item, BinaryContent) else len(str(item))
            for item in content
        )
    return len(json.dumps(content, default=str))


@dataclass
class IntegralContextAdmission(AbstractCapability):
    """Final non-inference guard; protected overflow never buys another model retry."""

    policy: IntegralContextPolicy = IntegralContextPolicy()

    async def before_model_request(self, ctx, request_context):
        estimated = request_estimate(request_context)
        limit = self.policy.input_limit(
            request_context.model, request_context.model_settings
        )
        # An independent wire-text estimate prevents optimistic usage anchors
        # from masking newly enlarged skill or catalogue instructions.
        parameters = request_context.model_request_parameters
        text_chars = sum(
            len(part.content) for part in parameters.instruction_parts or ()
        )
        text_chars += sum(
            _part_size(part)
            for message in request_context.messages
            for part in message.parts
        )
        schema_chars = sum(
            len(json.dumps(tool.parameters_json_schema, default=str))
            for tool in parameters.function_tools
            if not tool.defer_loading or tool.name in parameters.revealed_tool_names
        )
        estimate = max(estimated, (text_chars + schema_chars + 3) // 4)
        logger.info(
            "Harness context estimated_tokens=%d limit=%d messages=%d schema_chars=%d",
            estimate,
            limit,
            len(request_context.messages),
            schema_chars,
        )
        if estimate > limit:
            logger.warning(
                "Harness context overflow estimate=%d anchored=%d limit=%d text_chars=%d schema_chars=%d messages=%d",
                estimate,
                estimated,
                limit,
                text_chars,
                schema_chars,
                len(request_context.messages),
            )
            raise IntegralContextOverflow(
                "The current task is too large for the model's safe context budget. "
                "Its pending changes have been preserved. Narrow the request or select a model with a larger context window."
            )
        return request_context
