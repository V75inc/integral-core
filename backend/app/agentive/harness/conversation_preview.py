"""Project only the public answer field from Pydantic's ordinary routing pass."""

from time import monotonic

from app.agentive.harness.events import PydanticAIEventTranslator
from app.agentive.harness.pydantic_ai_compat import (
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    ToolCallPart,
    from_json,
)
from app.services.id_resolver import find_ids
from app.services.model_retry_status import emit_conversation_preview


class ConversationPreview:
    """Observe one model request. The framework still owns routing and validation."""

    def __init__(self, *, prompted, authorize):
        self.prompted = prompted
        self.authorize = authorize
        self.sent = ""
        self._observed = False
        self._last_check = 0.0

    async def discard(self):
        if self.sent:
            await emit_conversation_preview(delta=None)
            self.sent = ""

    async def observe(self, ctx, events):
        if self._observed:
            # A second request may be a validation retry or a steering correction.
            # Let Pydantic settle it without displaying another draft.
            return
        self._observed = True
        index = None
        current = None
        async for event in events:
            if isinstance(event, PartStartEvent):
                if not isinstance(event.part, (TextPart, ToolCallPart)):
                    continue
                if index is not None:
                    return
                index = event.index
                current = event.part
            elif (
                isinstance(event, PartDeltaEvent)
                and event.index == index
                and current is not None
            ):
                current = event.delta.apply(current)
            else:
                continue
            if self.prompted and isinstance(current, TextPart):
                raw = current.content
            elif (
                not self.prompted
                and isinstance(current, ToolCallPart)
                and current.tool_name == "answer_conversation"
            ):
                raw = current.args
            else:
                continue
            # Bound parsing and persistence per long answer. All remaining text
            # still comes through the ordinary final-result adapter.
            if raw is None:
                continue
            if not isinstance(raw, str) or len(raw) > 65536:
                return
            if monotonic() - self._last_check < 0.075:
                continue
            try:
                fields = from_json(raw, allow_partial="trailing-strings")
            except ValueError:
                continue
            if not isinstance(fields, dict) or (
                self.prompted
                and (
                    fields.get("route") != "answer_conversation"
                    or "text" not in fields
                    or list(fields).index("text") < list(fields).index("route")
                )
            ):
                continue
            text = fields.get("text")
            if not isinstance(text, str) or not text.startswith(self.sent):
                continue
            # Hold an unfinished word and any protocol-shaped output until
            # settlement. Never project provider thought, raw JSON, or tool arguments.
            end = max(text.rfind(" "), text.rfind("\n")) + 1
            # Languages without spaces can still release bounded spans.
            if len(text) - max(end, len(self.sent)) >= 32 and not text[-1].isascii():
                end = len(text)
            candidate = text[:end]
            if len(candidate) <= len(self.sent):
                continue
            # Resolve resource references once, in complete Markdown context.
            if find_ids(candidate) or not PydanticAIEventTranslator.can_preview_prose(
                candidate
            ):
                return
            await self.authorize()
            await emit_conversation_preview(delta=candidate[len(self.sent) :])
            self.sent = candidate
            self._last_check = monotonic()
