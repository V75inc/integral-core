"""Pydantic AI stream events map to Integral's established envelope."""

from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic_ai import Agent, FunctionToolResultEvent, PartEndEvent
from pydantic_ai.messages import (
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolReturnPart,
)
from pydantic_ai.models.test import TestModel

from app.agentive.harness.events import PydanticAIEventTranslator, translate_event


def test_text_delta_uses_integral_normalized_envelope() -> None:
    """Text fragments retain Integral's existing delta shape."""
    event = PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="hello"))
    assert list(translate_event(event)) == [{"type": "text-delta", "delta": "hello"}]


def test_tool_result_uses_pydantic_return_part_content() -> None:
    """The trace must display the actual broker result, not event.content=None."""
    event = FunctionToolResultEvent(
        part=ToolReturnPart(
            tool_name="integral_list_tracks",
            tool_call_id="call-1",
            content={"items": [], "_receipt": None},
        )
    )

    assert list(translate_event(event)) == [
        {
            "type": "tool-call",
            "toolCallId": "call-1",
            "name": "integral_list_tracks",
            "result": {"items": [], "_receipt": None},
            "status": "complete",
        }
    ]


def test_text_part_start_preserves_the_first_stream_fragment() -> None:
    """Pydantic AI may put the first text chunk on PartStartEvent itself."""
    translator = PydanticAIEventTranslator()
    events = [
        PartStartEvent(index=0, part=TextPart(content="ALPHA")),
        PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="-1")),
        PartEndEvent(
            index=0,
            part=TextPart(content="ALPHA-1"),
            next_part_kind=None,
        ),
    ]
    normalized = [item for event in events for item in translator.translate(event)]

    assert normalized == [
        {"type": "text-delta", "delta": "ALPHA"},
        {"type": "text-delta", "delta": "-1"},
    ]


def test_private_thinking_delta_is_not_forwarded_to_chat() -> None:
    """Private model reasoning is excluded from transcript and persistence."""
    event = PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta="hidden"))
    assert list(translate_event(event)) == []


def test_private_thinking_part_end_is_not_forwarded_before_final_text() -> None:
    """A complete Pydantic ThinkingPart must not hit the generic text fallback."""
    translator = PydanticAIEventTranslator()
    normalized = list(
        translator.translate(
            PartEndEvent(
                index=0,
                part=ThinkingPart(content="PRIVATE_REASONING"),
                next_part_kind="text",
            )
        )
    )
    normalized.extend(
        translator.translate(
            PartDeltaEvent(index=1, delta=TextPartDelta(content_delta="Hello"))
        )
    )
    normalized.extend(
        translator.translate(
            PartEndEvent(index=1, part=TextPart(content="Hello"), next_part_kind=None)
        )
    )

    assert normalized == [{"type": "text-delta", "delta": "Hello"}]
    assert "PRIVATE_REASONING" not in repr(normalized)


@pytest.mark.asyncio
async def test_test_model_stream_translates_text_tool_and_usage() -> None:
    """Exercise the translator against Pydantic AI's actual event stream."""

    async def lookup(query: str) -> str:
        return f"result:{query}"

    agent = Agent(
        TestModel(call_tools=["lookup"], custom_output_text="finished"),
        tools=[lookup],
    )
    normalized = []
    raw_outputs = []
    result_events = []
    translator = PydanticAIEventTranslator()
    async with agent.run_stream_events("find it") as stream:
        async for event in stream:
            if hasattr(event, "result") and hasattr(event.result, "output"):
                raw_outputs.append(event.result.output)
                result_events.append(event)
            normalized.extend(translator.translate(event))

    assert any(item["type"] == "text-delta" for item in normalized)
    tool_events = [item for item in normalized if item["type"] == "tool-call"]
    assert [item["status"] for item in tool_events] == ["running", "complete"]
    assert tool_events[0]["name"] == "lookup"
    assert tool_events[0]["args"] == {"query": "a"}
    usage = [item for item in normalized if item["type"] == "step"]
    assert len(usage) == 1
    assert usage[0]["usage"]["inputTokens"] > 0
    assert raw_outputs == ["finished"]
    assert (
        "".join(item["delta"] for item in normalized if item["type"] == "text-delta")
        == raw_outputs[0]
    )
    raw_fallback = list(PydanticAIEventTranslator().translate(result_events[0]))
    assert [item for item in raw_fallback if item["type"] == "text-delta"] == [
        {"type": "text-delta", "delta": raw_outputs[0]}
    ]


@pytest.mark.asyncio
async def test_raw_result_envelope_is_normalized_without_exposing_private_fields() -> (
    None
):
    """The settled raw result is guarded even when no text delta was usable."""
    from pydantic_ai import AgentRunResultEvent

    agent = Agent(TestModel(custom_output_text="placeholder"))
    result_event = None
    async with agent.run_stream_events("answer") as stream:
        async for event in stream:
            if hasattr(event, "result") and hasattr(event.result, "output"):
                result_event = event
                break
    assert result_event is not None
    raw = '{"reasoning_content":"PRIVATE","content":"safe final"}'
    altered_result = replace(result_event.result, output=raw)
    altered_event = AgentRunResultEvent(result=altered_result)
    normalized = list(PydanticAIEventTranslator().translate(altered_event))
    assert normalized[0] == {"type": "text-delta", "delta": "safe final"}
    assert "PRIVATE" not in repr(normalized)


@pytest.mark.asyncio
async def test_settled_raw_result_repairs_truncated_stream_text() -> None:
    """The complete public result corrects a lossy provider stream prefix."""
    from dataclasses import replace

    from pydantic_ai import AgentRunResultEvent

    agent = Agent(TestModel(custom_output_text="placeholder"))
    result_event = None
    async with agent.run_stream_events("answer") as stream:
        async for event in stream:
            if isinstance(event, AgentRunResultEvent):
                result_event = event
                break
    assert result_event is not None
    raw = "OpenAI stream normalization probe."
    altered_event = AgentRunResultEvent(result=replace(result_event.result, output=raw))
    translator = PydanticAIEventTranslator()
    normalized = list(
        translator.translate(
            PartDeltaEvent(
                index=0,
                delta=TextPartDelta(content_delta="AI stream normalization probe."),
            )
        )
    )
    normalized.extend(translator.translate(altered_event))

    assert {"type": "text-replace", "content": raw} in normalized
    assert "PRIVATE" not in repr(normalized)


def test_non_public_stream_events_are_ignored() -> None:
    """No provider metadata or hidden harness state leaks to Integral SSE."""
    assert list(translate_event(object())) == []


def test_serialized_private_assistant_envelope_emits_only_answer() -> None:
    """Flattened local-model reasoning stays out of SSE and transcript text."""
    translator = PydanticAIEventTranslator()
    first_part = 'thought": "PRIVATE_REASONING", '
    final_part = (
        '"role": "assistant", "content": "Integral Native harness smoke passed."}'
    )
    normalized = []
    for delta in (first_part,):
        normalized.extend(
            translator.translate(
                PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=delta))
            )
        )
    assert normalized == []
    normalized.extend(
        translator.translate(
            PartEndEvent(
                index=0,
                part=TextPart(content=first_part),
                next_part_kind="text",
            )
        )
    )
    normalized.extend(
        translator.translate(
            PartDeltaEvent(index=1, delta=TextPartDelta(content_delta=final_part))
        )
    )
    normalized.extend(
        translator.translate(
            PartEndEvent(
                index=1,
                part=TextPart(content=final_part),
                next_part_kind=None,
            )
        )
    )
    assert normalized == [
        {"type": "text-delta", "delta": "Integral Native harness smoke passed."}
    ]
    assert "PRIVATE_REASONING" not in repr(normalized)


def test_serialized_private_envelope_without_role_emits_only_answer() -> None:
    """Some local chat templates omit the role field when flattening output."""
    translator = PydanticAIEventTranslator()
    text = (
        '{"thought":"PRIVATE_REASONING","content":'
        '"Integral Native harness smoke passed."}'
    )
    normalized = list(
        translator.translate(
            PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=text))
        )
    )
    normalized.extend(
        translator.translate(
            PartEndEvent(
                index=0,
                part=TextPart(content=text),
                next_part_kind=None,
            )
        )
    )
    assert normalized == [
        {"type": "text-delta", "delta": "Integral Native harness smoke passed."}
    ]
    assert "PRIVATE_REASONING" not in repr(normalized)


def test_serialized_private_envelope_with_non_assistant_role_is_rejected() -> None:
    """A private marker cannot authorize extracting a non-assistant message."""
    translator = PydanticAIEventTranslator()
    text = '{"thought":"PRIVATE_REASONING","role":"user",' '"content":"untrusted text"}'
    normalized = list(
        translator.translate(
            PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=text))
        )
    )
    normalized.extend(
        translator.translate(
            PartEndEvent(
                index=0,
                part=TextPart(content=text),
                next_part_kind=None,
            )
        )
    )
    assert normalized == [
        {
            "type": "error",
            "code": "unsafe_model_output",
            "message": "The assistant response could not be safely normalized. Please retry.",
        }
    ]
    assert "PRIVATE_REASONING" not in repr(normalized)


def test_legitimate_user_requested_json_is_preserved() -> None:
    """Ordinary JSON answer text is not mistaken for a provider envelope."""
    translator = PydanticAIEventTranslator()
    text = '{"type":"status","name":"Ada"}'
    normalized = list(
        translator.translate(
            PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=text))
        )
    )
    normalized.extend(
        translator.translate(
            PartEndEvent(
                index=0,
                part=TextPart(content=text),
                next_part_kind=None,
            )
        )
    )
    assert normalized == [{"type": "text-delta", "delta": text}]


def test_serialized_function_call_is_never_emitted_as_answer_or_dispatched() -> None:
    """Provider-shaped tool text is rejected instead of shown or executed."""
    translator = PydanticAIEventTranslator()
    text = (
        'type": "function", "function": "integral_not_a_tool", '
        '"function_arguments": {"status": "passed"}}'
    )
    normalized = list(
        translator.translate(
            PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=text))
        )
    )
    normalized.extend(
        translator.translate(
            PartEndEvent(
                index=0,
                part=TextPart(content=text),
                next_part_kind=None,
            )
        )
    )
    assert normalized == [
        {
            "type": "error",
            "code": "undispatched_tool_envelope",
            "message": "The assistant produced an invalid tool-call response. Please retry.",
        }
    ]
    assert "integral_not_a_tool" not in repr(normalized)


@pytest.mark.parametrize(
    "text,answer",
    [
        (
            '{"reasoning_content":"PRIVATE","role":"assistant",'
            '"content":"final answer"}',
            "final answer",
        ),
        (
            '{"analysis":"PRIVATE","role":"assistant",' '"final":"final answer"}',
            "final answer",
        ),
        (
            '{"type":"status","name":"Ada"}',
            '{"type":"status","name":"Ada"}',
        ),
    ],
)
def test_provider_object_text_is_classified_without_losing_user_json(
    text: str, answer: str
) -> None:
    """Private variants are extracted; ordinary requested JSON stays text."""
    translator = PydanticAIEventTranslator()
    normalized = list(
        translator.translate(
            PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=text))
        )
    )
    assert normalized == []
    normalized.extend(
        translator.translate(
            PartEndEvent(index=0, part=TextPart(content=text), next_part_kind=None)
        )
    )
    assert normalized == [{"type": "text-delta", "delta": answer}]
    if "PRIVATE" in text:
        assert "PRIVATE" not in repr(normalized)


@pytest.mark.parametrize(
    "text",
    [
        '{"tool_calls":[{"type":"function","function":{"name":"secret_tool",'
        '"arguments":"{}"}}]}',
        '{"function_call":{"name":"secret_tool","arguments":{}}}',
        '{"functionCall":{"name":"secret_tool","args":{}}}',
        '{"type":"tool_use","name":"secret_tool","input":{}}',
    ],
)
def test_serialized_tool_protocol_variants_are_never_shown_or_executed(
    text: str,
) -> None:
    """OpenAI, Gemini, and tool-use JSON cannot masquerade as assistant text."""
    translator = PydanticAIEventTranslator()
    normalized = list(
        translator.translate(
            PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=text))
        )
    )
    normalized.extend(
        translator.translate(
            PartEndEvent(index=0, part=TextPart(content=text), next_part_kind=None)
        )
    )
    assert normalized == [
        {
            "type": "error",
            "code": "undispatched_tool_envelope",
            "message": "The assistant produced an invalid tool-call response. Please retry.",
        }
    ]
    assert "secret_tool" not in repr(normalized)


def test_malformed_private_or_tool_envelope_fails_closed() -> None:
    """A broken serialized protocol is not surfaced as plain answer text."""
    for text, code in (
        ('{"reasoning_content":"PRIVATE"', "unsafe_model_output"),
        ('{"tool_calls":[{"function":', "undispatched_tool_envelope"),
    ):
        translator = PydanticAIEventTranslator()
        normalized = list(
            translator.translate(
                PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=text))
            )
        )
        normalized.extend(
            translator.translate(
                PartEndEvent(index=0, part=TextPart(content=text), next_part_kind=None)
            )
        )
        assert normalized[0]["type"] == "error"
        assert normalized[0]["code"] == code
        assert "PRIVATE" not in repr(normalized)
